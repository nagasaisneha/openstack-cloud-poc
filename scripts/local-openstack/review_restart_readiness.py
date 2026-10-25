#!/usr/bin/python3
"""Prerequisite review and guarded retained-guest recovery for the POC.

Default mode is read-only apart from protected reports. The explicit
--resume-demo UUID option may start only the exact retained, owned, shut-off
demo through Nova. --retry-reviewed-demo is a separate one-shot path guarded by
a later successful Nova stop and followed by a stability observation. Neither
mode changes service configuration or reboots WSL.
"""
import argparse
import configparser
import contextlib
import datetime
import fcntl
import ipaddress
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
import traceback
import time
import uuid

import configure_nova as common
import launch_test_vm as launch
import rehearse_recovery as recovery
import review_operations as operations
import validate_guest as guest
import validate_lifecycle as lifecycle


class ReadinessError(RuntimeError):
    """Fixed, credential-free prerequisite failure."""


class CheckFailure(ReadinessError):
    """Carries only the check identifier and sanitized failure metadata."""

    def __init__(self, check, details):
        self.check, self.details = check, details
        super().__init__('prerequisite check failed: ' + check)


DEMO_SERVER = '82382430-4b64-4386-a55b-8468e04da011'
STAGES = {'journal', 'operational_files', 'sql', 'Open_vSwitch', 'OVN_Northbound',
          'OVN_Southbound', 'disk', 'guest_boot'}
SAFE_COMMANDS = {'systemctl', 'virsh', 'ip', 'ovs-vsctl', 'ssh'}
KNOWN_ERRORS = {
    'unexpected service property format': 'service_property_format',
    'incomplete service state': 'service_properties_missing',
    'existing pinned SSH trust required': 'pinned_ssh_trust_missing',
    'guest arithmetic result differs from the expected sum': 'guest_cpu_result_mismatch',
    'retained demo is not settled ACTIVE/running': 'demo_not_active',
    'retained private image is not active/owned': 'demo_image_not_active_or_owned',
    'required compute/OVN agent is not up/enabled': 'compute_or_network_agent_unhealthy',
    'resource belongs to another project; preserving it': 'resource_project_mismatch',
    'resource ownership marker does not match this run; preserving it': 'resource_marker_mismatch',
    'task guest is not ACTIVE/running with no pending task; preserve it for diagnosis': 'demo_state_changed_during_network_check',
    'test network is no longer private and isolated': 'network_isolation_drift',
    'test subnet relationship, CIDR or DHCP configuration drift detected': 'subnet_configuration_drift',
    'expected exactly one compute port on the owned network': 'compute_port_count_mismatch',
    'guest port ownership/security-group/port-security drift detected': 'compute_port_security_drift',
    'guest port address differs from the protected acceptance manifest': 'compute_port_address_drift',
    'expected one OVN metadata port/address on the test subnet': 'metadata_port_missing_or_ambiguous',
    'metadata port address changed; preserve probe rules for review': 'metadata_port_address_drift',
    'metadata namespace does not own the permitted probe source address': 'metadata_namespace_address_missing',
    'compute reports less than 256 MiB schedulable RAM after host reservation': 'placement_memory_below_demo_minimum',
    'unexpected catalog URL; refusing credential-bearing output': 'catalog_url_guard_failed',
}
NOVA_STATUSES = {'ACTIVE', 'BUILD', 'SHUTOFF', 'ERROR', 'PAUSED', 'SUSPENDED',
                 'RESCUE', 'RESIZE', 'VERIFY_RESIZE', 'REBOOT', 'HARD_REBOOT',
                 'REBUILD', 'MIGRATING', 'SHELVED', 'SHELVED_OFFLOADED', 'DELETED', 'UNKNOWN'}
RETRY_STABILITY_SECONDS = 360
RETRY_STABILITY_INTERVAL = 10


