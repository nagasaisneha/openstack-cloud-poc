#!/usr/bin/python3
"""Prepare, observe, or cancel the scoped Ubuntu distribution restart.

Only --poweroff-demo requests shutdown; run that mode from Windows after
saving Ubuntu work. All modes require the exact retained demo UUID. Pending
mutation intents are observed on retry, never resubmitted.
"""
import argparse
import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
import time
import uuid

import backup_application as backup
import configure_nova as common
import launch_test_vm as launch
import review_operations as operations
import review_restart_readiness as readiness
import review_wsl_restart as review
import validate_guest as guest
import validate_lifecycle as lifecycle
import validate_service_cycle as cycle

PREPARE_PHASES = ('baseline', 'stop_intent', 'stop_submitted', 'stopped',
                  'workers_stop_intent', 'workers_stop_submitted', 'workers_stopped', 'prepared')
RESUME_PHASES = ('boot_observed', 'cancel_intent', 'workers_start_intent',
                 'workers_start_submitted', 'controls_ready', 'vm_start_intent',
                 'vm_start_submitted', 'vm_active', 'verified', 'cancelled')
POWEROFF_PHASES = ('prepared', 'poweroff_intent', 'poweroff_submitted')


def progress(message):
    print('POC WSL restart: ' + message, flush=True)


def save(path, record, phase):
    if phase not in PREPARE_PHASES + RESUME_PHASES + POWEROFF_PHASES:
        raise readiness.ReadinessError('unknown WSL restart checkpoint phase')
    record.update(phase=phase, updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    record.setdefault('events', []).append({'phase': phase, 'at': record['updated_at']})
    launch.atomic_state(path, record)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    progress(phase)


def load_record(path):
    guest.protected_file(path, 0o600)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise readiness.ReadinessError('restart checkpoint exceeds the bounded size')
    record = json.loads(path.read_text())
    if record.get('server_id') != readiness.DEMO_SERVER or record.get('distro') != review.DISTRO:
        raise readiness.ReadinessError('restart checkpoint target differs from the authorized scope')
    if (set(record.get('units_before', {})) != set(operations.UNITS)
            or set(record.get('workers_before', {})) != set(cycle.STOP_UNITS)
            or set(record.get('shared_before', {})) != set(cycle.SHARED_UNITS)):
        raise readiness.ReadinessError('restart checkpoint lacks the complete original service scope')
    return record


def same_boot(first, second):
    return (first.get('distro') == second.get('distro') == review.DISTRO
            and first.get('kernel_boot_id') == second.get('kernel_boot_id')
            and first.get('init_start_ticks') == second.get('init_start_ticks'))


def latest_review(state, identity):
    candidates = [path for path in launch.DATA_DIR.glob('wsl-restart-review-*.json')
                  if re.fullmatch(r'wsl-restart-review-[0-9a-f]{32}\.json', path.name)]
    if not candidates:
        raise readiness.ReadinessError('completed WSL restart review required before preparation')
    path = max(candidates, key=lambda item: item.stat().st_mtime_ns)
    guest.protected_file(path, 0o600)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise readiness.ReadinessError('WSL review exceeds the bounded size')
    result = json.loads(path.read_text())
    if (result.get('verified') is not True or not result.get('checks')
            or any(value.get('status') != 'passed' for value in result['checks'].values())
            or not same_boot(result.get('boot_identity', {}), identity)
            or result.get('current_readiness', {}).get('cloud', {}).get('project_id') != state['project_id']
            or result.get('current_readiness', {}).get('cloud', {}).get('retained_server') != readiness.DEMO_SERVER
            or result.get('service_cycle', {}).get('cycle_verified') is not True):
        raise readiness.ReadinessError('latest WSL review is incomplete or belongs to another scope/boot')
    return {'path': str(path), 'sha256': backup.checksum(path)}


def boot_policy_ready(policy):
    if (policy.get('wsl_systemd_configured') is not True
            or policy.get('custom_boot_command_present')
            or policy.get('active_units_needing_boot_policy_review')):
        raise readiness.ReadinessError('systemd/boot policy needs review before distro shutdown')
    return policy


def assert_offline():
    domains = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                          'list', '--all', '--uuid'], timeout=15).split()
    stopped = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                          'list', '--all', '--state-shutoff', '--uuid'], timeout=15).split()
    if domains != [readiness.DEMO_SERVER] or stopped != domains:
        raise readiness.ReadinessError('sole retained libvirt guest must remain shut off for the checkpoint')
    if review.autostart()['demo_autostart_enabled']:
        raise readiness.ReadinessError('demo autostart must remain disabled for observed API recovery')


