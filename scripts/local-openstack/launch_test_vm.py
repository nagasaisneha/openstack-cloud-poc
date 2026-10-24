#!/usr/bin/python3
"""Task 11 stage 4: create one owned private test VM and verify actual KVM.

Resource IDs and an SSH test key stay under /root/openstack-poc-acceptance.
No automatic deletion on failure. No host routes, bridges or external gateways.
"""
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
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

import configure_nova as common


DATA_DIR = Path('/root/openstack-poc-acceptance')
IMAGE_ID = 'da953e4e-38ca-47d2-b97a-b62d3f33646a'
RAM_MB = 256
CANDIDATES = ('172.30.240.0/24', '10.203.0.0/24', '192.168.240.0/24')


def progress(message):
    print(f'Task 11 real VM launch: {message}', flush=True)


def choose_cidr(existing, candidates=CANDIDATES):
    occupied = [ipaddress.ip_network(cidr, strict=False) for cidr in existing
                if cidr != 'default']
    occupied = [network for network in occupied if network.prefixlen != 0]
    for cidr in candidates:
        candidate = ipaddress.ip_network(cidr)
        if not any(candidate.overlaps(network) for network in occupied
                   if network.version == candidate.version):
            return cidr
    raise RuntimeError('all candidate private CIDRs overlap observed routes/addresses/subnets')


def atomic_state(path, state):
    descriptor, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def assert_owned(resource, state, marker, *, server=False):
    if resource.project_id != state['project_id']:
        raise RuntimeError('resource belongs to another project; preserving it')
    actual = (resource.metadata or {}).get('task11_run_id') if server else resource.description
    expected = state['run_id'] if server else marker
    if actual != expected:
        raise RuntimeError('resource ownership marker does not match this run; preserving it')


def ensure_owned(state, state_path, slot, get, find, create, *, server=False):
    marker = 'Task 11 local POC run ' + state['run_id']
    if slot in state['ids']:
        resource = get(state['ids'][slot])
    else:
        matches = list(find())
        if len(matches) > 1:
            raise RuntimeError(f'multiple {slot} candidates; preserving all')
        if matches:
            resource = matches[0]
        else:
            resource = create()
            # Nova POST responses can omit ownership/metadata fields. Record
            # the returned provider ID immediately, then inspect its full state.
            state['ids'][slot] = resource.id
            atomic_state(state_path, state)
        resource = get(resource.id)
    assert_owned(resource, state, marker, server=server)
    state['ids'][slot] = resource.id
    atomic_state(state_path, state)
    progress(f'{slot}: {resource.id}')
    return resource


def assert_kvm(xml, monitor):
    if ET.fromstring(xml).get('type') != 'kvm':
        raise RuntimeError('guest domain is not type=kvm; preserving it for diagnosis')
    result = json.loads(monitor).get('return', {})
    if result.get('enabled') is not True or result.get('present') is not True:
        raise RuntimeError('QEMU reports KVM is not present and enabled for this guest')


def preflight(cloud, state):
    image = cloud.image.get_image(IMAGE_ID)
    if image.status != 'active' or image.disk_format != 'qcow2' or image.owner_id != state['project_id']:
        raise RuntimeError('verified private CirrOS image is not active/owned/qcow2')
    if (image.min_ram or 0) > RAM_MB or (image.min_disk or 0) > 1:
        raise RuntimeError('test flavor is smaller than the image minimum')
    memory = Path('/proc/meminfo').read_text()
    available = int(re.search(r'^MemAvailable:\s+(\d+)', memory, re.M).group(1))
    if 'server' not in state['ids'] and available < 768 * 1024:
        raise RuntimeError('less than 768 MiB MemAvailable; preserve control-plane headroom before launch')
    if 'server' not in state['ids']:
        limits = cloud.compute.get_limits().absolute
        for label, maximum, used, needed in (
            ('instances', limits.instances, limits.instances_used, 1),
            ('vCPU', limits.total_cores, limits.total_cores_used, 1),
            ('RAM', limits.total_ram, limits.total_ram_used, RAM_MB),
        ):
            if maximum is None or used is None or (maximum != -1 and maximum - used < needed):
                raise RuntimeError(f'insufficient or unknown project quota: {label}')
    marker = 'Task 11 local POC run ' + state['run_id']
    occupied = [subnet.cidr for subnet in cloud.network.subnets()
                if subnet.id != state['ids'].get('subnet')
                and not (subnet.project_id == state['project_id'] and subnet.description == marker)]
    routes = json.loads(common.run(['ip', '-j', '-4', 'route', 'show', 'table', 'all']))
    occupied += [route['dst'] for route in routes if route.get('dst') not in (None, 'default')]
    addresses = json.loads(common.run(['ip', '-j', '-4', 'addr', 'show']))
    occupied += [f"{address['local']}/{address['prefixlen']}" for interface in addresses
                 for address in interface['addr_info'] if address['family'] == 'inet']
    state['cidr'] = choose_cidr(occupied, (state['cidr'],) if state.get('cidr') else CANDIDATES)
    progress('image, quota, memory and CIDR checks: PASS; subnet=' + state['cidr'])


