#!/usr/bin/python3
"""Exercise real Swift object I/O and project isolation with owned test tenants.

Creates two run-marked, member-scoped Keystone identities, one private Swift
container and one small object. Cross-project and anonymous probes use the real
loopback Swift API. On any failed check, task resources are preserved for review.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import secrets
import stat
import sys
import traceback
from urllib.parse import quote

from keystoneauth1 import session
from keystoneauth1.identity import v3
from openstack.connection import Connection

import configure_nova as common
import configure_swift as swift_setup
import inspect_swift_readiness as swift_readiness
import launch_test_vm as launch


DATA = launch.DATA_DIR
STATE = DATA / 'swift-object-acceptance.json'
PRIVATE = DATA / 'swift-object-acceptance-private'
CREDS = PRIVATE / 'tenant-credentials.json'
PAYLOAD = PRIVATE / 'payload.bin'
SETUP_STATE = DATA / 'swift-setup.json'
LOCK = Path('/root/.openstack-poc-nova-prerequisites.lock')
PAYLOAD_SIZE = 4096
DENIED_STATUSES = {403, 404}


def progress(text):
    print('POC Swift objects: ' + text, flush=True)


def save(state):
    launch.atomic_state(STATE, state)


def protected_file(path, mode=0o600):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != mode):
        raise RuntimeError('unsafe protected acceptance file')
    return info


def protected_json(path):
    protected_file(path)
    return json.loads(path.read_text())


def marker(state):
    return 'T19 Swift object acceptance ' + state['run_id']


def passwords(state, resuming):
    if CREDS.exists() or CREDS.is_symlink():
        values = protected_json(CREDS)
        if (values.get('run_id') != state['run_id']
                or set(values) != {'run_id', 'a', 'b'}
                or any(not re.fullmatch(r'[0-9a-f]{64}', values.get(k, '')) for k in ('a', 'b'))):
            raise RuntimeError('invalid protected tenant credential record')
        return values
    if state.get('credentials_created') or any(
            item.get('project_id') or item.get('user_id') for item in state.get('tenants', {}).values()):
        raise RuntimeError('retained Swift tenant credentials are missing')
    values = {'run_id': state['run_id'], 'a': secrets.token_hex(32), 'b': secrets.token_hex(32)}
    launch.atomic_state(CREDS, values)
    state['credentials_created'] = True
    save(state)
    return values


def load_or_create_state(resume):
    if STATE.exists() or STATE.is_symlink():
        if not resume:
            raise RuntimeError('prior Swift object acceptance exists; use --resume after review')
        state = protected_json(STATE)
        if (state.get('schema') != 1 or state.get('task') != 'T19-swift-objects'
                or not re.fullmatch(r'[0-9a-f]{32}', state.get('run_id', ''))):
            raise RuntimeError('unexpected Swift object acceptance journal')
        expected_marker = marker(state)
        if state.get('marker') not in {None, expected_marker}:
            raise RuntimeError('Swift object acceptance marker drifted')
        if PRIVATE.exists() or PRIVATE.is_symlink():
            info = PRIVATE.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise RuntimeError('unsafe Swift object acceptance private directory')
        else:
            if (state.get('phase') != 'initializing'
                    or state.get('private_directory_intent') is not True
                    or state.get('credentials_created') or state.get('object', {}).get('payload_created')
                    or any(state.get('tenants', {}).values())):
                raise RuntimeError('retained Swift acceptance private directory is missing')
            PRIVATE.mkdir(mode=0o700)
            state['private_directory_ready'] = True
            save(state)
        return state
    if resume:
        raise RuntimeError('--resume requires the retained acceptance journal')
    if PRIVATE.exists() or PRIVATE.is_symlink():
        raise RuntimeError('unowned Swift object acceptance private directory exists')
    state = {'schema': 1, 'task': 'T19-swift-objects', 'run_id': secrets.token_hex(16),
             'phase': 'initializing', 'marker': None, 'tenants': {}, 'steps': [],
             'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'object': {}, 'results': {}, 'private_directory_intent': True}
    save(state)
    PRIVATE.mkdir(mode=0o700)
    state['private_directory_ready'] = True
    save(state)
    return state


def ensure_payload(state, resuming):
    record = state['object']
    if PAYLOAD.exists() or PAYLOAD.is_symlink():
        protected_file(PAYLOAD)
        data = PAYLOAD.read_bytes()
        if (len(data) != PAYLOAD_SIZE or record.get('length') != len(data)
                or record.get('sha256') != hashlib.sha256(data).hexdigest()):
            raise RuntimeError('retained task payload differs from its journal')
        return data
    if record.get('payload_created'):
        raise RuntimeError('retained task payload is missing')
    data = secrets.token_bytes(PAYLOAD_SIZE)
    descriptor = os.open(PAYLOAD, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    record.update({'payload_created': True, 'length': len(data),
                   'sha256': hashlib.sha256(data).hexdigest()})
    save(state)
    return data


def select_owned(items, saved_id, name, description):
    candidates = list(items)
    if len(candidates) > 1:
        raise RuntimeError('ambiguous task identity name; preserving all matches')
    if not candidates:
        if saved_id:
            raise RuntimeError('recorded task identity disappeared; refusing replacement')
        return None
    item = candidates[0]
    if (item.id != saved_id and saved_id) or item.name != name or item.description != description:
        raise RuntimeError('task identity marker or ID conflict; preserving it')
    return item


def tenant_connection(record, password):
    auth = v3.Password(auth_url=common.AUTH_URL, user_id=record['user_id'],
                       password=password, project_id=record['project_id'])
    return Connection(session=session.Session(auth=auth, timeout=10),
                      region_name='RegionOne', interface='internal')


def check_member_scope(cloud, record):
    access = cloud.session.auth.get_access(cloud.session)
    roles = set(access.role_names)
    if (access.user_id != record['user_id'] or access.project_id != record['project_id']
            or not access.project_scoped or access.system_scoped
            or 'member' not in roles or not roles.issubset({'member'})):
        raise RuntimeError('Swift probe token scope or roles differ from the expected member tenant')


def ensure_identity(admin, state, label, password, member_role):
    record = state['tenants'].setdefault(label, {})
    name = 'swift-poc-' + state['run_id'] + '-' + label
    description = marker(state)
    record['name'] = name
    save(state)
    project = select_owned(admin.identity.projects(name=name, domain_id='default'),
                           record.get('project_id'), name, description)
    if project is None:
        project = admin.identity.create_project(name=name, domain_id='default',
                                                description=description, is_enabled=True)
    if (project.domain_id != 'default' or not project.is_enabled or project.is_domain
            or project.description != description):
        raise RuntimeError('task project state or marker drift')
    record['project_id'] = project.id
    save(state)

    user = select_owned(admin.identity.users(name=name, domain_id='default'),
                        record.get('user_id'), name, description)
    if user is None:
        user = admin.identity.create_user(name=name, domain_id='default', password=password,
                                          description=description, is_enabled=True)
    if (user.domain_id != 'default' or not user.is_enabled or user.description != description):
        raise RuntimeError('task user state or marker drift')
    record['user_id'] = user.id
    save(state)

    assignments = list(admin.identity.role_assignments(user_id=user.id,
                                                       scope_project_id=project.id))
    role_ids = swift_setup.role_ids_from_assignments(assignments)
    if role_ids - {member_role.id}:
        raise RuntimeError('task user has an unexpected project role; preserving assignments')
    if member_role.id not in role_ids:
        record['role_create_intent'] = True
        save(state)
        admin.identity.assign_project_role_to_user(project.id, user.id, member_role.id)
    observed = list(admin.identity.role_assignments(user_id=user.id,
                                                    scope_project_id=project.id))
    if swift_setup.role_ids_from_assignments(observed) != {member_role.id}:
        raise RuntimeError('task member role assignment was not observed exactly')
    record['role_id'] = member_role.id
    save(state)
    return record


def endpoint_for(cloud, project_id):
    endpoint = cloud.session.get_endpoint(service_type='object-store', interface='internal',
                                          region_name='RegionOne')
    if endpoint == swift_setup.API_URL:
        endpoint = f'http://127.0.0.1:8080/v1/AUTH_{project_id}'
    if not swift_readiness.expected_account_endpoint(endpoint, project_id):
        raise RuntimeError('tenant Swift catalog endpoint is not the expected loopback project account')
    return endpoint.rstrip('/')


def api_request(cloud, method, url, **kwargs):
    return cloud.session.request(url, method, redirect=0, raise_exc=False, **kwargs)


def response_status(response):
    try:
        return int(response.status_code)
    finally:
        response.close()


def assert_denied(cloud, method, url, *, data=None, headers=None, restore=None):
    response = api_request(cloud, method, url, data=data, headers=headers)
    status = response_status(response)
    if status not in DENIED_STATUSES:
        if restore is not None:
            restore()
        raise RuntimeError('cross-project Swift operation was not denied with HTTP 403/404')
    return status


def ensure_container(owner, state, endpoint):
    record = state['object']
    name = record['container']
    url = endpoint + '/' + quote(name, safe='')
    headers = {'X-Container-Meta-Poc-Run-Id': state['run_id']}
    head = api_request(owner, 'HEAD', url)
    if head.status_code == 204:
        owned = head.headers.get('X-Container-Meta-Poc-Run-Id') == state['run_id']
        head.close()
        if not owned:
            raise RuntimeError('container name exists without this run marker; preserving it')
        record['container_created'] = True
        save(state)
        return name
    status = response_status(head)
    if status != 404:
        raise RuntimeError('task Swift container cannot be safely inspected before create')
    record['container_create_intent'] = True
    save(state)
    response = api_request(owner, 'PUT', url, headers=headers)
    status = response_status(response)
    if status not in {201, 202}:
        raise RuntimeError('task Swift container create did not return HTTP 201/202')
    check = api_request(owner, 'HEAD', url)
    if (check.status_code != 204
            or check.headers.get('X-Container-Meta-Poc-Run-Id') != state['run_id']):
        check.close()
        raise RuntimeError('task Swift container ownership metadata did not match')
    check.close()
    record['container_created'] = True
    save(state)
    return name


def put_object(cloud, state, endpoint, data):
    record = state['object']
    url = endpoint + '/' + quote(record['container'], safe='') + '/' + quote(record['name'], safe='')
    headers = {'X-Object-Meta-Poc-Run-Id': state['run_id'],
               'Content-Type': 'application/octet-stream'}
    existing = api_request(cloud, 'HEAD', url)
    if existing.status_code == 200:
        owned = (existing.headers.get('X-Object-Meta-Poc-Run-Id') == state['run_id']
                 and int(existing.headers.get('Content-Length', '-1')) == record['length'])
        existing.close()
        if not owned:
            raise RuntimeError('object name exists without this run marker; preserving it')
        get_and_verify(cloud, state, url, data)
        record['object_created'] = True
        save(state)
        return url
    status = response_status(existing)
    if status != 404:
        raise RuntimeError('task Swift object cannot be safely inspected before upload')
    record['object_create_intent'] = True
    save(state)
    response = api_request(cloud, 'PUT', url, data=data, headers=headers)
    status = response_status(response)
    if status not in {201, 202}:
        raise RuntimeError('task Swift object PUT did not return HTTP 201/202')
    head = api_request(cloud, 'HEAD', url)
    if (head.status_code != 200
            or head.headers.get('X-Object-Meta-Poc-Run-Id') != state['run_id']
            or int(head.headers.get('Content-Length', '-1')) != record['length']):
        head.close()
        raise RuntimeError('task Swift object metadata/length did not match')
    etag = head.headers.get('ETag', '').strip('"').lower()
    head.close()
    record['object_created'] = True
    record['etag'] = etag
    save(state)
    return url


def restore_owned_object(owner, state, endpoint, payload):
    record = state['object']
    if (not record.get('object_created')
            or record.get('container') != 'poc-' + state['run_id'][:24]
            or record.get('name') != 'payload-' + state['run_id']):
        raise RuntimeError('isolation probe recovery lacks task object ownership proof')
    url = endpoint + '/' + quote(record['container'], safe='') + '/' + quote(record['name'], safe='')
    record['probe_restore_intent'] = True
    save(state)
    head = api_request(owner, 'HEAD', url)
    status = head.status_code
    head.close()
    if status not in {200, 404}:
        raise RuntimeError('task object cannot be safely restored after isolation probe')
    response = api_request(owner, 'PUT', url, data=payload, headers={
        'X-Object-Meta-Poc-Run-Id': state['run_id'],
        'Content-Type': 'application/octet-stream'})
    if response_status(response) not in {201, 202}:
        raise RuntimeError('task object restore after isolation probe failed')
    get_and_verify(owner, state, url, payload)
    record['probe_restore_intent'] = False
    save(state)


def get_and_verify(cloud, state, url, expected):
    response = api_request(cloud, 'GET', url)
    if response.status_code != 200:
        status = response_status(response)
        raise RuntimeError('authorized Swift object GET did not return HTTP 200 (' + str(status) + ')')
    actual = response.content
    response.close()
    digest = hashlib.sha256(actual).hexdigest()
    if len(actual) != len(expected) or digest != hashlib.sha256(expected).hexdigest():
        raise RuntimeError('Swift object round-trip content digest or length mismatch')
    return {'status': 'PASS', 'length': len(actual), 'sha256': digest}


def anonymous_denial(url):
    import requests
    with requests.Session() as anonymous:
        anonymous.trust_env = False
        response = anonymous.head(url, allow_redirects=False, timeout=10)
        status = response.status_code
        response.close()
    if status not in DENIED_STATUSES:
        raise RuntimeError('anonymous Swift object request was not denied with HTTP 403/404')
    return status


def ensure_owned_records(admin, state):
    role = admin.identity.find_role('member', ignore_missing=False)
    records = {}
    passwords_value = protected_json(CREDS)
    for label in ('a', 'b'):
        records[label] = ensure_identity(admin, state, label, passwords_value[label], role)
    return records, passwords_value


def cleanup_object(owner, state, endpoint):
    record = state['object']
    container_url = endpoint + '/' + quote(record['container'], safe='')
    object_url = container_url + '/' + quote(record['name'], safe='')
    if not record.get('object_deleted'):
        head = api_request(owner, 'HEAD', object_url)
        if head.status_code == 200:
            if (head.headers.get('X-Object-Meta-Poc-Run-Id') != state['run_id']
                    or int(head.headers.get('Content-Length', '-1')) != record['length']):
                head.close()
                raise RuntimeError('object ownership changed; refusing cleanup')
            head.close()
            record['cleanup_object_intent'] = True
            save(state)
            status = response_status(api_request(owner, 'DELETE', object_url))
            if status not in {204, 404}:
                raise RuntimeError('task Swift object delete failed')
        elif response_status(head) != 404:
            raise RuntimeError('task Swift object cannot be safely inspected for cleanup')
        record['object_deleted'] = True
        save(state)
    if not record.get('container_deleted'):
        head = api_request(owner, 'HEAD', container_url)
        if head.status_code == 204:
            if head.headers.get('X-Container-Meta-Poc-Run-Id') != state['run_id']:
                head.close()
                raise RuntimeError('container ownership changed; refusing cleanup')
            head.close()
            record['cleanup_container_intent'] = True
            save(state)
            status = response_status(api_request(owner, 'DELETE', container_url))
            if status not in {204, 404}:
                raise RuntimeError('task Swift container delete failed')
        elif response_status(head) != 404:
            raise RuntimeError('task Swift container cannot be safely inspected for cleanup')
        record['container_deleted'] = True
        save(state)
    for url in (object_url, container_url):
        response = api_request(owner, 'HEAD', url)
        if response_status(response) != 404:
            raise RuntimeError('deleted task Swift resource is still visible')


def delete_tenant(admin, state, label, role_id):
    record = state['tenants'][label]
    name = record['name']
    description = marker(state)
    if not record.get('user_deleted'):
        user = admin.identity.get_user(record['user_id'])
        if user is not None:
            if user.name != name or user.description != description or not user.is_enabled:
                raise RuntimeError('task user ownership changed; refusing cleanup')
            project = admin.identity.get_project(record['project_id'])
            if project is None or project.name != name or project.description != description:
                raise RuntimeError('task project ownership changed; refusing role cleanup')
            assignments = list(admin.identity.role_assignments(user_id=user.id))
            for assignment in assignments:
                user_ref = getattr(assignment, 'user', None)
                role_ref = getattr(assignment, 'role', None)
                scope_ref = getattr(assignment, 'scope', None)
                if (user_ref != {'id': user.id} or role_ref != {'id': role_id}
                        or scope_ref != {'project': {'id': project.id}}):
                    raise RuntimeError('unexpected role assignment on task user; preserving identity')
            if assignments:
                admin.identity.unassign_project_role_from_user(project.id, user.id, role_id)
            if list(admin.identity.role_assignments(user_id=user.id)):
                raise RuntimeError('task user still has role assignments; preserving identity')
            admin.identity.delete_user(user.id, ignore_missing=False)
        record['user_deleted'] = True
        save(state)
    if not record.get('project_deleted'):
        project = admin.identity.get_project(record['project_id'])
        if project is not None:
            if project.name != name or project.description != description:
                raise RuntimeError('task project ownership changed; refusing cleanup')
            admin.identity.delete_project(project.id, ignore_missing=False)
        record['project_deleted'] = True
        save(state)


def cleanup_private(state):
    for path in (CREDS, PAYLOAD):
        if path.exists() or path.is_symlink():
            protected_file(path)
            path.unlink()
    if PRIVATE.exists():
        info = PRIVATE.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise RuntimeError('unsafe acceptance private directory; preserving it')
        PRIVATE.rmdir()


def run(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', action='store_true', help='resume the exact retained task-owned acceptance run')
    options = parser.parse_args(args)
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the Ubuntu terminal.', file=sys.stderr)
        return 1
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    state = None
    stage = 'host_preflight'
    try:
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M)
                or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('scoped to the existing Ubuntu 22.04 WSL POC')
        info = DATA.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError('protected POC acceptance directory required')
        descriptor = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            progress('BEGIN; setup, core, Cinder, and existing data preserved')
            stage = 'acceptance_journal'
            state = load_or_create_state(options.resume)
            if state.get('phase') == 'complete':
                raise RuntimeError('acceptance already completed; refusing replay')
            state['marker'] = marker(state)
            save(state)
            if state.get('phase') == 'cleanup_private':
                cleanup_private(state)
                state['phase'] = 'complete'
                state['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                save(state)
                progress('PASS; private acceptance artifacts cleaned; report=' + str(STATE))
                return 0
            progress('acceptance_journal: PASS')
            stage = 'credential_source'
            base = common.read_secrets(common.SECRET_FILE)
            with common.connection(base) as admin:
                stage = 'setup_report'
                setup_state = protected_json(SETUP_STATE)
                if (setup_state.get('phase') != 'service_ready_pending_object_acceptance'
                        or not {'packages', 'filesystem', 'mount_and_xattrs',
                                'keystone_identity_and_catalog', 'configuration_and_rings',
                                'services_and_authenticated_api', 'core_preservation'}
                           .issubset(set(setup_state.get('steps', [])))):
                    raise RuntimeError('Swift service setup journal is not complete')
                progress('setup_report: PASS')
                stage = 'configuration_integrity'
                swift_setup.verify_owned_files(setup_state)
                progress('configuration_integrity: PASS')
                stage = 'core_preservation'
                swift_setup.check_core(admin, setup_state)
                progress('core_preservation: PASS')
                stage = 'swift_service_health'
                setup_secrets = swift_setup.setup_secrets(setup_state)
                swift_setup.service_health(dict(base, **setup_secrets))
                progress('service, storage, and core preservation recheck: PASS')

                role = admin.identity.find_role('member', ignore_missing=False)
                if state.get('phase') == 'tenant_cleanup':
                    stage = 'tenant_cleanup'
                    for label in ('a', 'b'):
                        delete_tenant(admin, state, label, role.id)
                    state['phase'] = 'cleanup_private'
                    save(state)
                    cleanup_private(state)
                    state['phase'] = 'complete'
                    state['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    save(state)
                    progress('PASS; task-owned tenant identities and private artifacts cleaned; report=' + str(STATE))
                    return 0

                passwords_value = passwords(state, options.resume)
                payload = ensure_payload(state, options.resume)
                stage = 'tenant_identities'
                records, passwords_value = ensure_owned_records(admin, state)
                clouds = {label: tenant_connection(records[label], passwords_value[label])
                          for label in ('a', 'b')}
                try:
                    for label in ('a', 'b'):
                        check_member_scope(clouds[label], records[label])
                    progress('two member-scoped Keystone tenants: PASS')
                    if state.get('phase') == 'cleanup':
                        owner_endpoint = endpoint_for(clouds['a'], records['a']['project_id'])
                        stage = 'cleanup'
                        cleanup_object(clouds['a'], state, owner_endpoint)
                        state['phase'] = 'tenant_cleanup'
                        save(state)
                        for label in ('a', 'b'):
                            delete_tenant(admin, state, label, role.id)
                        state['phase'] = 'cleanup_private'
                        save(state)
                        cleanup_private(state)
                        state['phase'] = 'complete'
                        state['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        save(state)
                        progress('PASS; task-owned objects and tenant identities cleaned; report=' + str(STATE))
                        return 0

                    stage = 'object_round_trip'
                    owner_endpoint = endpoint_for(clouds['a'], records['a']['project_id'])
                    object_record = state['object']
                    object_record.setdefault('container', 'poc-' + state['run_id'][:24])
                    object_record.setdefault('name', 'payload-' + state['run_id'])
                    save(state)
                    if object_record.get('cross_probe_pending') in {'put', 'delete'}:
                        restore_owned_object(clouds['a'], state, owner_endpoint, payload)
                    ensure_container(clouds['a'], state, owner_endpoint)
                    object_url = put_object(clouds['a'], state, owner_endpoint, payload)
                    own_result = get_and_verify(clouds['a'], state, object_url, payload)
                    progress('authorized tenant object PUT/GET/SHA-256: PASS')

                    stage = 'access_denials'
                    anonymous = anonymous_denial(object_url)
                    cross_get = assert_denied(clouds['b'], 'GET', object_url)
                    altered = bytes((value ^ 0xff) for value in payload)
                    object_record['cross_probe_pending'] = 'put'
                    save(state)
                    cross_put = assert_denied(
                        clouds['b'], 'PUT', object_url, data=altered,
                        headers={'Content-Type': 'application/octet-stream'},
                        restore=lambda: restore_owned_object(clouds['a'], state, owner_endpoint, payload))
                    object_record['cross_probe_pending'] = None
                    save(state)
                    object_record['cross_probe_pending'] = 'delete'
                    save(state)
                    cross_delete = assert_denied(
                        clouds['b'], 'DELETE', object_url,
                        restore=lambda: restore_owned_object(clouds['a'], state, owner_endpoint, payload))
                    object_record['cross_probe_pending'] = None
                    save(state)
                    verified_after = get_and_verify(clouds['a'], state, object_url, payload)
                    if verified_after['sha256'] != own_result['sha256']:
                        raise RuntimeError('cross-project probes changed task object content')
                    state['results'] = {'tenant_object_round_trip': own_result,
                                        'anonymous_head_status': anonymous,
                                        'cross_project_get_status': cross_get,
                                        'cross_project_put_status': cross_put,
                                        'cross_project_delete_status': cross_delete,
                                        'owner_read_after_denials': verified_after,
                                        'authorization': 'member-scoped tokens verified'}
                    state['phase'] = 'cleanup'
                    save(state)
                    progress('anonymous denial and cross-project GET/PUT/DELETE denial: PASS')

                    stage = 'cleanup'
                    cleanup_object(clouds['a'], state, owner_endpoint)
                    state['phase'] = 'tenant_cleanup'
                    save(state)
                    for label in ('a', 'b'):
                        delete_tenant(admin, state, label, role.id)
                    state['phase'] = 'cleanup_private'
                    save(state)
                    cleanup_private(state)
                    state['phase'] = 'complete'
                    state['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    save(state)
                    progress('PASS; object integrity, tenant isolation, and cleanup verified; report=' + str(STATE))
                finally:
                    for cloud in clouds.values():
                        cloud.close()
        return 0
    except Exception as error:
        frames = traceback.extract_tb(error.__traceback__)
        call_trace = [{'module': Path(frame.filename).name, 'function': frame.name,
                       'line': frame.lineno} for frame in frames[-6:]]
        if state is not None:
            state['failure'] = {'stage': stage, 'error_type': type(error).__name__,
                                'call_trace': call_trace}
            save(state)
        print('STOP: Swift object acceptance incomplete: ' + json.dumps(
            {'stage': stage, 'error_type': type(error).__name__, 'call_trace': call_trace}, sort_keys=True)
              + '; task-owned resources and protected evidence are preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(run())