def snapshot(record, path):
    assert_offline()
    cycle.wait_workers('inactive', record['workers_before'])
    cycle.shared_unchanged(record)
    if shutil.disk_usage(launch.DATA_DIR).free < 3 * 1024 ** 3:
        raise readiness.ReadinessError('less than 3 GiB free for a new protected restart recovery point')
    directory = Path(tempfile.mkdtemp(prefix='recovery-' + readiness.DEMO_SERVER + '-wsl-', dir=launch.DATA_DIR))
    record.setdefault('backup_attempts', []).append(str(directory))
    launch.atomic_state(path, record)
    xml = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                      'dumpxml', readiness.DEMO_SERVER], timeout=15)
    (directory / 'domain.xml').write_text(xml)
    disk, metadata = backup.disk_from_xml(xml, readiness.DEMO_SERVER)
    metadata['source_format'] = backup.flatten_disk(disk, directory / 'guest-root.qcow2')
    assert_offline()
    backup.snapshot_journal(directory / 'application-jobs.sqlite')
    backup.snapshot_databases(directory)
    backup.snapshot_operational_files(directory / 'operational-files.tar.gz')
    (directory / 'RECOVERY.md').write_text('''# Protected pre-WSL restart recovery point

Contains credentials; preserve root ownership and directory 0700/file 0600.
The original demo was shut off and seven dedicated workers paused. The new
standalone disk passed qemu-img check/compare. SQLite uses its backup API,
seven InnoDB databases use one transactional dump and each OVSDB uses its
live backup endpoint. These sequential snapshots are not distributed atomic
rollback, a Windows host backup, or offsite protection. Existing RabbitMQ,
images and installed service accounts remain on the original distribution.

Do not overwrite live databases, operational files, Nova disks or an
existing guest. Preserve current data before recovery. Recovering the disk
as a NEW private Glance image/VM requires separately reviewed resource
scope. Full SQL/OVSDB rollback requires a separate service-quiescence and
consistency procedure. No automatic restoration or deletion is performed.
The earlier isolated restore rehearsal used the original application bundle;
this fresh retained-demo recovery point has integrity/byte comparison only.
''')
    saved = dict(metadata, server_id=readiness.DEMO_SERVER, project_id=record['project_id'],
                 run_id=record['run_id'], role='pre_wsl_restart', verified=True,
                 disk_check=True, disk_compare=True, restore_exercised=False,
                 sql_databases=list(backup.DATABASES),
                 sha256={item.name: backup.checksum(item) for item in directory.iterdir() if item.is_file()})
    launch.atomic_state(directory / 'recovery.json', saved)
    backup.sync_bundle(directory)
    backup.verify_bundle(directory, readiness.DEMO_SERVER)
    assert_offline()
    cycle.wait_workers('inactive', record['workers_before'])
    cycle.shared_unchanged(record)
    return str(directory)


def verified_backup(record):
    directory = Path(record['backup'])
    saved = backup.verify_bundle(directory, readiness.DEMO_SERVER)
    if (saved.get('project_id') != record['project_id'] or saved.get('run_id') != record['run_id']
            or saved.get('role') != 'pre_wsl_restart'
            or backup.checksum(directory / 'recovery.json') != record.get('backup_record_sha256')):
        raise readiness.ReadinessError('restart recovery point differs from the checkpoint scope')
    return True


