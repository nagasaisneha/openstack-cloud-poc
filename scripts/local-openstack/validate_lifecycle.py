#!/usr/bin/python3
"""Task 11 stage 7: observe real stop/start/reboot of the owned test VM.

Persists intent, submission and observed completion separately. Does not delete
resources or replay a reboot with an uncertain submission outcome.
"""
import datetime
import fcntl
import logging
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import uuid

import configure_nova as common
import launch_test_vm as launch
import validate_guest as guest
import validate_policy as policy


PHASES = ('baseline', 'stop_intent', 'stop_submitted', 'stopped',
          'start_intent', 'start_submitted', 'started',
          'reboot_intent', 'reboot_submitted', 'verified')


def progress(message):
    print(f'Task 11 lifecycle validation: {message}', flush=True)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(state, path, record, phase):
    if phase not in PHASES:
        raise RuntimeError('unknown lifecycle evidence phase')
    record['phase'] = phase
    record['updated_at'] = now()
    launch.atomic_state(path, state)


def owned_server(cloud, state):
    server = cloud.compute.get_server(state['ids']['server'])
    launch.assert_owned(server, state, '', server=True)
    if server.status == 'ERROR':
        raise RuntimeError('task instance entered ERROR: ' + str(server.fault or {}))
    return server


def settled(server, status, power):
    return (server.status == status and server.task_state is None
            and server.power_state is not None and int(server.power_state) == power)


def wait_server(cloud, state, status, power, timeout=180):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        server = owned_server(cloud, state)
        current = (server.status, server.task_state, server.power_state)
        if current != previous:
            progress(f'Nova state={server.status} task={server.task_state} power={server.power_state}')
            previous = current
        if settled(server, status, power):
            return server
        time.sleep(2)
    raise RuntimeError(f'timed out waiting for {status}/power={power}/no pending task; request acceptance is not completion')


def wait_attachment(cloud, state):
    # The metadata namespace and port may become ready after Nova reports ACTIVE.
    deadline = time.monotonic() + 60
    diagnostic = 'network attachment not ready'
    while time.monotonic() < deadline:
        owned_server(cloud, state)  # Recheck scope and provider errors each time.
        try:
            return guest.validate_attachment(cloud, state)
        except RuntimeError as exc:
            diagnostic = str(exc)
        time.sleep(2)
    raise RuntimeError('network attachment did not recover: ' + diagnostic)


def verify_acceleration(state):
    server_id = state['ids']['server']
    xml = common.run(['virsh', '-c', 'qemu:///system', 'dumpxml', server_id], timeout=15)
    monitor = common.run(['virsh', '-c', 'qemu:///system', 'qemu-monitor-command',
                          server_id, '{"execute":"query-kvm"}'], timeout=15)
    launch.assert_kvm(xml, monitor)


def read_boot_id(state):
    script = "printf 'TASK11_BOOT_ID_BEGIN\\n'; cat /proc/sys/kernel/random/boot_id; printf 'TASK11_BOOT_ID_END\\n'\n"
    output = common.run(guest.ssh_command(state), input_text=script, timeout=20)
    return str(uuid.UUID(guest.frame(output, 'BOOT_ID')))


def wait_reboot(cloud, state, before, timeout=180):
    deadline = time.monotonic() + timeout
    diagnostic = 'guest boot ID has not changed'
    attempts = 0
    while time.monotonic() < deadline:
        server = owned_server(cloud, state)
        if settled(server, 'ACTIVE', 1):
            try:
                boot_id = read_boot_id(state)
                if boot_id != before:
                    return boot_id
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                diagnostic = str(exc)
        if attempts % 5 == 0:
            progress('waiting for ACTIVE/running and a changed guest kernel boot ID')
        attempts += 1
        time.sleep(2)
    raise RuntimeError('reboot completion is unverified: ' + diagnostic
                       + '; no reboot was automatically replayed. Inspect Nova actions if submission was interrupted.')


