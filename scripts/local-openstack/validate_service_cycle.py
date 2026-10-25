#!/usr/bin/python3
"""Cycle only the retained demo and seven dedicated POC workers.

Requires an exact UUID argument, successful protected readiness evidence and
fresh baseline checks. Shared infrastructure, configuration, data and all
resources remain intact. Never terminates WSL or replays uncertain requests.
"""
import argparse
import contextlib
import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import platform
import re
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

import configure_nova as common
import launch_test_vm as launch
import review_operations as operations
import review_restart_readiness as readiness
import validate_guest as guest
import validate_lifecycle as lifecycle

STOP_UNITS = ('nova-compute.service', 'nova-scheduler.service', 'nova-conductor.service',
              'nova-api.service', 'neutron-server.service', 'neutron-ovn-metadata-agent.service',
              'glance-api.service')
START_UNITS = ('glance-api.service', 'neutron-server.service', 'neutron-ovn-metadata-agent.service',
               'nova-api.service', 'nova-conductor.service', 'nova-scheduler.service', 'nova-compute.service')
SHARED_UNITS = tuple(unit for unit in operations.UNITS
                     if unit not in STOP_UNITS and unit != 'logrotate.service')
PHASES = ('baseline', 'stop_intent', 'stop_submitted', 'stopped',
          'workers_stop_intent', 'workers_stop_submitted', 'workers_stopped',
          'workers_start_intent', 'workers_start_submitted', 'workers_started',
          'vm_start_intent', 'vm_start_submitted', 'vm_active', 'verified')


def progress(message):
    print('POC service cycle: ' + message, flush=True)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, record, phase):
    if phase not in PHASES:
        raise readiness.ReadinessError('invalid service-cycle phase')
    record.update(phase=phase, updated_at=now())
    record.setdefault('events', []).append({'phase': phase, 'at': record['updated_at']})
    launch.atomic_state(path, record)
    progress(phase)


def successful_readiness(state):
    candidates = []
    for path in launch.DATA_DIR.glob('restart-readiness-*.json'):
        if not re.fullmatch(r'restart-readiness-[0-9a-f]{32}\.json', path.name):
            continue
        guest.protected_file(path, 0o600)
        candidates.append(path)
    if not candidates:
        raise readiness.ReadinessError('a successful readiness report is required')
    path = max(candidates, key=lambda value: value.stat().st_mtime_ns)
    report = json.loads(path.read_text())
    checks = report.get('checks', {})
    if (report.get('verified') is not True or report.get('service_cycle_prerequisites_passed') is not True
            or report.get('failed_check') or not checks
            or not all(value.get('status') == 'passed' for value in checks.values())
            or report.get('cloud', {}).get('retained_server') != readiness.DEMO_SERVER
            or report.get('cloud', {}).get('project_id') != state['project_id']
            or report.get('guest_cpu', {}).get('verified') is not True):
        raise readiness.ReadinessError('latest readiness report has not passed for the retained demo/project')
    return path


def profiles(units):
    return {unit: operations.systemd_state(unit) for unit in units}


def shared_unchanged(record):
    current = profiles(SHARED_UNITS)
    for unit, original in record['shared_before'].items():
        if any(current[unit].get(key) != original.get(key)
               for key in ('LoadState', 'ActiveState', 'UnitFileState')):
            raise readiness.ReadinessError('shared service state/enablement changed; preserve the maintenance checkpoint')
    return current


def wait_workers(expected, before, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = profiles(STOP_UNITS)
        if any(value.get('LoadState') != 'loaded' or value.get('ActiveState') == 'failed'
               for value in current.values()):
            raise readiness.ReadinessError('a dedicated worker is missing/failed; no automatic repair applied')
        if any(current[unit].get('UnitFileState') != before[unit].get('UnitFileState') for unit in STOP_UNITS):
            raise readiness.ReadinessError('dedicated worker enablement changed during the cycle')
        if all(value.get('ActiveState') == expected for value in current.values()):
            return current
        time.sleep(2)
    raise readiness.ReadinessError('dedicated worker target state unverified; service request was not replayed')


def wait_vm(cloud, state, status, power, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        server = readiness.owned_demo(cloud, state)
        if server.status == 'ERROR':
            raise readiness.ReadinessError('retained demo entered ERROR; no force reset or recreation applied')
        if lifecycle.settled(server, status, power):
            selector = '--state-running' if status == 'ACTIVE' else '--state-shutoff'
            domains = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                                  'list', '--all', selector, '--uuid'], timeout=15).split()
            if domains == [readiness.DEMO_SERVER]:
                return {'nova_status': status, 'power_state': power, 'libvirt_confirmed': True}
        time.sleep(2)
    raise readiness.ReadinessError('demo target state unverified; Nova request was not replayed')


def transient_api_error(error):
    return (getattr(error, 'status_code', None) in (500, 502, 503, 504)
            or isinstance(error, (ConnectionError, TimeoutError))
            or type(error).__name__ in ('ConnectFailure', 'ConnectTimeout', 'ReadTimeout', 'ConnectionTimeout', 'ConnectionError'))


