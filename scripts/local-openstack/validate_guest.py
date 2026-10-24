#!/usr/bin/python3
"""Task 11 stage 5: inspect the owned guest through its OVN metadata namespace.

Uses key-only SSH and read-only guest commands. No service or policy changes.
Writes only protected acceptance results and a dedicated SSH known-hosts file.
"""
import argparse
import datetime
import fcntl
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

import configure_nova as common
import launch_test_vm as launch


GUEST_PROBE = r'''set -eu
iface=$(ip -4 route get 169.254.169.254 | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')
[ -n "$iface" ]
printf 'TASK11_INTERFACE_BEGIN\n%s\nTASK11_INTERFACE_END\n' "$iface"
printf 'TASK11_BOOT_ID_BEGIN\n'
cat /proc/sys/kernel/random/boot_id
printf 'TASK11_BOOT_ID_END\nTASK11_ADDRESS_BEGIN\n'
ip -4 addr show dev "$iface"
printf 'TASK11_ADDRESS_END\nTASK11_MAC_BEGIN\n'
cat "/sys/class/net/$iface/address"
printf 'TASK11_MAC_END\nTASK11_MTU_BEGIN\n'
cat "/sys/class/net/$iface/mtu"
printf 'TASK11_MTU_END\nTASK11_DHCP_BEGIN\n'
sudo -n dhcpcd -U "$iface"
printf 'TASK11_DHCP_END\nTASK11_METADATA_BEGIN\n'
if command -v curl >/dev/null 2>&1; then
    curl --noproxy '*' --fail --silent --show-error --connect-timeout 3 --max-time 10 \
        http://169.254.169.254/openstack/latest/meta_data.json
else
    wget -q -T 10 -O - http://169.254.169.254/openstack/latest/meta_data.json
fi
printf '\nTASK11_METADATA_END\n'
'''


def progress(message):
    print(f'Task 11 guest validation: {message}', flush=True)


def frame(text, name):
    pattern = r'^TASK11_' + name + r'_BEGIN\r?\n(.*?)^TASK11_' + name + r'_END\r?$'
    matches = re.findall(pattern, text, re.M | re.S)
    if len(matches) != 1:
        raise RuntimeError(f'guest report has missing/ambiguous {name} evidence')
    return matches[0].strip()


def parse_guest_report(text, state, expected_mtu, expected_mac):
    interface = frame(text, 'INTERFACE')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', interface):
        raise RuntimeError('guest interface name is invalid')
    boot_id = str(uuid.UUID(frame(text, 'BOOT_ID')))
    expected_ip = str(ipaddress.IPv4Address(state['guest_ip']))
    expected_prefix = ipaddress.ip_network(state['cidr']).prefixlen
    addresses = re.findall(r'\binet\s+([0-9.]+)/(\d+)\b', frame(text, 'ADDRESS'))
    if (expected_ip, str(expected_prefix)) not in addresses:
        raise RuntimeError('guest interface does not have the expected Neutron IPv4 address/prefix')
    if frame(text, 'MAC').lower() != expected_mac.lower():
        raise RuntimeError('guest MAC differs from its Neutron port')
    mtu = int(frame(text, 'MTU'))
    if mtu != expected_mtu:
        raise RuntimeError('guest MTU differs from the Neutron network MTU')
    lease_ips = re.findall(r"(?m)^ip_address=['\"]?([0-9.]+)['\"]?\s*$", frame(text, 'DHCP'))
    if lease_ips != [expected_ip]:
        raise RuntimeError('DHCP lease dump does not confirm the expected guest IPv4 address')
    metadata = json.loads(frame(text, 'METADATA'))
    if metadata.get('uuid') != state['ids']['server']:
        raise RuntimeError('guest metadata UUID differs from the task Nova instance')
    name = 'task11-' + state['run_id'][:12] + '-vm'
    if metadata.get('name') != name:
        raise RuntimeError('guest metadata name differs from this task instance')
    if 'project_id' in metadata and metadata['project_id'] != state['project_id']:
        raise RuntimeError('guest metadata project differs from this task project')
    return {'boot_id': boot_id, 'interface': interface, 'ipv4': expected_ip,
            'mac': expected_mac.lower(), 'mtu': mtu,
            'dhcp_lease_verified': True, 'metadata_uuid': metadata['uuid']}