def stop_start(cloud, state, path, record):
    if 'phase' not in record:
        server = owned_server(cloud, state)
        if not settled(server, 'ACTIVE', 1):
            raise RuntimeError('a new lifecycle test requires the owned VM ACTIVE/running with no pending task')
        network, port = guest.validate_attachment(cloud, state)
        baseline = guest.inspect_guest(state, network.mtu, port.mac_address)
        record['baseline_boot_id'] = baseline['boot_id']
        record['verified'] = False
        save(state, path, record, 'baseline')
        progress('baseline guest identity, DHCP, metadata and boot ID: PASS')
    if record['phase'] not in PHASES:
        raise RuntimeError('unexpected saved lifecycle phase; preserving the evidence')
    if record['phase'] in ('baseline', 'stop_intent', 'stop_submitted'):
        server = owned_server(cloud, state)
        if record['phase'] == 'baseline':
            if not settled(server, 'ACTIVE', 1):
                raise RuntimeError('guest state changed before stop intent; preserving it')
            save(state, path, record, 'stop_intent')
        if record['phase'] == 'stop_intent' and settled(server, 'ACTIVE', 1):
            # A retry is restricted to stop/start and only when the observed
            # source state is still settled; an in-flight operation is awaited.
            owned_server(cloud, state)
            cloud.compute.stop_server(state['ids']['server'])
            save(state, path, record, 'stop_submitted')
            progress('stop request submitted; awaiting actual shutdown')
        wait_server(cloud, state, 'SHUTOFF', 4)
        shutoff = common.run(['virsh', '-c', 'qemu:///system', 'list', '--all',
                              '--uuid', '--state-shutoff'], timeout=15).splitlines()
        if state['ids']['server'] not in shutoff:
            raise RuntimeError('Nova reports SHUTOFF but libvirt does not confirm an inactive shutoff domain')
        record['stop'] = {'verified': True, 'status': 'SHUTOFF', 'power_state': 4,
                          'libvirt_shutoff': True, 'checked_at': now()}
        save(state, path, record, 'stopped')
        progress('stop: Nova SHUTOFF/power=4 and libvirt shutoff: PASS')
    if record['phase'] in ('stopped', 'start_intent', 'start_submitted'):
        server = owned_server(cloud, state)
        if record['phase'] == 'stopped':
            if not settled(server, 'SHUTOFF', 4):
                raise RuntimeError('guest changed state before this run recorded a start intent')
            save(state, path, record, 'start_intent')
        if record['phase'] == 'start_intent' and settled(server, 'SHUTOFF', 4):
            owned_server(cloud, state)
            cloud.compute.start_server(state['ids']['server'])
            save(state, path, record, 'start_submitted')
            progress('start request submitted; awaiting VM and guest recovery')
        wait_server(cloud, state, 'ACTIVE', 1)
        network, port = wait_attachment(cloud, state)
        report = guest.inspect_guest(state, network.mtu, port.mac_address)
        if report['boot_id'] == record['baseline_boot_id']:
            raise RuntimeError('guest kernel boot ID did not change after the observed stop/start')
        guest.record_ping(state, path, network.mtu)
        verify_acceleration(state)
        record['start'] = {'verified': True, 'status': 'ACTIVE', 'power_state': 1,
                           'boot_id': report['boot_id'], 'checked_at': now()}
        state['results']['guest'] = dict(report, checked_at=now())
        save(state, path, record, 'started')
        progress('start: ACTIVE/running, new guest boot ID, DHCP/metadata, MTU ping and KVM: PASS')


def reboot(cloud, state, path, record):
    if record['phase'] == 'started':
        server = owned_server(cloud, state)
        if not settled(server, 'ACTIVE', 1):
            raise RuntimeError('guest is not settled before reboot')
        record['reboot_before_boot_id'] = read_boot_id(state)
        save(state, path, record, 'reboot_intent')
        # If interrupted around this API call, a later invocation observes the
        # existing intent instead of blindly issuing another reboot.
        owned_server(cloud, state)
        cloud.compute.reboot_server(state['ids']['server'], reboot_type='SOFT')
        save(state, path, record, 'reboot_submitted')
        progress('SOFT reboot requested; awaiting actual guest boot ID change')
    if record['phase'] in ('reboot_intent', 'reboot_submitted'):
        changed = wait_reboot(cloud, state, record['reboot_before_boot_id'])
        network, port = wait_attachment(cloud, state)
        report = guest.inspect_guest(state, network.mtu, port.mac_address)
        if report['boot_id'] != changed:
            raise RuntimeError('guest boot identity changed again during reboot verification')
        guest.record_ping(state, path, network.mtu)
        verify_acceleration(state)
        record['reboot'] = {'verified': True, 'requested_type': 'SOFT',
                            'boot_id': changed, 'checked_at': now()}
        state['results']['guest'] = dict(report, checked_at=now())
        record['verified'] = True
        save(state, path, record, 'verified')
        progress('reboot: settled ACTIVE/running, changed boot ID, guest networking/metadata and KVM: PASS')
    if (record['phase'] != 'verified' or record.get('verified') is not True
            or not all(record.get(name, {}).get('verified') is True for name in ('stop', 'start', 'reboot'))):
        raise RuntimeError('lifecycle evidence is incomplete; completion not recorded')


def main():
    secrets = {}
    try:
        if os.geteuid() != 0:
            raise RuntimeError('run with sudo /usr/bin/python3 in the actual Ubuntu 22.04 WSL terminal')
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M)
                or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('this stage is scoped to Ubuntu 22.04 WSL')
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock',
                             os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as cloud:
                state, state_path = guest.load_run(cloud)
                progress('BEGIN; server=' + state['ids']['server'])
                if state.get('policy_restore_pending'):
                    raise RuntimeError('restore the pending task SSH rule with the policy stage first')
                if not state['results'].get('security_group_policy', {}).get('verified'):
                    raise RuntimeError('successful policy restoration/acceptance evidence is required first')
                policy.select_probe_rules(cloud, state)  # Read-only drift check.
                record = state['results'].setdefault('lifecycle', {})
                if record.get('phase') == 'verified':
                    reboot(cloud, state, state_path, record)  # Check completeness only.
                    server = owned_server(cloud, state)
                    if not settled(server, 'ACTIVE', 1):
                        raise RuntimeError('completed evidence exists but guest is no longer ACTIVE/running')
                    progress('previous complete lifecycle evidence retained; no operation replayed')
                else:
                    stop_start(cloud, state, state_path, record)
                    reboot(cloud, state, state_path, record)
                progress('COMPLETE (stop/start/reboot only; tenant isolation, cleanup and application integration remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr, flush=True)
        print('Task VM and protected phase evidence retained. No state reset, deletion or unverified success was applied.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
