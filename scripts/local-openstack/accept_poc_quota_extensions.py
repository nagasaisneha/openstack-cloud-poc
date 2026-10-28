#!/usr/bin/python3
"""Reach owned Neutron and Swift container limits; never changes existing quotas."""
from contextlib import ExitStack
import json
import logging
from pathlib import Path
from uuid import uuid4

import browser_resources as controls
import configure_nova as common
import continue_poc as task
import manage_cinder_loop as safety

NAME = 't50-quota-extensions'


def quota_denial(response):
    return response.status_code in (403, 409, 413) and any(word in response.text.lower() for word in ('quota', 'limit'))


def main():
    logging.disable(logging.CRITICAL)
    source, private = task.initialize()
    with task.locked(), ExitStack() as stack:
        a, b = [stack.enter_context(controls.connection(source, private, label)) for label in ('a', 'b')]
        operator = stack.enter_context(common.connection(common.read_secrets(common.SECRET_FILE)))
        state = task.read(NAME, source)
        safety.require(not state['intents'], 'quota_extension_preserved_no_replay')
        project = source['projects']['b']['id']
        quota = operator.network.get('/quotas/' + project, raise_exc=True).json()['quota']
        before_networks = {row['id'] for row in b.network.get('/networks?project_id=' + project, raise_exc=True).json()['networks']}
        before_routers = {row['id'] for row in b.network.get('/routers?project_id=' + project, raise_exc=True).json()['routers']}
        safety.require(len(before_networks) == 1 and quota['network'] == 2 and len(before_routers) == quota['router'] == 1,
                       'expected_bounded_project_quota_or_usage_changed')
        prefix = 'poc-t50-quota-' + uuid4().hex[:10]
        container = 'http://127.0.0.1:8080/v1/AUTH_' + project + '/' + prefix
        task.intent(NAME, state, 'new-quota-resources', 'create_only_marked_network_and_container_for_limit_checks')
        network_names = {prefix + '-net', prefix + '-over'}
        router_name = prefix + '-router-over'
        container_created = False
        safety.require(b.object_store.head(container, raise_exc=False).status_code == 404, 'new_quota_container_name_exists')
        try:
            b.network.post('/networks', json={'network': {'name': prefix + '-net', 'description': state['marker']}}, raise_exc=True)
            over = b.network.post('/networks', json={'network': {'name': prefix + '-over', 'description': state['marker']}}, raise_exc=False)
            safety.require(quota_denial(over), 'neutron_network_quota_not_enforced')
            over_router = b.network.post('/routers', json={'router': {'name': router_name, 'description': state['marker']}}, raise_exc=False)
            safety.require(quota_denial(over_router), 'neutron_router_quota_not_enforced')
            state['checks'].update(network_limit_two_denied_third=True, router_limit_one_denied_second=True)
            b.object_store.put(container, headers={'X-Container-Meta-Poc': state['marker']}, raise_exc=True)
            container_created = True
            b.object_store.put(container + '/first', data=b'x' * 64, raise_exc=True)
            b.object_store.post(container, headers={'X-Container-Meta-Quota-Bytes': '64', 'X-Container-Meta-Quota-Count': '2'}, raise_exc=True)
            over_bytes = b.object_store.put(container + '/first', data=b'y' * 65, raise_exc=False)
            safety.require(over_bytes.status_code == 413, 'swift_container_byte_limit_not_enforced')
            safety.require(b.object_store.get(container + '/first', raise_exc=True).content == b'x' * 64,
                           'rejected_overwrite_changed_object')
            b.object_store.post(container, headers={'X-Container-Meta-Quota-Bytes': '128', 'X-Container-Meta-Quota-Count': '1'}, raise_exc=True)
            over_count = b.object_store.put(container + '/second', data=b'z' * 64, raise_exc=False)
            safety.require(over_count.status_code == 413 and b.object_store.head(container + '/second', raise_exc=False).status_code == 404,
                           'swift_container_count_limit_or_no_orphan_failed')
            denied = a.object_store.post(container, headers={'X-Container-Meta-Quota-Count': '999'}, raise_exc=False)
            safety.require(denied.status_code in (403, 404), 'foreign_container_quota_edit_not_denied')
            metadata = b.object_store.head(container, raise_exc=True)
            safety.require(metadata.headers.get('X-Container-Meta-Quota-Bytes') == '128'
                and metadata.headers.get('X-Container-Meta-Quota-Count') == '1', 'container_limits_changed_after_denial')
            state['checks'].update(swift_container_byte_and_count_413=True, rejected_object_unchanged_and_no_orphan=True,
                                   foreign_quota_edit_denied=True, account_wide_quota_claimed=False)
        finally:
            container_state = b.object_store.head(container, raise_exc=False)
            safety.require(container_state.status_code in (204, 404), 'quota_container_cleanup_state_unknown')
            if container_created or container_state.status_code == 204:
                metadata = b.object_store.head(container, raise_exc=True)
                safety.require(metadata.headers.get('X-Container-Meta-Poc') == state['marker'], 'quota_container_cleanup_scope_changed')
                for name in ('first', 'second'):
                    if b.object_store.head(container + '/' + name, raise_exc=False).status_code == 200:
                        b.object_store.delete(container + '/' + name, raise_exc=True)
                b.object_store.delete(container, raise_exc=True)
            for collection, key, names in (('routers', 'router', {router_name}), ('networks', 'network', network_names)):
                rows = b.network.get('/' + collection + '?project_id=' + project, raise_exc=True).json()[collection]
                for row in rows:
                    if row['name'] in names and row['description'] == state['marker']:
                        b.network.delete('/' + collection + '/' + row['id'], raise_exc=True)
            after_networks = {row['id'] for row in b.network.get('/networks?project_id=' + project, raise_exc=True).json()['networks']}
            after_routers = {row['id'] for row in b.network.get('/routers?project_id=' + project, raise_exc=True).json()['routers']}
            safety.require(after_networks == before_networks and after_routers == before_routers,
                           'original_network_or_router_set_changed')
            safety.require(operator.network.get('/quotas/' + project, raise_exc=True).json()['quota'] == quota,
                           'existing_neutron_quotas_changed')
            state['checks'].update(existing_networks_routers_quotas_preserved=True,
                                   new_container_absent=b.object_store.head(container, raise_exc=False).status_code == 404)
            task.save(NAME, state)
        state['phase'] = 'network_router_and_swift_container_quota_enforcement_verified'
        task.save(NAME, state)
        summary = {'phase': state['phase'], 'checks': state['checks']}
        output = Path(__file__).resolve().parents[2] / 'docs/evidence/quota-extensions-20261029.json'
        output.write_text(json.dumps(summary, indent=2) + '\n')
        output.chmod(0o644)
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'quota_extension_acceptance_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
