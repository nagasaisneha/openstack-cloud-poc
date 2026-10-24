#!/usr/bin/python3
"""Task 11 stage 6: allow/deny/restore TCP/22 on the owned test VM.

Temporarily removes only this run's SSH ingress rule. The ICMP rule remains.
Restoration runs in finally; a persisted marker supports interrupted recovery.
"""
import datetime
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import stat
import sys
import time

import configure_nova as common
import launch_test_vm as launch
import validate_guest as guest


TCP_PROBE = '''import json, socket, sys
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
    connection.settimeout(2)
    connection.bind((sys.argv[1], 0))
    port = connection.getsockname()[1]
    try:
        connection.connect((sys.argv[2], 22))
        result = {"outcome": "connected", "source_port": port}
    except socket.timeout:
        result = {"outcome": "timeout", "source_port": port}
    except OSError as error:
        result = {"outcome": "error", "errno": error.errno, "source_port": port}
    print(json.dumps(result))
'''


def progress(message):
    print(f'Task 11 policy validation: {message}', flush=True)


def tighten_known_hosts(path):
    """Correct only the observed root-owned 0644 task file, retaining its keys."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) not in (0o600, 0o644)):
            raise RuntimeError('unexpected known_hosts ownership/type/mode/link count; preserving it')
        before = hashlib.sha256(stream.read()).digest()
        if stat.S_IMODE(info.st_mode) == 0o644:
            os.fchmod(stream.fileno(), 0o600)
        stream.seek(0)
        if hashlib.sha256(stream.read()).digest() != before:
            raise RuntimeError('SSH trust file contents changed during mode correction')


def expected_rule(state, protocol):
    return dict(security_group_id=state['ids']['security_group'], direction='ingress',
                ether_type='IPv4', protocol=protocol,
                remote_ip_prefix=state['metadata_ip'] + '/32',
                port_range_min=22 if protocol == 'tcp' else None,
                port_range_max=22 if protocol == 'tcp' else None,
                description='Task 11 local POC run ' + state['run_id'])


def matches_rule(rule, spec):
    return (all(getattr(rule, key) == value for key, value in spec.items())
            and rule.remote_group_id is None
            and getattr(rule, 'remote_address_group_id', None) is None)


def owned_group(cloud, state):
    group = cloud.network.get_security_group(state['ids']['security_group'])
    launch.assert_owned(group, state, 'Task 11 local POC run ' + state['run_id'])
    return group


def select_probe_rules(cloud, state):
    owned_group(cloud, state)
    rules = list(cloud.network.security_group_rules(security_group_id=state['ids']['security_group']))
    ingress = [rule for rule in rules if rule.direction == 'ingress']
    ssh = [rule for rule in ingress if matches_rule(rule, expected_rule(state, 'tcp'))]
    icmp = [rule for rule in ingress if matches_rule(rule, expected_rule(state, 'icmp'))]
    if len(ingress) != 2 or len(ssh) != 1 or len(icmp) != 1:
        raise RuntimeError('test ingress differs from the two owned narrow probe rules; preserving all rules')
    return ssh[0], icmp[0]


def restore_pending_rule(cloud, state, state_path):
    pending = state.get('policy_restore_pending')
    if pending is None:
        return None
    if (pending.get('run_id') != state['run_id']
            or pending.get('group_id') != state['ids']['security_group']
            or pending.get('source') != state['metadata_ip'] + '/32'):
        raise RuntimeError('pending restoration does not match the protected task scope')
    owned_group(cloud, state)
    spec = expected_rule(state, 'tcp')
    rules = list(cloud.network.security_group_rules(security_group_id=state['ids']['security_group']))
    matches = [rule for rule in rules if matches_rule(rule, spec)]
    if len(matches) > 1:
        raise RuntimeError('multiple owned SSH rules found; preserving them for review')
    rule = matches[0] if matches else cloud.network.create_security_group_rule(**spec)
    # Persist the restored ID before clearing the recovery marker. A crash after
    # a successful API response can be reconciled by finding the identical rule.
    state['last_restored_ssh_rule_id'] = rule.id
    del state['policy_restore_pending']
    launch.atomic_state(state_path, state)
    return rule.id


def tcp_probe(state):
    text = common.run(['ip', 'netns', 'exec', state['namespace'], '/usr/bin/python3',
                       '-c', TCP_PROBE, state['metadata_ip'], state['guest_ip']], timeout=6)
    result = json.loads(text)
    if (result.get('outcome') not in ('connected', 'timeout', 'error')
            or not isinstance(result.get('source_port'), int)
            or not 1 <= result['source_port'] <= 65535):
        raise RuntimeError('invalid fresh TCP probe result')
    return result


def observe_denial(state, used_ports, probe=None, pause=None):
    probe = tcp_probe if probe is None else probe
    pause = time.sleep if pause is None else pause
    consecutive = 0
    for _ in range(12):
        result = probe(state)
        port = result['source_port']
        if port in used_ports:
            # A reused tuple supplies no fresh-connection evidence.
            pause(1)
            continue
        used_ports.add(port)
        if result['outcome'] == 'error':
            raise RuntimeError(f"TCP probe returned errno={result.get('errno')}; this is not policy-denial evidence")
        consecutive = consecutive + 1 if result['outcome'] == 'timeout' else 0
        if consecutive == 3:
            return 3
        pause(1)
    raise RuntimeError('did not observe three consecutive fresh TCP timeouts after rule removal')


def wait_tcp_allowed(state, used_ports):
    for _ in range(12):
        result = tcp_probe(state)
        if result['source_port'] not in used_ports:
            used_ports.add(result['source_port'])
            if result['outcome'] == 'connected':
                return
        time.sleep(1)
    raise RuntimeError('fresh TCP/22 did not recover after restoring its ingress rule')


def validate_policy(cloud, state, state_path, network, port):
    restored = restore_pending_rule(cloud, state, state_path)
    if restored:
        progress('recovered a pending task SSH rule restoration')
    ssh_rule, _ = select_probe_rules(cloud, state)
    before = guest.inspect_guest(state, network.mtu, port.mac_address)
    guest.probe_ping(state, network.mtu)
    baseline = tcp_probe(state)
    if baseline['outcome'] != 'connected':
        raise RuntimeError('baseline fresh TCP/22 connection did not succeed')
    used_ports = {baseline['source_port']}
    progress('baseline key-only SSH, guest identity, ICMP and fresh TCP/22: PASS')
    state['results']['guest'] = dict(before, checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    result = {'verified': False, 'original_ssh_rule_id': ssh_rule.id}
    state['results']['security_group_policy'] = result
    state['policy_restore_pending'] = {
        'run_id': state['run_id'], 'group_id': state['ids']['security_group'],
        'source': state['metadata_ip'] + '/32', 'original_rule_id': ssh_rule.id}
    launch.atomic_state(state_path, state)
    try:
        # The selected rule has the exact task marker, project-owned group,
        # protocol, source /32 and TCP/22 range; no other rule is removed.
        cloud.network.delete_security_group_rule(ssh_rule.id, ignore_missing=False)
        progress('task SSH ingress rule removed; checking new TCP connections')
        result['fresh_denials'] = observe_denial(state, used_ports)
        guest.validate_attachment(cloud, state)
        guest.probe_ping(state, network.mtu)
        progress('three distinct TCP/22 connection attempts timed out while ICMP remained reachable: PASS')
    finally:
        restored = restore_pending_rule(cloud, state, state_path)
        progress('task SSH ingress rule restored')
    wait_tcp_allowed(state, used_ports)
    after = guest.inspect_guest(state, network.mtu, port.mac_address)
    if after['boot_id'] != before['boot_id']:
        raise RuntimeError('guest rebooted during the policy test; policy evidence is inconclusive')
    guest.record_ping(state, state_path, network.mtu)
    result.update(verified=True, restored_ssh_rule_id=restored,
                  guest_boot_id_unchanged=True,
                  checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    state['results']['guest'] = dict(after, checked_at=result['checked_at'])
    launch.atomic_state(state_path, state)
    progress('fresh TCP/22 and pinned-key SSH recovered after restoration: PASS')


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
                state, state_path = guest.load_run(cloud, require_ssh=False)
                progress('BEGIN; server=' + state['ids']['server'])
                # Recovery must not depend on the guest still being powered on
                # or SSH/trust-file checks succeeding after an interruption.
                if restore_pending_rule(cloud, state, state_path):
                    progress('recovered pending task SSH ingress before guest checks')
                network, port = guest.validate_attachment(cloud, state)
                guest.protected_file(launch.DATA_DIR / 'id_ed25519', 0o600)
                tighten_known_hosts(launch.DATA_DIR / 'known_hosts')
                progress('known_hosts mode=0600; existing pinned key contents preserved')
                guest.load_run(cloud)  # Reapply the normal strict SSH file checks.
                validate_policy(cloud, state, state_path, network, port)
                progress('COMPLETE (TCP/22 policy test only; lifecycle, tenant isolation, cleanup and application integration remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr, flush=True)
        print('If policy restoration was interrupted, rerun this stage to restore only the recorded task SSH rule.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
