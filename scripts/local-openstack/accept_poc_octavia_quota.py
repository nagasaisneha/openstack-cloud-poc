#!/usr/bin/python3
"""Exercise the isolated tenant B Octavia LB quota with task-owned API resources."""
import configparser
import json
import logging
import time
from uuid import uuid4

import browser_resources as controls
import configure_nova as common
import continue_poc as task
import manage_cinder_loop as safety

NAME = 'octavia-quota-acceptance'
COLLECTION = '/lbaas/loadbalancers'


def rows(cloud):
    return cloud.load_balancer.get(COLLECTION, raise_exc=True).json()['loadbalancers']


def find_exact(cloud, name, description):
    return [item for item in rows(cloud) if item['name'] == name and item['description'] == description]


def create(cloud, source, state, key, name, description):
    matches = find_exact(cloud, name, description)
    safety.require(len(matches) <= 1, 'tenant_quota_marker_not_unique')
    if matches:
        safety.require(key in state['intents'] and matches[0]['project_id'] == source['projects']['b']['id'],
                       'unrecorded_tenant_quota_resource')
        result = matches[0]
    else:
        safety.require(key not in state['intents'], 'uncertain_' + key + '_no_automatic_replay')
        task.intent(NAME, state, key, 'tenant_b_create_only_marked_quota_probe_loadbalancer')
        response = cloud.load_balancer.post(COLLECTION, json={'loadbalancer': {
            'name': name, 'description': description,
            'vip_subnet_id': source['resources']['b']['subnet']['id'], 'provider': 'ovn'}}, raise_exc=False)
        safety.require(response.status_code in (200, 201, 202), 'tenant_b_quota_probe_create_http_' + str(response.status_code))
        result = response.json()['loadbalancer']
        safety.require(result.get('project_id') in (None, source['projects']['b']['id']),
                       'tenant_quota_probe_owner_mismatch')
    state['resources'][key] = {'id': result['id'], 'name': name, 'description': description}
    task.save(NAME, state)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        item = cloud.load_balancer.get('/lbaas/loadbalancers/' + result['id'], raise_exc=True).json()['loadbalancer']
        safety.require(item['project_id'] == source['projects']['b']['id'] and item['name'] == name
                       and item['description'] == description, 'tenant_quota_probe_identity_changed')
        if item['provisioning_status'] == 'ACTIVE':
            return item
        safety.require(item['provisioning_status'] != 'ERROR', 'tenant_quota_probe_provider_error')
        time.sleep(2)
    raise safety.GuardError('tenant_quota_probe_creation_timeout')


def attempt_over_limit(cloud, source, state, name, description):
    matches = find_exact(cloud, name, description)
    safety.require(len(matches) <= 1, 'over_limit_marker_not_unique')
    if matches:
        safety.require('create-over-limit-loadbalancer' in state['intents']
                       and matches[0]['project_id'] == source['projects']['b']['id'],
                       'unrecorded_over_limit_loadbalancer')
        state['resources']['over_limit_probe'] = {'id': matches[0]['id'], 'name': name, 'description': description}
        state['checks']['quota_enforced'] = False
        task.save(NAME, state)
        return {'status': 202, 'quota_error': False, 'unexpected_resource_created': True}
    safety.require('create-over-limit-loadbalancer' not in state['intents'],
                   'uncertain_over_limit_attempt_no_automatic_replay')
    before = [item for item in rows(cloud) if item['project_id'] == source['projects']['b']['id']]
    safety.require(len(before) == 1 and before[0]['id'] == state['resources']['first_probe']['id'],
                   'tenant_b_quota_usage_changed_before_probe')
    task.intent(NAME, state, 'create-over-limit-loadbalancer', 'verify_default_load_balancer_quota_one')
    response = cloud.load_balancer.post(COLLECTION, json={'loadbalancer': {
        'name': name, 'description': description,
        'vip_subnet_id': source['resources']['b']['subnet']['id'], 'provider': 'ovn'}}, raise_exc=False)
    body_text = response.text.lower()
    quota_error = response.status_code in (403, 409, 413) and ('quota' in body_text or 'limit' in body_text)
    result = {'status': response.status_code, 'quota_error': quota_error, 'unexpected_resource_created': False}
    if response.status_code in (200, 201, 202):
        item = response.json()['loadbalancer']
        safety.require(item.get('project_id') in (None, source['projects']['b']['id']), 'over_limit_probe_owner_mismatch')
        state['resources']['over_limit_probe'] = {'id': item['id'], 'name': name, 'description': description}
        result['unexpected_resource_created'] = True
    state['checks']['quota_attempt'] = result
    state['checks']['quota_enforced'] = quota_error
    task.save(NAME, state)
    return result


