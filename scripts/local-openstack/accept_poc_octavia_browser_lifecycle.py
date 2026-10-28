#!/usr/bin/python3
"""Native Horizon update/delete acceptance on a fresh tenant B LB graph."""
import argparse
import json
import logging
from pathlib import Path
import time
from uuid import uuid4

import browser_poc as browser
import browser_resources as controls
import configure_nova as common
import continue_poc as task
import manage_cinder_loop as safety
from accept_poc_operations_browser import login_dom

NAME = 't50-octavia-browser-lifecycle'


def raw(cloud, path):
    return cloud.load_balancer.get('/lbaas/' + path, raise_exc=True).json()


def settle(cloud, identifier):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        lb = raw(cloud, 'loadbalancers/' + identifier)['loadbalancer']
        if lb['provisioning_status'] == 'ACTIVE':
            return lb
        safety.require(lb['provisioning_status'] != 'ERROR', 'test_lb_provisioning_error')
        time.sleep(2)
    raise safety.GuardError('test_lb_provisioning_timeout')


def owned(cloud, source, state):
    lb = raw(cloud, 'loadbalancers/' + state['resources']['loadbalancer']['id'])['loadbalancer']
    safety.require(lb['project_id'] == source['projects']['b']['id'] and lb['description'] == state['marker']
                   and lb['name'] in (state['prefix'], state['prefix'] + '-updated'), 'browser_lb_owner_changed')
    return lb


def create(cloud, source, state):
    safety.require(not state['intents'] and not state['resources'], 'existing_lb_lifecycle_preserved_no_replay')
    safety.require(not raw(cloud, 'loadbalancers')['loadbalancers'], 'existing_tenant_b_lbs_preserved')
    state['prefix'] = 'poc-t50-b-lb-' + uuid4().hex[:10]
    vm, net, port = task.validate_tenant(cloud, source, 'b')
    subnet = source['resources']['b']['subnet']['id']
    address = next(value['ip_address'] for value in port.fixed_ips if value['subnet_id'] == subnet)
    pool = {'name': state['prefix'] + '-pool', 'description': state['marker'], 'protocol': 'TCP',
        'lb_algorithm': 'SOURCE_IP_PORT', 'members': [{'address': address, 'protocol_port': 8080, 'subnet_id': subnet}],
        'healthmonitor': {'type': 'TCP', 'delay': 2, 'timeout': 1, 'max_retries': 2, 'max_retries_down': 2}}
    body = {'loadbalancer': {'name': state['prefix'], 'description': state['marker'], 'provider': 'ovn',
        'vip_subnet_id': subnet, 'listeners': [{'name': state['prefix'] + '-listener', 'description': state['marker'],
            'protocol': 'TCP', 'protocol_port': 8080, 'default_pool': pool}]}}
    task.intent(NAME, state, 'create-test-graph', 'create_only_new_tenant_b_graph_for_browser_cleanup')
    response = cloud.load_balancer.post('/lbaas/loadbalancers', json=body, raise_exc=True)
    lb = response.json()['loadbalancer']
    state['resources']['loadbalancer'] = {'id': lb['id']}
    task.save(NAME, state)
    lb = settle(cloud, lb['id'])
    owned(cloud, source, state)
    listener = raw(cloud, 'listeners?loadbalancer_id=' + lb['id'])['listeners']
    safety.require(len(listener) == 1, 'test_listener_relationship_mismatch')
    pool = raw(cloud, 'pools/' + listener[0]['default_pool_id'])['pool']
    members = raw(cloud, 'pools/' + pool['id'] + '/members')['members']
    state['resources'].update(listener={'id': listener[0]['id']}, pool={'id': pool['id']},
        monitor={'id': pool['healthmonitor_id']}, members=[{'id': item['id']} for item in members],
        vip_port={'id': lb['vip_port_id']})
    task.save(NAME, state)
    print(json.dumps({'phase': 'new_tenant_b_graph_ready_for_browser', 'members': len(members)}), flush=True)


def inspect(page, cloud, source, state):
    owned(cloud, source, state)
    page.goto(browser.URL + '/horizon/project/load_balancer/', wait_until='domcontentloaded')
    row = page.locator('tr').filter(has_text=state['prefix'])
    print(json.dumps({'phase': 'lb_browser_row_inspection', 'rows': row.count(),
        'actions': row.locator('button,a,input').evaluate_all(
            'xs=>xs.map(x=>({tag:x.tagName,text:x.innerText.trim(),id:x.id,name:x.name,href:x.getAttribute("href")}))')}), flush=True)


