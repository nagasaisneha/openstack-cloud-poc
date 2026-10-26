#!/usr/bin/python3
"""Plan/prepare/resume a scoped cold-recovery maintenance window.

Default --plan is read-only. --prepare needs --confirm-recovery-maintenance.
Stops only the sole recorded demo and allowlisted POC services; no distro
shutdown/export/import, package change, database restore or asset deletion.
A reviewed Windows procedure is still required for whole-WSL recovery.
"""
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys
import tempfile
import time

import backup_application as backup
import configure_nova as common
import configure_swift as swift
import launch_test_vm as launch
import inspect_cinder_readiness as cinder
import review_operations as operations
import review_restart_readiness as readiness
import validate_guest as guest
import validate_lifecycle as lifecycle
import validate_service_cycle as cycle
from audit_upgrade_recovery import DATA, LOCK, PACKAGES, SCHEMAS, broker_inventory, protected

APPS = tuple(dict.fromkeys((*swift.ALL_UNITS, 'apache2.service', *cycle.STOP_UNITS,
                           'cinder-scheduler.service', 'cinder-volume.service')))
NETWORK_WRITERS = ('ovn-controller-vtep.service', 'ovn-controller.service', 'ovn-northd.service')
INFRA = ('rabbitmq-server.service', 'mariadb.service', 'ovn-central.service',
         'openvswitch-switch.service', 'libvirtd.service', 'tgt.service')
UNITS = APPS + NETWORK_WRITERS + INFRA
START = ('openvswitch-switch.service', 'ovn-central.service', 'ovn-northd.service',
         'ovn-controller.service', 'ovn-controller-vtep.service', 'mariadb.service',
         'rabbitmq-server.service', 'libvirtd.service', 'tgt.service', 'apache2.service',
         'glance-api.service', 'neutron-server.service', 'neutron-ovn-metadata-agent.service',
         'nova-api.service', 'nova-conductor.service', 'nova-scheduler.service', 'nova-compute.service',
         'cinder-scheduler.service', 'cinder-volume.service', *swift.ALL_UNITS)
CRITICAL = ('etc/keystone', 'etc/glance', 'etc/placement', 'etc/nova', 'etc/neutron',
            'etc/cinder', 'etc/swift', 'etc/apache2', 'etc/mysql', 'etc/rabbitmq',
            'etc/iscsi', 'etc/tgt', 'etc/lvm', 'etc/systemd/system', 'etc/wsl.conf',
            'var/lib/mysql', 'var/lib/rabbitmq', 'var/lib/glance/images',
            'var/lib/nova/instances', 'var/lib/cinder-poc/cinder-backend.img',
            'var/lib/swift-poc/swift-backend.img', 'root/openstack-poc-keystone-secrets',
            'root/openstack-poc-acceptance/run.json', 'root/openstack-poc-acceptance/application-jobs.sqlite')


class ColdRecoveryError(RuntimeError):
    """Only fixed, credential-free guard reasons use this class."""


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, record, phase):
    record.update(phase=phase, updated_at=now())
    launch.atomic_state(path, record)
    backup.sync_bundle(path.parent)


def source_guard():
    if os.environ.get('WSL_DISTRO_NAME') != 'Ubuntu' or 'microsoft' not in platform.release().lower():
        raise ColdRecoveryError('existing_ubuntu_distribution_required')
    if Path('/proc/1/comm').read_text().strip() != 'systemd':
        raise ColdRecoveryError('systemd_required')
    if 'VERSION_ID="22.04"' not in Path('/etc/os-release').read_text():
        raise ColdRecoveryError('reviewed_source_os_changed')


def repository_checkpoint():
    root = Path(__file__).resolve().parents[2]
    command = ['git', '-c', 'safe.directory=' + str(root), '-C', str(root)]
    commit = common.run(command + ['rev-parse', 'HEAD'], timeout=15).strip()
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ColdRecoveryError('repository_commit_unverified')
    if common.run(command + ['status', '--porcelain=v1', '--untracked-files=normal'], timeout=15).strip():
        raise ColdRecoveryError('clean_reviewed_repository_required')
    return {'commit': commit, 'worktree_clean': True}


def package_versions():
    return {package: common.run(['dpkg-query', '-W', '-f=${Version}', package], timeout=15).strip()
            for package in PACKAGES}


def observe_units():
    result = {unit: operations.systemd_state(unit) for unit in UNITS}
    if any(row.get('ActiveState') not in ('active', 'inactive', 'failed') for row in result.values()):
        raise ColdRecoveryError('service_transition_requires_review')
    return result


