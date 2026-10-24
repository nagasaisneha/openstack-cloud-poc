#!/usr/bin/python3
"""Prepare owned tenant assets; launch one VM through the real application.

No service/configuration changes. Retains both VMs and all evidence on failure.
Uses member credentials for application APIs; admin only prepares a private
flavor grant and reads the existing source image/protected inventory.
"""
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import re
import sys
import time
import uuid

import configure_nova as common
import launch_test_vm as launch
import validate_guest as guest
import validate_tenants as tenants
import validate_policy as policy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openstack_cloud_poc.catalog import CatalogCategory
from openstack_cloud_poc.real_openstack import RealLifecycleManagementService, RealOpenStackAdapter, RealProviderError

SOURCE_SHA256 = '7d6355852aeb6dbcd191bcda7cd74f1536cfe5cbf8a10495a7283a8396e4b75b'
SOURCE_SIZE = 21692416


def progress(message):
    print('Task 11 application create: ' + message, flush=True)


def missing_only(get, identifier):
    try:
        return get(identifier)
    except Exception as exc:
        if getattr(exc, 'status_code', None) == 404:
            return None
        raise


def ensure_probe_rules(cloud, group, source, marker):
    specs = [dict(security_group_id=group.id, direction='ingress', ether_type='IPv4',
        protocol=protocol, remote_ip_prefix=source + '/32',
        port_range_min=22 if protocol == 'tcp' else None,
        port_range_max=22 if protocol == 'tcp' else None, description=marker)
        for protocol in ('icmp', 'tcp')]
    ingress = [rule for rule in cloud.network.security_group_rules(security_group_id=group.id)
               if rule.direction == 'ingress']
    if (any(not any(policy.matches_rule(rule, spec) for spec in specs) for rule in ingress)
            or any(sum(policy.matches_rule(rule, spec) for rule in ingress) > 1 for spec in specs)):
        raise RuntimeError('application ingress policy drift; preserving rules')
    launch.allow_metadata_probe(cloud, group, source, marker)
    ingress = [rule for rule in cloud.network.security_group_rules(security_group_id=group.id)
               if rule.direction == 'ingress']
    if len(ingress) != 2 or any(sum(policy.matches_rule(rule, spec) for rule in ingress) != 1 for spec in specs):
        raise RuntimeError('application narrow ingress rules were not observed')


def source_image(admin):
    path = launch.DATA_DIR / 'application-cirros-source.qcow2'
    if not path.exists():
        original = admin.image.get_image(launch.IMAGE_ID)
        if original.status != 'active' or original.size != SOURCE_SIZE:
            raise RuntimeError('source image state/size changed; preserving it')
        # Exclusive creation: never overwrite a pre-existing image file.
        with path.open('xb') as output:
            admin.image.download_image(original, output=output)
    guest.protected_file(path, 0o600)
    if path.stat().st_size != SOURCE_SIZE or hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError('retained application source image failed integrity; preserving it for review')
    return path