def wait_controls(cloud, state, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            readiness.exclusive_demo(cloud, state)
            compute = common.observed_compute(cloud)
            agents = {agent.agent_type for agent in cloud.network.agents()
                      if agent.is_alive and agent.is_admin_state_up}
            if compute is not None and {'OVN Controller Gateway agent', 'OVN Metadata agent'} <= agents:
                return {'nova_compute_placement_ready': True, 'ovn_agents_ready': True}
        except Exception as error:
            if not transient_api_error(error):
                raise
        time.sleep(2)
    raise readiness.ReadinessError('control APIs/agents did not recover within the bounded wait')


def pinned_guest(cloud, state, timeout=180):
    for filename in ('id_ed25519', 'known_hosts'):
        guest.protected_file(launch.DATA_DIR / filename, 0o600)
    if not (launch.DATA_DIR / 'known_hosts').stat().st_size:
        raise readiness.ReadinessError('existing pinned SSH trust required before service-cycle testing')
    network, port = guest.validate_attachment(cloud, state)
    command = guest.ssh_command(state)
    command[command.index('StrictHostKeyChecking=accept-new')] = 'StrictHostKeyChecking=yes'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not lifecycle.settled(readiness.owned_demo(cloud, state), 'ACTIVE', 1):
            raise readiness.ReadinessError('demo lost its settled ACTIVE state during guest validation')
        try:
            result = subprocess.run(command, input=guest.GUEST_PROBE, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
        except subprocess.TimeoutExpired:
            time.sleep(2)
            continue
        if result.returncode == 0:
            return guest.parse_guest_report(result.stdout, state, network.mtu, port.mac_address)
        if result.returncode != 255:
            raise readiness.ReadinessError('guest probe command failed; raw guest output remains private')
        time.sleep(2)
    raise readiness.ReadinessError('pinned-key guest/network validation did not recover within the bounded wait')


def wait_cloud_after_start(cloud, state, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert_vm(cloud, state, 'ACTIVE', 1)
        names = {line.split()[0] for line in common.run(['ip', 'netns', 'list'], timeout=10).splitlines() if line.strip()}
        if state['namespace'] in names:
            return readiness.wait_cloud_state(cloud, state)
        time.sleep(2)
    raise readiness.ReadinessError('expected metadata namespace did not recover; no manual network changes applied')


@contextlib.contextmanager
def journal_maintenance_lock():
    source = launch.DATA_DIR / 'application-jobs.sqlite'
    lock_path = Path(str(source) + '.lock')
    guest.protected_file(source, 0o600)
    guest.protected_file(lock_path, 0o600)
    descriptor = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with contextlib.closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as database:
            if any(state not in ('succeeded', 'failed') for (state,) in database.execute('SELECT DISTINCT state FROM jobs')):
                raise readiness.ReadinessError('unresolved application jobs prevent the maintenance cycle')
        yield


def assert_vm(cloud, state, status, power):
    if not lifecycle.settled(readiness.owned_demo(cloud, state), status, power):
        raise readiness.ReadinessError('demo state changed before the next cycle operation')


def assert_complete(record):
    if (record.get('phase') != 'verified' or record.get('verified') is not True
            or record.get('stop', {}).get('nova_status') != 'SHUTOFF'
            or record.get('stop', {}).get('libvirt_confirmed') is not True
            or record.get('start', {}).get('nova_status') != 'ACTIVE'
            or record.get('start', {}).get('libvirt_confirmed') is not True
            or record.get('kvm_after') is not True or record.get('mtu_ping_after') is not True
            or record.get('guest_cpu_after', {}).get('verified') is not True
            or not record.get('guest_before', {}).get('boot_id')
            or not record.get('guest_after', {}).get('boot_id')
            or record['guest_before']['boot_id'] == record['guest_after']['boot_id']):
        raise readiness.ReadinessError('service-cycle acceptance evidence is incomplete')


def execute(cloud, state, record, path):
    if record['phase'] not in PHASES:
        raise readiness.ReadinessError('unknown saved cycle phase; preserve evidence')
    if record['phase'] == 'baseline':
        shared_unchanged(record)
        wait_workers('active', record['workers_before'])
        readiness.exclusive_demo(cloud, state)
        assert_vm(cloud, state, 'ACTIVE', 1)
        save(path, record, 'stop_intent')
        cloud.compute.stop_server(readiness.DEMO_SERVER)
        save(path, record, 'stop_submitted')
    if record['phase'] in ('stop_intent', 'stop_submitted'):
        record['stop'] = wait_vm(cloud, state, 'SHUTOFF', 4)
        save(path, record, 'stopped')
    if record['phase'] == 'stopped':
        readiness.exclusive_demo(cloud, state)
        assert_vm(cloud, state, 'SHUTOFF', 4)
        shared_unchanged(record)
        save(path, record, 'workers_stop_intent')
        common.run(['systemctl', '--no-pager', '--no-block', 'stop', *STOP_UNITS], timeout=20)
        save(path, record, 'workers_stop_submitted')
    if record['phase'] in ('workers_stop_intent', 'workers_stop_submitted'):
        record['workers_paused'] = wait_workers('inactive', record['workers_before'])
        record['shared_during_pause'] = shared_unchanged(record)
        save(path, record, 'workers_stopped')
    if record['phase'] == 'workers_stopped':
        shared_unchanged(record)
        save(path, record, 'workers_start_intent')
        common.run(['systemctl', '--no-pager', '--no-block', 'start', *START_UNITS], timeout=20)
        save(path, record, 'workers_start_submitted')
    if record['phase'] in ('workers_start_intent', 'workers_start_submitted'):
        record['workers_resumed'] = wait_workers('active', record['workers_before'])
        shared_unchanged(record)
        readiness.addressing()
        record['control_recovery'] = wait_controls(cloud, state)
        save(path, record, 'workers_started')
    if record['phase'] == 'workers_started':
        readiness.exclusive_demo(cloud, state)
        assert_vm(cloud, state, 'SHUTOFF', 4)
        if operations.capacity()['meminfo_kib']['MemAvailable'] < 768 * 1024:
            raise readiness.ReadinessError('less than 768 MiB available memory after worker recovery; no demo start submitted')
        save(path, record, 'vm_start_intent')
        cloud.compute.start_server(readiness.DEMO_SERVER)
        save(path, record, 'vm_start_submitted')
    if record['phase'] in ('vm_start_intent', 'vm_start_submitted'):
        record['start'] = wait_vm(cloud, state, 'ACTIVE', 1)
        save(path, record, 'vm_active')
    if record['phase'] == 'vm_active':
        record['cloud_after'] = wait_cloud_after_start(cloud, state)
        record['guest_after'] = pinned_guest(cloud, state)
        if record['guest_after']['boot_id'] == record['guest_before']['boot_id']:
            raise readiness.ReadinessError('guest boot ID did not change after the observed shutdown/start')
        lifecycle.verify_acceleration(state)
        guest.probe_ping(state, record['guest_after']['mtu'])
        record['guest_cpu_after'] = operations.cpu_smoke(state)
        record['shared_after'] = shared_unchanged(record)
        record.update(verified=True, kvm_after=True, mtu_ping_after=True, wsl_restart_tested=False)
        save(path, record, 'verified')
    assert_complete(record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-demo', required=True, choices=(readiness.DEMO_SERVER,), metavar='UUID')
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    record, path, attempt = None, None, None
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
            readiness.latest_rehearsal()
            path = launch.DATA_DIR / 'service-cycle.json'
            attempt = launch.DATA_DIR / ('service-cycle-attempt-' + uuid.uuid4().hex + '.json')
            with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
                state, _ = guest.load_run(cloud, require_ssh=False)
                boot_id = str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
                if path.exists() or path.is_symlink():
                    guest.protected_file(path, 0o600)
                    record = json.loads(path.read_text())
                    if (record.get('server_id') != readiness.DEMO_SERVER or record.get('run_id') != state['run_id']
                            or record.get('project_id') != state['project_id'] or record.get('host_boot_id') != boot_id):
                        raise readiness.ReadinessError('cycle checkpoint scope/host boot differs; no operation replayed')
                    if record.get('phase') == 'verified' and record.get('verified') is True:
                        assert_complete(record)
                        progress('previous cycle PASS retained; no cycle replayed; report=' + str(path))
                        return 0
                else:
                    evidence = successful_readiness(state)
                    baseline = readiness.review(cloud, state)
                    lifecycle.verify_acceleration(state)
                    guest_before = pinned_guest(cloud, state)
                    record = {'verified': False, 'server_id': readiness.DEMO_SERVER,
                              'run_id': state['run_id'], 'project_id': state['project_id'], 'host_boot_id': boot_id,
                              'readiness_report': str(evidence), 'baseline': baseline, 'guest_before': guest_before,
                              'workers_before': profiles(STOP_UNITS), 'shared_before': profiles(SHARED_UNITS)}
                    save(path, record, 'baseline')
                with journal_maintenance_lock():
                    execute(cloud, state, record, path)
                progress('PASS; demo ACTIVE, workers recovered, guest boot/network/KVM/MTU/CPU verified; report=' + str(path))
                return 0
    except Exception as error:
        details = readiness.safe_error(error)
        report = {'verified': False, 'failure': details,
                  'phase': record.get('phase') if isinstance(record, dict) else 'setup', 'at': now()}
        if attempt is not None:
            try:
                report['private_diagnostic'] = readiness.private_diagnostic(attempt, error)
                launch.atomic_state(attempt, report)
                progress('protected incomplete attempt=' + str(attempt))
            except OSError:
                pass
        print('STOP: service cycle incomplete: ' + json.dumps(details, sort_keys=True)
              + '; inspect the saved phase; no automatic rollback, deletion or WSL restart.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