def safe_error(error):
    """Do not echo arbitrary SDK exceptions, command output, paths or tokens."""
    if isinstance(error, CheckFailure):
        return dict(error.details)
    details = {'error_type': type(error).__name__, 'category': 'observation_failed'}
    if isinstance(error, ReadinessError):
        # Only this module raises these fixed messages; external exception
        # strings are never treated as safe diagnostic text.
        details.update(category='prerequisite_guard', reason=str(error))
    elif str(error) in KNOWN_ERRORS:
        details['category'] = KNOWN_ERRORS[str(error)]
    elif isinstance(error, subprocess.TimeoutExpired):
        details['category'] = 'command_timeout'
        if isinstance(error.cmd, (list, tuple)) and error.cmd:
            command = Path(str(error.cmd[0])).name
            if command in SAFE_COMMANDS:
                details['command'] = command
    else:
        match = re.match(r'^(systemctl|virsh|ip|ovs-vsctl|ssh) failed \(exit (-?[0-9]{1,3})\):', str(error))
        if match:
            details.update(category='command_failed', command=match[1], exit_code=int(match[2]))
        elif isinstance(error, OSError) and isinstance(error.errno, int):
            details.update(category='operating_system_error', errno=error.errno)
        status = getattr(error, 'status_code', None)
        if type(status) is int and 100 <= status <= 599:
            details['http_status'] = status
    return details


def run_check(report, name, action, path=None):
    if not re.fullmatch(r'[a-z][a-z0-9_.:-]{0,95}', name):
        raise ReadinessError('invalid internal prerequisite check identifier')
    checks = report.setdefault('checks', {})
    report['current_check'] = name
    checks[name] = {'status': 'running'}
    if path is not None:
        launch.atomic_state(path, report)
    print('POC restart readiness: ' + name + ': BEGIN', flush=True)
    try:
        result = action()
    except Exception as error:
        details = safe_error(error)
        checks[name] = {'status': 'failed', **details}
        report.update(verified=False, failed_check=name)
        if path is not None:
            launch.atomic_state(path, report)
        print('POC restart readiness: ' + name + ': FAIL ' + json.dumps(details, sort_keys=True), flush=True)
        raise CheckFailure(name, details) from error
    checks[name] = {'status': 'passed'}
    if path is not None:
        launch.atomic_state(path, report)
    print('POC restart readiness: ' + name + ': PASS', flush=True)
    return result


def private_diagnostic(path, error):
    """Retain original failure locally without recording local variable values."""
    destination = path.with_suffix('.diagnostic.txt')
    text = ''.join(traceback.TracebackException.from_exception(error, capture_locals=False).format())
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(text[-65536:])
    return str(destination)


def decode_diagnostic(text):
    """Extract only known categories and allowlisted source-code locations."""
    origin = None
    modules = {'review_restart_readiness.py', 'review_operations.py', 'validate_guest.py',
               'launch_test_vm.py', 'configure_nova.py'}
    for line in text.splitlines():
        frame = re.fullmatch(r'  File "([^"\n]+)", line ([0-9]{1,7}), in ([a-zA-Z_][a-zA-Z0-9_]*)', line)
        if frame and Path(frame[1]).name in modules:
            origin = {'module': Path(frame[1]).name, 'function': frame[3], 'line': int(frame[2])}
        if line.startswith(('RuntimeError: ', 'ReadinessError: ')):
            # Only safe_error's fixed mappings/command metadata are emitted;
            # unknown message bodies remain private, even if they contain secrets.
            if line.startswith('ReadinessError: '):
                result = safe_error(ReadinessError(line[len('ReadinessError: '):]))
            else:
                result = safe_error(RuntimeError(line[len('RuntimeError: '):]))
            if origin is not None:
                result['origin'] = origin
            return result
    result = {'category': 'original_exception_not_classified'}
    if origin is not None:
        result['origin'] = origin
    return result


