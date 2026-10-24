#!/usr/bin/python3
"""Remove only recorded application/isolation dependencies after VM deletion.

Requires an exact run marker, an intact VM recovery bundle, complete protected
inventory and a fresh metadata backup. Preserves the original demo resources.
No filesystem deletion, service restart, raw OVS mutation or SQL mutation.
"""
import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import platform
import re
import stat
import sys
import tempfile
import time
import uuid

import backup_application as backup
import configure_nova as common
import launch_test_vm as launch
import inspect_cleanup_dependencies as inventory_read
import validate_application_guest as application
import validate_application_lifecycle as lifecycle
import validate_guest as guest
import validate_tenants as tenants


def progress(message):
    print('Task 11 dependency cleanup: ' + message, flush=True)


def get_existing(get, identifier):
    try:
        return get(identifier)
    except Exception as exc:
        if getattr(exc, 'status_code', None) == 404:
            return None
        raise


def keypair_request(admin, method, user_id, name=None):
    from openstack import exceptions as sdk_exceptions

    url = '/os-keypairs' + ('/' + name if name is not None else '')
    # SDK Proxy.request defaults to raise_exc=False. A normal post-delete
    # 404 otherwise becomes an error JSON object without a "keypair" field.
    response = getattr(admin.compute, method)(url, params={'user_id': user_id},
        microversion='2.10', raise_exc=False)
    # Use the SDK exception convention (status_code), matching resource get
    # methods and get_existing; keystoneauth exceptions use http_status.
    sdk_exceptions.raise_from_response(response, error_message='Nova keypair request failed')
    expected = 204 if method == 'delete' else 200
    if response.status_code != expected:
        raise RuntimeError('unexpected Nova keypair HTTP status: ' + str(response.status_code))
    return response


def keypair(admin, name, user_id):
    payload = keypair_request(admin, 'get', user_id, name).json()
    if not isinstance(payload, dict) or not isinstance(payload.get('keypair'), dict):
        raise RuntimeError('unexpected Nova keypair response shape; preserving resources')
    return payload['keypair']


def keypairs(admin, user_id):
    payload = keypair_request(admin, 'get', user_id).json()
    if (not isinstance(payload, dict) or not isinstance(payload.get('keypairs'), list)
            or any(not isinstance(item, dict) or not isinstance(item.get('keypair'), dict)
                or not isinstance(item['keypair'].get('name'), str) for item in payload['keypairs'])):
        raise RuntimeError('unexpected Nova keypair list response shape; preserving resources')
    return payload['keypairs']


def clients(admin, item):
    kind = item['kind']
    if kind == 'keypair':
        return (lambda identifier: keypair(admin, identifier, item['user_id']),
                lambda identifier: keypair_request(admin, 'delete', item['user_id'], identifier))
    mapping = {'subnet': (admin.network.get_subnet, admin.network.delete_subnet),
        'network': (admin.network.get_network, admin.network.delete_network),
        'security_group': (admin.network.get_security_group, admin.network.delete_security_group),
        'image': (admin.image.get_image, admin.image.delete_image),
        'flavor': (admin.compute.get_flavor, admin.compute.delete_flavor),
        'user': (admin.identity.get_user, admin.identity.delete_user),
        'project': (admin.identity.get_project, admin.identity.delete_project)}
    get, delete = mapping[kind]
    return get, lambda identifier: delete(identifier, ignore_missing=False)