def prepare(admin, cloud, original, state, path):
    marker = 'Task 11 application run ' + state['run_id']
    project = state['project_id']
    name = 'task11-app-' + state['run_id'][:12]
    network = cloud.network.get_network(state['ids']['network'])
    if (network.project_id != project or network.description != tenants.marker(original)
            or network.is_shared or network.is_router_external):
        raise RuntimeError('recorded tenant network no longer matches this task')
    subnet = tenants.select_owned(cloud.network.subnets(name=name + '-subnet'),
        state['ids'].get('subnet'), name + '-subnet', marker, project_id=project)
    if subnet is None:
        if list(cloud.network.ports(network_id=network.id)) or list(cloud.network.subnets(network_id=network.id)):
            raise RuntimeError('tenant network has unexpected existing consumers; preserving it')
        occupied = [item.cidr for item in admin.network.subnets()]
        occupied += [item['dst'] for item in json.loads(common.run(['ip', '-j', '-4', 'route', 'show', 'table', 'all']))
                     if item.get('dst') not in (None, 'default')]
        state['cidr'] = launch.choose_cidr(occupied, ('172.30.241.0/24', '10.203.1.0/24', '192.168.241.0/24'))
        launch.atomic_state(path, state)
        subnet = cloud.network.create_subnet(name=name + '-subnet', network_id=network.id,
            cidr=state['cidr'], ip_version=4, is_dhcp_enabled=True,
            gateway_ip=str(ipaddress.ip_network(state['cidr']).network_address + 1),
            dns_nameservers=[], description=marker)
    if subnet.network_id != network.id or subnet.cidr != state['cidr'] or not subnet.is_dhcp_enabled:
        raise RuntimeError('application subnet drift; preserving it')
    state['ids']['subnet'] = subnet.id
    launch.atomic_state(path, state)
    group = tenants.select_owned(cloud.network.security_groups(name=name + '-sg'),
        state['ids'].get('security_group'), name + '-sg', marker, project_id=project)
    if group is None:
        group = cloud.network.create_security_group(name=name + '-sg', description=marker)
    state['ids']['security_group'] = group.id
    state['metadata_ip'] = launch.metadata_address(admin, network, subnet)
    state['namespace'] = 'ovnmeta-' + network.id
    launch.atomic_state(path, state)
    ensure_probe_rules(cloud, group, state['metadata_ip'], marker)
    launch.setup_keypair(cloud, state, path, name + '-key')
    flavor = admin.compute.find_flavor(state['ids']['flavor'])
    if flavor is None:
        flavor = admin.compute.create_flavor(id=state['ids']['flavor'], name=name + '-tiny',
                                             ram=256, vcpus=1, disk=1, is_public=False)
    if (flavor.id != state['ids']['flavor'] or flavor.name != name + '-tiny'
            or flavor.ram != 256 or flavor.vcpus != 1 or flavor.disk != 1 or flavor.is_public):
        raise RuntimeError('application flavor conflict; preserving it')
    if not any(item['tenant_id'] == project for item in admin.compute.get_flavor_access(flavor)):
        admin.compute.flavor_add_tenant_access(flavor, project)
    image = missing_only(cloud.image.get_image, state['image_id'])
    if image is None:
        source = source_image(admin)
        image = cloud.image.create_image(name=name + '-image', id=state['image_id'],
            filename=str(source), disk_format='qcow2', container_format='bare', visibility='private',
            allow_duplicates=True, wait=True, timeout=90, validate_checksum=True, sha256=SOURCE_SHA256,
            properties={'poc_run_id': state['run_id']})
    image = cloud.image.get_image(state['image_id'])
    if (image.owner_id != project or image.visibility != 'private' or image.status != 'active'
            or image.name != name + '-image' or image.disk_format != 'qcow2' or image.size != SOURCE_SIZE
            or image.properties.get('poc_run_id') != state['run_id']):
        raise RuntimeError('application image conflict or incomplete upload; preserving it')
    progress('owned tenant subnet/security group/key, private flavor and private image prepared')
    return network, group


