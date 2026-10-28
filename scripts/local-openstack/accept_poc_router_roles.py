#!/usr/bin/python3
"""Tenant router/interface/gateway CRUD with exact temporary quota restoration."""
from contextlib import ExitStack
import json
import logging
from pathlib import Path
from uuid import uuid4

import browser_resources as controls
import configure_nova as common
import continue_poc as task
import manage_cinder_loop as safety

NAME = 't50-router-roles'


def call(cloud, method, path, allowed=(200,), **kwargs):
    response = getattr(cloud.network, method)(path, raise_exc=False, **kwargs)
    safety.require(response.status_code in allowed, 'router_test_' + method + '_http_' + str(response.status_code))
    return response


def main():
    logging.disable(logging.CRITICAL)
    source, private = task.initialize()
    with task.locked(), ExitStack() as stack:
        a, b = [stack.enter_context(controls.connection(source, private, label)) for label in ('a', 'b')]
        operator = stack.enter_context(common.connection(common.read_secrets(common.SECRET_FILE)))
        state = task.read(NAME, source)
        safety.require(not state['intents'], 'router_journey_preserved_no_replay')
        project = source['projects']['b']['id']
        quota_path = '/quotas/' + project
        before = {row['id'] for row in call(b, 'get', '/routers').json()['routers']}
        original = call(operator, 'get', quota_path).json()['quota']['router']
        changed = original >= 0 and original <= len(before)
        state.update(original_router_quota=original, original_router_ids=sorted(before), prefix='poc-t50-router-' + uuid4().hex[:10])
        task.save(NAME, state)
        if changed:
            task.intent(NAME, state, 'temporary-router-quota', 'temporarily_allow_one_additional_owned_test_router')
        try:
            if changed:
                call(operator, 'put', quota_path, json={'quota': {'router': len(before) + 1}})
            for kind, collection, attrs in (
                ('network', 'networks', {}),
                ('subnet', 'subnets', {'network_id': None, 'ip_version': 4, 'cidr': '10.250.51.0/28', 'enable_dhcp': False}),
                ('router', 'routers', {'admin_state_up': True})):
                if kind == 'subnet':
                    attrs['network_id'] = state['resources']['network']['id']
                attrs.update(name=state['prefix'] + '-' + kind, description=state['marker'])
                task.intent(NAME, state, 'create-' + kind, 'create_only_new_router_test_' + kind)
                item = call(b, 'post', '/' + collection, (201,), json={kind: attrs}).json()[kind]
                state['resources'][kind] = {'id': item['id']}
                task.save(NAME, state)
            identifier = state['resources']['router']['id']
            path = '/routers/' + identifier
            owned = call(b, 'get', path).json()['router']
            safety.require(owned['project_id'] == project and owned['description'] == state['marker'], 'new_router_scope_mismatch')
            for method, kwargs in (('get', {}), ('put', {'json': {'router': {'name': 'forbidden'}}}), ('delete', {})):
                call(a, method, path, (403, 404), **kwargs)
            subnet = state['resources']['subnet']['id']
            call(a, 'put', path + '/add_router_interface', (403, 404), json={'subnet_id': subnet})
            interface = call(b, 'put', path + '/add_router_interface', json={'subnet_id': subnet}).json()
            state['resources']['interface'] = {'id': interface['port_id']}
            task.save(NAME, state)
            ingress = task.read('connectivity', source)
            external = ingress['resources']['external_network']['id']
            call(b, 'put', path, json={'router': {'name': state['prefix'] + '-updated',
                 'external_gateway_info': {'network_id': external}}})
            updated = call(b, 'get', path).json()['router']
            safety.require(updated['name'] == state['prefix'] + '-updated'
                and updated['external_gateway_info']['network_id'] == external, 'router_update_or_gateway_not_applied')
            state['checks'].update(owner_router_interface_gateway_create_update=True,
                foreign_router_read_update_delete_interface_denied=True)
            task.save(NAME, state)
        finally:
            resources = state['resources']
            if 'router' in resources:
                path = '/routers/' + resources['router']['id']
                response = call(b, 'get', path, (200, 404))
                if response.status_code == 200:
                    safety.require(response.json()['router']['description'] == state['marker'], 'router_cleanup_marker_changed')
                    if 'interface' in resources:
                        call(b, 'put', path + '/remove_router_interface', (200, 404),
                             json={'subnet_id': resources['subnet']['id']})
                    call(b, 'put', path, json={'router': {'external_gateway_info': {}}})
                    call(b, 'delete', path, (204,))
                safety.require(call(b, 'get', path, (404,)).status_code == 404, 'router_cleanup_not_confirmed')
            for kind, collection in (('subnet', 'subnets'), ('network', 'networks')):
                if kind in resources:
                    path = '/' + collection + '/' + resources[kind]['id']
                    response = call(b, 'get', path, (200, 404))
                    if response.status_code == 200:
                        safety.require(response.json()[kind]['description'] == state['marker'], 'router_network_cleanup_marker_changed')
                        call(b, 'delete', path, (204,))
                    call(b, 'get', path, (404,))
            after = {row['id'] for row in call(b, 'get', '/routers').json()['routers']}
            safety.require(after == before, 'original_router_set_changed')
            if changed:
                call(operator, 'put', quota_path, json={'quota': {'router': original}})
            safety.require(call(operator, 'get', quota_path).json()['quota']['router'] == original,
                           'router_quota_not_restored')
            state['checks'].update(new_resources_absent=True, original_router_ids_preserved=True, router_quota_restored=original)
            task.save(NAME, state)
        state['phase'] = 'router_role_gateway_and_cleanup_verified'
        task.save(NAME, state)
        summary = {'phase': state['phase'], 'checks': state['checks']}
        path = Path(__file__).resolve().parents[2] / 'docs/evidence/router-roles-20261028.json'
        path.write_text(json.dumps(summary, indent=2) + '\n')
        path.chmod(0o644)
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'router_role_acceptance_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