def plan(cloud, state):
    readiness.exclusive_demo(cloud, state)
    readiness.journal_quiescent()
    server = readiness.owned_demo(cloud, state)
    if not (lifecycle.settled(server, 'ACTIVE', 1) or lifecycle.settled(server, 'SHUTOFF', 4)):
        raise ColdRecoveryError('guest_transition_requires_review')
    if list(cloud.block_storage.volumes(all_projects=True)):
        raise ColdRecoveryError('existing_volumes_need_explicit_consistency_scope')
    names = set(common.sql('SHOW DATABASES').splitlines())
    if names - set(SCHEMAS) - {'information_schema', 'mysql', 'performance_schema', 'sys'}:
        raise ColdRecoveryError('other_databases_require_separate_maintenance_review')
    if not set(SCHEMAS) <= names:
        raise ColdRecoveryError('required_database_missing')
    broker, private = broker_inventory()
    if broker['messages'] or broker['messages_unacknowledged']:
        raise ColdRecoveryError('pending_broker_work_requires_review')
    return {'schema': 1, 'purpose': 'cold_recovery_maintenance', 'created_at': now(),
            'server_id': readiness.DEMO_SERVER, 'project_id': state['project_id'], 'run_id': state['run_id'],
            'initial_guest_status': server.status, 'units_before': observe_units(),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'broker_before': broker, 'full_wsl_recovery_verified': False, 'source_upgrade_performed': False}


def stop_units(units, record, path):
    if set(units) - set(UNITS):
        raise ColdRecoveryError('unapproved_service_scope')
    for unit in units:
        if record['units_before'][unit]['ActiveState'] != 'active':
            continue
        record.setdefault('stop_intents', []).append(unit)
        save(path, record, 'stopping_services')
        common.run(['systemctl', 'stop', unit], timeout=90)
        if operations.systemd_state(unit).get('ActiveState') != 'inactive':
            raise ColdRecoveryError('service_stop_unverified')


