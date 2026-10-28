#!/usr/bin/python3
"""Real two-tenant image, object and network role journeys on new marked data."""
from contextlib import ExitStack
import argparse
import hashlib
import json
import logging
import re
import time
from uuid import uuid4

import browser_poc as browser
import browser_resources as controls
import continue_poc as task
import manage_cinder_loop as safety

NAME = 't50-resource-roles'


def call(service, method, path, allowed=(200,), **kwargs):
    response = getattr(service, method)(path, raise_exc=False, **kwargs)
    safety.require(response.status_code in allowed, method + '_' + path.split('?')[0] + '_acceptance_http_' + str(response.status_code))
    return response


def save_resource(state, kind, value):
    state['resources'][kind] = value
    task.save(NAME, state)


def image_journey(a, b, source, state):
    name, marker = state['prefix'] + '-image', state['marker']
    content = call(a.image, 'get', '/images/' + source['catalog']['image_id'] + '/file').content
    safety.require(0 < len(content) < 32 * 1024**2, 'source_image_copy_size_unexpected')
    digest = hashlib.sha256(content).hexdigest()
    task.intent(NAME, state, 'image-create', 'create_only_new_private_image_copy')
    image = call(a.image, 'post', '/images', (201,), json={'name': name, 'visibility': 'private',
        'disk_format': 'qcow2', 'container_format': 'bare', 'poc_marker': marker}).json()
    save_resource(state, 'image', {'id': image['id'], 'name': name})
    path = '/images/' + image['id']
    call(a.image, 'put', path + '/file', (204,), data=content, headers={'Content-Type': 'application/octet-stream'})
    safety.require(hashlib.sha256(call(a.image, 'get', path + '/file').content).hexdigest() == digest,
                   'private_image_copy_digest_mismatch')
    for method in ('get', 'delete'):
        call(b.image, method, path, (403, 404))
    patch_headers = {'Content-Type': 'application/openstack-images-v2.1-json-patch'}
    call(b.image, 'patch', path, (403, 404), headers=patch_headers,
         json=[{'op': 'replace', 'path': '/name', 'value': 'forbidden'}])
    call(a.image, 'patch', path, headers=patch_headers,
         json=[{'op': 'replace', 'path': '/visibility', 'value': 'shared'}])
    member = source['projects']['b']['id']
    call(a.image, 'post', path + '/members', (200,), json={'member': member})
    call(b.image, 'put', path + '/members/' + member, json={'status': 'accepted'})
    safety.require(hashlib.sha256(call(b.image, 'get', path + '/file').content).hexdigest() == digest,
                   'accepted_shared_image_digest_mismatch')
    call(b.image, 'delete', path, (403, 404))
    call(b.image, 'patch', path, (403, 404), headers=patch_headers,
         json=[{'op': 'replace', 'path': '/name', 'value': 'forbidden'}])
    call(a.image, 'patch', path, headers=patch_headers,
         json=[{'op': 'add', 'path': '/poc_updated', 'value': 'verified'}])
    state['checks']['image'] = {'private_foreign_read_update_delete_denied': True,
        'shared_member_accept_download_sha256': digest, 'shared_foreign_update_delete_denied': True,
        'owner_update_verified': call(a.image, 'get', path).json()['poc_updated'] == 'verified'}
    task.save(NAME, state)


def network_journey(a, b, source, state):
    marker, name = state['marker'], state['prefix'] + '-net'
    task.intent(NAME, state, 'network-create', 'create_new_isolated_tenant_b_network')
    network = call(b.network, 'post', '/networks', (201,), json={'network': {
        'name': name, 'description': marker, 'admin_state_up': True}}).json()['network']
    save_resource(state, 'network', {'id': network['id'], 'name': name})
    subnet = call(b.network, 'post', '/subnets', (201,), json={'subnet': {
        'name': name + '-subnet', 'description': marker, 'network_id': network['id'],
        'ip_version': 4, 'cidr': '10.250.50.0/28', 'enable_dhcp': False}}).json()['subnet']
    save_resource(state, 'subnet', {'id': subnet['id']})
    group = call(b.network, 'post', '/security-groups', (201,), json={'security_group': {
        'name': state['prefix'] + '-sg', 'description': marker}}).json()['security_group']
    save_resource(state, 'security_group', {'id': group['id']})
    rule = call(b.network, 'post', '/security-group-rules', (201,), json={'security_group_rule': {
        'security_group_id': group['id'], 'direction': 'ingress', 'ethertype': 'IPv4', 'protocol': 'tcp',
        'port_range_min': 8080, 'port_range_max': 8080, 'remote_ip_prefix': '10.250.50.0/28'}}).json()['security_group_rule']
    save_resource(state, 'security_group_rule', {'id': rule['id']})
    port = call(b.network, 'post', '/ports', (201,), json={'port': {'name': name + '-port',
        'description': marker, 'network_id': network['id'], 'security_groups': [group['id']]}}).json()['port']
    save_resource(state, 'port', {'id': port['id']})
    for collection, kind in (('networks', 'network'), ('subnets', 'subnet'), ('ports', 'port'),
                             ('security-groups', 'security_group')):
        path = '/' + collection + '/' + state['resources'][kind]['id']
        singular = {'networks': 'network', 'subnets': 'subnet', 'ports': 'port', 'security-groups': 'security_group'}[collection]
        call(b.network, 'get', path)
        call(a.network, 'get', path, (403, 404))
        call(a.network, 'put', path, (403, 404), json={singular: {'name': 'forbidden'}})
        call(a.network, 'delete', path, (403, 404))
    call(a.network, 'post', '/ports', (403, 404), json={'port': {'network_id': network['id']}})
    path = '/ports/' + port['id']
    renamed = name + '-updated'
    call(b.network, 'put', path, json={'port': {'name': renamed}})
    safety.require(call(b.network, 'get', path).json()['port']['name'] == renamed, 'own_port_update_failed')
    state['checks']['network'] = {'private_network_subnet_port_security_group_create': True,
        'foreign_read_update_delete_and_port_creation_denied': True, 'own_port_update_verified': True}
    task.save(NAME, state)


