#!/usr/bin/python3
"""Reconcile source records and provisional browser statements independently.

Never changes provider data or finalizes an invoice. Existing cards/statements
remain immutable. New demonstration rates have explicit observed-meter units.
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import os
import sqlite3

import browser_poc as browser
import browser_resources as controls
import configure_nova as common
import continue_poc as task
import manage_cinder_loop as safety
from openstack_cloud_poc.poc_metering import month_bounds
from openstack_cloud_poc.provider_usage import read_meter

VERSION = 'poc-measured-demo-20261028'
RATES = {'active_vcpu_rate': '1', 'active_memory_rate': '0.001', 'verified_disk_rate': '0.1',
         'volume_rate': '0.2', 'snapshot_rate': '0.3', 'cpu_seconds_rate': '0.0001',
         'interface_bytes_rate': '0.00000001'}


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def elapsed(a, b):
    delta = instant(b) - instant(a)
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000


def reconcile(source):
    start, end = month_bounds(datetime.now(timezone.utc))
    project = source['projects']['a']['id']
    measured = read_meter(project, start, end)
    samples = {(row['resource_id'], row['metric']): Decimal(row['quantity']) for row in measured['batch']['samples']}
    db = sqlite3.connect('file:/var/lib/openstack-poc-meter/evidence.sqlite?mode=ro', uri=True)
    try:
        raw = json.loads(db.execute('SELECT payload FROM snapshots WHERE project_id=? AND observed_at=?',
                                   (project, measured['latest_source_timestamp'])).fetchone()[0])
        known = task.read('lifecycle-a', source)
        resource = known['resources']['server']['id']
        actions = {item['id']: item['action'] for item in raw['actions'] if item['instance_uuid'] == resource}
        boundaries = {}
        for event in raw['events']:
            if event['action_id'] in actions and event['result'] == 'Success' and event['event'].startswith('compute_'):
                kind = actions[event['action_id']]
                safety.require(kind not in boundaries, 'multiple_test_transition_events_require_review')
                boundaries[kind] = event['finish_time']
        safety.require({'create', 'stop', 'start', 'delete'} <= boundaries.keys(), 'closed_test_lifecycle_not_available')
        # This is a separate four-boundary calculation, not the meter compiler.
        active = elapsed(boundaries['create'], boundaries['stop']) + elapsed(boundaries['start'], boundaries['delete'])
        stopped = elapsed(boundaries['stop'], boundaries['start'])
        safety.require(stopped == 8 and samples[(resource, 'compute.active_vcpu_hours')] == active / 3600
                       and samples[(resource, 'compute.active_memory_mib_hours')] == active * 256 / 3600,
                       'independent_active_time_reconciliation_failed')
        for kind, table, metric in [('volume', 'volumes', 'storage.volume_gib_hours'),
                                    ('snapshot', 'snapshots', 'storage.snapshot_gib_hours')]:
            identifier = known['resources'][kind]['id']
            row = next(item for item in raw[table] if item['id'] == identifier)
            safety.require(row['deleted_at'] and samples[(identifier, metric)] == elapsed(row['created_at'], row['deleted_at']) / 3600,
                           'independent_storage_lifetime_reconciliation_failed')
        # Compare deltas to the independently grouped first/last raw counters.
        primary = source['resources']['a']['server']['id']
        groups = {}
        for row in db.execute('SELECT payload FROM snapshots WHERE project_id=? AND observed_at>=? AND observed_at<=? '
                              'ORDER BY observed_at', (project, start.isoformat(), measured['latest_source_timestamp'])):
            for counter in json.loads(row[0]).get('counters', []):
                if counter['resource_id'] == primary:
                    groups.setdefault(counter['generation'], []).append(counter)
        cpu, network = 0, 0
        for group in groups.values():
            # Detect resets explicitly; never turn them into negative usage.
            pieces, begin, previous = [], group[0], group[0]
            for counter in group[1:]:
                if counter['cpu_ns'] < previous['cpu_ns'] or counter['network_bytes'] < previous['network_bytes']:
                    pieces.append((begin, previous))
                    begin = counter
                previous = counter
            pieces.append((begin, previous))
            for first, last in pieces:
                cpu += last['cpu_ns'] - first['cpu_ns']
                network += last['network_bytes'] - first['network_bytes']
        safety.require(samples[(primary, 'compute.cpu_seconds')] == Decimal(cpu) / 1000000000
                       and samples[(primary, 'network.interface_bytes')] == network,
                       'independent_raw_counter_reconciliation_failed')
    finally:
        db.close()
    return {'active_seconds': str(active), 'excluded_stopped_seconds': str(stopped),
            'storage_lifetimes_verified': 2, 'cpu_counter_delta_ns': cpu, 'combined_interface_delta_bytes': network,
            'measurements_verified_for_observed_window': True, 'full_month_complete': False,
            'incomplete_reasons': measured['batch']['incomplete_reasons']}


def browser_statements(source, private, state):
    from playwright.sync_api import sync_playwright
    os.environ['NODE_EXTRA_CA_CERTS'] = '/etc/ssl/certs/horizon-poc-local-ca.crt'
    name = 'verified-billing'
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    with sync_playwright() as pw:
        br = browser.browser_launch(pw)
        try:
            context = browser.local_context(br)
            page = context.new_page()
            browser.login(page, 'admin', common.read_secrets(common.SECRET_FILE)['ADMIN_PASS'])
            page.goto(browser.URL + '/horizon/admin/poc_billing/', wait_until='networkidle')
            if not state.get('rate_created'):
                safety.require(VERSION not in page.locator('body').inner_text(), 'unrecorded_measured_rate_preserved')
                form = page.locator('#poc-rate-form')
                form.locator('[name=version]').fill(VERSION)
                form.locator('[name=currency]').select_option('INR')
                form.locator('[name=effective_from]').fill(month + '-01')
                for field, value in RATES.items():
                    form.locator('[name=' + field + ']').fill(value)
                task.intent(name, state, 'rate', 'native_create_new_immutable_measured_demo_rate')
                form.get_by_role('button', name='Save demo rates').click()
                page.wait_for_load_state('networkidle')
                safety.require('Demo rate version saved' in page.locator('body').inner_text(), 'measured_rate_not_saved')
                state['rate_created'] = VERSION
                task.save(name, state)
            for label in ('a', 'b'):
                if label in state.get('statements', {}):
                    continue
                project = source['projects'][label]['id']
                before = set(page.get_by_role('link', name='JSON', exact=True).evaluate_all('xs=>xs.map(x=>x.getAttribute("href"))'))
                form = page.locator('#poc-statement-form')
                form.locator('[name=project_id]').select_option(project)
                form.locator('[name=month]').fill(month)
                form.locator('[name=rate_version]').select_option(VERSION)
                task.intent(name, state, 'statement-' + label, 'native_issue_provisional_observed_meter_demo_statement')
                form.get_by_role('button', name='Collect and issue statement').click()
                page.wait_for_load_state('networkidle')
                safety.require('Project statement saved' in page.locator('body').inner_text(), 'measured_statement_not_saved')
                after = set(page.get_by_role('link', name='JSON', exact=True).evaluate_all('xs=>xs.map(x=>x.getAttribute("href"))'))
                links = list(after - before)
                safety.require(len(links) == 1 and '/' + project + '/' in links[0], 'new_statement_link_ambiguous')
                response = context.request.get(browser.URL + links[0])
                safety.require(response.status == 200, 'measured_admin_statement_download_failed')
                data = response.json()
                safety.require(data['project_id'] == project and data['rate_card_version'] == VERSION
                               and data['status'] == 'provisional' and data['total'] is None and data['lines'],
                               'measured_statement_not_provisional')
                for line in data['lines']:
                    expected = (Decimal(line['quantity']) * Decimal(line['rate_per_unit'])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                    safety.require(Decimal(line['amount']) == expected, 'independent_decimal_amount_mismatch')
                state.setdefault('statements', {})[label] = {'id': data['statement_id'], 'lines': len(data['lines']),
                    'metrics': sorted(set(item['metric'] for item in data['lines'])),
                    'admin_json_path': links[0], 'status': 'provisional', 'total': None, 'decimal_lines_verified': True}
                task.save(name, state)
            context.close()
            for label, other in (('a', 'b'), ('b', 'a')):
                context = browser.local_context(br)
                try:
                    page = context.new_page()
                    browser.login(page, private['tenants'][label]['username'], private['tenants'][label]['password'])
                    page.goto(browser.URL + '/horizon/project/poc_billing/', wait_until='networkidle')
                    statement = state['statements'][label]['id']
                    links = page.locator('a[href*="' + statement + '"]')
                    safety.require(links.count() == 2, 'tenant_measured_statement_links_missing')
                    for link in links.all():
                        path = link.get_attribute('href')
                        response = context.request.get(browser.URL + path)
                        safety.require(response.status == 200 and source['projects'][other]['id'] not in response.text(),
                                       'measured_tenant_download_scope_failed')
                        foreign = path.replace(statement, state['statements'][other]['id'])
                        safety.require(context.request.get(browser.URL + foreign).status == 404, 'foreign_measured_statement_not_hidden')
                    state['statements'][label]['tenant_csv_json_verified'] = True
                    state['statements'][label]['foreign_download_http'] = 404
                    task.save(name, state)
                finally:
                    context.close()
        finally:
            br.close()


def main():
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    with task.locked():
        state = task.read('verified-billing', source)
        state['reconciliation'] = reconcile(source)
        task.save('verified-billing', state)
        print(json.dumps({'phase': 'source_measurements_independently_reconciled', **state['reconciliation']}), flush=True)
        browser_statements(source, private, state)
        state['phase'] = 'source_measurements_and_provisional_browser_calculations_verified'
        task.save('verified-billing', state)
        print(json.dumps({'phase': state['phase'], 'statements': state['statements']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'meter_billing_check_stopped', 'reason': str(error) if isinstance(error, safety.GuardError)
                          else type(error).__name__, 'statements_remain_provisional': True}), flush=True)
        raise SystemExit(2)
