#!/usr/bin/python3
"""Prepare private browser-test access, then configure only marked POC tenants.

Projects and tenant users are created through Horizon by browser_poc.py.
--configure adds explicit billing roles, a separate reader/collector identity,
bounded quotas and the collector's protected configuration. No existing user's
password is reset; no project, user, resource, backup, or data is deleted.
"""
import argparse
import ipaddress
import json
import logging
import os
from pathlib import Path
import pwd
import secrets
import tempfile
import time
from uuid import uuid4

import configure_nova as common
import launch_test_vm as launch
import manage_cinder_loop as safety
import validate_guest as guest

DIRECTORY = launch.DATA_DIR / 'browser-poc-20261027'
STATE = DIRECTORY / 'state.json'
PRIVATE = DIRECTORY / 'credentials.json'
RUNTIME = Path('/var/lib/openstack-poc-billing/collector.json')
PROJECTS = {label: 'poc-browser-20261027-' + label for label in ('a', 'b')}
USERS = {label: 'poc-browser-' + label for label in ('a', 'b')}
COMPUTE_QUOTA = {'instances': 2, 'cores': 2, 'ram': 768}
VOLUME_QUOTA = {'volumes': 2, 'gigabytes': 2, 'snapshots': 2}
NETWORK_QUOTA = {'network': 2, 'subnet': 2, 'port': 8, 'router': 1,
                 'security_group': 3, 'security_group_rule': 16, 'floatingip': 0}


def save(state):
    state['updated_at'] = safety.now()
    launch.atomic_state(STATE, state)


def load():
    for path in (STATE, PRIVATE):
        safety.protected(path, 0o600)
    state, private = json.loads(STATE.read_text()), json.loads(PRIVATE.read_text())
    safety.require(state.get('schema') == private.get('schema') == 1
                   and state.get('marker') == private.get('marker'), 'browser_access_scope_changed')
    return state, private


def owned(resource, expected_name, marker, expected_id=None):
    safety.require(resource is not None and resource.name == expected_name
                   and resource.description == marker and resource.domain_id == 'default'
                   and (expected_id is None or resource.id == expected_id), 'browser_resource_ownership_changed')
    return resource