def explain_report(path):
    """Read one existing protected report/diagnostic; no APIs or writes."""
    if (path.parent != launch.DATA_DIR
            or not re.fullmatch(r'restart-readiness-[0-9a-f]{32}\.json', path.name)):
        raise ReadinessError('diagnostic must name one existing readiness report in the protected directory')
    root = launch.DATA_DIR.lstat()
    if not stat.S_ISDIR(root.st_mode) or root.st_uid != 0 or stat.S_IMODE(root.st_mode) != 0o700:
        raise ReadinessError('protected acceptance directory required')
    guest.protected_file(path, 0o600)
    if path.stat().st_size > 1024 * 1024:
        raise ReadinessError('readiness report exceeds the bounded diagnostic size')
    report = json.loads(path.read_text())
    diagnostic = path.with_suffix('.diagnostic.txt')
    if report.get('private_diagnostic') != str(diagnostic):
        raise ReadinessError('this report does not identify its expected private diagnostic')
    guest.protected_file(diagnostic, 0o600)
    if diagnostic.stat().st_size > 65536:
        raise ReadinessError('private diagnostic exceeds the bounded size')
    check = report.get('failed_check')
    if not isinstance(check, str) or not re.fullmatch(r'[a-z][a-z0-9_.:-]{0,95}', check):
        check = 'unclassified'
    return {'failed_check': check, 'failure': decode_diagnostic(diagnostic.read_text()),
            'demo_start_observed': report.get('resume_demo', {}).get('nova_active') is True,
            'guest_kvm_passed': report.get('checks', {}).get('guest_kvm', {}).get('status') == 'passed'}


def validate_rehearsal(record):
    boot = record.get('stages', {}).get('guest_boot', {})
    if (record.get('verified') is not True or record.get('source_bundle_unchanged') is not True
            or record.get('source_server') != recovery.SOURCE_SERVER
            or record.get('source_bundle') != str(launch.DATA_DIR / recovery.SOURCE_BUNDLE)
            or set(record.get('stages', {})) != STAGES
            or not all(value.get('verified') is True for value in record['stages'].values())
            or not all(boot.get(key) is True for key in ('kvm_enabled', 'serial_login_prompt', 'child_stopped', 'base_disk_unchanged'))
            or type(boot.get('network_devices')) is not int or boot['network_devices'] != 0
            or record['stages']['sql'].get('child_stopped') is not True
            or not all(record['stages'][name].get('children_stopped') is True
                       for name in ('Open_vSwitch', 'OVN_Northbound', 'OVN_Southbound'))):
        raise ReadinessError('latest restore rehearsal has not passed every isolated stage')


def latest_rehearsal():
    candidates = []
    for directory in launch.DATA_DIR.glob('restore-rehearsal-*'):
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise ReadinessError('unexpected rehearsal directory ownership/type/mode')
        path = directory / 'report.json'
        guest.protected_file(path, 0o600)
        candidates.append(path)
    if not candidates:
        raise ReadinessError('a completed recovery rehearsal report is required')
    # An incomplete newer attempt must not be hidden by an older successful one.
    path = max(candidates, key=lambda candidate: candidate.stat().st_mtime_ns)
    record = json.loads(path.read_text())
    validate_rehearsal(record)
    recovery.source_bundle()  # Recheck original artifact integrity/cleanup scope.
    return path


def exclusive_demo(cloud, state):
    if state['ids']['server'] != DEMO_SERVER:
        raise ReadinessError('original demo identity differs from the recorded restart scope')
    servers = list(cloud.compute.servers(all_projects=True))
    if len(servers) != 1 or servers[0].id != DEMO_SERVER or servers[0].task_state is not None:
        raise ReadinessError('other Nova guests or pending tasks require a separate maintenance scope')
    launch.assert_owned(cloud.compute.get_server(DEMO_SERVER), state, '', server=True)
    domains = common.run(['virsh', '-c', 'qemu:///system', 'list', '--all', '--uuid'], timeout=15).split()
    if domains != [DEMO_SERVER]:
        raise ReadinessError('unexpected libvirt domains; preserve unrelated workloads')
    return {'sole_nova_and_libvirt_guest': True, 'server_id': DEMO_SERVER}