def setup_keypair(cloud, state, state_path, name):
    private = DATA_DIR / 'id_ed25519'
    public = DATA_DIR / 'id_ed25519.pub'
    if private.is_symlink() or public.is_symlink():
        raise RuntimeError('SSH key path is a symlink; preserving it')
    if not private.exists() and not public.exists():
        common.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', name,
                    '-f', str(private)], timeout=15)
    if (not private.is_file() or not public.is_file() or private.stat().st_uid != 0
            or public.stat().st_uid != 0 or private.stat().st_mode & 0o077):
        raise RuntimeError('SSH test key is incomplete or not protected; preserving it')
    public_key = public.read_text().strip()
    keypair = cloud.compute.find_keypair(name)
    if keypair is None:
        keypair = cloud.compute.create_keypair(name=name, public_key=public_key)
    if keypair.public_key.split()[:2] != public_key.split()[:2]:
        raise RuntimeError('existing keypair does not match the retained local test key')
    state['ids']['keypair'] = name
    atomic_state(state_path, state)
    progress('SSH public key registered; private key remains root-only')


def metadata_address(cloud, network, subnet):
    for attempt in range(15):
        addresses = [ip['ip_address'] for port in cloud.network.ports(
            network_id=network.id, device_owner='network:distributed')
            for ip in port.fixed_ips if ip['subnet_id'] == subnet.id]
        if len(addresses) == 1:
            return str(ipaddress.IPv4Address(addresses[0]))
        time.sleep(1)
    raise RuntimeError('expected one OVN metadata port/address on the test subnet')


def allow_metadata_probe(cloud, group, source, marker):
    existing = list(cloud.network.security_group_rules(security_group_id=group.id))
    for protocol, lower, upper in (('icmp', None, None), ('tcp', 22, 22)):
        matches = [rule for rule in existing if rule.direction == 'ingress'
                   and rule.protocol == protocol and rule.remote_ip_prefix == source + '/32'
                   and rule.port_range_min == lower and rule.port_range_max == upper]
        if not matches:
            cloud.network.create_security_group_rule(
                security_group_id=group.id, direction='ingress', ether_type='IPv4',
                protocol=protocol, remote_ip_prefix=source + '/32',
                port_range_min=lower, port_range_max=upper, description=marker)


def launch(cloud, state, state_path):
    prefix = 'task11-' + state['run_id'][:12]
    marker = 'Task 11 local POC run ' + state['run_id']
    project = state['project_id']
    network = ensure_owned(state, state_path, 'network', cloud.network.get_network,
        lambda: cloud.network.networks(name=prefix + '-net'),
        lambda: cloud.network.create_network(name=prefix + '-net', project_id=project,
            description=marker, is_shared=False, is_router_external=False,
            is_port_security_enabled=True, provider_network_type='geneve'))
    if network.is_shared or network.is_router_external or network.provider_network_type != 'geneve':
        raise RuntimeError('test network is not private Geneve; preserving it')
    subnet = ensure_owned(state, state_path, 'subnet', cloud.network.get_subnet,
        lambda: cloud.network.subnets(name=prefix + '-subnet'),
        lambda: cloud.network.create_subnet(name=prefix + '-subnet', project_id=project,
            description=marker, network_id=network.id, cidr=state['cidr'], ip_version=4,
            is_dhcp_enabled=True, gateway_ip=str(ipaddress.ip_network(state['cidr']).network_address + 1),
            dns_nameservers=[]))
    if subnet.network_id != network.id or subnet.cidr != state['cidr'] or not subnet.is_dhcp_enabled:
        raise RuntimeError('existing task subnet differs from the retained run configuration')
    group = ensure_owned(state, state_path, 'security_group', cloud.network.get_security_group,
        lambda: cloud.network.security_groups(name=prefix + '-sg'),
        lambda: cloud.network.create_security_group(name=prefix + '-sg', project_id=project, description=marker))
    source = metadata_address(cloud, network, subnet)
    allow_metadata_probe(cloud, group, source, marker)
    state['metadata_ip'] = source
    state['namespace'] = 'ovnmeta-' + network.id
    atomic_state(state_path, state)
    progress('SSH/ICMP ingress limited to metadata namespace source ' + source + '/32')
    # Reserve a random flavor ID in the manifest before creation. A retry can
    # identify a completed POST even if the original response was interrupted.
    flavor_id = state['planned_flavor_id']
    flavor = cloud.compute.find_flavor(flavor_id)
    if flavor is None:
        flavor = cloud.compute.create_flavor(id=flavor_id, name=prefix + '-tiny',
            ram=RAM_MB, vcpus=1, disk=1, is_public=False)
    if flavor.id != flavor_id or flavor.name != prefix + '-tiny' or flavor.ram != RAM_MB or flavor.vcpus != 1 or flavor.disk != 1 or flavor.is_public:
        raise RuntimeError('test flavor differs from this run; preserving it')
    access = cloud.compute.get_flavor_access(flavor)
    if not any(entry['tenant_id'] == project for entry in access):
        cloud.compute.flavor_add_tenant_access(flavor, project)
    state['ids']['flavor'] = flavor.id
    atomic_state(state_path, state)
    setup_keypair(cloud, state, state_path, prefix + '-key')
    server_name = prefix + '-vm'
    server = ensure_owned(state, state_path, 'server', cloud.compute.get_server,
        lambda: (server for server in cloud.compute.servers(name=server_name)
                 if server.name == server_name),
        lambda: cloud.compute.create_server(name=server_name, image_id=IMAGE_ID,
            flavor_id=flavor.id, networks=[{'uuid': network.id}],
            security_groups=[{'name': group.name}], key_name=state['ids']['keypair'],
            config_drive=False, metadata={'task11_run_id': state['run_id']}), server=True)
    last_status = None
    for attempt in range(120):
        server = cloud.compute.get_server(server.id)
        assert_owned(server, state, marker, server=True)
        if server.status != last_status or attempt % 10 == 0:
            progress(f'Nova server state: {server.status}; id={server.id}')
            last_status = server.status
        if server.status == 'ACTIVE':
            break
        if server.status == 'ERROR':
            raise RuntimeError('Nova server entered ERROR: ' + str(server.fault or {}))
        time.sleep(2)
    else:
        raise RuntimeError('Nova server did not reach ACTIVE within the bounded launch wait')
    xml = common.run(['virsh', '-c', 'qemu:///system', 'dumpxml', server.id], timeout=15)
    monitor = common.run(['virsh', '-c', 'qemu:///system', 'qemu-monitor-command',
                          server.id, '{"execute":"query-kvm"}'], timeout=15)
    assert_kvm(xml, monitor)
    state['results']['kvm_enabled'] = True
    addresses = [ip['ip_address'] for port in cloud.network.ports(device_id=server.id)
                 if port.network_id == network.id for ip in port.fixed_ips
                 if ip['subnet_id'] == subnet.id]
    if len(addresses) != 1:
        raise RuntimeError('expected one Neutron-allocated IPv4 address for the guest')
    state['guest_ip'] = addresses[0]
    state['results']['nova_active'] = True
    atomic_state(state_path, state)
    progress('actual guest domain type=kvm; QEMU query-kvm present/enabled: PASS')
    progress(f'Neutron allocated address: {addresses[0]}; guest DHCP still requires verification')
    progress(f'network MTU={network.mtu}; acceptance manifest={state_path}')