def protected_file(path, mode):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != mode:
        raise RuntimeError(f'{path.name} must be a root-owned regular file with mode {mode:04o}')


def load_run(cloud):
    directory = launch.DATA_DIR
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError('acceptance directory must be a root-owned directory with mode 0700')
    path = directory / 'run.json'
    protected_file(path, 0o600)
    state = json.loads(path.read_text())
    if not re.fullmatch('[0-9a-f]{32}', state['run_id']):
        raise RuntimeError('invalid run marker in acceptance manifest')
    for key in ('server', 'network', 'subnet', 'security_group'):
        if str(uuid.UUID(state['ids'][key])) != state['ids'][key]:
            raise RuntimeError('invalid resource ID in acceptance manifest')
    if state['project_id'] != cloud.session.get_project_id() or state['image_id'] != launch.IMAGE_ID:
        raise RuntimeError('acceptance manifest project/image differs from this run')
    if state['namespace'] != 'ovnmeta-' + state['ids']['network']:
        raise RuntimeError('namespace differs from the owned test network')
    subnet = ipaddress.ip_network(state['cidr'])
    for key in ('guest_ip', 'metadata_ip'):
        if ipaddress.IPv4Address(state[key]) not in subnet:
            raise RuntimeError('manifest IPv4 address is outside the test subnet')
    protected_file(directory / 'id_ed25519', 0o600)
    known_hosts = directory / 'known_hosts'
    if known_hosts.exists() or known_hosts.is_symlink():
        protected_file(known_hosts, 0o600)
    return state, path


def validate_attachment(cloud, state):
    marker = 'Task 11 local POC run ' + state['run_id']
    server = cloud.compute.get_server(state['ids']['server'])
    launch.assert_owned(server, state, marker, server=True)
    if server.status != 'ACTIVE' or server.task_state is not None or int(server.power_state) != 1:
        raise RuntimeError('task guest is not ACTIVE/running with no pending task; preserve it for diagnosis')
    resources = (
        (cloud.network.get_network, 'network'), (cloud.network.get_subnet, 'subnet'),
        (cloud.network.get_security_group, 'security_group'))
    found = {}
    for get, key in resources:
        found[key] = get(state['ids'][key])
        launch.assert_owned(found[key], state, marker)
    if found['network'].is_shared or found['network'].is_router_external:
        raise RuntimeError('test network is no longer private and isolated')
    if (found['subnet'].network_id != found['network'].id
            or found['subnet'].cidr != state['cidr'] or not found['subnet'].is_dhcp_enabled):
        raise RuntimeError('test subnet relationship, CIDR or DHCP configuration drift detected')
    ports = [port for port in cloud.network.ports(device_id=server.id)
             if port.network_id == found['network'].id]
    if len(ports) != 1:
        raise RuntimeError('expected exactly one compute port on the owned network')
    port = ports[0]
    if port.project_id != state['project_id'] or set(port.security_group_ids) != {state['ids']['security_group']} or not port.is_port_security_enabled:
        raise RuntimeError('guest port ownership/security-group/port-security drift detected')
    addresses = [ip['ip_address'] for ip in port.fixed_ips if ip['subnet_id'] == state['ids']['subnet']]
    if addresses != [state['guest_ip']]:
        raise RuntimeError('guest port address differs from the protected acceptance manifest')
    if launch.metadata_address(cloud, found['network'], found['subnet']) != state['metadata_ip']:
        raise RuntimeError('metadata port address changed; preserve probe rules for review')
    local = json.loads(common.run(['ip', 'netns', 'exec', state['namespace'],
                                  'ip', '-j', '-4', 'addr', 'show']))
    namespace_ips = {address['local'] for interface in local for address in interface['addr_info']}
    if state['metadata_ip'] not in namespace_ips:
        raise RuntimeError('metadata namespace does not own the permitted probe source address')
    return found['network'], port