def object_journey(a, b, source, state):
    for label, owner, other in (('a', a, b), ('b', b, a)):
        container = state['prefix'] + '-' + label + '-objects'
        project = source['projects'][label]['id']
        base = 'http://127.0.0.1:8080/v1/AUTH_' + project + '/' + container
        task.intent(NAME, state, 'container-' + label, 'create_only_new_private_container')
        call(owner.object_store, 'put', base, (201, 202), headers={'X-Container-Meta-Poc': state['marker']})
        save_resource(state, 'container_' + label, {'name': container, 'url': base})
        data = bytes(range(256)) * 16
        obj = base + '/owned-object'
        call(owner.object_store, 'put', obj, (201,), data=data, headers={'Content-Type': 'application/octet-stream'})
        for method, path, extra in (('get', base + '?format=json', {}), ('get', obj, {}),
            ('put', obj, {'data': b'forbidden'}), ('post', obj, {'headers': {'X-Object-Meta-Test': 'forbidden'}}),
            ('delete', obj, {})):
            call(other.object_store, method, path, (403, 404), **extra)
        safety.require(call(owner.object_store, 'get', obj).content == data, 'foreign_object_attempt_changed_data')
        replacement = data * 2
        call(owner.object_store, 'put', obj, (201,), data=replacement)
        safety.require(call(owner.object_store, 'get', obj).content == replacement, 'object_overwrite_mismatch')
        call(owner.object_store, 'post', obj, (202,), headers={'X-Object-Meta-Poc-Test': 'updated'})
        head = call(owner.object_store, 'head', obj)
        safety.require(head.headers.get('X-Object-Meta-Poc-Test') == 'updated', 'object_metadata_update_mismatch')
        call(owner.object_store, 'delete', obj, (204,))
        call(owner.object_store, 'get', obj, (404,))
        state['checks']['objects_' + label] = {'private_crud_overwrite_metadata': True,
            'foreign_list_read_write_metadata_delete_denied': True,
            'replacement_sha256': hashlib.sha256(replacement).hexdigest()}
        task.save(NAME, state)


def cleanup(a, b, source, state):
    checks = {}
    image = state['resources'].get('image')
    if image:
        path = '/images/' + image['id']
        result = call(a.image, 'get', path, (200, 404))
        if result.status_code == 200:
            safety.require(result.json().get('poc_marker') == state['marker'], 'image_cleanup_marker_changed')
            call(a.image, 'delete', path, (204,))
        checks['image_absent'] = call(a.image, 'get', path, (404,)).status_code == 404
    for label, owner in (('a', a), ('b', b)):
        container = state['resources'].get('container_' + label)
        if container:
            base = container['url']
            response = call(owner.object_store, 'head', base, (204, 404))
            if response.status_code != 404:
                safety.require(response.headers.get('X-Container-Meta-Poc') == state['marker'], 'container_cleanup_marker_changed')
                call(owner.object_store, 'delete', base + '/owned-object', (204, 404))
                call(owner.object_store, 'delete', base, (204,))
            checks['container_' + label + '_absent'] = call(owner.object_store, 'head', base, (404,)).status_code == 404
    for collection, kind in (('ports', 'port'), ('security-group-rules', 'security_group_rule'),
                             ('security-groups', 'security_group'), ('subnets', 'subnet'), ('networks', 'network')):
        item = state['resources'].get(kind)
        if item:
            path = '/' + collection + '/' + item['id']
            raw = call(b.network, 'get', path, (200, 404))
            if raw.status_code != 404:
                singular = kind
                value = raw.json()[singular]
                if kind != 'security_group_rule':
                    safety.require(value['project_id'] == source['projects']['b']['id']
                        and value.get('description') == state['marker'], 'network_cleanup_marker_changed')
                else:
                    safety.require(value['security_group_id'] == state['resources']['security_group']['id'],
                                   'rule_cleanup_group_changed')
                call(b.network, 'delete', path, (204,))
            checks[kind + '_absent'] = call(b.network, 'get', path, (404,)).status_code == 404
    state['checks']['cleanup'] = checks
    task.save(NAME, state)


def main():
    global NAME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='')
    args = parser.parse_args()
    safety.require(not args.run_id or re.fullmatch('[a-z0-9-]{1,32}', args.run_id), 'invalid_role_run_id')
    NAME += '-' + args.run_id if args.run_id else ''
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    with task.locked(), ExitStack() as stack:
        state = task.read(NAME, source)
        safety.require(not state['intents'], 'existing_role_journey_preserved_no_automatic_replay')
        state['prefix'] = 'poc-t50-' + uuid4().hex[:10]
        task.save(NAME, state)
        a = stack.enter_context(controls.connection(source, private, 'a'))
        b = stack.enter_context(controls.connection(source, private, 'b'))
        try:
            image_journey(a, b, source, state)
            network_journey(a, b, source, state)
            object_journey(a, b, source, state)
            state['phase'] = 'two_tenant_resource_role_journeys_verified'
        finally:
            cleanup(a, b, source, state)
        task.save(NAME, state)
        print(json.dumps({'phase': state['phase'], 'checks': state['checks']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'resource_role_acceptance_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
