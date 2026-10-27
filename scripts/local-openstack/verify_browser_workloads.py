#!/usr/bin/python3
"""Verify real tenant boundaries, console access and demonstration statements.

Uses private operator state. Never logs credentials, console URLs, cookie values
or private keys. Billing forms are submitted once with durable operator intents.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import os
from urllib.parse import urlsplit

import browser_poc as browser
import browser_resources as resources
import configure_nova as common
import manage_cinder_loop as safety
import prepare_browser_tenants as tenants

RATE_VERSION = 'poc-demo-20261027'


def admin_management(br, state, private):
    context = browser.local_context(br)
    try:
        page = context.new_page()
        browser.login(page, 'admin', common.read_secrets(common.SECRET_FILE)['ADMIN_PASS'])
        with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
            project = state['projects']['a']['id']
            tenants.owned(cloud.identity.get_project(project), tenants.PROJECTS['a'], state['marker'], project)
            for value, phase in ((1, 'tighten'), (2, 'restore')):
                page.goto(browser.URL + '/horizon/identity/' + project + '/update_quotas/', wait_until='networkidle')
                page.locator('a[data-toggle=tab]').filter(has_text=__import__('re').compile(r'^\s*Compute\s*$')).click()
                page.locator('[name=instances]').fill(str(value))
                resources.intent(state, 'admin', 'quota-' + phase, project)
                page.locator('input[type=submit]').click()
                page.wait_for_load_state('networkidle')
                safety.require(cloud.compute.get_quota_set(project).instances == value, 'browser_quota_edit_not_applied')
                print(json.dumps({'phase': 'browser_admin_quota_' + phase, 'instances': value}), flush=True)
            user = state['users']['a']['id']
            tenants.owned(cloud.identity.get_user(user), tenants.USERS['a'], state['marker'], user)
            page.goto(browser.URL + '/horizon/identity/users/' + user + '/update/', wait_until='networkidle')
            page.locator('[name=email]').fill('poc-browser-a@example.invalid')
            resources.intent(state, 'admin', 'user-update', user)
            page.locator('input[type=submit]').click()
            page.wait_for_load_state('networkidle')
            safety.require(cloud.identity.get_user(user).email == 'poc-browser-a@example.invalid', 'browser_user_edit_not_applied')
            print(json.dumps({'phase': 'browser_admin_user_update_verified'}), flush=True)
    finally:
        context.close()
    state['browser_admin_management_checks'] = {'admin_user_update': True,
        'admin_quota_tighten_and_restore': True}
    tenants.save(state)


def quota_feedback(br, state, private):
    from playwright.sync_api import expect
    checks = []
    for label in ('a', 'b'):
        context = browser.local_context(br)
        try:
            page = context.new_page()
            browser.login(page, private['tenants'][label]['username'], private['tenants'][label]['password'])
            page.goto(browser.URL + '/horizon/project/instances/', wait_until='networkidle')
            page.locator('#instances__action_launch-ng').click()
            dialog = page.get_by_role('dialog')
            page.wait_for_load_state('networkidle')
            dialog.locator('[name=name]').fill('quota-check-no-submission')
            dialog.get_by_text('Source', exact=True).first.click()
            dialog.locator('[name=boot-source-type]').select_option(value='image', force=True)
            dialog.locator('label#vol-create').filter(has_text=__import__('re').compile('^No$')).click()
            resources.allocate(dialog, state['catalog']['image_name'])
            dialog.get_by_text('Flavor', exact=True).first.click()
            resources.allocate(dialog, state['catalog']['flavor_name'])
            dialog.get_by_text('Networks', exact=True).first.click()
            resources.allocate(dialog, state['resources'][label]['network']['name'])
            dialog.get_by_text('Key Pair', exact=True).first.click()
            resources.allocate(dialog, state['resources'][label]['keypair']['name'])
            dialog.get_by_text('Details', exact=True).first.click()
            dialog.locator('[name=count]').fill('1')
            expect(dialog.locator('button.finish')).to_be_enabled(timeout=15000)
            dialog.locator('[name=count]').fill('2')
            expect(dialog.locator('button.finish')).to_be_disabled(timeout=15000)
            # Horizon's icon contributes to the accessible name; match the
            # visible Cancel label without requiring an icon-free exact name.
            dialog.get_by_role('button', name='Cancel').click()
            response = page.goto(browser.URL + '/horizon/identity/users/' + state['users']['a']['id'] + '/update/', wait_until='networkidle')
            safety.require(response.status in (401, 403) or '/identity/users/' not in urlsplit(page.url).path,
                           'tenant_admin_user_edit_not_denied')
            checks.append({'label': label, 'valid_launch_enabled': True, 'over_quota_launch_disabled': True,
                           'launch_submitted': False, 'admin_user_edit_denied': True})
        finally:
            context.close()
    state['browser_management_checks'] = {'tenant_checks': checks,
                                         **state.get('browser_admin_management_checks', {})}
    tenants.save(state)
    print(json.dumps({'phase': 'browser_management_and_quota_verified', **state['browser_management_checks']}), flush=True)


def management(br, state, private):
    admin_management(br, state, private)
    quota_feedback(br, state, private)


def provider_quotas(state, private):
    def observation(response):
        try:
            fault = next(iter(response.json().values()))
            quota = isinstance(fault, dict) and 'quota' in str(fault.get('message', '')).lower()
        except (ValueError, StopIteration, TypeError):
            quota = False
        return {'http_status': response.status_code, 'quota_message_verified': quota}
    checks = []
    for label in ('a', 'b'):
        with resources.connection(state, private, label) as cloud:
            project = state['projects'][label]['id']
            limit = cloud.compute.get_quota_set(project)
            volume_limit = cloud.block_storage.get_quota_set(project)
            safety.require(limit.instances == 2 and limit.cores == 2 and volume_limit.gigabytes == 2,
                           'negative_quota_probe_limits_changed')
            prefix = 'poc-browser-' + label + '-over-quota'
            safety.require(not any(vm.name.startswith(prefix) for vm in cloud.compute.servers())
                           and cloud.block_storage.find_volume(prefix) is None,
                           'negative_quota_probe_name_exists')
            observed = state.setdefault('provider_quota_observations', {})
            if label + ':nova' not in observed:
                resources.intent(state, label, 'provider-compute-quota', prefix)
                response = cloud.compute.post('/servers', json={'server': {
                    'name': prefix, 'imageRef': state['public_image_id'], 'flavorRef': state['browser_flavor_id'],
                    'min_count': 2, 'max_count': 2, 'networks': [{'uuid': state['resources'][label]['network']['id']}],
                    'metadata': {'poc_owner': state['marker']}}}, raise_exc=False)
                observed[label + ':nova'] = observation(response)
                tenants.save(state)
            # Nova maps its OverQuota exception to HTTPForbidden in this API.
            nova = observed[label + ':nova']
            safety.require(nova['http_status'] == 403 and nova['quota_message_verified'],
                           'provider_compute_over_quota_not_denied')
            resources.intent(state, label, 'provider-volume-quota', prefix)
            volume = cloud.block_storage.post('/volumes', json={'volume': {
                'name': prefix, 'size': 3, 'description': state['marker']}}, raise_exc=False)
            observed[label + ':cinder'] = observation(volume)
            tenants.save(state)
            safety.require(volume.status_code == 413 and observed[label + ':cinder']['quota_message_verified'],
                           'provider_volume_over_quota_not_denied')
            safety.require(not any(vm.name.startswith(prefix) for vm in cloud.compute.servers())
                           and cloud.block_storage.find_volume(prefix) is None,
                           'denied_quota_probe_created_resource')
            checks.append({'label': label, 'nova_over_quota_http': nova['http_status'],
                           'cinder_over_quota_http': volume.status_code, 'created_resources': 0})
            print(json.dumps({'phase': 'provider_quotas_denied', **checks[-1]}), flush=True)
    state['provider_quota_checks'] = checks
    tenants.save(state)


def consoles(br, state, private):
    checks = []
    for label in ('a', 'b'):
        context = browser.local_context(br)
        try:
            page = context.new_page()
            browser.login(page, private['tenants'][label]['username'], private['tenants'][label]['password'])
            instance = state['resources'][label]['server']['id']
            page.goto(browser.URL + '/horizon/project/instances/' + instance + '/auto_console',
                      wait_until='networkidle')
            page.locator('#status').filter(has_text='Connected').wait_for(timeout=30000)
            safety.require(urlsplit(page.url).scheme == 'https' and page.locator('canvas').count() == 1,
                           'https_console_canvas_missing')
            dimensions = page.locator('canvas').evaluate('x=>({width:x.width,height:x.height})')
            safety.require(dimensions['width'] > 0 and dimensions['height'] > 0, 'console_empty_framebuffer')
            checks.append({'label': label, 'https_wss_connected': True, 'canvas': dimensions})
            print(json.dumps({'phase': 'tenant_console_connected', **checks[-1]}), flush=True)
        finally:
            context.close()
    state['browser_console_checks'] = checks
    tenants.save(state)


def isolation(state, private):
    checks = []
    for label, foreign in (('a', 'b'), ('b', 'a')):
        with resources.connection(state, private, label) as cloud:
            target = state['resources'][foreign]
            paths = [('nova', cloud.compute, '/servers/' + target['server']['id']),
                     ('cinder', cloud.block_storage, '/volumes/' + target['volume']['id']),
                     ('neutron', cloud.network, '/networks/' + target['network']['id'])]
            for service, proxy, path in paths:
                response = proxy.get(path, raise_exc=False)
                safety.require(response.status_code in (403, 404), 'foreign_' + service + '_not_denied')
                checks.append({'label': label, 'service': service, 'foreign_get_status': response.status_code})
            endpoint = cloud.object_store.get_endpoint().rstrip('/')
            own_project = state['projects'][label]['id']
            other_project = state['projects'][foreign]['id']
            safety.require(endpoint.endswith('/AUTH_' + own_project), 'unexpected_swift_scope')
            response = cloud.session.get(endpoint[:-len(own_project)] + other_project + '/' + target['container']['name'],
                                         raise_exc=False)
            safety.require(response.status_code in (403, 404), 'foreign_swift_not_denied')
            checks.append({'label': label, 'service': 'swift', 'foreign_get_status': response.status_code})
            quota = cloud.compute.get_quota_set(own_project)
            safety.require(quota.instances == 2 and quota.cores == 2 and quota.ram == 768,
                           'tenant_compute_quota_changed')
    # Probe only an invalid-image Nova write and an unchanged Cinder description.
    # A policy error must occur before a provider operation can change any data.
    from keystoneauth1 import session
    from keystoneauth1.identity import v3
    from openstack.connection import Connection
    for label in ('a', 'b'):
        project = state['projects'][label]['id']
        auth = v3.Password(auth_url=common.AUTH_URL, user_id=state['collector_id'],
                            password=private['collector_password'], project_id=project)
        with Connection(session=session.Session(auth=auth, timeout=20), region_name='RegionOne') as cloud:
            endpoint = cloud.object_store.get_endpoint().rstrip('/')
            response = cloud.session.head(endpoint, raise_exc=False)
            safety.require(response.status_code == 204, 'collector_account_head_failed')
            statuses = {}
            for name, method, path in [('swift_body', 'get', endpoint),
                                       ('swift_container', 'head', endpoint + '/' + state['resources'][label]['container']['name']),
                                       ('swift_write', 'post', endpoint)]:
                result = getattr(cloud.session, method)(path, raise_exc=False)
                safety.require(result.status_code == 403, 'collector_' + name + '_not_denied')
                statuses[name] = result.status_code
            result = cloud.compute.post('/servers', json={'server': {
                'name': 'invalid-image-no-resource', 'imageRef': '00000000-0000-0000-0000-000000000000',
                'flavorRef': state['browser_flavor_id']}}, raise_exc=False)
            safety.require(result.status_code == 403, 'collector_compute_write_not_denied')
            statuses['nova_write'] = result.status_code
            result = cloud.block_storage.put('/volumes/' + state['resources'][label]['volume']['id'],
                json={'volume': {'description': state['marker']}}, raise_exc=False)
            safety.require(result.status_code == 403, 'collector_cinder_write_not_denied')
            statuses['cinder_write'] = result.status_code
            checks.append({'label': label, 'collector_account_head_status': response.status_code,
                           'collector_denials': statuses})
    state['provider_isolation_checks'] = checks
    tenants.save(state)
    print(json.dumps({'phase': 'provider_scope_and_collector_verified', 'checks': checks}), flush=True)


def billing(br, state, private):
    context = browser.local_context(br)
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    try:
        page = context.new_page()
        browser.login(page, 'admin', common.read_secrets(common.SECRET_FILE)['ADMIN_PASS'])
        page.goto(browser.URL + '/horizon/admin/poc_billing/', wait_until='networkidle')
        if not state.get('browser_demo_rate'):
            safety.require(RATE_VERSION not in page.locator('body').inner_text(), 'unrecorded_demo_rate_exists')
            form = page.locator('#poc-rate-form')
            form.locator('[name=version]').fill(RATE_VERSION)
            form.locator('[name=currency]').select_option('INR')
            form.locator('[name=effective_from]').fill(month + '-01')
            for field, value in [('vcpu_rate', '1'), ('memory_rate', '0.001'), ('disk_rate', '0.1')]:
                form.locator('[name=' + field + ']').fill(value)
            resources.intent(state, 'admin', 'rate', RATE_VERSION)
            form.get_by_role('button', name='Save demo rates').click()
            page.wait_for_load_state('networkidle')
            safety.require('Demo rate version saved' in page.locator('body').inner_text(), 'browser_rate_not_confirmed')
            state['browser_demo_rate'] = {'version': RATE_VERSION, 'currency': 'INR',
                'demo_only': True, 'vcpu_hour': '1', 'memory_mib_hour': '0.001', 'disk_gib_hour': '0.1'}
            tenants.save(state)
        for label in ('a', 'b'):
            if label in state.get('browser_statements', {}):
                continue
            form = page.locator('#poc-statement-form')
            form.locator('[name=project_id]').select_option(state['projects'][label]['id'])
            form.locator('[name=month]').fill(month)
            form.locator('[name=rate_version]').select_option(RATE_VERSION)
            resources.intent(state, label, 'statement', month)
            form.get_by_role('button', name='Collect and issue statement').click()
            page.wait_for_load_state('networkidle')
            safety.require('Project statement saved' in page.locator('body').inner_text(), 'browser_statement_not_confirmed')
            project = state['projects'][label]['id']
            link = page.get_by_role('link', name='JSON', exact=True)
            hrefs = link.evaluate_all('xs=>xs.map(x=>x.getAttribute("href"))')
            matches = [url for url in hrefs if '/' + project + '/' in url]
            safety.require(len(matches) == 1, 'browser_statement_link_ambiguous')
            response = context.request.get(browser.URL + matches[0])
            safety.require(response.status == 200, 'admin_statement_download_failed')
            data = response.json()
            safety.require(data['project_id'] == project and data['status'] == 'provisional'
                           and data['total'] is None and len(data['lines']) > 0
                           and data['rate_card_version'] == RATE_VERSION, 'provisional_statement_mismatch')
            state.setdefault('browser_statements', {})[label] = {
                'id': data['statement_id'], 'status': data['status'], 'line_count': len(data['lines']),
                'total': data['total'], 'created_through_browser': True}
            tenants.save(state)
    finally:
        context.close()
    checks = []
    for label, other in (('a', 'b'), ('b', 'a')):
        context = browser.local_context(br)
        try:
            page = context.new_page()
            browser.login(page, private['tenants'][label]['username'], private['tenants'][label]['password'])
            page.goto(browser.URL + '/horizon/project/poc_billing/', wait_until='networkidle')
            page.get_by_role('button', name='Refresh provider usage').click()
            page.wait_for_load_state('networkidle')
            safety.require('Provider usage refreshed' in page.locator('body').inner_text(), 'tenant_usage_refresh_failed')
            inventory = page.locator('table').first.locator('tbody td').all_text_contents()
            safety.require([value.strip() for value in inventory] == ['1', '1', '1', '40', '1'],
                           'browser_inventory_provider_reconciliation_failed')
            for fmt in ('csv', 'json'):
                link = page.get_by_role('link', name=fmt.upper(), exact=True)
                safety.require(link.count() == 1, 'tenant_download_link_ambiguous')
                response = context.request.get(browser.URL + link.get_attribute('href'))
                safety.require(response.status == 200, 'tenant_statement_download_failed')
                safety.require(state['projects'][label]['id'] in response.text()
                               and state['projects'][other]['id'] not in response.text(), 'tenant_download_scope_mismatch')
                if fmt == 'json':
                    data = response.json()
                    safety.require(len(data['lines']) == 3 and data['status'] == 'provisional'
                                   and data['total'] is None, 'tenant_statement_coverage_mismatch')
                    for line in data['lines']:
                        safety.require(line['resource_id'] == state['resources'][label]['server']['id'],
                                       'tenant_statement_foreign_resource')
                        expected = (Decimal(line['quantity']) * Decimal(line['rate_per_unit'])).quantize(
                            Decimal('0.01'), rounding=ROUND_HALF_UP)
                        safety.require(Decimal(line['amount']) == expected, 'statement_decimal_charge_mismatch')
                    own = link.get_attribute('href')
                    foreign = own.replace(state['browser_statements'][label]['id'], state['browser_statements'][other]['id'])
                    denied = context.request.get(browser.URL + foreign)
                    safety.require(denied.status == 404, 'foreign_statement_not_hidden')
            checks.append({'label': label, 'usage_refresh': True, 'csv_json_http': 200,
                           'foreign_statement_http': 404, 'decimal_lines_verified': 3,
                           'inventory_counts': {'instances': 1, 'volumes': 1, 'volume_gib': 1,
                                                'object_bytes': 40, 'object_count': 1}})
        finally:
            context.close()
    state['browser_billing_checks'] = checks
    tenants.save(state)
    print(json.dumps({'phase': 'browser_provisional_statements_verified', 'checks': checks,
                      'demo_rates': state['browser_demo_rate']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', choices=('console', 'isolation', 'billing', 'management', 'quota', 'provider-quotas'), required=True)
    args = parser.parse_args()
    safety.host_guard(); os.umask(0o077); logging.disable(logging.CRITICAL)
    state, private = tenants.load()
    if args.check == 'isolation':
        isolation(state, private)
    elif args.check == 'provider-quotas':
        provider_quotas(state, private)
    else:
        # Playwright's API request client is a separate TLS client from the
        # Chromium page. Trust the existing constrained CA without bypassing
        # certificate validation for statement downloads.
        os.environ['NODE_EXTRA_CA_CERTS'] = '/etc/ssl/certs/horizon-poc-local-ca.crt'
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = browser.browser_launch(pw)
            try:
                {'console': consoles, 'billing': billing, 'management': management,
                 'quota': quota_feedback}[args.check](br, state, private)
            finally:
                br.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print(json.dumps({'phase': 'browser_workload_check_failed', 'reason': reason,
                          'automatic_submission_replay': False}), flush=True)
        raise SystemExit(2)
