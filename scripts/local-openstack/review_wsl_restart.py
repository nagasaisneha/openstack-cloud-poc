#!/usr/bin/python3
"""Read-only review before preparing the scoped Ubuntu WSL restart.

Writes one new protected report. Does not stop guests/services, change boot
policy, or invoke Windows/WSL commands. A review PASS is not restart readiness.
"""
import argparse
import configparser
import datetime
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
import uuid

import configure_nova as common
import launch_test_vm as launch
import review_operations as operations
import review_restart_readiness as readiness
import validate_guest as guest
import validate_service_cycle as cycle

DISTRO = 'Ubuntu'
WSL_CONF = Path('/etc/wsl.conf')


def init_start_ticks(text):
    head, separator, tail = text.rpartition(')')
    fields = tail.split()
    # Fields after comm begin at field 3; starttime is field 22, index 19.
    if (not separator or not head.startswith('1 (') or len(fields) < 20
            or not fields[19].isdecimal() or int(fields[19]) <= 0):
        raise readiness.ReadinessError('PID 1 start-time observation is invalid')
    return int(fields[19])


def boot_identity(distro):
    actual = os.environ.get('WSL_DISTRO_NAME')
    if distro != DISTRO or (actual is not None and actual != distro):
        raise readiness.ReadinessError('WSL distribution differs from the scoped Ubuntu target')
    return {'distro': distro,
            'kernel_boot_id': str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip())),
            'init_start_ticks': init_start_ticks(Path('/proc/1/stat').read_text()),
            'pid1_is_systemd': Path('/proc/1/comm').read_text().strip() == 'systemd'}


def cycle_evidence(state):
    path = launch.DATA_DIR / 'service-cycle.json'
    guest.protected_file(path, 0o600)
    if path.stat().st_size > 1024 * 1024:
        raise readiness.ReadinessError('cycle evidence exceeds the bounded report size')
    content = path.read_bytes()
    record = json.loads(content)
    if (record.get('server_id') != readiness.DEMO_SERVER or record.get('project_id') != state['project_id']
            or record.get('run_id') != state['run_id']):
        raise readiness.ReadinessError('verified cycle evidence differs from the retained demo scope')
    cycle.assert_complete(record)
    for unit in cycle.STOP_UNITS:
        before = record.get('workers_before', {}).get(unit, {})
        paused = record.get('workers_paused', {}).get(unit, {})
        resumed = record.get('workers_resumed', {}).get(unit, {})
        if (before.get('LoadState') != 'loaded' or before.get('ActiveState') != 'active'
                or paused.get('LoadState') != 'loaded' or paused.get('ActiveState') != 'inactive'
                or resumed.get('LoadState') != 'loaded' or resumed.get('ActiveState') != 'active'
                or not before.get('UnitFileState')
                or before['UnitFileState'] != paused.get('UnitFileState')
                or before['UnitFileState'] != resumed.get('UnitFileState')):
            raise readiness.ReadinessError('cycle lacks complete dedicated-worker pause/resume evidence')
    for unit in cycle.SHARED_UNITS:
        before = record.get('shared_before', {}).get(unit, {})
        if not all(key in before for key in ('LoadState', 'ActiveState', 'UnitFileState')):
            raise readiness.ReadinessError('cycle lacks the original shared-service profile')
        for field in ('shared_during_pause', 'shared_after'):
            if any(record.get(field, {}).get(unit, {}).get(key) != before[key]
                   for key in ('LoadState', 'ActiveState', 'UnitFileState')):
                raise readiness.ReadinessError('cycle shared-service/enablement evidence differs from baseline')
    return {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest(),
            'cycle_verified': True, 'worker_pause_resume_verified': True,
            'shared_profiles_preserved': True, 'guest_boot_changed': True,
            'wsl_restart_verified': False}


def boot_policy():
    parser = configparser.ConfigParser(interpolation=None)
    if WSL_CONF.exists():
        parser.read_string(WSL_CONF.read_text())
    systemd = parser.getboolean('boot', 'systemd', fallback=None)
    units = {unit: operations.systemd_state(unit) for unit in operations.UNITS}
    review = [unit for unit, properties in units.items()
              if properties.get('ActiveState') == 'active'
              and properties.get('UnitFileState') in ('disabled', 'masked', 'masked-runtime', 'enabled-runtime')]
    return {'wsl_systemd_configured': systemd,
            'custom_boot_command_present': bool(parser.get('boot', 'command', fallback='').strip()),
            'units': units, 'active_units_needing_boot_policy_review': review}


def autostart():
    domains = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                          'list', '--all', '--autostart', '--uuid'], timeout=15).split()
    if set(domains) - {readiness.DEMO_SERVER}:
        raise readiness.ReadinessError('unexpected autostart domain requires separate workload review')
    return {'demo_autostart_enabled': readiness.DEMO_SERVER in domains}


def optional_inventory(action):
    try:
        return {'available': True, **action()}
    except Exception as error:
        # An unavailable inventory is unknown, never an invented zero count.
        return {'available': False, 'error_type': type(error).__name__}


