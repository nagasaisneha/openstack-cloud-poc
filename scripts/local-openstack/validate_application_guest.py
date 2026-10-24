#!/usr/bin/python3
"""Read guest evidence and probe ARP isolation on the two owned networks.

No cloud resource/policy, route, address, service or VM lifecycle changes.
Creates a dedicated protected SSH trust file and writes acceptance evidence.
"""
import datetime
import contextlib
import fcntl
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import re
import sqlite3
import sys
import uuid

import configure_nova as common
import launch_test_vm as launch
import validate_guest as guest
import validate_policy as policy
import validate_tenants as tenants

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openstack_cloud_poc.packet_probe import mac_bytes
from openstack_cloud_poc.real_openstack import RealOpenStackAdapter

PROBE = Path(__file__).resolve().parents[2] / 'openstack_cloud_poc/packet_probe.py'


def progress(message):
    print('Task 11 application guest: ' + message, flush=True)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def validate_manifest(state, original):
    tenant = original['results']['tenant_api']['tenants']['a']
    if (state['run_id'] != original['run_id'] or state['project_id'] != tenant['project_id']
            or state['user_id'] != tenant['user_id'] or state['ids']['network'] != tenant['network_id']
            or not state['results'].get('application_create', {}).get('verified')
            or state.get('image_upload', {}).get('phase') != 'verified'
            or not re.fullmatch(r'openstack-job-[0-9a-f]{32}', state['create_job_id'])
            or state['server_name'] != state['create_job_id']):
        raise RuntimeError('application manifest does not match completed task create scope')
    for key in ('server', 'network', 'subnet', 'security_group', 'port'):
        if str(uuid.UUID(state['ids'][key])) != state['ids'][key]:
            raise RuntimeError('invalid application resource UUID')
    cidr = ipaddress.IPv4Network(state['cidr'])
    if (state['namespace'] != 'ovnmeta-' + state['ids']['network']
            or any(ipaddress.IPv4Address(state[key]) not in cidr for key in ('guest_ip', 'metadata_ip'))
            or state['guest_ip'] == state['metadata_ip']):
        raise RuntimeError('application namespace/address scope mismatch')
    for key in ('project_id', 'namespace'):
        if state[key] == original[key]:
            raise RuntimeError('packet experiment requires distinct task projects/namespaces')
    if cidr.overlaps(ipaddress.IPv4Network(original['cidr'])):
        raise RuntimeError('this packet experiment expects the two recorded non-overlapping subnets')


def verify_journal(state):
    journal = launch.DATA_DIR / 'application-jobs.sqlite'
    guest.protected_file(journal, 0o600)
    with contextlib.closing(sqlite3.connect(journal.as_uri() + '?mode=ro', uri=True)) as database:
        row = database.execute('SELECT project_id,actor_id,run_id,operation,state,instance_id,request FROM jobs WHERE job_id=?',
                               (state['create_job_id'],)).fetchone()
    expected = (state['project_id'], state['user_id'], state['run_id'], 'compute.create', 'succeeded', state['ids']['server'])
    if row is None or tuple(row[:6]) != expected:
        raise RuntimeError('durable application create record differs from the guest acceptance scope')
    request = json.loads(row[6])
    if request != {'operation': 'compute.create', 'project_id': state['project_id'],
                   'image_id': state['image_id'], 'flavor_id': state['ids']['flavor'], 'network_id': state['ids']['network']}:
        raise RuntimeError('journal create references differ from the protected manifest')


def trust_file():
    path = launch.DATA_DIR / 'application-known_hosts'
    if not path.exists() and not path.is_symlink():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
    guest.protected_file(path, 0o600)
    if path.stat().st_nlink != 1:
        raise RuntimeError('application SSH trust file must have a single link')
    return path


def namespace_interface(state):
    interfaces = json.loads(common.run(['ip', 'netns', 'exec', state['namespace'],
                                       'ip', '-j', '-4', 'addr', 'show']))
    if not isinstance(interfaces, list):
        raise RuntimeError('invalid metadata namespace address inventory')
    matches = [interface for interface in interfaces if isinstance(interface, dict) and any(
        isinstance(address, dict) and address.get('local') == state['metadata_ip']
        for address in (interface.get('addr_info') or []))]
    if len(matches) != 1:
        raise RuntimeError('expected one metadata interface owning the permitted probe source')
    name, index = matches[0].get('ifname'), matches[0].get('ifindex')
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', name)
            or type(index) is not int or index <= 0):
        raise RuntimeError('metadata interface name/index is missing or invalid')
    # IPv4-only address output can omit link-layer attributes. Get the MAC
    # explicitly and correlate both snapshots; never guess or reuse a cache.
    links = json.loads(common.run(['ip', 'netns', 'exec', state['namespace'],
                                  'ip', '-j', 'link', 'show', 'dev', name]))
    if (not isinstance(links, list) or len(links) != 1 or not isinstance(links[0], dict)
            or links[0].get('ifname') != name or links[0].get('ifindex') != index
            or links[0].get('link_type') != 'ether' or 'UP' not in (links[0].get('flags') or [])):
        raise RuntimeError('metadata link inventory is missing, changed or not an active Ethernet interface')
    mac = links[0].get('address')
    try:
        mac_bytes(mac)
    except (TypeError, ValueError):
        raise RuntimeError('metadata link inventory has no valid unicast MAC; no probes sent') from None
    return {'name': name, 'mac': mac.lower()}


