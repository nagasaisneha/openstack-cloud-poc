#!/usr/bin/python3
"""Task 11: two member-scoped identities and read-only cross-project probes.

Creates only run-marked projects/users and one empty network per project.
No VM operation, foreign-resource mutation, role/policy replacement or deletion.
Credentials remain in a separate root-only file; retained IDs support cleanup.
"""
import contextlib
import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import platform
import re
import secrets as random_secrets
import sys

import configure_nova as common
import launch_test_vm as launch
import validate_guest as guest


def progress(message):
    print('Task 11 tenant validation: ' + message, flush=True)


def marker(state):
    return 'Task 11 tenant acceptance ' + state['run_id']


def persist(path, state):
    launch.atomic_state(path, state)


def tenant_passwords(path):
    if path.exists() or path.is_symlink():
        guest.protected_file(path, 0o600)
        values = json.loads(path.read_text())
    else:
        values = {key: random_secrets.token_hex(32) for key in ('a', 'b')}
        # The shared root lock serializes all acceptance operations.
        persist(path, values)
    if (set(values) != {'a', 'b'}
            or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                   for value in values.values())):
        raise RuntimeError('invalid protected tenant credential record; preserving it')
    return values


def select_owned(items, saved_id, name, description, *, project_id=None):
    candidates = list(items)
    if len(candidates) > 1:
        raise RuntimeError('ambiguous task resource name; preserving all matches')
    if not candidates:
        if saved_id:
            raise RuntimeError('recorded resource disappeared; refusing automatic replacement')
        return None
    item = candidates[0]
    if (item.name != name or item.description != description
            or (saved_id and item.id != saved_id)
            or (project_id and item.project_id != project_id)):
        raise RuntimeError('resource name/ID/ownership marker conflict; preserving it')
    return item


def validate_assignments(assignments, user_id, project_id, role_id):
    found = False
    for assignment in assignments:
        if (assignment.user != {'id': user_id} or assignment.role != {'id': role_id}
                or assignment.scope != {'project': {'id': project_id}}):
            raise RuntimeError('unexpected role assignment on task user; refusing to change privileges')
        found = True
    return found


def prepare_identity(admin, state, path, label, password, role):
    record = state['results']['tenant_api']['tenants'].setdefault(label, {})
    name = 'task11-' + state['run_id'] + '-' + label
    description = marker(state)
    # Save the deterministic names before any create; a response-loss retry
    # recovers only a unique object bearing this exact run's marker.
    record['name'] = name
    persist(path, state)
    project = select_owned(admin.identity.projects(name=name, domain_id='default'),
                           record.get('project_id'), name, description)
    if project is None:
        project = admin.identity.create_project(name=name, domain_id='default',
                                                description=description, is_enabled=True)
    if project.domain_id != 'default' or not project.is_enabled or project.is_domain:
        raise RuntimeError('task project domain/enabled/type drift')
    record['project_id'] = project.id
    persist(path, state)
    user = select_owned(admin.identity.users(name=name, domain_id='default'),
                        record.get('user_id'), name, description)
    if user is None:
        user = admin.identity.create_user(name=name, domain_id='default', password=password,
                                          description=description, is_enabled=True)
    if user.domain_id != 'default' or not user.is_enabled:
        raise RuntimeError('task user domain/enabled drift')
    record['user_id'] = user.id
    persist(path, state)
    assignments = list(admin.identity.role_assignments(user_id=user.id))
    if not validate_assignments(assignments, user.id, project.id, role.id):
        admin.identity.assign_project_role_to_user(project.id, user.id, role.id)
    if not validate_assignments(list(admin.identity.role_assignments(user_id=user.id)),
                                user.id, project.id, role.id):
        raise RuntimeError('member role assignment not observed')
    record['role_id'] = role.id
    persist(path, state)
    return record


def tenant_connection(record, password, project_id=None):
    from keystoneauth1 import session
    from keystoneauth1.identity import v3
    from openstack.connection import Connection
    auth = v3.Password(auth_url=common.AUTH_URL, user_id=record['user_id'],
                       password=password, project_id=project_id or record['project_id'])
    return Connection(session=session.Session(auth=auth, timeout=10),
                      region_name='RegionOne', interface='internal')


def check_token(cloud, record):
    access = cloud.session.auth.get_access(cloud.session)
    roles = set(access.role_names)
    if (access.user_id != record['user_id'] or access.project_id != record['project_id']
            or not access.project_scoped or access.system_scoped
            or 'member' not in roles or not roles.issubset({'member', 'reader'})):
        raise RuntimeError('unexpected token identity/scope/roles; no probes performed')


