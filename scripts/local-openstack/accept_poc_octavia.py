#!/usr/bin/python3
"""Real native-browser OVN LB acceptance; only marked task resources are eligible."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shlex
import time
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

import browser_poc as browser
import browser_resources as controls
import configure_nova as common
import configure_poc_connectivity as connectivity
import continue_poc as task
import install_poc_browser as install
import manage_cinder_loop as safety
import prepare_octavia_backends as backends

NAME = 'octavia-acceptance'


def owned(cloud, source, state):
    lb = cloud.load_balancer.get_load_balancer(state['resources']['loadbalancer']['id'])
    safety.require(lb.project_id == source['projects']['a']['id'] and lb.description == state['marker']
                   and lb.name == state['prefix'], 'load_balancer_owner_or_identity_changed')
    return lb


def settle(cloud, lb):
    deadline = time.monotonic() + 120
    while True:
        result = cloud.load_balancer.get_load_balancer(lb.id)
        safety.require(result.provisioning_status != 'ERROR', 'owned_load_balancer_provider_error')
        if result.provisioning_status == 'ACTIVE':
            return result
        safety.require(time.monotonic() < deadline, 'load_balancer_did_not_settle')
        time.sleep(2)


def create(page, cloud, source, state):
    def step(value):
        state['diagnostic_step'] = value
        task.save(NAME, state)
        print(json.dumps({'phase': 'browser_lb_step', 'step': value}), flush=True)
    backend = task.read('octavia-backends', source)
    safety.require(backend.get('phase') == 'two_real_owned_backends_ready', 'two_backends_not_ready')
    if 'prefix' not in state:
        state['prefix'] = 'poc-t49-a-lb-' + uuid4().hex[:10]
        task.save(NAME, state)
    safety.require(not state['resources'] and cloud.load_balancer.find_load_balancer(state['prefix']) is None,
                   'unrecorded_load_balancer_preserved_no_replay')
    # Provider administration is deliberately denied to ordinary members.
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        providers = operator.load_balancer.get('/lbaas/providers', raise_exc=True).json()['providers']
    safety.require([p['name'] for p in providers] == ['ovn'], 'native_browser_requires_only_ovn_provider')
    page.goto(browser.URL + '/horizon/project/load_balancer/', wait_until='networkidle')
    step('open-create-wizard')
    page.get_by_text('Create Load Balancer', exact=True).first.click()
    dialog = page.get_by_role('dialog')
    page.wait_for_load_state('networkidle')
    details = dialog.locator('[ng-form=loadBalancerDetailsForm]')
    step('loadbalancer-details')
    details.locator('[name=name]').fill(state['prefix'])
    details.locator('[name=description]').fill(state['marker'])
    select = details.locator('filter-select[ng-model="model.spec.loadbalancer.vip_subnet_id"]')
    select.locator('button.dropdown-toggle').click()
    step('select-owned-subnet')
    select.locator('tbody tr:visible').filter(has_text=backend['subnet_id']).click()
    dialog.get_by_text('Listener Details', exact=True).first.click()
    step('listener-details')
    form = dialog.locator('[ng-form=listenerDetailsForm]')
    form.locator('[name=name]').fill(state['prefix'] + '-listener')
    form.locator('[name=description]').fill(state['marker'])
    form.locator('[name=protocol]').select_option('TCP')
    form.locator('[name=port]').fill('8080')
    dialog.get_by_text('Pool Details', exact=True).first.click()
    step('pool-details')
    form = dialog.locator('[ng-form=poolDetailsForm]')
    form.locator('[name=name]').fill(state['prefix'] + '-pool')
    form.locator('[name=description]').fill(state['marker'])
    form.locator('[name=method]').select_option(label='SOURCE_IP_PORT')
    dialog.get_by_text('Pool Members', exact=True).first.click()
    step('pool-members')
    for kind in ('primary', 'secondary'):
        step('read-member-' + kind)
        vm_id = backend[kind]['id']
        with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
            vm_name = operator.compute.get_server(vm_id).name
        step('select-member-' + kind)
        row = dialog.locator('available tbody tr:visible').filter(has=page.get_by_text(vm_name, exact=True))
        print(json.dumps({'phase': 'browser_lb_member_row', 'kind': kind, 'rows': row.count(),
            'row_text': row.all_inner_texts(), 'buttons': row.locator('button').evaluate_all(
                'xs=>xs.map(x=>({text:x.innerText.trim(),disabled:x.disabled}))')}), flush=True)
        safety.require(row.count() == 1, 'browser_backend_selection_ambiguous')
        port_fields = dialog.locator('.transfer-allocated input[name$="-port"]')
        prior_port_count = port_fields.count()
        row.locator('button').filter(has_text='Add').click()
        step('member-' + kind + '-added')
        target = port_fields.nth(prior_port_count)
        target.wait_for(state='visible', timeout=10000)
        port_names = port_fields.evaluate_all('xs=>xs.map(x=>x.name)')
        print(json.dumps({'phase': 'browser_lb_member_ports', 'kind': kind, 'before': prior_port_count,
                          'after_names': port_names, 'target_name': target.get_attribute('name')}), flush=True)
        port_index = prior_port_count // 2
        safety.require(port_names.count(str(port_index) + '-port') == 1
                       and target.get_attribute('name') == str(port_index) + '-port',
                       'browser_member_port_input_ambiguous')
        target.fill('8080')
        step('member-' + kind + '-configured')
    dialog.get_by_text('Monitor Details', exact=True).first.click()
    step('health-monitor-details')
    form = dialog.locator('[ng-form=monitorDetailsForm]')
    form.locator('[name=name]').fill(state['prefix'] + '-monitor')
    form.locator('[name=type]').select_option(label='TCP')
    for field, value in [('retry_down', '2'), ('interval', '2'), ('retry', '2'), ('timeout', '1')]:
        form.locator('[name=' + field + ']').fill(value)
    from playwright.sync_api import expect
    expect(dialog.locator('button.finish')).to_be_enabled()
    task.intent(NAME, state, 'create-native-lb-graph', 'native_create_ovn_tcp_listener_pool_two_members_health_monitor')
    dialog.locator('button.finish').click()
    lb = browser.wait_resource(lambda: cloud.load_balancer.find_load_balancer(state['prefix']))
    state['resources']['loadbalancer'] = {'id': lb.id}
    task.save(NAME, state)
    lb = settle(cloud, owned(cloud, source, state))
    safety.require(lb.provider == 'ovn' and lb.vip_subnet_id == backend['subnet_id'], 'browser_load_balancer_provider_mismatch')
    # OpenStack SDK's nested list filters vary across Octavia resource models;
    # use the public API relationship fields for independent graph traversal.
    raw_lb = cloud.load_balancer.get('/lbaas/loadbalancers/' + lb.id, raise_exc=True).json()['loadbalancer']
    raw_listeners = cloud.load_balancer.get('/lbaas/listeners?loadbalancer_id=' + lb.id, raise_exc=True).json()['listeners']
    safety.require(raw_lb['project_id'] == lb.project_id and len(raw_listeners) == 1
                   and raw_listeners[0]['loadbalancers'][0]['id'] == lb.id, 'browser_nested_listener_graph_incomplete')
    listener = raw_listeners[0]
    pool_id = listener.get('default_pool_id')
    safety.require(pool_id, 'browser_default_pool_missing')
    pool = cloud.load_balancer.get('/lbaas/pools/' + pool_id, raise_exc=True).json()['pool']
    raw_members = cloud.load_balancer.get('/lbaas/pools/' + pool_id + '/members', raise_exc=True).json()['members']
    monitor_id = pool.get('healthmonitor_id')
    safety.require(monitor_id, 'browser_pool_healthmonitor_missing')
    monitor = cloud.load_balancer.get('/lbaas/healthmonitors/' + monitor_id, raise_exc=True).json()['healthmonitor']
    state['resources'].update(listener={'id': listener['id']}, pool={'id': pool_id}, monitor={'id': monitor_id},
        members=[{'id': m['id'], 'address': m['address']} for m in raw_members])
    task.save(NAME, state)
    safety.require(listener['protocol'] == pool['protocol'] == 'TCP' and listener['protocol_port'] == 8080
                   and pool['lb_algorithm'] == 'SOURCE_IP_PORT' and len(raw_members) == 2
                   and {m['address'] for m in raw_members} == {backend[k]['address'] for k in ('primary', 'secondary')}
                   and all(m['project_id'] == lb.project_id and m['protocol_port'] == 8080 and m['admin_state_up']
                           and m['provisioning_status'] == 'ACTIVE' and m['operating_status'] == 'ONLINE'
                           for m in raw_members)
                   and monitor['type'] == 'TCP' and monitor['delay'] == 2 and monitor['timeout'] == 1
                   and monitor['max_retries'] == 2 and monitor['max_retries_down'] == 2
                   and monitor['provisioning_status'] == 'ACTIVE' and monitor['operating_status'] == 'ONLINE',
                   'native_lb_graph_attributes_mismatch')
    state['checks']['native_create'] = {'provider': 'ovn', 'protocol': 'TCP', 'algorithm': 'SOURCE_IP_PORT',
        'members': 2, 'health_monitor': 'TCP', 'provisioning_status': 'ACTIVE'}
    task.save(NAME, state)
    print(json.dumps({'phase': 'native_browser_load_balancer_graph_verified', **state['checks']['native_create']}), flush=True)


def reconcile(cloud, source, state):
    """Checkpoint a graph that the native Horizon wizard created before a client disconnect."""
    backend = task.read('octavia-backends', source)
    lb = owned(cloud, source, state)
    api = cloud.load_balancer
    raw_lb = api.get('/lbaas/loadbalancers/' + lb.id, raise_exc=True).json()['loadbalancer']
    listeners = api.get('/lbaas/listeners?loadbalancer_id=' + lb.id, raise_exc=True).json()['listeners']
    safety.require(raw_lb['project_id'] == lb.project_id and len(listeners) == 1
                   and listeners[0]['loadbalancers'][0]['id'] == lb.id, 'reconciled_listener_graph_mismatch')
    listener = listeners[0]
    pool_id = listener.get('default_pool_id')
    safety.require(pool_id, 'reconciled_default_pool_missing')
    pool = api.get('/lbaas/pools/' + pool_id, raise_exc=True).json()['pool']
    members = api.get('/lbaas/pools/' + pool_id + '/members', raise_exc=True).json()['members']
    monitor_id = pool.get('healthmonitor_id')
    safety.require(monitor_id, 'reconciled_monitor_missing')
    monitor = api.get('/lbaas/healthmonitors/' + monitor_id, raise_exc=True).json()['healthmonitor']
    state['resources'].update(listener={'id': listener['id']}, pool={'id': pool_id}, monitor={'id': monitor_id},
        members=[{'id': member['id'], 'address': member['address']} for member in members])
    task.save(NAME, state)
    safety.require(listener['protocol'] == pool['protocol'] == 'TCP' and listener['protocol_port'] == 8080
                   and pool['lb_algorithm'] == 'SOURCE_IP_PORT' and len(members) == 2
                   and {member['address'] for member in members} == {backend[key]['address'] for key in ('primary', 'secondary')}
                   and all(member['project_id'] == lb.project_id and member['protocol_port'] == 8080
                           and member['admin_state_up'] and member['provisioning_status'] == 'ACTIVE'
                           and member['operating_status'] == 'ONLINE' for member in members)
                   and monitor['type'] == 'TCP' and monitor['delay'] == 2 and monitor['timeout'] == 1
                   and monitor['max_retries'] == 2 and monitor['max_retries_down'] == 2
                   and monitor['provisioning_status'] == 'ACTIVE' and monitor['operating_status'] == 'ONLINE',
                   'reconciled_graph_attributes_mismatch')
    state['checks']['native_create'] = {'provider': 'ovn', 'protocol': 'TCP', 'algorithm': 'SOURCE_IP_PORT',
        'members': 2, 'health_monitor': 'TCP', 'provisioning_status': 'ACTIVE', 'reconciled_from_provider_api': True}
    task.save(NAME, state)
    print(json.dumps({'phase': 'existing_native_browser_graph_reconciled', **state['checks']['native_create']}), flush=True)


def route(page, cloud, source, state):
    def step(value):
        state['diagnostic_step'] = 'route-' + value
        task.save(NAME, state)
        print(json.dumps({'phase': 'browser_vip_route_step', 'step': value}), flush=True)

    lb = settle(cloud, owned(cloud, source, state))
    backend = task.read('octavia-backends', source)
    ingress = task.read('connectivity', source)
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        hm = [p for p in operator.network.ports(network_id=backend['network_id'])
              if p.device_owner == 'ovn-lb-hm:distributed' and any(ip['subnet_id'] == backend['subnet_id'] for ip in p.fixed_ips)]
        safety.require(len(hm) == 1, 'ovn_healthcheck_source_port_ambiguous')
        address = next(ip['ip_address'] for ip in hm[0].fixed_ips if ip['subnet_id'] == backend['subnet_id'])
        step('health-check-source-discovered')
        group = cloud.network.get_security_group(ingress['resources']['a_access_group']['id'])
        safety.require(group.project_id == backend['project_id'] and group.description == state['marker'], 'backend_access_group_changed')
        rules = list(cloud.network.security_group_rules(security_group_id=group.id))
        if not any(r.direction == 'ingress' and r.protocol == 'tcp' and r.port_range_min == r.port_range_max == 8080
                   and r.remote_ip_prefix == address + '/32' for r in rules):
            task.intent(NAME, state, 'health-check-source', 'allow_only_discovered_ovn_healthcheck_source_to_8080')
            rule = cloud.network.create_security_group_rule(security_group_id=group.id, direction='ingress', ether_type='IPv4',
                protocol='tcp', port_range_min=8080, port_range_max=8080, remote_ip_prefix=address + '/32', description=state['marker'])
            state['resources']['healthcheck_rule'] = {'id': rule.id}
            task.save(NAME, state)
        step('health-check-source-allowed')
    if 'floating_ip' not in state['resources']:
        description = state['marker'] + '/' + state['prefix']
        matches = [ip for ip in cloud.network.ips() if ip.description == description]
        safety.require(len(matches) <= 1, 'vip_floating_ip_marker_not_unique')
        if matches:
            floating = matches[0]
            safety.require('allocate-vip-floating-ip' in state['intents'] and floating.project_id == lb.project_id
                           and floating.port_id is None, 'unrecorded_or_changed_vip_floating_ip')
            state['resources']['floating_ip'] = {'id': floating.id, 'address': floating.floating_ip_address,
                                                  'description': description, 'reconciled_after_browser_timeout': True}
            task.save(NAME, state)
            step('allocation-reconciled-from-tenant-api')
        else:
            safety.require('allocate-vip-floating-ip' not in state['intents'], 'unconfirmed_allocation_intent_no_replay')
            page.goto(browser.URL + '/horizon/project/floating_ips/allocate/', wait_until='domcontentloaded', timeout=60000)
            page.locator('body').wait_for(state='visible', timeout=10000)
            pool_select = page.locator('[name=pool]')
            if pool_select.count() != 1:
                print(json.dumps({'phase': 'vip_allocate_form_diagnostic', 'url': page.url,
                    'title': page.title(), 'fields': page.locator('input,select,button').evaluate_all(
                        'xs=>xs.filter(x=>x.offsetParent!==null).map(x=>({tag:x.tagName,name:x.name||"",type:x.type||"",text:(x.innerText||"").trim().slice(0,80)}))')}), flush=True)
                safety.require(False, 'native_vip_allocate_pool_control_missing')
            step('allocate-form-ready')
            pool_select.select_option(ingress['resources']['external_network']['id'], force=True)
            page.locator('[name=description]').fill(description)
            task.intent(NAME, state, 'allocate-vip-floating-ip', 'native_allocate_vip_floating_ip')
            page.locator('input[type=submit]').click()
            step('allocate-submitted')
            floating = browser.wait_resource(lambda: next((ip for ip in cloud.network.ips() if ip.description == description), None))
            state['resources']['floating_ip'] = {'id': floating.id, 'address': floating.floating_ip_address, 'description': description}
            task.save(NAME, state)
    floating = cloud.network.get_ip(state['resources']['floating_ip']['id'])
    safety.require(floating.project_id == lb.project_id and floating.description == state['resources']['floating_ip']['description'],
                   'vip_floating_ip_scope_changed')
    if floating.port_id is None:
        page.goto(browser.URL + '/horizon/project/floating_ips/associate/', wait_until='domcontentloaded', timeout=60000)
        page.locator('body').wait_for(state='visible', timeout=10000)
        safety.require(page.locator('[name=ip_id]').count() == 1 and page.locator('[name=port_id]').count() == 1,
                       'native_vip_associate_controls_missing')
        step('associate-form-ready')
        page.locator('[name=ip_id]').select_option(floating.id, force=True)
        page.locator('[name=port_id]').select_option(lb.vip_port_id + '_' + lb.vip_address, force=True)
        task.intent(NAME, state, 'associate-vip-floating-ip', 'native_associate_only_owned_vip_port')
        page.locator('input[type=submit]').click()
        step('association-submitted')
        browser.wait_resource(lambda: cloud.network.get_ip(floating.id).port_id == lb.vip_port_id)
    safety.require(cloud.network.get_ip(floating.id).port_id == lb.vip_port_id, 'vip_floating_ip_not_associated')
    step('association-confirmed-by-api')
    unit = Path('/etc/systemd/system/poc-vip-http.service')
    install.write_root(unit, connectivity.forward_unit(floating.floating_ip_address, 8080, 8083))
    safety.run(['systemctl', 'daemon-reload'])
    safety.run(['systemctl', 'enable', '--now', unit.name])
    state['checks']['vip_route'] = {'browser_associated': True, 'local_port': 8083, 'healthcheck_source': address + '/32'}
    task.save(NAME, state)
    print(json.dumps({'phase': 'native_vip_floating_ip_associated', 'local_url': 'http://127.0.0.1:8083/'}), flush=True)


def traffic(source, state, expected, count=60):
    backend = task.read('octavia-backends', source)
    client, observations, errors = build_opener(ProxyHandler({})), Counter(), 0
    for ignored in range(count):
        try:
            # A new request/socket creates a fresh source port for SOURCE_IP_PORT.
            with client.open('http://127.0.0.1:8083/', timeout=3) as response:
                data = json.loads(response.read(4096))
                safety.require(response.status == 200 and data['tenant'] == 'a' and data['backend'] in expected
                               and data['server_id'] == backend['primary' if data['backend'] == 'primary-a' else 'secondary']['id'],
                               'vip_returned_unexpected_backend_identity')
                observations[data['backend']] += 1
        except safety.GuardError:
            raise
        except Exception:
            errors += 1
    safety.require(errors == 0 and set(observations) == set(expected), 'vip_distribution_or_recovery_failed')
    return {'fresh_connections': count, 'responses': dict(observations), 'errors': errors}


def inspect_route(cloud, source, state):
    description = state['marker'] + '/' + state['prefix']
    matches = [ip for ip in cloud.network.ips() if ip.description == description]
    result = {'matching_floating_ips': len(matches), 'tenant_floating_ip_count': len(list(cloud.network.ips()))}
    if matches:
        result['matching'] = [{'id': ip.id, 'port_id': ip.port_id, 'status': ip.status} for ip in matches]
    print(json.dumps({'phase': 'read_only_vip_allocation_inspection', **result}), flush=True)


def member_status(cloud, state):
    pool_id = state['resources']['pool']['id']
    return cloud.load_balancer.get('/lbaas/pools/' + pool_id + '/members', raise_exc=True).json()['members']


def wait_member_status(cloud, state, address, expected, timeout=60):
    expected = (expected,) if isinstance(expected, str) else tuple(expected)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = [member for member in member_status(cloud, state) if member['address'] == address]
        safety.require(len(rows) == 1 and rows[0]['admin_state_up'], 'octavia_member_admin_state_changed')
        if rows[0]['operating_status'] in expected:
            return rows[0]
        safety.require(isinstance(rows[0]['operating_status'], str), 'invalid_member_operating_state')
        time.sleep(2)
    raise safety.GuardError('health_monitor_transition_timeout_' + '_or_'.join(x.lower() for x in expected))


def stop_secondary_application(state):
    marker = state['marker']
    script = '''set -eu
directory=/home/cirros/poc-t49-app
[ "$(cat "$directory/owner")" = MARKER ]
[ -s "$directory/httpd.pid" ]
pid=$(cat "$directory/httpd.pid")
case "$pid" in *[!0-9]*|'') exit 2;; esac
if kill -0 "$pid" 2>/dev/null; then
    tr '\\000' ' ' < "/proc/$pid/cmdline" | grep -F 'busybox httpd -f -p 8080 -h /home/cirros/poc-t49-app' >/dev/null
    kill "$pid"
fi
i=0
while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 20 ]; do sleep 0.25; i=$((i+1)); done
if kill -0 "$pid" 2>/dev/null; then exit 3; fi
printf 'POC_APP_STOPPED\\n'
'''.replace('MARKER', shlex.quote(marker))
    return script


def recover_failure(cloud, source, state):
    backend = task.read('octavia-backends', source)
    secondary = backend['secondary']
    safety.require(state['checks'].get('vip_route', {}).get('browser_associated') is True,
                   'verified_vip_route_required')
    safety.require(state['checks'].get('distribution', {}).get('errors') == 0,
                   'baseline_distribution_not_verified')
    initial = [row for row in member_status(cloud, state) if row['address'] == secondary['address']]
    if 'failure_recovery' not in state['checks']:
        safety.require(len(initial) == 1 and initial[0]['admin_state_up'] and initial[0]['operating_status'] == 'ONLINE',
                       'secondary_not_online_before_failure_test')
        state['checks']['failure_recovery'] = {'member_admin_state_up_before': initial[0]['admin_state_up']}
        task.save(NAME, state)
    failure = state['checks']['failure_recovery']
    try:
        if not failure.get('secondary_unhealthy'):
            if 'stop-secondary-application' not in state['intents']:
                task.intent(NAME, state, 'stop-secondary-application', 'stop_only_marked_secondary_httpd_pid')
            output = backends.ssh(source, backend, stop_secondary_application(state))
            safety.require('POC_APP_STOPPED' in output, 'secondary_test_application_stop_unconfirmed')
            failure['application_stopped_at'] = datetime.now(timezone.utc).isoformat()
            task.save(NAME, state)
            row = wait_member_status(cloud, state, secondary['address'], ('OFFLINE', 'ERROR'))
            safety.require(row['admin_state_up'], 'secondary_admin_state_changed_during_failure')
            failure.update(secondary_unhealthy=True, unhealthy_member_status=row['operating_status'],
                           unhealthy_observed_at=datetime.now(timezone.utc).isoformat(),
                           member_admin_state_up_during=row['admin_state_up'])
            task.save(NAME, state)
        if 'during_outage_traffic' not in failure:
            failure['during_outage_traffic'] = traffic(source, state, {'primary-a'}, 30)
            task.save(NAME, state)
    except Exception:
        restore_secondary(cloud, source, state)
        raise
    if not failure.get('secondary_online_after_recovery'):
        try:
            if 'restart-secondary-application' not in state['intents']:
                task.intent(NAME, state, 'restart-secondary-application', 'restart_only_marked_secondary_httpd_via_existing_start_script')
            output = backends.ssh(source, backend,
                'set -eu\n[ "$(cat /home/cirros/poc-t49-app/owner)" = ' + shlex.quote(state['marker']) +
                ' ]\n/home/cirros/poc-t49-app/start.sh\nwget -q -T 5 -O - http://127.0.0.1:8080/\n')
            identity = json.loads(output.splitlines()[-1])
            safety.require(identity['server_id'] == secondary['id'] and identity['backend'] == 'secondary-a',
                           'secondary_application_recovery_identity_mismatch')
            failure['application_restarted_at'] = datetime.now(timezone.utc).isoformat()
            task.save(NAME, state)
            row = wait_member_status(cloud, state, secondary['address'], 'ONLINE')
            safety.require(row['admin_state_up'] == failure['member_admin_state_up_before'],
                           'secondary_admin_state_changed_during_recovery')
            failure.update(secondary_online_after_recovery=True, online_observed_at=datetime.now(timezone.utc).isoformat(),
                           member_admin_state_up_after=row['admin_state_up'])
            task.save(NAME, state)
        except Exception:
            restore_secondary(cloud, source, state)
            raise
    failure['after_recovery_traffic'] = traffic(source, state, {'primary-a', 'secondary-a'}, 60)
    task.save(NAME, state)
    print(json.dumps({'phase': 'octavia_health_monitor_failure_and_recovery_verified',
        'secondary_unhealthy_observed': failure['secondary_unhealthy'],
        'provider_unhealthy_status': failure['unhealthy_member_status'],
        'secondary_online_after_recovery': failure['secondary_online_after_recovery'],
        'member_admin_state_preserved': failure['member_admin_state_up_before'] == failure['member_admin_state_up_during']
            == failure['member_admin_state_up_after'], 'during_outage_traffic': failure['during_outage_traffic'],
        'after_recovery_traffic': failure['after_recovery_traffic']}), flush=True)


def inspect_failure(cloud, source, state):
    backend = task.read('octavia-backends', source)
    rows = [row for row in member_status(cloud, state) if row['address'] == backend['secondary']['address']]
    safety.require(len(rows) == 1, 'secondary_member_lookup_ambiguous')
    script = '''set -eu
directory=/home/cirros/poc-t49-app
[ "$(cat "$directory/owner")" = MARKER ]
pid=$(cat "$directory/httpd.pid")
case "$pid" in *[!0-9]*|'') exit 2;; esac
if kill -0 "$pid" 2>/dev/null; then
    tr '\\000' ' ' < "/proc/$pid/cmdline" | grep -F 'busybox httpd -f -p 8080 -h /home/cirros/poc-t49-app' >/dev/null
    printf 'application_running\\n'
else
    printf 'application_stopped\\n'
fi
'''.replace('MARKER', shlex.quote(state['marker']))
    output = backends.ssh(source, backend, script).strip()
    print(json.dumps({'phase': 'read_only_failure_state', 'operating_status': rows[0]['operating_status'],
        'admin_state_up': rows[0]['admin_state_up'], 'application_process': output,
        'failure_recovery_checkpoint': state['checks'].get('failure_recovery')}), flush=True)


def restore_secondary(cloud, source, state):
    backend = task.read('octavia-backends', source)
    if 'restart-secondary-application' not in state['intents']:
        task.intent(NAME, state, 'restart-secondary-application', 'restore_only_marked_secondary_httpd_via_existing_start_script')
    output = backends.ssh(source, backend,
        'set -eu\n[ "$(cat /home/cirros/poc-t49-app/owner)" = ' + shlex.quote(state['marker']) +
        ' ]\n/home/cirros/poc-t49-app/start.sh\nwget -q -T 5 -O - http://127.0.0.1:8080/\n')
    identity = json.loads(output.splitlines()[-1])
    safety.require(identity['server_id'] == backend['secondary']['id'] and identity['backend'] == 'secondary-a',
                   'secondary_application_restore_identity_mismatch')
    deadline = time.monotonic() + 60
    status = None
    while time.monotonic() < deadline:
        rows = [row for row in member_status(cloud, state) if row['address'] == backend['secondary']['address']]
        safety.require(len(rows) == 1 and rows[0]['admin_state_up'], 'secondary_member_admin_state_changed')
        status = rows[0]['operating_status']
        if status == 'ONLINE':
            break
        time.sleep(2)
    state.setdefault('checks', {}).setdefault('failure_recovery', {}).update(
        restored_after_interrupted_test=True, secondary_status_after_restore=status,
        application_identity_after_restore=identity['server_id'])
    safety.require(status == 'ONLINE', 'secondary_member_not_online_after_restore')
    task.save(NAME, state)
    print(json.dumps({'phase': 'marked_secondary_application_restored', 'application_http_identity_verified': True,
                      'member_operating_status': status, 'member_admin_state_up': rows[0]['admin_state_up']}), flush=True)


def tenant_isolation(cloud, source, private, state):
    foreign_id = state['resources']['loadbalancer']['id']
    foreign_subnet = source['resources']['a']['subnet']['id']
    result = {}
    with controls.connection(source, private, 'b') as tenant_b:
        safety.require(tenant_b.session.get_project_id() == source['projects']['b']['id'],
                       'tenant_b_token_scope_mismatch')
        for name, service, path in (('loadbalancer', tenant_b.load_balancer, '/lbaas/loadbalancers/' + foreign_id),
                                     ('subnet', tenant_b.network, '/v2.0/subnets/' + foreign_subnet)):
            try:
                service.get(path, raise_exc=True)
                status = 200
            except Exception as error:
                response = getattr(error, 'response', None)
                status = getattr(response, 'status_code', None) or getattr(error, 'status_code', None)
            safety.require(status in (403, 404), 'tenant_b_foreign_' + name + '_read_was_not_denied')
            result[name + '_foreign_read_status'] = status
        try:
            provider_result = tenant_b.load_balancer.get('/lbaas/providers', raise_exc=True)
            provider_status = provider_result.status_code
            provider_names = sorted(item['name'] for item in provider_result.json()['providers'])
        except Exception as error:
            response = getattr(error, 'response', None)
            provider_status = getattr(response, 'status_code', None) or getattr(error, 'status_code', None)
            provider_names = []
        safety.require(provider_status == 200 and provider_names == ['ovn'], 'tenant_b_provider_catalog_not_minimal')
        result['provider_catalog_status'] = provider_status
        result['visible_provider_names'] = provider_names
        rows = tenant_b.load_balancer.get('/lbaas/loadbalancers', raise_exc=True).json()['loadbalancers']
        safety.require(all(row['project_id'] == source['projects']['b']['id'] for row in rows),
                       'tenant_b_loadbalancer_list_leaked_foreign_project')
        result['tenant_b_loadbalancer_count'] = len(rows)
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        quota = operator.load_balancer.get('/lbaas/quotas/' + source['projects']['b']['id'], raise_exc=True).json().get('quota', {})
    safe_quota = {key: value for key, value in quota.items() if key in {
        'loadbalancer', 'listener', 'pool', 'member', 'healthmonitor', 'l7policy', 'l7rule'}}
    state['checks']['tenant_isolation'] = dict(result, tenant_b_quota=safe_quota,
                                               foreign_reads_denied=True, provider_catalog_minimal=True)
    task.save(NAME, state)
    print(json.dumps({'phase': 'octavia_tenant_isolation_verified', **state['checks']['tenant_isolation']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('create', 'reconcile', 'route', 'inspect-route', 'traffic', 'failure-recovery', 'inspect-failure', 'restore-secondary', 'tenant-isolation'), required=True)
    args = parser.parse_args()
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    with task.locked(), controls.connection(source, private, 'a') as cloud:
        state = task.read(NAME, source)
        if args.phase == 'reconcile':
            reconcile(cloud, source, state)
            return
        if args.phase == 'inspect-route':
            inspect_route(cloud, source, state)
            return
        if args.phase == 'failure-recovery':
            recover_failure(cloud, source, state)
            return
        if args.phase == 'inspect-failure':
            inspect_failure(cloud, source, state)
            return
        if args.phase == 'restore-secondary':
            restore_secondary(cloud, source, state)
            return
        if args.phase == 'tenant-isolation':
            tenant_isolation(cloud, source, private, state)
            return
        if args.phase == 'traffic':
            owned(cloud, source, state)
            state['checks']['distribution'] = traffic(source, state, {'primary-a', 'secondary-a'})
            task.save(NAME, state)
            print(json.dumps({'phase': 'real_two_vm_distribution_verified', **state['checks']['distribution']}), flush=True)
            return
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = browser.browser_launch(pw)
            try:
                context = browser.local_context(br)
                page = context.new_page()
                browser.login(page, private['tenants']['a']['username'], private['tenants']['a']['password'])
                {'create': create, 'route': route}[args.phase](page, cloud, source, state)
                context.close()
            finally:
                br.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'octavia_acceptance_stopped', 'reason': str(error) if isinstance(error, safety.GuardError)
                          else type(error).__name__, 'automatic_replay': False}), flush=True)
        raise SystemExit(2)