def inspect_forms(page, cloud, source, state):
    owned(cloud, source, state)
    page.goto(browser.URL + '/horizon/project/load_balancer/', wait_until='domcontentloaded')
    page.get_by_role('button', name='Edit Load Balancer', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.wait_for()
    print(json.dumps({'phase': 'native_lb_edit_form', 'fields': browser.fields(dialog),
        'buttons': dialog.locator('button,input[type=submit]').evaluate_all('xs=>xs.map(x=>({text:x.innerText.trim(),type:x.type}))')}), flush=True)
    print(json.dumps({'phase': 'native_lb_edit_visibility', 'inputs': dialog.locator('input,textarea').evaluate_all(
        'xs=>xs.map(x=>({name:x.name,disabled:x.disabled,visible:x.offsetParent!==null,html:x.outerHTML.slice(0,500)}))')}), flush=True)
    page.screenshot(path='/tmp/poc-t50-lb-edit.png')
    __import__('os').chmod('/tmp/poc-t50-lb-edit.png', 0o644)


def update(page, cloud, source, state):
    print(json.dumps({'phase': 'native_update_provider_read'}), flush=True)
    lb = owned(cloud, source, state)
    name = state['prefix'] + '-updated'
    if lb['name'] != name:
        settle(cloud, lb['id'])
        page.goto(browser.URL + '/horizon/project/load_balancer/', wait_until='domcontentloaded')
        print(json.dumps({'phase': 'native_update_list_loaded'}), flush=True)
        page.get_by_role('button', name='Edit Load Balancer', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.wait_for()
        print(json.dumps({'phase': 'native_update_modal_open'}), flush=True)
        from playwright.sync_api import expect
        expect(dialog.locator('[name=name]')).to_have_value(lb['name'], timeout=20000)
        dialog.locator('[name=name]').fill(name)
        finish = dialog.locator('button').filter(has_text='Update Load Balancer')
        for step in range(6):
            print(json.dumps({'phase': 'native_edit_step', 'step': step,
                'finish_visible': finish.is_visible(), 'finish_enabled': finish.is_enabled()}), flush=True)
            if finish.is_visible() and finish.is_enabled():
                break
            dialog.get_by_role('button', name='Next', exact=True).click()
        safety.require(finish.is_visible() and finish.is_enabled(), 'native_edit_finish_not_ready')
        if 'browser-update-name' not in state['intents']:
            task.intent(NAME, state, 'browser-update-name', 'native_update_only_new_test_lb_name')
        else:
            state.setdefault('reconciliations', []).append({'action': 'idempotent_name_update',
                'provider_name_before_retry': lb['name'], 'at': safety.now()})
            task.save(NAME, state)
        finish.click()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            lb = owned(cloud, source, state)
            if lb['name'] == name:
                break
            time.sleep(2)
    safety.require(lb['name'] == name, 'browser_lb_name_update_not_confirmed')
    settle(cloud, lb['id'])
    state['checks']['browser_update'] = {'name_verified_by_provider': True, 'description_preserved': True}
    task.save(NAME, state)
    print(json.dumps({'phase': 'native_lb_update_verified'}), flush=True)


def inspect_delete(page, cloud, source, state):
    owned(cloud, source, state)
    page.goto(browser.URL + '/horizon/project/load_balancer/', wait_until='domcontentloaded')
    page.locator('button').filter(has_text='Toggle Dropdown').click()
    print(json.dumps({'phase': 'native_delete_menu_visible', 'links': page.locator('.dropdown-menu a:visible').all_inner_texts()}), flush=True)
    page.locator('.dropdown-menu a:visible').filter(has_text='Delete Load Balancer').click()
    dialog = page.get_by_role('dialog')
    dialog.wait_for()
    print(json.dumps({'phase': 'native_lb_delete_confirmation', 'fields': browser.fields(dialog),
        'text': dialog.inner_text()[:1600], 'buttons': dialog.locator('button').all_inner_texts()}), flush=True)


def delete(page, cloud, source, state):
    safety.require(state['checks'].get('browser_update', {}).get('name_verified_by_provider'), 'browser_update_required')
    identifier = state['resources']['loadbalancer']['id']
    response = cloud.load_balancer.get('/lbaas/loadbalancers/' + identifier, raise_exc=False)
    if response.status_code == 200:
        lb = owned(cloud, source, state)
        safety.require({item['id'] for item in raw(cloud, 'loadbalancers')['loadbalancers']} == {identifier},
                       'unexpected_other_tenant_b_lb_preserved')
        port = cloud.network.get('/ports/' + state['resources']['vip_port']['id'], raise_exc=True).json()['port']
        safety.require(port['project_id'] == source['projects']['b']['id'], 'test_vip_port_owner_changed')
        if 'browser-delete-graph' not in state['intents']:
            page.goto(browser.URL + '/horizon/project/load_balancer/', wait_until='domcontentloaded')
            page.locator('button').filter(has_text='Toggle Dropdown').click()
            page.locator('.dropdown-menu a:visible').filter(has_text='Delete Load Balancer').click()
            dialog = page.get_by_role('dialog')
            dialog.wait_for()
            safety.require(lb['name'] in dialog.inner_text() and 'all of the sub-resources' in dialog.inner_text(),
                           'native_delete_confirmation_scope_mismatch')
            task.intent(NAME, state, 'browser-delete-graph', 'native_cascade_delete_only_new_tenant_b_graph')
            dialog.get_by_role('button', name='Delete Load Balancer', exact=True).click()
    else:
        safety.require(response.status_code == 404 and 'browser-delete-graph' in state['intents'],
                       'unrecorded_test_lb_deletion')
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = cloud.load_balancer.get('/lbaas/loadbalancers/' + identifier, raise_exc=False)
        if result.status_code == 404:
            break
        time.sleep(2)
    checks = {}
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        for kind, collection in (('loadbalancer', 'loadbalancers'), ('listener', 'listeners'),
                                  ('pool', 'pools'), ('monitor', 'healthmonitors')):
            path = '/lbaas/' + collection + '/' + state['resources'][kind]['id']
            status = operator.load_balancer.get(path, raise_exc=False).status_code
            safety.require(status == 404, 'test_' + kind + '_not_deleted')
            checks[kind + '_absent'] = True
        status = operator.network.get('/ports/' + state['resources']['vip_port']['id'], raise_exc=False).status_code
        safety.require(status == 404, 'test_vip_port_not_deleted')
        checks['vip_port_absent'] = True
        retained = task.read('octavia-acceptance', source)['resources']['loadbalancer']['id']
        original = operator.load_balancer.get('/lbaas/loadbalancers/' + retained, raise_exc=True).json()['loadbalancer']
        safety.require(original['project_id'] == source['projects']['a']['id'] and original['provisioning_status'] == 'ACTIVE',
                       'existing_tenant_a_load_balancer_health_changed')
        checks['original_a_loadbalancer_preserved'] = True
    safety.require(not raw(cloud, 'loadbalancers')['loadbalancers'], 'tenant_b_test_lbs_remaining')
    checks['tenant_b_list_empty'] = True
    checks['members_removed_with_parent_pool'] = True
    state['checks']['browser_cascade_delete'] = checks
    state['phase'] = 'native_octavia_update_and_cascade_delete_verified'
    task.save(NAME, state)
    summary = {'phase': state['phase'], 'checks': state['checks']}
    path = Path(__file__).resolve().parents[2] / 'docs/evidence/octavia-browser-lifecycle-20261028.json'
    path.write_text(json.dumps(summary, indent=2) + '\n')
    path.chmod(0o644)
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('create', 'inspect', 'inspect-forms', 'update', 'inspect-delete', 'delete'), required=True)
    args = parser.parse_args()
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    with task.locked(), controls.connection(source, private, 'b') as cloud:
        state = task.read(NAME, source)
        if args.phase == 'create':
            create(cloud, source, state)
            return
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = browser.browser_launch(pw)
            try:
                context = browser.local_context(br)
                page = context.new_page()
                print(json.dumps({'phase': 'native_lb_login_start'}), flush=True)
                login_dom(page, private['tenants']['b']['username'], private['tenants']['b']['password'])
                print(json.dumps({'phase': 'native_lb_login_complete'}), flush=True)
                {'inspect': inspect, 'inspect-forms': inspect_forms, 'update': update,
                 'inspect-delete': inspect_delete, 'delete': delete}[args.phase](page, cloud, source, state)
            finally:
                br.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'octavia_browser_lifecycle_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