def prepare():
    if STATE.exists() and PRIVATE.exists():
        state, ignored = load()
        print(json.dumps({'phase': 'private_access_exists', 'state': str(STATE),
                          'projects': PROJECTS, 'users': USERS}))
        return
    safety.require(not DIRECTORY.exists(), 'partial_browser_access_requires_inspection')
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        original, ignored = guest.load_run(cloud, require_ssh=False)
        for label in PROJECTS:
            safety.require(cloud.identity.find_project(PROJECTS[label], domain_id='default') is None
                           and cloud.identity.find_user(USERS[label], domain_id='default') is None,
                           'browser_identity_name_already_exists')
    DIRECTORY.mkdir(mode=0o700)
    marker = 'browser-poc-20261027/' + str(uuid4())
    private = {'schema': 1, 'marker': marker,
               'tenants': {label: {'username': USERS[label], 'project_name': PROJECTS[label],
                                    'password': secrets.token_hex(32)} for label in PROJECTS},
               'collector_name': 'poc-billing-collector', 'collector_password': secrets.token_hex(32)}
    descriptor = os.open(PRIVATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(private, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    state = {'schema': 1, 'marker': marker, 'created_at': safety.now(), 'phase': 'private_access_prepared',
             'retained_vm_id': original['ids']['server'], 'retained_project_id': original['project_id'],
             'retained_image_id': original['image_id'], 'projects': {}, 'users': {}}
    save(state)
    print(json.dumps({'phase': state['phase'], 'state': str(STATE), 'credentials': str(PRIVATE),
                      'projects': PROJECTS, 'users': USERS, 'compute_quota': COMPUTE_QUOTA,
                      'volume_quota': VOLUME_QUOTA, 'network_quota': NETWORK_QUOTA}))


def write_collector_config(data):
    account = pwd.getpwnam('horizon')
    info = RUNTIME.parent.lstat()
    safety.require(info.st_uid == account.pw_uid and info.st_mode & 0o777 == 0o700,
                   'unsafe_collector_directory')
    if RUNTIME.exists():
        info = RUNTIME.lstat()
        safety.require(info.st_uid == account.pw_uid and info.st_mode & 0o777 == 0o600,
                       'unsafe_existing_collector_config')
        old = json.loads(RUNTIME.read_text())
        safety.require(old['user_id'] == data['user_id'] and old['password'] == data['password'],
                       'existing_collector_credentials_changed')
        data['project_ids'] = sorted(set(data['project_ids']) | set(old['project_ids']))
    descriptor, temporary = tempfile.mkstemp(prefix='.collector-', dir=RUNTIME.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, RUNTIME)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure():
    state, private = load()
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        original, ignored = guest.load_run(cloud, require_ssh=False)
        safety.require(original['ids']['server'] == state['retained_vm_id']
                       and original['project_id'] == state['retained_project_id'], 'retained_scope_changed')
        projects, users = {}, {}
        for label in PROJECTS:
            safety.require(label in state['projects'] and label in state['users'],
                           'browser_identity_creation_not_recorded')
            projects[label] = owned(cloud.identity.get_project(state['projects'][label]['id']),
                                    PROJECTS[label], state['marker'], state['projects'][label]['id'])
            users[label] = owned(cloud.identity.get_user(state['users'][label]['id']),
                                 USERS[label], state['marker'], state['users'][label]['id'])
        state['phase'] = 'scoped_billing_configuration_intent'
        save(state)
        roles = {}
        for name in ('member', 'reader', 'billing_viewer', 'billing_collector', 'cloud_admin'):
            roles[name] = cloud.identity.find_role(name) or cloud.identity.create_role(name=name)
        for label in PROJECTS:
            for name in ('member', 'billing_viewer'):
                cloud.identity.assign_project_role_to_user(projects[label], users[label], roles[name])
            state['phase'] = 'configure_quotas_' + label
            save(state)
            # QuotaSet URLs use a project_id URI attribute rather than the
            # resource id. Fetching the scoped resource preserves that URI.
            compute_quota = cloud.compute.get_quota_set(projects[label].id)
            volume_quota = cloud.block_storage.get_quota_set(projects[label].id)
            cloud.compute.update_quota_set(compute_quota, **COMPUTE_QUOTA)
            cloud.block_storage.update_quota_set(volume_quota, **VOLUME_QUOTA)
            cloud.network.update_quota(projects[label].id, **NETWORK_QUOTA)
        collector = cloud.identity.find_user(private['collector_name'], domain_id='default')
        if collector is None:
            state['collector_create_intent'] = True
            save(state)
            collector = cloud.identity.create_user(name=private['collector_name'], domain_id='default',
                password=private['collector_password'], description=state['marker'], is_enabled=True)
            state['collector_id'] = collector.id
            save(state)
        else:
            owned(collector, private['collector_name'], state['marker'], state.get('collector_id'))
            safety.require(state.get('collector_create_intent') is True, 'collector_not_owned_by_this_run')
        # Validate the retained password without resetting any existing user.
        from keystoneauth1 import session
        from keystoneauth1.identity import v3
        check = session.Session(auth=v3.Password(auth_url=common.AUTH_URL, user_id=collector.id,
                                                 password=private['collector_password']), timeout=10)
        safety.require(check.get_user_id() == collector.id, 'collector_identity_not_verified')
        project_ids = sorted({project.id for project in projects.values()} | {state['retained_project_id']})
        for project_id in project_ids:
            for name in ('reader', 'billing_collector'):
                cloud.identity.assign_project_role_to_user(project_id, collector, roles[name])
        admin_id = cloud.session.get_user_id()
        cloud.identity.assign_system_role_to_user(admin_id, roles['cloud_admin'], 'all')
        safety.require(cloud.identity.validate_user_has_system_role(admin_id, roles['cloud_admin'], 'all'),
                       'explicit_system_billing_admin_not_verified')
        write_collector_config({'schema': 1, 'auth_url': 'http://127.0.0.1:5000/v3/',
                                'user_id': collector.id, 'password': private['collector_password'],
                                'project_ids': project_ids})
        state.update(phase='scoped_billing_roles_and_quotas_configured', collector_id=collector.id,
                     collector_project_ids=project_ids, role_ids={name: role.id for name, role in roles.items()},
                     quotas={'compute': COMPUTE_QUOTA, 'volume': VOLUME_QUOTA, 'network': NETWORK_QUOTA})
        save(state)
    print(json.dumps({'phase': state['phase'], 'collector_id': collector.id,
                      'collector_project_ids': project_ids, 'state': str(STATE)}))


def catalog():
    state, private = load()
    safety.require(state.get('collector_id'), 'tenant_roles_not_configured')
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        source = cloud.image.get('/images/' + state['retained_image_id'], raise_exc=True).json()
        safety.require(source.get('status') == 'active' and source.get('owner') == state['retained_project_id']
                       and source.get('os_hash_value'), 'retained_source_image_changed')
        # Publish only the pinned public CirrOS artifact, never arbitrary
        # contents from an existing project's private image.
        from validate_application_create import source_image, SOURCE_SHA256
        safety.require(state['retained_image_id'] == launch.IMAGE_ID, 'unexpected_sample_image_source')
        known_source = source_image(cloud)
        if not state.get('public_image_id'):
            state['public_image_id'] = str(uuid4())
            state['public_image_create_intent'] = True
            save(state)
            cloud.image.post('/images', json={
                'id': state['public_image_id'], 'name': 'POC Browser CirrOS',
                'disk_format': source['disk_format'], 'container_format': source['container_format'],
                'visibility': 'public', 'poc_owner': state['marker'],
            }, raise_exc=True)
            state['public_image_registered'] = True
            save(state)
        image_path = '/images/' + state['public_image_id']
        image = cloud.image.get(image_path, raise_exc=True).json()
        safety.require(image.get('poc_owner') == state['marker'] and image.get('visibility') == 'public'
                       and image.get('owner') == state['retained_project_id'], 'public_sample_image_scope_changed')
        if image['status'] == 'queued':
            safety.require(state.get('public_image_registered') is True
                           and not state.get('public_image_upload_intent'), 'image_upload_uncertain_no_replay')
            state['public_image_upload_intent'] = True
            save(state)
            with known_source.open('rb') as data:
                cloud.image.put(image_path + '/file', data=data,
                                headers={'Content-Type': 'application/octet-stream'}, raise_exc=True)
        deadline = time.monotonic() + 90
        while True:
            image = cloud.image.get(image_path, raise_exc=True).json()
            if image['status'] == 'active':
                break
            safety.require(time.monotonic() < deadline and image['status'] in ('queued', 'saving'),
                           'public_image_upload_not_verified')
            time.sleep(1)
        safety.require(image.get('os_hash_algo') == source.get('os_hash_algo')
                       and image.get('os_hash_value') == source.get('os_hash_value'), 'public_image_content_hash_mismatch')
        if not state.get('browser_flavor_id'):
            state['browser_flavor_id'] = str(uuid4())
            state['browser_flavor_create_intent'] = True
            save(state)
            cloud.compute.post('/flavors', json={'flavor': {
                'id': state['browser_flavor_id'], 'name': 'poc.browser.tiny', 'ram': 256,
                'vcpus': 1, 'disk': 1, 'os-flavor-access:is_public': True, 'description': state['marker'],
            }}, microversion='2.55', raise_exc=True)
        flavor = cloud.compute.get('/flavors/' + state['browser_flavor_id'], microversion='2.55', raise_exc=True).json()['flavor']
        safety.require(flavor.get('description') == state['marker'] and flavor.get('ram') == 256
                       and flavor.get('vcpus') == 1 and flavor.get('disk') == 1, 'browser_flavor_changed')
        occupied = [ipaddress.ip_network(subnet.cidr) for subnet in cloud.network.subnets()]
        cidrs = {'a': '10.77.10.0/24', 'b': '10.77.20.0/24'}
        if 'network_cidrs' not in state:
            safety.require(not any(ipaddress.ip_network(value).overlaps(other)
                                   for value in cidrs.values() for other in occupied), 'browser_network_cidr_in_use')
            state['network_cidrs'] = cidrs
        for label in PROJECTS:
            key = DIRECTORY / ('id_ed25519_' + label)
            if not key.exists():
                safety.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)])
            safety.protected(key, 0o600)
        state.update(phase='browser_catalog_prepared', public_image_hash_verified=True,
                     public_sample_source_sha256=SOURCE_SHA256,
                     catalog={'image_id': state['public_image_id'], 'flavor_id': state['browser_flavor_id'],
                              'flavor_name': 'poc.browser.tiny', 'image_name': 'POC Browser CirrOS',
                              'ram_mib': 256, 'vcpus': 1, 'disk_gib': 1})
        save(state)
    print(json.dumps({'phase': state['phase'], 'catalog': state['catalog'],
                      'network_cidrs': state['network_cidrs'], 'original_image_preserved': True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--configure', action='store_true')
    mode.add_argument('--catalog', action='store_true')
    args = parser.parse_args()
    safety.host_guard()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    with safety.locked(safety.MANAGEMENT_LOCK):
        if args.prepare:
            prepare()
        elif args.configure:
            configure()
        else:
            catalog()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print('STOP: browser tenant setup: ' + reason + '; preserve existing identities and credentials')
        raise SystemExit(2)