def prepare(cloud, state, record, path):
    if record['phase'] not in PREPARE_PHASES:
        raise readiness.ReadinessError('saved restart phase cannot be prepared again')
    if not same_boot(record['boot_before'], review.boot_identity(review.DISTRO)):
        raise readiness.ReadinessError('distribution restarted before preparation finished; preserve evidence')
    if record['phase'] == 'baseline':
        cycle.shared_unchanged(record)
        cycle.wait_workers('active', record['workers_before'])
        readiness.exclusive_demo(cloud, state)
        cycle.assert_vm(cloud, state, 'ACTIVE', 1)
        save(path, record, 'stop_intent')
        cloud.compute.stop_server(readiness.DEMO_SERVER)
        save(path, record, 'stop_submitted')
    if record['phase'] in ('stop_intent', 'stop_submitted'):
        record['stop'] = cycle.wait_vm(cloud, state, 'SHUTOFF', 4)
        save(path, record, 'stopped')
    if record['phase'] == 'stopped':
        readiness.exclusive_demo(cloud, state)
        cycle.assert_vm(cloud, state, 'SHUTOFF', 4)
        cycle.shared_unchanged(record)
        save(path, record, 'workers_stop_intent')
        common.run(['systemctl', '--no-pager', '--no-block', 'stop', *cycle.STOP_UNITS], timeout=20)
        save(path, record, 'workers_stop_submitted')
    if record['phase'] in ('workers_stop_intent', 'workers_stop_submitted'):
        record['workers_paused'] = cycle.wait_workers('inactive', record['workers_before'])
        record['shared_during_pause'] = cycle.shared_unchanged(record)
        save(path, record, 'workers_stopped')
    if record['phase'] == 'workers_stopped':
        record['backup'] = snapshot(record, path)
        record['backup_record_sha256'] = backup.checksum(Path(record['backup']) / 'recovery.json')
        record.update(shutdown_prepared=True, wsl_restart_verified=False)
        os.sync()
        save(path, record, 'prepared')
    verified_backup(record)
    assert_offline()
    cycle.wait_workers('inactive', record['workers_before'])
    cycle.shared_unchanged(record)


def wait_boot_units(record, timeout=180):
    deadline = time.monotonic() + timeout
    while True:
        current = cycle.profiles(operations.UNITS)
        for unit, original in record['units_before'].items():
            if (current[unit].get('LoadState') != original.get('LoadState')
                    or current[unit].get('UnitFileState') != original.get('UnitFileState')
                    or current[unit].get('ActiveState') == 'failed'):
                raise readiness.ReadinessError('service boot/enablement differs from baseline; no automatic repair')
        if all(current[unit].get('ActiveState') == original.get('ActiveState')
               for unit, original in record['units_before'].items() if unit != 'logrotate.service'):
            return current
        if time.monotonic() >= deadline:
            raise readiness.ReadinessError('required services did not recover at distro boot; no manual start applied')
        time.sleep(2)


def observe_boot(record, path):
    current = review.boot_identity(review.DISTRO)
    if not current['pid1_is_systemd'] or same_boot(record['boot_before'], current):
        raise readiness.ReadinessError('Ubuntu distribution restart has not been observed')
    if record['phase'] in POWEROFF_PHASES:
        verified_backup(record)
        record['boot_after'] = current
        record['kernel_restart_observed'] = current['kernel_boot_id'] != record['boot_before']['kernel_boot_id']
        record['units_at_boot'] = wait_boot_units(record)
        record['addressing_after'] = readiness.addressing()
        if readiness.kvm_devices() != record['kvm_before']:
            raise readiness.ReadinessError('KVM/vhost ownership or mode changed after distro restart')
        boot_policy_ready(review.boot_policy())
        assert_offline()
        save(path, record, 'boot_observed')
    elif not same_boot(record.get('boot_after', {}), current):
        raise readiness.ReadinessError('distribution restarted again during recovery; no request replayed')