def cleanup(cloud, source, state):
    resources = [state['resources'][key] for key in ('first_probe', 'over_limit_probe') if key in state['resources']]
    for item in resources:
        current = cloud.load_balancer.get('/lbaas/loadbalancers/' + item['id'], raise_exc=False)
        if current.status_code == 404:
            continue
        safety.require(current.status_code == 200, 'tenant_quota_probe_cleanup_read_failed')
        lb = current.json()['loadbalancer']
        safety.require(lb['project_id'] == source['projects']['b']['id'] and lb['name'] == item['name']
                       and lb['description'] == item['description'] and not lb.get('listeners'),
                       'tenant_quota_probe_cleanup_scope_mismatch')
    if resources and 'delete-quota-probes' not in state['intents']:
        task.intent(NAME, state, 'delete-quota-probes', 'delete_only_the_marked_tenant_b_quota_probe_loadbalancers')
    for item in resources:
        response = cloud.load_balancer.delete('/lbaas/loadbalancers/' + item['id'], raise_exc=False)
        safety.require(response.status_code in (202, 204, 404), 'tenant_quota_probe_cleanup_http_' + str(response.status_code))
    deadline = time.monotonic() + 90
    target_ids = {item['id'] for item in resources}
    while time.monotonic() < deadline:
        remaining = {item['id'] for item in rows(cloud)} & target_ids
        if not remaining:
            return
        time.sleep(2)
    raise safety.GuardError('tenant_quota_probe_cleanup_timeout')


def apply(source, private):
    state = task.read(NAME, source)
    if state.get('checks', {}).get('complete'):
        print(json.dumps({'phase': 'tenant_b_octavia_quota_previously_verified', **state['checks']}), flush=True)
        return
    if 'prefix' not in state:
        state['prefix'] = 'poc-t49-b-q-' + uuid4().hex[:10]
        task.save(NAME, state)
    parser = configparser.ConfigParser(interpolation=None)
    safety.require(parser.read('/etc/octavia/octavia.conf') == ['/etc/octavia/octavia.conf'],
                   'octavia_quota_configuration_unavailable')
    limit = parser.getint('quotas', 'default_load_balancer_quota')
    safety.require(limit == 1, 'unexpected_default_load_balancer_quota')
    with controls.connection(source, private, 'b') as cloud:
        safety.require(cloud.session.get_project_id() == source['projects']['b']['id'], 'tenant_b_token_scope_mismatch')
        existing = [item for item in rows(cloud) if item['project_id'] == source['projects']['b']['id']]
        allowed_id = state.get('resources', {}).get('first_probe', {}).get('id')
        safety.require(not existing or {item['id'] for item in existing} <= {allowed_id},
                       'preexisting_tenant_b_loadbalancer_preserved_no_mutation')
        try:
            first_name = state['prefix'] + '-first'
            first_description = state['marker'] + '/quota-probe/first'
            first = create(cloud, source, state, 'first_probe', first_name, first_description)
            over_name = state['prefix'] + '-over'
            over_description = state['marker'] + '/quota-probe/over-limit'
            if 'quota_attempt' not in state.get('checks', {}):
                attempt_over_limit(cloud, source, state, over_name, over_description)
        finally:
            if any(key in state['resources'] for key in ('first_probe', 'over_limit_probe')):
                cleanup(cloud, source, state)
    state['checks'].update(default_load_balancer_quota=limit, tenant_b_live_usage_before=0,
        one_load_balancer_accepted=first['provisioning_status'] == 'ACTIVE', all_probe_loadbalancers_deleted=True,
        quota_enforced_verified=state['checks'].get('quota_enforced') is True, complete=True)
    task.save(NAME, state)
    print(json.dumps({'phase': 'tenant_b_octavia_load_balancer_quota_acceptance', **state['checks']}), flush=True)


if __name__ == '__main__':
    try:
        logging.disable(logging.CRITICAL)
        source, private = task.initialize()
        with task.locked():
            apply(source, private)
    except Exception as error:
        print(json.dumps({'phase': 'tenant_b_octavia_quota_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__,
            'automatic_replay': False}), flush=True)
        raise SystemExit(2)