def main():
    secrets = {}
    try:
        if os.geteuid() != 0:
            raise RuntimeError('run with sudo /usr/bin/python3 in the actual Ubuntu 22.04 WSL terminal')
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M) or 'microsoft' not in platform.release().lower():
            raise RuntimeError('this stage is scoped to Ubuntu 22.04 WSL')
        for executable in ('ssh-keygen', 'virsh', 'ip'):
            if not shutil.which(executable):
                raise RuntimeError('required host command missing: ' + executable)
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock',
                             os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            secrets = common.read_secrets(common.SECRET_FILE)
            if DATA_DIR.is_symlink():
                raise RuntimeError('acceptance directory is a symlink')
            DATA_DIR.mkdir(mode=0o700, exist_ok=True)
            info = DATA_DIR.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                raise RuntimeError('acceptance directory must be root-owned with mode 0700')
            state_path = DATA_DIR / 'run.json'
            with common.connection(secrets) as cloud:
                project = cloud.session.get_project_id()
                if state_path.exists() or state_path.is_symlink():
                    info = state_path.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
                        raise RuntimeError('unsafe acceptance manifest; preserving it')
                    state = json.loads(state_path.read_text())
                    if state['project_id'] != project or state['image_id'] != IMAGE_ID:
                        raise RuntimeError('manifest project/image differs from this approved run')
                else:
                    state = {'run_id': uuid.uuid4().hex, 'project_id': project,
                             'image_id': IMAGE_ID, 'planned_flavor_id': str(uuid.uuid4()),
                             'ids': {}, 'results': {}}
                    atomic_state(state_path, state)
                if 'server' not in state['ids']:
                    server_name = 'task11-' + state['run_id'][:12] + '-vm'
                    matches = [server for server in cloud.compute.servers(name=server_name)
                               if server.name == server_name]
                    if len(matches) > 1:
                        raise RuntimeError('multiple server candidates for this run; preserving all')
                    if matches:
                        assert_owned(matches[0], state, '', server=True)
                        state['ids']['server'] = matches[0].id
                        atomic_state(state_path, state)
                progress('BEGIN; run=' + state['run_id'])
                preflight(cloud, state)
                atomic_state(state_path, state)
                launch(cloud, state, state_path)
                progress('COMPLETE (guest DHCP, metadata, packet isolation, lifecycle and cleanup remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr, flush=True)
        print('Task-owned resources and root-only manifest retained for diagnosis/resumption; nothing was automatically deleted.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