def sessions():
    text = common.run(['loginctl', 'list-sessions', '--no-legend', '--no-pager'], timeout=10)
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if any(len(line) < 3 or not line[1].isdecimal() for line in lines):
        raise readiness.ReadinessError('session inventory format is unrecognized')
    return {'session_count': len(lines), 'user_uids': sorted({int(line[1]) for line in lines})}


def containers():
    if not Path('/var/run/docker.sock').is_socket() or not shutil.which('docker'):
        raise readiness.ReadinessError('local Docker inventory unavailable')
    text = common.run(['docker', '--host=unix:///var/run/docker.sock', 'ps', '--format', '{{.ID}}'], timeout=15)
    ids = text.split()
    if any(not re.fullmatch('[0-9a-f]{12,64}', value) for value in ids):
        raise readiness.ReadinessError('local Docker inventory format is unrecognized')
    return {'running_container_count': len(ids)}


def running_services():
    text = common.run(['systemctl', 'list-units', '--type=service', '--state=running',
                       '--no-legend', '--no-pager', '--plain'], timeout=15)
    units = [line.split()[0] for line in text.splitlines() if line.strip()]
    if any(not re.fullmatch(r'[a-zA-Z0-9_.@:-]{1,160}\.service', unit) for unit in units):
        raise readiness.ReadinessError('running-service inventory format is unrecognized')
    others = sorted(set(units) - set(operations.UNITS))
    return {'running_service_count': len(units), 'other_service_units_for_private_review': others}


def process_counts():
    # No arguments, environment, paths, usernames or process contents collected.
    allowed = {'python', 'python3', 'node', 'jupyter', 'uvicorn', 'gunicorn',
               'postgres', 'redis-server', 'mariadbd', 'mysqld', 'dockerd', 'containerd', 'qemu-system-x86'}
    text = common.run(['ps', '-eo', 'comm='], timeout=10)
    counts = {name: 0 for name in allowed}
    for line in text.splitlines():
        if line.strip() in allowed:
            counts[line.strip()] += 1
    return {'selected_process_counts': {name: counts[name] for name in sorted(counts) if counts[name]}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distro', required=True, choices=(DISTRO,))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    path = None
    report = {'verified': False, 'mode': 'read_only_restart_review', 'termination_ready': False,
              'wsl_restart_verified': False, 'checks': {},
              'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise readiness.ReadinessError('existing Ubuntu 22.04 WSL required')
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            readiness.recovery.source_bundle()
            path = launch.DATA_DIR / ('wsl-restart-review-' + uuid.uuid4().hex + '.json')
            launch.atomic_state(path, report)
            report['boot_identity'] = readiness.run_check(report, 'boot_identity', lambda: boot_identity(args.distro), path)
            with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
                state, _ = guest.load_run(cloud, require_ssh=False)
                report['service_cycle'] = readiness.run_check(report, 'cycle_evidence', lambda: cycle_evidence(state), path)
                fresh = {}
                readiness.run_check(report, 'current_readiness', lambda: readiness.review(cloud, state, report=fresh), path)
                report['current_readiness'] = fresh
            report['boot_policy'] = readiness.run_check(report, 'boot_policy', boot_policy, path)
            report['libvirt_autostart'] = readiness.run_check(report, 'libvirt_autostart', autostart, path)
            report['kvm_devices'] = readiness.run_check(report, 'kvm_devices', readiness.kvm_devices, path)
            for name, action in (('sessions', sessions), ('containers', containers),
                                 ('running_services', running_services), ('processes', process_counts)):
                report[name] = readiness.run_check(report, name, lambda action=action: optional_inventory(action), path)
            report.update(verified=True, current_check=None, workload_review_required=True)
            launch.atomic_state(path, report)
            summary = {'distro': args.distro, 'cycle_verified': True,
                       'demo_autostart_enabled': report['libvirt_autostart']['demo_autostart_enabled'],
                       'wsl_systemd_configured': report['boot_policy']['wsl_systemd_configured'],
                       'boot_policy_review_count': len(report['boot_policy']['active_units_needing_boot_policy_review']),
                       'running_container_count': report['containers'].get('running_container_count'),
                       'other_running_service_count': len(report['running_services'].get('other_service_units_for_private_review', []))
                           if report['running_services']['available'] else None,
                       'workload_review_required': True, 'termination_ready': False}
            print('POC WSL restart review: summary=' + json.dumps(summary, sort_keys=True), flush=True)
            print('POC WSL restart review: PASS (read-only; termination not prepared); report=' + str(path), flush=True)
            return 0
    except Exception as error:
        details = readiness.safe_error(error)
        report.update(verified=False, failure=details)
        if path is not None:
            try:
                report['private_diagnostic'] = readiness.private_diagnostic(path, error)
                launch.atomic_state(path, report)
            except OSError:
                pass
            print('POC WSL restart review: protected incomplete report=' + str(path), flush=True)
        print('STOP: WSL restart review incomplete: ' + json.dumps(details, sort_keys=True)
              + '; no VM/service/boot-policy changes performed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