def application_attachment(admin, cloud, state, original):
    server = RealOpenStackAdapter(cloud, state['project_id'], state['user_id']).server(state['project_id'], state['ids']['server'])
    if (server.status != 'ACTIVE' or server.task_state is not None or server.power_state != 1
            or (server.metadata or {}).get('poc_run_id') != state['run_id']
            or server.metadata.get('poc_job_id') != state['create_job_id'] or server.name != state['server_name']):
        raise RuntimeError('application server ownership/state differs from its create job')
    network = cloud.network.get_network(state['ids']['network'])
    if (network.project_id != state['project_id'] or network.description != tenants.marker(original)
            or network.is_shared or network.is_router_external):
        raise RuntimeError('application network ownership/private scope drift')
    subnet = cloud.network.get_subnet(state['ids']['subnet'])
    group = cloud.network.get_security_group(state['ids']['security_group'])
    marker = 'Task 11 application run ' + state['run_id']
    if (subnet.project_id != state['project_id'] or subnet.description != marker
            or subnet.network_id != network.id or subnet.cidr != state['cidr'] or not subnet.is_dhcp_enabled
            or group.project_id != state['project_id'] or group.description != marker):
        raise RuntimeError('application subnet/group scope drift')
    ports = list(cloud.network.ports(device_id=server.id))
    if len(ports) != 1:
        raise RuntimeError('expected exactly one application compute port')
    port = ports[0]
    if (port.id != state['ids']['port'] or port.network_id != network.id or port.project_id != state['project_id']
            or set(port.security_group_ids) != {group.id} or not port.is_port_security_enabled
            or port.status != 'ACTIVE' or port.fixed_ips != [{'subnet_id': subnet.id, 'ip_address': state['guest_ip']}]):
        raise RuntimeError('application port identity/address/security drift')
    metadata = list(admin.network.ports(network_id=network.id, device_owner='network:distributed'))
    if (len(metadata) != 1 or metadata[0].device_id != state['namespace']
            or metadata[0].project_id != state['project_id']
            or metadata[0].fixed_ips != [{'subnet_id': subnet.id, 'ip_address': state['metadata_ip']}]):
        raise RuntimeError('application metadata port differs from the recorded source')
    ingress = [rule for rule in cloud.network.security_group_rules(security_group_id=group.id) if rule.direction == 'ingress']
    expected = [dict(security_group_id=group.id, direction='ingress', ether_type='IPv4',
        protocol=protocol, remote_ip_prefix=state['metadata_ip'] + '/32',
        port_range_min=22 if protocol == 'tcp' else None, port_range_max=22 if protocol == 'tcp' else None,
        description=marker) for protocol in ('icmp', 'tcp')]
    if len(ingress) != 2 or any(sum(policy.matches_rule(rule, spec) for rule in ingress) != 1 for spec in expected):
        raise RuntimeError('application narrow ingress policy differs from approved probes')
    return network, port


def read_arp_policy(state, interface, known_hosts=None):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', interface):
        raise RuntimeError('invalid guest interface for read-only ARP policy check')
    script = "printf 'TASK11_ARP_POLICY_BEGIN\\n'\n"
    for name in ('all', interface):
        for setting in ('arp_ignore', 'arp_filter'):
            script += f"cat '/proc/sys/net/ipv4/conf/{name}/{setting}'\n"
    script += "printf 'TASK11_ARP_POLICY_END\\n'\n"
    output = common.run(guest.ssh_command(state, known_hosts=known_hosts), input_text=script, timeout=20)
    if guest.frame(output, 'ARP_POLICY').splitlines() != ['0', '0', '0', '0']:
        raise RuntimeError('guest ARP policy could suppress off-subnet replies; isolation probe remains unverified')


def arp_probe(state, interface, target_ip):
    output = common.run(['ip', 'netns', 'exec', state['namespace'], '/usr/bin/python3', '-B', str(PROBE),
                          interface['name'], state['metadata_ip'], target_ip], timeout=8)
    result = json.loads(output)
    if (result.get('attempts') != 3 or result.get('source_mac') != interface['mac']
            or result.get('source_ip') != state['metadata_ip'] or result.get('target_ip') != target_ip
            or not isinstance(result.get('replies'), list) or len(result['replies']) != 3
            or any(not isinstance(replies, list) for replies in result['replies'])):
        raise RuntimeError('invalid ARP probe result; no isolation result established')
    for replies in result['replies']:
        for mac in replies:
            mac_bytes(mac)
    return result