def assert_resource(resource, item, public_key):
    if item['kind'] == 'keypair':
        if (resource.get('name') != item['name'] or resource.get('user_id') != item['user_id']
                or resource.get('public_key', '').split()[:2] != public_key.split()[:2]):
            raise RuntimeError('keypair ownership/public material differs; preserving it')
        return
    if resource.id != item['id'] or resource.name != item['name']:
        raise RuntimeError('resource identity/name differs from the protected cleanup plan')
    kind = item['kind']
    if kind in ('project', 'user'):
        if resource.domain_id != 'default' or resource.description != item['marker']:
            raise RuntimeError('identity domain/ownership marker differs; preserving it')
    elif kind == 'image':
        if (resource.owner_id != item['project_id'] or resource.visibility != 'private'
                or (resource.properties or {}).get('poc_run_id') != item['marker']):
            raise RuntimeError('private image owner/run marker differs; preserving it')
    elif kind == 'flavor':
        if resource.is_public or (resource.ram, resource.vcpus, resource.disk) != (256, 1, 1):
            raise RuntimeError('private flavor differs from the recorded test size')
    elif resource.project_id != item['project_id'] or resource.description != item['marker']:
        raise RuntimeError('network resource owner/marker differs; preserving it')
    if kind == 'network' and (resource.is_shared or resource.is_router_external):
        raise RuntimeError('network is shared/external; cleanup blocked')


def preserved_demo(admin, original):
    server = admin.compute.get_server(original['ids']['server'])
    launch.assert_owned(server, original, '', server=True)
    if not lifecycle.infrastructure.settled(server, 'ACTIVE', 1):
        raise RuntimeError('original demo VM is not ACTIVE; preserve infrastructure for review')


def project_empty(admin, project_id):
    # Neutron's project-filtered SG list lazily creates a missing default SG.
    # Inspect host-local metadata in an explicit SQL read-only transaction.
    resources = inventory_read.network_resources([project_id])
    if resources:
        kinds = ','.join(sorted({item['kind'] for item in resources}))
        raise RuntimeError('project still contains network resources (' + kinds + '); no identity deletion')
    if any(server.project_id == project_id for server in admin.compute.servers(all_projects=True)):
        raise RuntimeError('project still contains a compute instance; no identity deletion')
    if list(admin.image.images(owner=project_id)) or list(admin.image.images(owner=project_id, is_hidden=True)):
        raise RuntimeError('project still owns images; no identity deletion')


def validate_plan(plan, original, state):
    """Bind persisted retry targets to the original protected manifests again."""
    records = original['results']['tenant_api']['tenants']
    run_id = state['run_id']
    prefix = 'task11-app-' + run_id[:12]
    app_marker = 'Task 11 application run ' + run_id
    expected = {}
    def expect(kind, identifier, name, project, marker, **extra):
        expected[kind, identifier] = dict(kind=kind, id=identifier, name=name,
            project_id=project, marker=marker, **extra)
    for label in ('a', 'b'):
        record = records[label]
        name = 'task11-' + run_id + '-' + label
        expect('network', record['network_id'], name + '-net', record['project_id'], tenants.marker(original))
        expect('user', record['user_id'], name, record['project_id'], tenants.marker(original), role_id=record['role_id'])
        expect('project', record['project_id'], name, record['project_id'], tenants.marker(original))
    project = records['a']['project_id']
    expect('subnet', state['ids']['subnet'], prefix + '-subnet', project, app_marker)
    expect('security_group', state['ids']['security_group'], prefix + '-sg', project, app_marker)
    expect('image', state['image_id'], prefix + '-image', project, run_id)
    expect('flavor', state['ids']['flavor'], prefix + '-tiny', project, run_id)
    expect('keypair', state['ids']['keypair'], prefix + '-key', project, run_id, user_id=records['a']['user_id'])
    protected = set(original['ids'].values()) | {original['image_id'], original['project_id']}
    seen, defaults = set(), set()
    for item in plan:
        key = item['kind'], item['id']
        if key in seen or item['id'] in protected:
            raise RuntimeError('duplicate/protected cleanup target; preserving all resources')
        seen.add(key)
        if item['kind'] != 'keypair':
            try:
                uuid.UUID(item['id'])
            except (ValueError, TypeError, AttributeError):
                raise RuntimeError('cleanup target is not a UUID') from None
        if key in expected:
            if item != expected[key]:
                raise RuntimeError('cleanup plan differs from the recorded task scope')
        elif (item['kind'] == 'security_group' and item['name'] == 'default'
                and item['marker'] == 'Default security group'
                and item['project_id'] in {records['a']['project_id'], records['b']['project_id']}
                and item['project_id'] not in defaults
                and set(item) == {'kind', 'id', 'name', 'project_id', 'marker'}):
            defaults.add(item['project_id'])
        else:
            raise RuntimeError('unexpected cleanup target; preserving all resources')
    if not set(expected) <= seen:
        raise RuntimeError('cleanup plan omits a recorded dependency')
    identity_order = [('user', records[label]['user_id']) if kind == 'user'
        else ('project', records[label]['project_id']) for label in ('a', 'b') for kind in ('user', 'project')]
    if [(item['kind'], item['id']) for item in plan[-4:]] != identity_order:
        raise RuntimeError('identity cleanup must follow all resource cleanup')