def ensure_network(admin, cloud, state, path, record):
    name = record['name'] + '-net'
    network = select_owned(admin.network.networks(name=name, project_id=record['project_id']),
                           record.get('network_id'), name, marker(state),
                           project_id=record['project_id'])
    if network is None:
        # Creation is deliberately through the member token, not admin.
        network = cloud.network.create_network(name=name, description=marker(state),
                                                is_admin_state_up=True)
        if network.project_id != record['project_id']:
            raise RuntimeError('created network belongs to an unexpected project')
        record['network_id'] = network.id
        persist(path, state)
    if network.is_shared or network.is_router_external:
        raise RuntimeError('task tenant network is shared/external; preserving it')
    record['network_id'] = network.id
    persist(path, state)
    own = cloud.network.get_network(network.id)
    if own.project_id != record['project_id'] or own.description != marker(state):
        raise RuntimeError('member own-network read did not match recorded owner/marker')
    if network.id not in {item.id for item in cloud.network.networks()}:
        raise RuntimeError('member network list omitted its own network')


def expect_hidden(operation, *, accepted=(403, 404)):
    try:
        operation()
    except Exception as exc:
        # Only an actual provider denial/hiding response is accepted. Do not
        # mistake auth failures, unavailable endpoints or timeouts for isolation.
        status = getattr(exc, 'status_code', None)
        if status is None:
            status = getattr(exc, 'http_status', None)
        if status in accepted:
            return int(status)
        raise RuntimeError('isolation probe failed without the required provider response') from None
    raise RuntimeError('cross-project access unexpectedly succeeded; no foreign resource changed')


def check_hidden_lists(cloud, own, other, state):
    networks = {item.id for item in cloud.network.networks()}
    servers = {item.id for item in cloud.compute.servers()}
    images = {item.id for item in cloud.image.images()}
    if (own['network_id'] not in networks
            or other['network_id'] in networks or state['ids']['network'] in networks
            or state['ids']['server'] in servers or state['image_id'] in images):
        raise RuntimeError('list visibility violates the expected private project boundary')


def validate(admin, state, path, passwords):
    lifecycle = state['results'].get('lifecycle', {})
    if not lifecycle.get('verified') or state.get('policy_restore_pending'):
        raise RuntimeError('completed lifecycle and restored policy evidence required first')
    # Confirm the protected admin resources still exist before negative probes;
    # a 404 for an already absent object would not prove isolation.
    server = admin.compute.get_server(state['ids']['server'])
    launch.assert_owned(server, state, '', server=True)
    network = admin.network.get_network(state['ids']['network'])
    launch.assert_owned(network, state, 'Task 11 local POC run ' + state['run_id'])
    image = admin.image.get_image(state['image_id'])
    if (image.owner_id != state['project_id'] or image.visibility != 'private'
            or image.status != 'active' or network.is_shared or network.is_router_external):
        raise RuntimeError('private admin resource preconditions no longer hold')
    result = state['results'].setdefault('tenant_api', {'tenants': {}})
    result['verified'] = False
    persist(path, state)
    role = admin.identity.find_role('member', ignore_missing=False)
    records = {label: prepare_identity(admin, state, path, label, passwords[label], role)
               for label in ('a', 'b')}
    with contextlib.ExitStack() as stack:
        clients = {label: stack.enter_context(tenant_connection(records[label], passwords[label]))
                   for label in ('a', 'b')}
        for label, cloud in clients.items():
            check_token(cloud, records[label])
            ensure_network(admin, cloud, state, path, records[label])
            progress('tenant ' + label + ': member-scoped authentication and own-network create/read/list: PASS')
        for label, cloud in clients.items():
            own, other = records[label], records['b' if label == 'a' else 'a']
            checks = {
                'other_network': expect_hidden(lambda: cloud.network.get_network(other['network_id'])),
                'admin_network': expect_hidden(lambda: cloud.network.get_network(state['ids']['network'])),
                'admin_server': expect_hidden(lambda: cloud.compute.get_server(state['ids']['server'])),
                'private_image': expect_hidden(lambda: cloud.image.get_image(state['image_id'])),
            }
            check_hidden_lists(cloud, own, other, state)
            with tenant_connection(own, passwords[label], project_id=other['project_id']) as wrong_scope:
                checks['other_project_token'] = expect_hidden(wrong_scope.session.get_token, accepted=(401, 403))
            own['checks'] = dict(checks, list_visibility=True)
            persist(path, state)
            progress('tenant ' + label + ': foreign network/VM/private-image GETs hidden, lists filtered, other-project token denied: PASS')
    result['verified'] = True
    result['checked_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    persist(path, state)


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
            with common.connection(secrets) as admin:
                state, path = guest.load_run(admin, require_ssh=False)
                passwords = tenant_passwords(launch.DATA_DIR / 'tenant-secrets.json')
                secrets.update({'TENANT_' + key: value for key, value in passwords.items()})
                progress('BEGIN; run=' + state['run_id'])
                validate(admin, state, path, passwords)
                progress('COMPLETE (API project boundaries only; packet isolation, cleanup and application integration remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr)
        print('Task identities/networks and protected evidence retained; no foreign resources or policies changed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