def require_positive(result, expected_mac):
    mac_bytes(expected_mac)
    if result.get('attempts') != 3 or len(result.get('replies', [])) != 3 or any(replies != [expected_mac.lower()] for replies in result['replies']):
        raise RuntimeError('same-network ARP positive control failed or returned an unexpected MAC')


def require_negative(result):
    if result.get('attempts') != 3 or result.get('replies') != [[], [], []]:
        raise RuntimeError('cross-network ARP target replied or observation is incomplete; preserve topology for investigation')


def validate(admin, cloud, original, state, path, known_hosts):
    state['results']['application_guest'] = {'verified': False}
    state['results']['packet_isolation'] = {'verified': False}
    launch.atomic_state(path, state)
    network, port = application_attachment(admin, cloud, state, original)
    report = guest.inspect_guest(state, network.mtu, port.mac_address,
                                expected_name=state['server_name'], known_hosts=known_hosts)
    guest.probe_ping(state, network.mtu)
    state['results']['application_guest'] = dict(report, verified=True, checked_at=now())
    launch.atomic_state(path, state)
    progress('application guest key-only SSH, identity, DHCP, metadata and MTU-sized ping: PASS')
    original_network, original_port = guest.validate_attachment(admin, original)
    policy.select_probe_rules(admin, original)
    baseline = guest.inspect_guest(original, original_network.mtu, original_port.mac_address)
    guest.probe_ping(original, original_network.mtu)
    read_arp_policy(state, report['interface'], known_hosts)
    read_arp_policy(original, baseline['interface'])
    endpoints = ((state, namespace_interface(state), port.mac_address, original['guest_ip']),
                 (original, namespace_interface(original), original_port.mac_address, state['guest_ip']))
    observations = []
    for source, interface, mac, target in endpoints:
        require_positive(arp_probe(source, interface, source['guest_ip']), mac)
    progress('both networks: guest reachability and three same-network ARP replies with matching Neutron MACs: PASS')
    for source, interface, mac, target in endpoints:
        result = arp_probe(source, interface, target)
        require_negative(result)
        observations.append({'source_network': source['ids']['network'], 'source_namespace': source['namespace'],
                             'target_ip': target, 'attempts': 3, 'reply_count': 0})
    for source, interface, mac, target in endpoints:
        require_positive(arp_probe(source, interface, source['guest_ip']), mac)
    # Guest identity/boot continuity plus positive packets after the negative
    # observations prevents dead guests or changed instances passing the test.
    after = guest.inspect_guest(state, network.mtu, port.mac_address,
                               expected_name=state['server_name'], known_hosts=known_hosts)
    original_after = guest.inspect_guest(original, original_network.mtu, original_port.mac_address)
    if after['boot_id'] != report['boot_id'] or original_after['boot_id'] != baseline['boot_id']:
        raise RuntimeError('guest rebooted during packet observations; result remains unverified')
    guest.probe_ping(state, network.mtu)
    guest.probe_ping(original, original_network.mtu)
    state['results']['packet_isolation'] = {'verified': True, 'protocol': 'ARP', 'directions': observations,
        'positive_controls_before_after': True, 'arp_ignore_filter_zero': True, 'checked_at': now(),
        'scope': 'metadata namespace L2 ARP path to these two IPv4 targets only'}
    launch.atomic_state(path, state)
    progress('three cross-network ARP requests per direction had no replies; before/after controls and guest continuity: PASS')


def main():
    secrets = {}
    try:
        if os.geteuid() != 0:
            raise RuntimeError('run with sudo /usr/bin/python3 -B in the actual Ubuntu 22.04 WSL terminal')
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('this stage is scoped to Ubuntu 22.04 WSL')
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as admin:
                original, _ = guest.load_run(admin)
                if original.get('policy_restore_pending'):
                    raise RuntimeError('restore the original pending SSH policy before packet acceptance')
                path = launch.DATA_DIR / 'application.json'
                guest.protected_file(path, 0o600)
                state = json.loads(path.read_text())
                validate_manifest(state, original)
                verify_journal(state)
                secret_path = launch.DATA_DIR / 'tenant-secrets.json'
                guest.protected_file(secret_path, 0o600)
                passwords = tenants.tenant_passwords(secret_path)
                secrets.update({'TENANT_' + key: value for key, value in passwords.items()})
                progress('BEGIN; server=' + state['ids']['server'])
                with tenants.tenant_connection(original['results']['tenant_api']['tenants']['a'], passwords['a']) as cloud:
                    validate(admin, cloud, original, state, path, trust_file())
                progress('COMPLETE (guest and bounded ARP path only; application stop/start/delete and final cleanup remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr)
        print('Guests, topology, policies, pinned keys and protected evidence preserved; no configuration or lifecycle changes.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