def ssh_command(state):
    return ['ip', 'netns', 'exec', state['namespace'], 'ssh',
            '-F', '/dev/null',
            '-i', str(launch.DATA_DIR / 'id_ed25519'),
            '-b', state['metadata_ip'], '-l', 'cirros',
            '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
            '-o', 'KbdInteractiveAuthentication=no', '-o', 'IdentitiesOnly=yes',
            '-o', 'ProxyCommand=none', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
            '-o', 'ServerAliveInterval=3', '-o', 'ServerAliveCountMax=1',
            '-o', 'StrictHostKeyChecking=accept-new',
            '-o', 'UserKnownHostsFile=' + str(launch.DATA_DIR / 'known_hosts'),
            '-o', 'GlobalKnownHostsFile=/dev/null', state['guest_ip'], 'sh', '-s']


def inspect_guest(state, mtu, mac):
    deadline = time.monotonic() + 180
    diagnostic = 'SSH not ready'
    attempt = 0
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(ssh_command(state), input=GUEST_PROBE, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
            if result.returncode == 0:
                return parse_guest_report(result.stdout, state, mtu, mac)
            diagnostic = result.stdout[-1000:]
            if result.returncode != 255:
                raise RuntimeError(f'guest inspection command failed (exit {result.returncode}): {diagnostic}')
        except subprocess.TimeoutExpired:
            diagnostic = 'SSH/guest command exceeded its bounded timeout'
        if attempt % 5 == 0:
            progress('waiting for key-only SSH and guest readiness')
        attempt += 1
        time.sleep(2)
    raise RuntimeError('guest SSH readiness timed out: ' + diagnostic)


def probe_ping(state, mtu):
    if not isinstance(mtu, int) or not 576 <= mtu <= 65535:
        raise RuntimeError('unsupported network MTU for IPv4 packet probe')
    output = common.run(['env', 'LC_ALL=C', 'ip', 'netns', 'exec', state['namespace'],
                         'ping', '-n', '-I', state['metadata_ip'],
                         '-c', '3', '-W', '2', '-M', 'do', '-s', str(mtu - 28), state['guest_ip']], timeout=12)
    if not re.search(r'3 packets transmitted, 3 (?:packets )?received', output):
        raise RuntimeError('MTU-sized ping did not receive all three replies')


def record_ping(state, state_path, mtu):
    # Clear a prior successful result before a new attempt, and preserve any
    # already collected guest evidence if this individual check fails.
    result = {'verified': False, 'mtu': mtu, 'payload_bytes': mtu - 28,
              'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    state['results']['mtu_ping'] = result
    launch.atomic_state(state_path, state)
    probe_ping(state, mtu)
    result['verified'] = True
    launch.atomic_state(state_path, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ping-only', action='store_true',
                        help='retry only the MTU ping, keeping other evidence unchanged')
    args = parser.parse_args(argv)
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
        for command in ('ip', 'ssh', 'ping'):
            if not shutil.which(command):
                raise RuntimeError('required host command missing: ' + command)
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock',
                             os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as cloud:
                state, state_path = load_run(cloud)
                progress('BEGIN; server=' + state['ids']['server'])
                network, port = validate_attachment(cloud, state)
                if args.ping_only:
                    record_ping(state, state_path, network.mtu)
                    progress('three MTU-sized IPv4 echo replies through the OVN metadata namespace: PASS')
                    progress('COMPLETE (MTU ping only; other acceptance evidence unchanged)')
                    return 0
                report = inspect_guest(state, network.mtu, port.mac_address)
                progress('key-only SSH, matching guest IPv4/MAC and DHCP lease: PASS')
                progress(f"guest MTU matches Neutron: {report['mtu']}")
                progress('HTTP metadata UUID/name matches the Nova instance: PASS')
                report['checked_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                state['results']['guest'] = report
                launch.atomic_state(state_path, state)
                record_ping(state, state_path, network.mtu)
                progress('three MTU-sized IPv4 echo replies through the OVN metadata namespace: PASS')
                progress('COMPLETE (negative policy, tenant authorization, lifecycle, cleanup and application integration remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr, flush=True)
        print('Guest, network policy, services, backups and credentials retained without configuration changes.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