def journal_quiescent():
    source = launch.DATA_DIR / 'application-jobs.sqlite'
    lock_path = Path(str(source) + '.lock')
    guest.protected_file(source, 0o600)
    guest.protected_file(lock_path, 0o600)
    descriptor = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with contextlib.closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as database:
            states = database.execute('SELECT DISTINCT state FROM jobs').fetchall()
            if any(state not in ('succeeded', 'failed') for (state,) in states):
                raise ReadinessError('pending/interrupted application jobs must be reconciled before maintenance')
    return {'no_active_worker': True, 'no_unresolved_jobs': True}


def addressing():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(Path('/etc/nova/nova.conf').read_text())
    configured = str(ipaddress.IPv4Address(parser.get('DEFAULT', 'my_ip')))
    inventory = json.loads(common.run(['ip', '-j', '-4', 'addr', 'show', 'dev', 'eth0'], timeout=10))
    addresses = {str(ipaddress.IPv4Address(entry['local'])) for interface in inventory
                 for entry in interface.get('addr_info', []) if entry.get('scope') == 'global'}
    encap = json.loads(common.run(['ovs-vsctl', '--timeout=5', 'get', 'Open_vSwitch', '.',
                                  'external_ids:ovn-encap-ip'], timeout=10))
    if configured not in addresses or str(ipaddress.IPv4Address(encap)) != configured:
        raise ReadinessError('WSL address differs from Nova/OVN configuration; no automatic reconfiguration')
    return {'nova_my_ip': configured, 'ovn_encap_ip': encap, 'eth0_addresses': sorted(addresses), 'match': True}


def kvm_devices():
    records = []
    for name in ('/dev/kvm', '/dev/vhost-net'):
        info = Path(name).lstat()
        if not stat.S_ISCHR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o660:
            raise ReadinessError('unexpected KVM/vhost device type/ownership/mode')
        records.append({'device': name, 'uid': info.st_uid, 'gid': info.st_gid, 'mode': '0660'})
    return records


def owned_demo(cloud, state):
    if state['ids']['server'] != DEMO_SERVER:
        raise ReadinessError('demo resume scope differs from the retained UUID')
    server = cloud.compute.get_server(DEMO_SERVER)
    launch.assert_owned(server, state, '', server=True)
    return server


def demo_state(cloud, state):
    server = owned_demo(cloud, state)
    power = server.power_state
    if isinstance(power, str) and power.isdecimal() and len(power) == 1:
        power = int(power)
    if type(power) is not int or not 0 <= power <= 7:
        power = None
    return {'server_id': DEMO_SERVER,
            'nova_status': server.status if server.status in NOVA_STATUSES else 'UNRECOGNIZED',
            'task_pending': server.task_state is not None, 'power_state': power}