def execute(admin, cloud, original, state, path):
    network, group = prepare(admin, cloud, original, state, path)
    adapter = RealOpenStackAdapter(cloud, state['project_id'], state['user_id'])
    catalog = adapter.read_catalog(state['project_id'])
    for category, identifier in ((CatalogCategory.IMAGE, state['image_id']),
            (CatalogCategory.FLAVOR, state['ids']['flavor']), (CatalogCategory.NETWORK, network.id)):
        item = catalog.get(category, identifier)
        if item is None or not item.source.startswith('openstack:') or item.status.value == 'simulated':
            raise RuntimeError('application catalog did not observe the real tenant resource')
    try:
        adapter.read_catalog(original['project_id'])
    except RealProviderError as exc:
        if exc.code != 'authorization_denied':
            raise
    else:
        raise RuntimeError('application accepted foreign project scope')
    progress('real application catalog and project-scope rejection: PASS')
    journal = launch.DATA_DIR / 'application-jobs.sqlite'
    with RealLifecycleManagementService(adapter, journal, state['run_id'], create_options={
            'key_name': state['ids']['keypair'], 'security_groups': [{'name': group.name}]}) as service:
        if not state.get('create_job_id'):
            memory = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M).group(1))
            if memory < 768 * 1024:
                raise RuntimeError('less than 768 MiB available; no additional VM requested')
            limits = cloud.compute.get_limits().absolute
            for maximum, used, increment in (('max_total_instances', 'total_instances_used', 1),
                    ('max_total_cores', 'total_cores_used', 1), ('max_total_ram_size', 'total_ram_used', 256)):
                limit = getattr(limits, maximum)
                if limit != -1 and getattr(limits, used) + increment > limit:
                    raise RuntimeError('tenant compute quota cannot accommodate this small VM')
        accepted = service.submit({'operation': 'compute.create', 'project_id': state['project_id'],
            'image_id': state['image_id'], 'flavor_id': state['ids']['flavor'], 'network_id': network.id},
            idempotency_key='task11-application-create')
        state['create_job_id'] = accepted['job_id']
        state['server_name'] = accepted['job_id']
        launch.atomic_state(path, state)
        progress('application returned accepted job=' + accepted['job_id'] + '; awaiting provider completion')
        deadline = time.monotonic() + 220
        previous = None
        while time.monotonic() < deadline:
            job = service.get_job(accepted['job_id'], state['project_id'])
            if job['instance_id']:
                state['ids']['server'] = job['instance_id']
                launch.atomic_state(path, state)
            if job['state'] != previous:
                progress('application job state=' + job['state'])
                previous = job['state']
            if job['state'] == 'succeeded':
                break
            if job['state'] in ('failed', 'unverified'):
                raise RuntimeError('application job did not establish completion; safe category=' + str(job['failure_code']))
            time.sleep(2)
        else:
            raise RuntimeError('application job polling timed out; journal retained')
        inspected = adapter.inspect(state['project_id'], state['ids']['server'])
        if inspected['state'] != 'ACTIVE' or inspected['task_state'] is not None or inspected['power_state'] != 1:
            raise RuntimeError('application synchronous inspect did not confirm settled ACTIVE/running')
        duplicate = service.submit({'operation': 'compute.create', 'project_id': state['project_id'],
            'image_id': state['image_id'], 'flavor_id': state['ids']['flavor'], 'network_id': network.id},
            idempotency_key='task11-application-create')
        if duplicate != accepted:
            raise RuntimeError('same application request did not return the same durable job')
    server_id = state['ids']['server']
    launch.assert_kvm(common.run(['virsh', '-c', 'qemu:///system', 'dumpxml', server_id], timeout=15),
        common.run(['virsh', '-c', 'qemu:///system', 'qemu-monitor-command', server_id, '{"execute":"query-kvm"}'], timeout=15))
    ports = list(cloud.network.ports(device_id=server_id, network_id=network.id))
    if (len(ports) != 1 or ports[0].project_id != state['project_id']
            or ports[0].security_group_ids != [group.id] or not ports[0].is_port_security_enabled
            or ports[0].status != 'ACTIVE'):
        raise RuntimeError('application VM Neutron attachment/security does not match scope')
    addresses = [ip['ip_address'] for ip in ports[0].fixed_ips if ip['subnet_id'] == state['ids']['subnet']]
    if len(addresses) != 1:
        raise RuntimeError('expected one application VM IPv4 allocation')
    state['guest_ip'] = addresses[0]
    state['ids']['port'] = ports[0].id
    state['results']['application_create'] = {'verified': True, 'evidence_mode': 'real',
        'job_id': state['create_job_id'], 'instance_id': server_id, 'kvm_enabled': True,
        'checked_at': inspected['checked_at']}
    launch.atomic_state(path, state)
    progress('real application create/inspect, durable duplicate lookup, KVM and Neutron binding: PASS; server=' + server_id)


def main():
    secrets = {}
    try:
        if os.geteuid() != 0:
            raise RuntimeError('run with sudo /usr/bin/python3 in the actual Ubuntu 22.04 WSL terminal')
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
                original, _ = guest.load_run(admin, require_ssh=False)
                if not original['results'].get('tenant_api', {}).get('verified'):
                    raise RuntimeError('verified member-project API isolation is required first')
                secret_path = launch.DATA_DIR / 'tenant-secrets.json'
                guest.protected_file(secret_path, 0o600)
                passwords = tenants.tenant_passwords(secret_path)
                secrets.update({'TENANT_' + key: value for key, value in passwords.items()})
                tenant = original['results']['tenant_api']['tenants']['a']
                path = launch.DATA_DIR / 'application.json'
                if path.exists() or path.is_symlink():
                    guest.protected_file(path, 0o600)
                    state = json.loads(path.read_text())
                    if (state['run_id'] != original['run_id'] or state['project_id'] != tenant['project_id']
                            or state['user_id'] != tenant['user_id'] or state['ids']['network'] != tenant['network_id']):
                        raise RuntimeError('application manifest differs from the recorded task identity/network')
                else:
                    state = {'run_id': original['run_id'], 'project_id': tenant['project_id'],
                        'user_id': tenant['user_id'], 'image_id': str(uuid.uuid4()),
                        'ids': {'network': tenant['network_id'], 'flavor': str(uuid.uuid4())}, 'results': {}}
                    launch.atomic_state(path, state)
                state['results']['application_create'] = {'verified': False}
                launch.atomic_state(path, state)
                progress('BEGIN; member project=' + state['project_id'])
                with tenants.tenant_connection(tenant, passwords['a']) as cloud:
                    tenants.check_token(cloud, tenant)
                    execute(admin, cloud, original, state, path)
                progress('COMPLETE (application create/inspect only; guest, packet isolation, application stop/start/delete and cleanup remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr)
        print('Existing VM, task assets, credentials and durable journal retained; no automatic replay or deletion.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