def file_manifest():
    result = {}
    def visit(path):
        info = path.lstat()
        label = str(path).lstrip('/')
        row = {'uid': info.st_uid, 'gid': info.st_gid, 'mode': format(stat.S_IMODE(info.st_mode), '04o')}
        if stat.S_ISLNK(info.st_mode):
            row.update(type='symlink', target=os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            row['type'] = 'directory'
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(info.st_mode):
            row.update(type='file', bytes=info.st_size, sha256=backup.checksum(path))
        else:
            # Runtime sockets/FIFOs are recreated by the original service units.
            return
        result[label] = row
    for relative in CRITICAL:
        path = Path('/') / relative
        if path.exists() or path.is_symlink():
            visit(path)
        else:
            raise ColdRecoveryError('critical_recovery_path_missing')
    return result


def prepare(cloud, state, record, path):
    record['package_versions'] = package_versions()
    save(path, record, 'baseline')
    if record['initial_guest_status'] == 'ACTIVE':
        record['vm_stop_intent'] = True
        save(path, record, 'vm_stop_intent')
        cloud.compute.stop_server(readiness.DEMO_SERVER)
    record['guest_stop'] = cycle.wait_vm(cloud, state, 'SHUTOFF', 4)
    save(path, record, 'vm_stopped')
    stop_units(APPS + NETWORK_WRITERS, record, path)
    broker, private = broker_inventory()
    if broker['consumers'] or broker['messages'] or broker['messages_unacknowledged']:
        raise ColdRecoveryError('broker_clients_or_work_remain')
    record['broker_quiesced'] = broker
    bundle = path.parent / 'bundle'; bundle.mkdir(mode=0o700)
    backup.snapshot_databases(bundle, databases=SCHEMAS)
    xml = common.run(['virsh', '--readonly', '-c', 'qemu:///system', 'dumpxml', readiness.DEMO_SERVER], timeout=15)
    (bundle / 'domain.xml').write_text(xml)
    disk, metadata = backup.disk_from_xml(xml, readiness.DEMO_SERVER)
    metadata['source_format'] = backup.flatten_disk(disk, bundle / 'guest-root.qcow2')
    record['guest_disk_backup'] = metadata
    backup.snapshot_journal(bundle / 'application-jobs.sqlite')
    # TGT has no permitted volume exports in this maintenance scope (plan requires zero volumes).
    targets = common.run(['tgtadm', '--lld', 'iscsi', '--op', 'show', '--mode', 'target'], timeout=15)
    if targets.strip():
        raise ColdRecoveryError('unexpected_iscsi_export_requires_review')
    stop_units(INFRA, record, path)
    common.run(['sync'], timeout=120)
    record['bundle_sha256'] = {item.name: backup.checksum(item) for item in bundle.iterdir() if item.is_file()}
    record['critical_files'] = file_manifest()
    record['services_quiesced'] = True
    save(path, record, 'prepared_for_operator_poweroff')


def resume(path, record):
    if (record.get('purpose') != 'cold_recovery_maintenance' or record.get('schema') != 1
            or record.get('server_id') != readiness.DEMO_SERVER or set(record.get('units_before', {})) != set(UNITS)
            or record.get('initial_guest_status') not in ('ACTIVE', 'SHUTOFF')):
        raise ColdRecoveryError('unexpected_checkpoint_scope')
    for unit in START:
        if record['units_before'][unit]['ActiveState'] == 'active':
            state = operations.systemd_state(unit)
            if state.get('ActiveState') != 'active':
                common.run(['systemctl', 'start', unit], timeout=90)
            if operations.systemd_state(unit).get('ActiveState') != 'active':
                raise ColdRecoveryError('original_service_start_unverified')
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        state, ignored = guest.load_run(cloud, require_ssh=False)
        if record['project_id'] != state['project_id'] or record['run_id'] != state['run_id']:
            raise ColdRecoveryError('checkpoint_identity_changed')
        cycle.wait_controls(cloud, state)
        server = readiness.owned_demo(cloud, state)
        if record['initial_guest_status'] == 'ACTIVE' and lifecycle.settled(server, 'SHUTOFF', 4):
            if record.get('vm_start_intent'):
                raise ColdRecoveryError('saved_start_intent_requires_observation_not_replay')
            record['vm_start_intent'] = True
            save(path, record, 'vm_start_intent')
            cloud.compute.start_server(readiness.DEMO_SERVER)
        desired = record['initial_guest_status']
        cycle.wait_vm(cloud, state, desired, 1 if desired == 'ACTIVE' else 4)
        if desired == 'ACTIVE':
            record['core_resume'] = readiness.wait_cloud_state(cloud, state)
        deadline = time.monotonic() + 90
        while True:
            checked = cinder.readiness({'cloud': cinder.cloud_inventory(cloud),
                                       'backend_configuration': cinder.backend_configuration()})
            if checked['status'] == 'ready_for_scoped_volume_test':
                record['cinder_resume'] = checked
                break
            if time.monotonic() > deadline:
                raise ColdRecoveryError('cinder_resume_health_unverified')
            time.sleep(2)
    record['swift_resume'] = swift.service_health(common.read_secrets(common.SECRET_FILE))
    save(path, record, 'source_resumed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--plan', action='store_true')
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--resume', type=Path)
    parser.add_argument('--confirm-recovery-maintenance', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        print('STOP: run sudo /usr/bin/python3 -B ' + str(Path(__file__).resolve()))
        return 1
    path = record = None
    os.umask(0o077); logging.disable(logging.CRITICAL)
    try:
        source_guard(); protected(DATA, directory=True)
        descriptor = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            protected(LOCK); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.resume:
                path = args.resume.absolute()
                if path.parent.parent != DATA or not path.parent.name.startswith('cold-recovery-') or path.name != 'checkpoint.json':
                    raise ColdRecoveryError('unexpected_checkpoint_path')
                protected(path.parent, directory=True); protected(path)
                record = json.loads(path.read_text()); resume(path, record)
            else:
                if args.prepare and not args.confirm_recovery_maintenance:
                    raise ColdRecoveryError('explicit_recovery_maintenance_confirmation_required')
                implementation = repository_checkpoint() if args.prepare else None
                with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
                    state, ignored = guest.load_run(cloud, require_ssh=False)
                    record = plan(cloud, state)
                    if implementation:
                        record['implementation'] = implementation
                    if not args.prepare:
                        print('POC cold recovery plan: ' + json.dumps({'scope': 'sole_retained_demo_and_POC_services',
                            'guest_stop_required': record['initial_guest_status'] == 'ACTIVE',
                            'active_units_to_stop': [u for u, value in record['units_before'].items() if value['ActiveState'] == 'active'],
                            'source_upgrade_performed': False, 'host_changes_performed': False}, sort_keys=True))
                        return 0
                    if shutil.disk_usage(DATA).free < 3 * 1024**3:
                        raise ColdRecoveryError('backup_capacity_unavailable')
                    directory = Path(tempfile.mkdtemp(prefix='cold-recovery-', dir=DATA))
                    path = directory / 'checkpoint.json'; prepare(cloud, state, record, path)
            print('POC cold recovery maintenance: ' + json.dumps({'phase': record['phase'], 'checkpoint': str(path),
                'source_upgrade_performed': False, 'full_wsl_recovery_verified': False}, sort_keys=True))
            return 0
    except Exception as error:
        if path is not None and record is not None:
            record['error_type'] = type(error).__name__; save(path, record, record.get('phase', 'incomplete'))
        reason = str(error) if isinstance(error, ColdRecoveryError) else type(error).__name__
        print('STOP: cold recovery maintenance unavailable: ' + reason
              + '; inspect protected checkpoint; no action is automatically replayed; checkpoint=' + str(path))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