def resume_demo(cloud, state):
    """Persist one start intent; a retry observes it without another request."""
    server = owned_demo(cloud, state)
    path = launch.DATA_DIR / 'demo-resume.json'
    record = None
    if path.exists() or path.is_symlink():
        guest.protected_file(path, 0o600)
        record = json.loads(path.read_text())
        if (record.get('server_id') != DEMO_SERVER or record.get('run_id') != state['run_id']
                or record.get('phase') not in ('start_intent', 'start_submitted', 'unverified', 'active')):
            raise ReadinessError('existing demo start record differs from this scope; preserve it for review')
        if record['phase'] == 'active' and not lifecycle.settled(server, 'ACTIVE', 1):
            raise ReadinessError('demo stopped again after the recorded start; a new start requires review')
    if lifecycle.settled(server, 'ACTIVE', 1):
        if record is not None:
            record.update(phase='active', observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            launch.atomic_state(path, record)
        return {'start_requested': False, 'already_active': True}
    if record is None:
        if not lifecycle.settled(server, 'SHUTOFF', 4):
            raise ReadinessError('start requires Nova SHUTOFF, power 4 and no pending task')
        stopped = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                              'list', '--all', '--state-shutoff', '--uuid'], timeout=15).split()
        if stopped != [DEMO_SERVER]:
            raise ReadinessError('libvirt does not confirm the sole retained demo is shut off')
        if operations.capacity()['meminfo_kib']['MemAvailable'] < 768 * 1024:
            raise ReadinessError('less than 768 MiB available memory; no demo start submitted')
        # Recheck ownership and settled state immediately before persisting intent.
        if not lifecycle.settled(owned_demo(cloud, state), 'SHUTOFF', 4):
            raise ReadinessError('demo changed state before start; no request submitted')
        record = {'server_id': DEMO_SERVER, 'run_id': state['run_id'], 'phase': 'start_intent',
                  'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        launch.atomic_state(path, record)
        try:
            cloud.compute.start_server(DEMO_SERVER)
        except Exception:
            record['phase'] = 'unverified'
            launch.atomic_state(path, record)
            raise
        record['phase'] = 'start_submitted'
        launch.atomic_state(path, record)
        print('POC restart readiness: retained demo start submitted; awaiting observed completion', flush=True)
    # Never repeat a request after interruption, rejection or uncertain response.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        server = owned_demo(cloud, state)
        if server.status == 'ERROR':
            raise ReadinessError('retained demo entered ERROR; preserve state and inspect the protected diagnostic')
        if lifecycle.settled(server, 'ACTIVE', 1):
            running = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                                  'list', '--all', '--state-running', '--uuid'], timeout=15).split()
            if running == [DEMO_SERVER]:
                record.update(phase='active', observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                launch.atomic_state(path, record)
                return {'start_requested': True, 'nova_active': True, 'libvirt_running': True,
                        'intent_record': str(path)}
        time.sleep(2)
    raise ReadinessError('demo start completion unverified after 180 seconds; no request replayed')


def parse_utc_timestamp(value):
    if not isinstance(value, str):
        raise ReadinessError('reviewed retry timestamp is missing or invalid')
    try:
        parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ReadinessError('reviewed retry timestamp is missing or invalid') from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def confirmed_stop_after_previous_start(cloud, previous_observed_at):
    response = cloud.compute.get(
        '/servers/' + DEMO_SERVER + '/os-instance-actions?limit=100', raise_exc=True)
    body = response.json()
    actions = body.get('instanceActions', []) if isinstance(body, dict) else []
    if not actions or not isinstance(actions[0], dict) or actions[0].get('action') != 'stop':
        raise ReadinessError('latest Nova action is not the reviewed retained-demo stop')
    stop = actions[0]
    if parse_utc_timestamp(stop.get('start_time')) <= previous_observed_at:
        raise ReadinessError('latest Nova stop does not follow the saved active observation')
    request_id = stop.get('request_id')
    if not isinstance(request_id, str) or not re.fullmatch(r'req-[0-9a-fA-F-]{36}', request_id):
        raise ReadinessError('latest Nova stop request identifier is invalid')
    detail_response = cloud.compute.get(
        '/servers/' + DEMO_SERVER + '/os-instance-actions/' + request_id,
        raise_exc=True, microversion='2.51')
    detail_body = detail_response.json()
    detail = detail_body.get('instanceAction') if isinstance(detail_body, dict) else None
    if not isinstance(detail, dict) or detail.get('action') != 'stop':
        raise ReadinessError('Nova stop action details are unavailable for reviewed retry')
    events = detail.get('events', [])
    if not any(isinstance(event, dict) and event.get('event') == 'compute_stop_instance'
               and event.get('result') == 'Success' for event in events):
        raise ReadinessError('Nova stop action did not record a successful compute event')
    return {'action': 'stop', 'start_time': stop.get('start_time'),
            'compute_stop_instance': 'Success'}


def reviewed_retry_demo(cloud, state):
    """Start once after a reviewed stop; preserve the original active intent."""
    previous_path = launch.DATA_DIR / 'demo-resume.json'
    retry_path = launch.DATA_DIR / 'demo-resume-reviewed-retry.json'
    if not (previous_path.exists() or previous_path.is_symlink()):
        raise ReadinessError('saved active demo start intent is required for reviewed retry')
    guest.protected_file(previous_path, 0o600)
    previous = json.loads(previous_path.read_text())
    if (previous.get('server_id') != DEMO_SERVER or previous.get('run_id') != state['run_id']
            or previous.get('phase') != 'active'):
        raise ReadinessError('saved demo start intent differs from the reviewed active scope')
    previous_observed_at = parse_utc_timestamp(previous.get('observed_at'))
    server = owned_demo(cloud, state)
    if not lifecycle.settled(server, 'SHUTOFF', 4):
        raise ReadinessError('reviewed retry requires Nova SHUTOFF, power 4 and no pending task')
    stopped = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                          'list', '--all', '--state-shutoff', '--uuid'], timeout=15).split()
    domains = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                          'list', '--all', '--uuid'], timeout=15).split()
    if stopped != [DEMO_SERVER] or domains != [DEMO_SERVER]:
        raise ReadinessError('reviewed retry requires the sole retained guest shut off in libvirt')
    stop = confirmed_stop_after_previous_start(cloud, previous_observed_at)
    if operations.capacity()['meminfo_kib']['MemAvailable'] < 768 * 1024:
        raise ReadinessError('less than 768 MiB available memory; reviewed retry not submitted')
    if retry_path.exists() or retry_path.is_symlink():
        guest.protected_file(retry_path, 0o600)
        raise ReadinessError('reviewed retry intent already exists; preserve it and do not retry')
    record = {'server_id': DEMO_SERVER, 'run_id': state['run_id'], 'phase': 'start_intent',
              'previous_observed_at': previous['observed_at'], 'reviewed_stop': stop,
              'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'stability_seconds': RETRY_STABILITY_SECONDS}
    launch.atomic_state(retry_path, record)
    try:
        cloud.compute.start_server(DEMO_SERVER)
    except Exception:
        record.update(phase='unverified', failure='start request outcome uncertain')
        launch.atomic_state(retry_path, record)
        raise
    record['phase'] = 'start_submitted'
    launch.atomic_state(retry_path, record)
    print('POC restart readiness: reviewed retained demo start submitted once; awaiting observed completion', flush=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        server = owned_demo(cloud, state)
        if server.status == 'ERROR':
            record.update(phase='start_failed', observed_status='ERROR')
            launch.atomic_state(retry_path, record)
            raise ReadinessError('reviewed retry guest entered ERROR; preserve intent and diagnose')
        if lifecycle.settled(server, 'ACTIVE', 1):
            running = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                                  'list', '--all', '--state-running', '--uuid'], timeout=15).split()
            if running == [DEMO_SERVER]:
                record.update(phase='active', observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
                launch.atomic_state(retry_path, record)
                return {'start_requested': True, 'nova_active': True, 'libvirt_running': True,
                        'intent_record': str(retry_path)}
        time.sleep(2)
    record.update(phase='unverified', failure='start completion unverified; no request replayed')
    launch.atomic_state(retry_path, record)
    raise ReadinessError('reviewed retry start completion unverified after 180 seconds; no request replayed')


def watch_demo_stability(cloud, state, retry_path, duration=RETRY_STABILITY_SECONDS):
    guest.protected_file(retry_path, 0o600)
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        server = owned_demo(cloud, state)
        if not lifecycle.settled(server, 'ACTIVE', 1):
            record = json.loads(retry_path.read_text())
            record.update(phase='stability_failed',
                          last_state={'status': server.status if server.status in NOVA_STATUSES else 'UNKNOWN',
                                      'task_pending': server.task_state is not None,
                                      'power_state': server.power_state if type(server.power_state) is int else None},
                          stability_failed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            launch.atomic_state(retry_path, record)
            raise ReadinessError('reviewed retry guest stopped during the stability window; preserve the evidence')
        running = common.run(['virsh', '--readonly', '--connect', 'qemu:///system',
                              'list', '--all', '--state-running', '--uuid'], timeout=15).split()
        if running != [DEMO_SERVER]:
            record = json.loads(retry_path.read_text())
            record.update(phase='stability_failed',
                          stability_failed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          libvirt_running=False)
            launch.atomic_state(retry_path, record)
            raise ReadinessError('reviewed retry guest left libvirt running state; preserve the evidence')
        time.sleep(min(RETRY_STABILITY_INTERVAL, max(0, deadline - time.monotonic())))
    record = json.loads(retry_path.read_text())
    record.update(phase='stable', stability_observed_seconds=duration,
                  stable_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    launch.atomic_state(retry_path, record)
    return {'verified': True, 'observed_seconds': duration}


def wait_guest_cpu(cloud, state):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if not lifecycle.settled(owned_demo(cloud, state), 'ACTIVE', 1):
            raise ReadinessError('demo is no longer settled ACTIVE while waiting for guest access')
        try:
            return operations.cpu_smoke(state)
        except (RuntimeError, subprocess.TimeoutExpired):
            time.sleep(2)
    raise ReadinessError('pinned-key guest CPU check did not recover within 120 seconds')


def wait_cloud_state(cloud, state, timeout=60):
    """Retry only the observed metadata-source delay, keeping every guard."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            # Repeat the complete observation: scope, VM/image, network policy,
            # addresses, agents and Placement must all pass on the final attempt.
            return operations.cloud_state(cloud, state)
        except RuntimeError as error:
            if KNOWN_ERRORS.get(str(error)) != 'metadata_namespace_address_missing':
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise  # Preserve the original guarded failure/category.
            print('POC restart readiness: waiting for OVN metadata probe-source reconciliation', flush=True)
            time.sleep(min(2, remaining))


def review(cloud, state, *, report=None, path=None, resume=False, retry_reviewed=False):
    result = {} if report is None else report
    for name, action in (('exclusive_demo', lambda: exclusive_demo(cloud, state)),
                         ('jobs', journal_quiescent), ('addressing', addressing),
                         ('kvm_devices', kvm_devices)):
        result[name] = run_check(result, name, action, path)
    result['units'] = {}
    for unit in operations.UNITS:
        def check_unit(unit=unit):
            properties = operations.systemd_state(unit)
            if properties.get('expected_profile_match') is False:
                raise ReadinessError('a required service is not loaded/active; no maintenance should begin')
            return properties
        result['units'][unit] = run_check(result, 'service:' + unit, check_unit, path)
    result['demo_state'] = run_check(result, 'demo_state', lambda: demo_state(cloud, state), path)
    print('POC restart readiness: demo_state: ' + json.dumps(result['demo_state'], sort_keys=True), flush=True)
    if retry_reviewed:
        result['reviewed_retry_demo'] = run_check(
            result, 'reviewed_retry_demo', lambda: reviewed_retry_demo(cloud, state), path)
        run_check(result, 'guest_kvm', lambda: lifecycle.verify_acceleration(state), path)
        retry_path = launch.DATA_DIR / 'demo-resume-reviewed-retry.json'
        result['stability'] = run_check(
            result, 'stability', lambda: watch_demo_stability(cloud, state, retry_path), path)
    elif resume:
        result['resume_demo'] = run_check(result, 'resume_demo', lambda: resume_demo(cloud, state), path)
        run_check(result, 'guest_kvm', lambda: lifecycle.verify_acceleration(state), path)
    result['cloud'] = run_check(result, 'cloud', lambda: wait_cloud_state(cloud, state), path)
    action = (lambda: wait_guest_cpu(cloud, state)) if (resume or retry_reviewed) else (lambda: operations.cpu_smoke(state))
    result['guest_cpu'] = run_check(result, 'guest_cpu', action, path)
    result['service_cycle_prerequisites_passed'] = True
    result['wsl_restart_ready'] = False
    result['next_step'] = 'Prepare the scoped service-cycle/checkpoint; review VTEP boot enablement and unrelated host workloads before WSL termination'
    return result


def main(argv=None):
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--resume-demo', choices=(DEMO_SERVER,), metavar='UUID',
                      help='start only the exact retained demo if Nova/libvirt agree it is shut off')
    mode.add_argument('--retry-reviewed-demo', action='store_true',
                      help='one reviewed start after a confirmed later Nova stop, followed by a six-minute stability observation')
    mode.add_argument('--explain-report', type=Path, metavar='REPORT',
                      help='print safe failure metadata from an existing protected report; no APIs or writes')
    args = parser.parse_args(argv)
    if args.explain_report is not None:
        try:
            print('POC readiness diagnostic: ' + json.dumps(explain_report(args.explain_report), sort_keys=True), flush=True)
            return 0
        except Exception as error:
            print('STOP: readiness diagnostic unavailable: ' + json.dumps(safe_error(error), sort_keys=True), file=sys.stderr)
            return 1
    path = None
    report = {'verified': False, 'diagnostic_version': 3, 'checks': {},
              'mode': ('reviewed_retry_retained_demo' if args.retry_reviewed_demo else
                       'resume_retained_demo' if args.resume_demo else 'read_only'),
              'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise ReadinessError('existing Ubuntu 22.04 WSL required')
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Check directory protection before writing the first checkpoint.
            info = launch.DATA_DIR.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise ReadinessError('protected acceptance directory required')
            path = launch.DATA_DIR / ('restart-readiness-' + uuid.uuid4().hex + '.json')
            launch.atomic_state(path, report)
            run_check(report, 'recovery_source', recovery.source_bundle, path)
            report['recovery_report'] = str(run_check(report, 'recovery_report', latest_rehearsal, path))
            secrets = run_check(report, 'credentials', lambda: common.read_secrets(common.SECRET_FILE), path)
            with contextlib.ExitStack() as stack:
                cloud = run_check(report, 'cloud_session',
                    lambda: stack.enter_context(common.connection(secrets)), path)
                run_check(report, 'keystone_auth', lambda: cloud.session.get_project_id(), path)
                state, _ = run_check(report, 'manifest', lambda: guest.load_run(cloud, require_ssh=False), path)
                review(cloud, state, report=report, path=path, resume=bool(args.resume_demo),
                       retry_reviewed=args.retry_reviewed_demo)
            report.update(verified=True, current_check=None)
            launch.atomic_state(path, report)
            scope = ('retained demo running and stable; no service/WSL restart' if args.retry_reviewed_demo else
                     'retained demo running; no service/WSL restart' if args.resume_demo else
                     'observations only; no restart performed')
            print('POC restart readiness: PASS (' + scope + '); report=' + str(path), flush=True)
            return 0
    except Exception as exc:
        details = safe_error(exc)
        report.update(verified=False, failure=details)
        if path is not None:
            try:
                report['private_diagnostic'] = private_diagnostic(path, exc)
                launch.atomic_state(path, report)
            except OSError:
                print('POC restart readiness: diagnostic persistence unavailable', file=sys.stderr)
            print('POC restart readiness: protected incomplete report=' + str(path), flush=True)
        check = report.get('failed_check', report.get('current_check', 'setup'))
        boundary = ('no service/WSL changes; reviewed retry intent is preserved and never replayed' if args.retry_reviewed_demo else
                    'no service/WSL changes; inspect saved demo start intent' if args.resume_demo else
                    'no service/guest changes performed')
        print('STOP: restart prerequisites unverified at ' + str(check) + ': '
              + json.dumps(details, sort_keys=True) + '; ' + boundary + '.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