def validate_metadata_ports(ports, record, expected_ips):
    if len(ports) > 1:
        raise RuntimeError('multiple metadata/other port consumers; preserve the network')
    for port in ports:
        if (port.project_id != record['project_id'] or port.network_id != record['network_id']
                or port.device_owner != 'network:distributed'
                or port.device_id != 'ovnmeta-' + record['network_id']
                or {(ip['subnet_id'], ip['ip_address']) for ip in port.fixed_ips} != expected_ips
                or len(port.fixed_ips) != len(expected_ips)):
            raise RuntimeError('network contains an unexpected port consumer')


def inventory(admin, original, state, passwords, public_key):
    records = original['results']['tenant_api']['tenants']
    run_id = state['run_id']
    app_name = 'task11-app-' + run_id[:12]
    marker = 'Task 11 application run ' + run_id
    plan = []
    def add(kind, identifier, name, project, owner_marker, **extra):
        item = dict(kind=kind, id=identifier, name=name, project_id=project, marker=owner_marker, **extra)
        resource = clients(admin, item)[0](identifier)
        assert_resource(resource, item, public_key)
        plan.append(item)
        return resource
    # A catalogue with another service family requires a separate protected
    # inventory; do not infer Cinder/Swift/Heat project emptiness from Neutron.
    service_types = {service.type for service in admin.identity.services()}
    if service_types - {'identity', 'image', 'compute', 'placement', 'network'}:
        raise RuntimeError('additional service families require inventory before project cleanup')
    preserved_demo(admin, original)
    if not lifecycle.is_absent(admin.compute.get_server, state['ids']['server']):
        raise RuntimeError('application VM is still present; dependency cleanup blocked')
    all_servers = list(admin.compute.servers(all_projects=True))
    if any(server.project_id in {records['a']['project_id'], records['b']['project_id']} for server in all_servers):
        raise RuntimeError('test project contains an unexpected VM; preserve it')
    for label in ('a', 'b'):
        record = records[label]
        expected_name = 'task11-' + run_id + '-' + label
        project = admin.identity.get_project(record['project_id'])
        user = admin.identity.get_user(record['user_id'])
        for resource in (project, user):
            if resource.name != expected_name or resource.domain_id != 'default' or resource.description != tenants.marker(original):
                raise RuntimeError('test identity ownership no longer matches; preserving it')
        if project.id == original['project_id'] or user.id == admin.session.get_user_id():
            raise RuntimeError('cleanup cannot target operator identities')
        tenants.validate_assignments(list(admin.identity.role_assignments(user_id=user.id)), user.id, project.id, record['role_id'])
        with tenants.tenant_connection(record, passwords[label]) as member:
            tenants.check_token(member, record)
            if (member.identity.get('/users/' + user.id + '/groups').json()['groups']
                    or list(member.identity.application_credentials(user.id))):
                raise RuntimeError('test user has unexpected group/application-credential consumers')
        pairs = keypairs(admin, user.id)
        expected_keys = {state['ids']['keypair']} if label == 'a' else set()
        if {item['keypair']['name'] for item in pairs} != expected_keys:
            raise RuntimeError('test user has unexpected/missing SSH keypairs')
        expected_subnets = {state['ids']['subnet']} if label == 'a' else set()
        if {item.id for item in admin.network.networks(project_id=project.id)} != {record['network_id']}:
            raise RuntimeError('project contains unexpected/missing networks')
        if {item.id for item in admin.network.subnets(project_id=project.id)} != expected_subnets:
            raise RuntimeError('project contains unexpected/missing subnets')
        ports = list(admin.network.ports(network_id=record['network_id']))
        expected_ips = {(state['ids']['subnet'], state['metadata_ip'])} if label == 'a' else set()
        validate_metadata_ports(ports, record, expected_ips)
        if {port.id for port in admin.network.ports(project_id=project.id)} != {port.id for port in ports}:
            raise RuntimeError('project contains an unexpected off-network port')
        if list(admin.network.routers(project_id=project.id)) or list(admin.network.ips(project_id=project.id)):
            raise RuntimeError('project contains an unexpected router/floating IP')
        images = {image.id for image in list(admin.image.images(owner=project.id))
                  + list(admin.image.images(owner=project.id, is_hidden=True))}
        if images != ({state['image_id']} if label == 'a' else set()):
            raise RuntimeError('project owns unexpected/missing images')
        groups = list(admin.network.security_groups(project_id=project.id))
        default = [group for group in groups if group.name == 'default' and group.description == 'Default security group']
        expected_groups = {group.id for group in default} | ({state['ids']['security_group']} if label == 'a' else set())
        if len(default) > 1 or {group.id for group in groups} != expected_groups:
            raise RuntimeError('project contains an unexpected security group')
        if label == 'a':
            add('subnet', state['ids']['subnet'], app_name + '-subnet', project.id, marker)
        add('network', record['network_id'], expected_name + '-net', project.id, tenants.marker(original))
        if label == 'a':
            add('security_group', state['ids']['security_group'], app_name + '-sg', project.id, marker)
        for group in default:
            add('security_group', group.id, 'default', project.id, 'Default security group')
    add('image', state['image_id'], app_name + '-image', records['a']['project_id'], run_id)
    flavor = add('flavor', state['ids']['flavor'], app_name + '-tiny', records['a']['project_id'], run_id)
    if {item['tenant_id'] for item in admin.compute.get_flavor_access(flavor)} != {records['a']['project_id']}:
        raise RuntimeError('flavor has unexpected project grants')
    for server in all_servers:
        details = server.flavor or {}
        if 'id' not in details and 'original_name' not in details:
            raise RuntimeError('cannot establish flavor consumers safely')
        if details.get('id') == flavor.id or details.get('original_name') == flavor.name:
            raise RuntimeError('private test flavor still has a consumer')
    if state['ids']['keypair'] != app_name + '-key':
        raise RuntimeError('application keypair name differs from expected task name')
    add('keypair', state['ids']['keypair'], app_name + '-key', records['a']['project_id'], run_id, user_id=records['a']['user_id'])
    for label in ('a', 'b'):
        record = records[label]
        name = 'task11-' + run_id + '-' + label
        add('user', record['user_id'], name, record['project_id'], tenants.marker(original), role_id=record['role_id'])
        add('project', record['project_id'], name, record['project_id'], tenants.marker(original))
    targets = {item['id'] for item in plan}
    protected = set(original['ids'].values()) | {original['image_id'], original['project_id']}
    if targets & protected:
        raise RuntimeError('cleanup target overlaps the protected original demo scope')
    if any(policy.object_id in targets for policy in admin.network.rbac_policies()):
        raise RuntimeError('cleanup target has an RBAC sharing policy; preserve it')
    group_ids = {item['id'] for item in plan if item['kind'] == 'security_group'}
    if any(rule.remote_group_id in group_ids and rule.security_group_id not in group_ids
           for rule in admin.network.security_group_rules()):
        raise RuntimeError('another security group references a cleanup target')
    return plan