def resume(cloud, state, record, path, *, cancel=False):
    if record['phase'] == 'boot_observed':
        # Workers must have started through systemd, as observed above.
        record['controls_after'] = cycle.wait_controls(cloud, state)
        save(path, record, 'controls_ready')
    if record['phase'] == 'cancel_intent':
        cycle.shared_unchanged(record)
        assert_offline()
        save(path, record, 'workers_start_intent')
        common.run(['systemctl', '--no-pager', '--no-block', 'start', *cycle.START_UNITS], timeout=20)
        save(path, record, 'workers_start_submitted')
    if record['phase'] in ('workers_start_intent', 'workers_start_submitted'):
        record['workers_restored'] = cycle.wait_workers('active', record['workers_before'])
        record['controls_after'] = cycle.wait_controls(cloud, state)
        save(path, record, 'controls_ready')
    if record['phase'] == 'controls_ready':
        readiness.exclusive_demo(cloud, state)
        cycle.assert_vm(cloud, state, 'SHUTOFF', 4)
        assert_offline()
        readiness.addressing()
        if operations.capacity()['meminfo_kib']['MemAvailable'] < 768 * 1024:
            raise readiness.ReadinessError('less than 768 MiB available after control recovery; no demo start')
        save(path, record, 'vm_start_intent')
        cloud.compute.start_server(readiness.DEMO_SERVER)
        save(path, record, 'vm_start_submitted')
    if record['phase'] in ('vm_start_intent', 'vm_start_submitted'):
        record['start'] = cycle.wait_vm(cloud, state, 'ACTIVE', 1)
        save(path, record, 'vm_active')
    if record['phase'] == 'vm_active':
        record['cloud_after'] = cycle.wait_cloud_after_start(cloud, state)
        record['guest_after'] = cycle.pinned_guest(cloud, state)
        if record['guest_after']['boot_id'] == record['guest_before']['boot_id']:
            raise readiness.ReadinessError('guest boot ID did not change after retained-demo shutdown/start')
        lifecycle.verify_acceleration(state)
        guest.probe_ping(state, record['guest_after']['mtu'])
        record['guest_cpu_after'] = operations.cpu_smoke(state)
        record['units_after'] = wait_boot_units(record)
        record['backup_unchanged'] = verified_backup(record)
        record.update(verified=True, kvm_after=True, mtu_ping_after=True, wsl_restart_verified=not cancel)
        save(path, record, 'cancelled' if cancel else 'verified')
    assert_complete(record, cancel=cancel)


def assert_complete(record, *, cancel=False):
    if (record.get('phase') != ('cancelled' if cancel else 'verified') or record.get('verified') is not True
            or record.get('wsl_restart_verified') is not (not cancel)
            or record.get('stop', {}).get('nova_status') != 'SHUTOFF'
            or record.get('stop', {}).get('libvirt_confirmed') is not True
            or record.get('start', {}).get('nova_status') != 'ACTIVE'
            or record.get('start', {}).get('libvirt_confirmed') is not True
            or not record.get('guest_after', {}).get('boot_id')
            or record['guest_after']['boot_id'] == record['guest_before']['boot_id']
            or any(record.get(key) is not True for key in ('kvm_after', 'mtu_ping_after', 'backup_unchanged'))
            or record.get('guest_cpu_after', {}).get('verified') is not True
            or (not cancel and (not record.get('units_at_boot')
                               or record.get('boot_after', {}).get('distro') != review.DISTRO
                               or record.get('boot_after', {}).get('pid1_is_systemd') is not True
                               or same_boot(record['boot_before'], record.get('boot_after', {}))))):
        raise readiness.ReadinessError('WSL restart/recovery acceptance evidence is incomplete')


def poweroff(record, path):
    if record['phase'] != 'prepared' or not record.get('shutdown_prepared'):
        raise readiness.ReadinessError('poweroff requires a prepared checkpoint; uncertain shutdown is not replayed')
    if not same_boot(record['boot_before'], review.boot_identity(review.DISTRO)):
        raise readiness.ReadinessError('distribution identity changed before requested poweroff')
    boot_policy_ready(review.boot_policy())
    assert_offline()
    cycle.wait_workers('inactive', record['workers_before'])
    cycle.shared_unchanged(record)
    verified_backup(record)
    os.sync()
    save(path, record, 'poweroff_intent')
    progress('requesting graceful Ubuntu systemd poweroff; reopen only after Ubuntu is Stopped')
    common.run(['systemctl', 'poweroff'], timeout=20)
    save(path, record, 'poweroff_submitted')