def cleanup_backup(state, path, plan):
    directory = Path(tempfile.mkdtemp(prefix='dependency-recovery-' + state['run_id'] + '-', dir=launch.DATA_DIR))
    record = state['results']['dependency_cleanup']
    record.setdefault('backup_attempts', []).append(str(directory))
    launch.atomic_state(path, state)
    backup.snapshot_journal(directory / 'application-jobs.sqlite')
    backup.snapshot_databases(directory)
    backup.snapshot_operational_files(directory / 'operational-files.tar.gz')
    (directory / 'cleanup-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    (directory / 'RECOVERY.md').write_text('''# Dependency cleanup recovery
This root-only metadata snapshot complements the separately verified VM disk
recovery bundle named in application.json. Keep both, credentials and source
images. SQL/OVSDB snapshots are individually consistent, not one distributed
transaction. Restoration has not been exercised. Prefer recreating a NEW
project/user/image/network scope from the recorded configuration and retained
source or flattened VM disk, with explicit recovery scope. Same-ID restoration
requires approved, quiesced whole-cloud SQL/OVSDB rollback and reconstruction
of any removed Glance payload from the verified retained source image. Do not
overwrite unrelated data, live database files or an existing VM directory.
''')
    hashes = {item.name: backup.checksum(item) for item in directory.iterdir() if item.is_file()}
    (directory / 'metadata.json').write_text(json.dumps({'run_id': state['run_id'], 'sha256': hashes}, indent=2) + '\n')
    backup.sync_bundle(directory)
    return directory


def verify_cleanup_backup(directory, state, plan):
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700
            or directory.parent != launch.DATA_DIR or not directory.name.startswith('dependency-recovery-' + state['run_id'] + '-')):
        raise RuntimeError('invalid protected metadata backup directory')
    guest.protected_file(directory / 'metadata.json', 0o600)
    record = json.loads((directory / 'metadata.json').read_text())
    required = {'application-jobs.sqlite', 'cloud-databases.sql', 'operational-files.tar.gz',
                'cleanup-plan.json', 'RECOVERY.md'} | {item[0] for item in backup.OVSDB}
    if record['run_id'] != state['run_id'] or set(record['sha256']) != required:
        raise RuntimeError('metadata recovery bundle incomplete or wrong run')
    for filename, digest in record['sha256'].items():
        target = directory / filename
        guest.protected_file(target, 0o600)
        if backup.checksum(target) != digest:
            raise RuntimeError('metadata backup integrity failed; cleanup blocked')
    if json.loads((directory / 'cleanup-plan.json').read_text()) != plan:
        raise RuntimeError('cleanup plan differs from verified recovery point')


def delete_item(admin, item, public_key):
    get, delete = clients(admin, item)
    resource = get_existing(get, item['id'])
    if resource is not None:
        assert_resource(resource, item, public_key)
        if item['kind'] in ('user', 'project'):
            project_empty(admin, item['project_id'])
        if item['kind'] == 'user':
            pairs = keypairs(admin, item['id'])
            if pairs:
                raise RuntimeError('test user still owns SSH keypairs; no identity deletion')
            assignments = list(admin.identity.role_assignments(user_id=item['id']))
            tenants.validate_assignments(assignments, item['id'], item['project_id'], item['role_id'])
            if assignments:
                admin.identity.unassign_project_role_from_user(item['project_id'], item['id'], item['role_id'])
        if item['kind'] == 'project' and list(admin.identity.role_assignments(scope_project_id=item['id'])):
            raise RuntimeError('project still has role assignments; no project deletion')
        delete(item['id'])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if get_existing(get, item['id']) is None:
            return
        time.sleep(2)
    raise RuntimeError('cleanup resource removal was not observed; no forced removal performed')


def verify_networks_gone(admin, plan):
    for item in plan:
        if item['kind'] != 'network':
            continue
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            logical = common.run(['ovn-nbctl', '--db=unix:/run/ovn/ovnnb_db.sock', '--timeout=5',
                '--bare', '--no-heading', '--columns=name', 'find', 'Logical_Switch', 'name=neutron-' + item['id']], timeout=10).strip()
            namespaces = [line.split()[0] for line in common.run(['ip', 'netns', 'list'], timeout=10).splitlines() if line.split()]
            if not logical and 'ovnmeta-' + item['id'] not in namespaces and not list(admin.network.ports(network_id=item['id'])):
                break
            time.sleep(2)
        else:
            raise RuntimeError('Neutron/OVN namespace cleanup not yet complete; no manual topology removal')


def execute(admin, original, state, path, passwords, public_key):
    record = state['results'].get('dependency_cleanup')
    if record is None:
        plan = inventory(admin, original, state, passwords, public_key)
        record = {'verified': False, 'plan': plan, 'done': []}
        state['results']['dependency_cleanup'] = record
        launch.atomic_state(path, state)
        progress('protected inventory: PASS; scoped resources=' + str(len(plan)))
    plan = record['plan']
    validate_plan(plan, original, state)
    if not record.get('backup_directory'):
        directory = cleanup_backup(state, path, plan)
        verify_cleanup_backup(directory, state, plan)
        record['backup_directory'] = str(directory)
        launch.atomic_state(path, state)
        progress('fresh metadata recovery bundle: PASS; path=' + str(directory))
    verify_cleanup_backup(Path(record['backup_directory']), state, plan)
    for index, item in enumerate(plan):
        if index not in record['done']:
            delete_item(admin, item, public_key)
            record['done'].append(index)
            launch.atomic_state(path, state)
            progress(item['kind'] + ' absent: ' + item['id'])
    for item in plan:
        if get_existing(clients(admin, item)[0], item['id']) is not None:
            raise RuntimeError('previously removed resource reappeared; no deletion replay')
    verify_networks_gone(admin, plan)
    preserved_demo(admin, original)
    record.update(verified=True, checked_at=application.now())
    launch.atomic_state(path, state)
    progress('scoped dependencies absent; OVN logical switches/metadata namespaces removed; original demo ACTIVE: PASS')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-run', required=True, help='exact task run marker whose recorded dependencies may be removed')
    args = parser.parse_args()
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
        fd = os.open('/root/.openstack-poc-nova-prerequisites.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as admin:
                original, _ = guest.load_run(admin, require_ssh=False)
                path = launch.DATA_DIR / 'application.json'
                guest.protected_file(path, 0o600)
                state = json.loads(path.read_text())
                application.validate_manifest(state, original)
                application.verify_journal(state)
                record = state['results']['application_lifecycle']
                if args.apply_run != state['run_id'] or record.get('phase') != 'deleted' or not record.get('verified'):
                    raise RuntimeError('exact run and verified application deletion required before cleanup')
                backup.verify_bundle(Path(record['backup_directory']), state['ids']['server'])
                secret_path = launch.DATA_DIR / 'tenant-secrets.json'
                guest.protected_file(secret_path, 0o600)
                passwords = tenants.tenant_passwords(secret_path)
                secrets.update({'TENANT_' + key: value for key, value in passwords.items()})
                public_path = launch.DATA_DIR / 'id_ed25519.pub'
                info = public_path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
                    raise RuntimeError('task public key is not a root-owned regular file')
                public_key = public_path.read_text().strip()
                if len(public_key.split()) < 2:
                    raise RuntimeError('task public key is incomplete')
                progress('BEGIN; exact run=' + state['run_id'])
                execute(admin, original, state, path, passwords, public_key)
                progress('COMPLETE (application/isolation dependencies only; operational closeout remains pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr)
        print('Original demo, historical assets and backups preserved; partial cleanup recorded without forced removals.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