def wait_manifest(cloud, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return guest.load_run(cloud, require_ssh=False)
        except Exception as error:
            transient = (cycle.transient_api_error(error)
                         or getattr(error, 'http_status', None) in (500, 502, 503, 504))
            if not transient or time.monotonic() >= deadline:
                raise
            time.sleep(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distro', required=True, choices=(review.DISTRO,))
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ('prepare', 'verify', 'cancel', 'poweroff'):
        mode.add_argument('--' + name + '-demo', choices=(readiness.DEMO_SERVER,), metavar='UUID')
    args = parser.parse_args(argv)
    action = next(name for name in ('prepare', 'verify', 'cancel', 'poweroff') if getattr(args, name + '_demo'))
    if os.geteuid() != 0:
        print('STOP: root required in the scoped Ubuntu distribution.', file=sys.stderr)
        return 1
    path, record, attempt = launch.DATA_DIR / 'wsl-restart.json', None, None
    try:
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise readiness.ReadinessError('existing Ubuntu 22.04 WSL required')
        identity = review.boot_identity(args.distro)
        if not identity['pid1_is_systemd']:
            raise readiness.ReadinessError('systemd must be PID 1 in the scoped Ubuntu distribution')
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            attempt = launch.DATA_DIR / ('wsl-restart-attempt-' + uuid.uuid4().hex + '.json')
            readiness.recovery.source_bundle()
            readiness.latest_rehearsal()
            if path.exists() or path.is_symlink():
                record = load_record(path)
            elif action != 'prepare':
                raise readiness.ReadinessError('prepare the persistent checkpoint before restart/recovery')
            if action == 'verify':
                observe_boot(record, path)
            elif record is not None and not same_boot(record['boot_before'], identity):
                raise readiness.ReadinessError('use verify mode after distro restart; no preparation/cancellation replayed')
            with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
                state, _ = wait_manifest(cloud) if action == 'verify' else guest.load_run(cloud, require_ssh=False)
                if record is None:
                    evidence = latest_review(state, identity)
                    review.cycle_evidence(state)
                    policy = boot_policy_ready(review.boot_policy())
                    if review.autostart()['demo_autostart_enabled']:
                        raise readiness.ReadinessError('review the demo autostart policy before preparation')
                    baseline = readiness.review(cloud, state)
                    if shutil.disk_usage(launch.DATA_DIR).free < 3 * 1024 ** 3:
                        raise readiness.ReadinessError('less than 3 GiB free for restart preparation; demo remains active')
                    record = {'server_id': readiness.DEMO_SERVER, 'distro': args.distro,
                              'project_id': state['project_id'], 'run_id': state['run_id'],
                              'boot_before': identity, 'review': evidence, 'baseline': baseline,
                              'units_before': policy['units'], 'workers_before': cycle.profiles(cycle.STOP_UNITS),
                              'shared_before': cycle.profiles(cycle.SHARED_UNITS),
                              'kvm_before': readiness.kvm_devices(), 'guest_before': cycle.pinned_guest(cloud, state),
                              'verified': False, 'wsl_restart_verified': False}
                    save(path, record, 'baseline')
                if record['project_id'] != state['project_id'] or record['run_id'] != state['run_id']:
                    raise readiness.ReadinessError('restart checkpoint project/run differs from the retained manifest')
                with cycle.journal_maintenance_lock():
                    if action == 'prepare':
                        prepare(cloud, state, record, path)
                        progress('PREPARED; demo SHUTOFF, seven workers paused, fresh recovery point verified; report=' + str(path))
                    elif action == 'poweroff':
                        poweroff(record, path)
                    else:
                        if action == 'cancel':
                            if record['phase'] in POWEROFF_PHASES:
                                verified_backup(record)
                                record['cancel_requested'] = True
                                save(path, record, 'cancel_intent')
                            elif record.get('cancel_requested') is not True:
                                raise readiness.ReadinessError('cancellation requires a completed preparation on the original boot')
                        elif record.get('cancel_requested'):
                            raise readiness.ReadinessError('cancelled preparation cannot count as a WSL restart')
                        resume(cloud, state, record, path, cancel=action == 'cancel')
                        progress(('CANCELLED; demo/workers recovered; WSL restart remains unverified' if action == 'cancel'
                                  else 'PASS; distro restart, boot services, addressing, KVM and retained guest verified')
                                 + '; report=' + str(path))
        return 0
    except Exception as error:
        details = readiness.safe_error(error)
        failure = {'verified': False, 'failure': details, 'phase': record.get('phase') if record else 'setup'}
        if attempt is not None:
            try:
                failure['private_diagnostic'] = readiness.private_diagnostic(attempt, error)
                launch.atomic_state(attempt, failure)
                progress('protected incomplete attempt=' + str(attempt))
            except OSError:
                pass
        print('STOP: WSL restart incomplete: ' + json.dumps(details, sort_keys=True)
              + '; saved intent preserved; no automatic rollback/deletion/configuration repair.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
