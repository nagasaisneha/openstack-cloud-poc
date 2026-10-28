#!/usr/bin/python3
"""Issue one sealed-period demo statement through Horizon and verify all amounts."""
import argparse
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import os
from pathlib import Path
import re
import sqlite3

import browser_poc as browser
import configure_nova as common
import continue_poc as task
import manage_cinder_loop as safety
from accept_poc_operations_browser import login_dom

RATES = {'active_vcpu_rate': ('compute.active_vcpu_hours', '10'),
    'active_memory_rate': ('compute.active_memory_mib_hours', '.01'),
    'verified_disk_rate': ('compute.local_disk_gib_hours', '1'), 'volume_rate': ('storage.volume_gib_hours', '2'),
    'snapshot_rate': ('storage.snapshot_gib_hours', '3'), 'cpu_seconds_rate': ('compute.cpu_seconds', '.5'),
    'interface_bytes_rate': ('network.interface_bytes', '.0001'),
    'object_byte_hours_rate': ('storage.object_byte_hours', '.01')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    safety.require(re.fullmatch('[a-z0-9-]{1,32}', args.run_id), 'invalid_closed_bill_run_id')
    name = 't50-closed-billing-' + args.run_id
    logging.disable(logging.CRITICAL)
    os.environ['NODE_EXTRA_CA_CERTS'] = '/etc/ssl/certs/horizon-poc-local-ca.crt'
    source, private = task.initialize()
    project = source['projects']['a']['id']
    with task.locked():
        state = task.read(name, source)
        safety.require(state.get('phase') == 'closed_source_period_independently_verified', 'verified_closed_epoch_required')
        version = 'poc-closed-demo-20261028-' + args.run_id
        from openstack_cloud_poc.provider_usage import read_meter
        measured = read_meter(project, datetime.fromisoformat(state['start']), datetime.fromisoformat(state['end']))
        expected = {(row['resource_id'], row['metric']): Decimal(row['quantity']) for row in measured['batch']['samples']}
        safety.require(measured['batch']['complete'], 'closed_epoch_no_longer_certified')
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = browser.browser_launch(pw)
            try:
                context = browser.local_context(br)
                page = context.new_page()
                login_dom(page, 'admin', common.read_secrets(common.SECRET_FILE)['ADMIN_PASS'])
                page.goto(browser.URL + '/horizon/admin/poc_billing/', wait_until='domcontentloaded')
                if not state.get('closed_rate_created'):
                    safety.require(version not in page.locator('body').inner_text(), 'unrecorded_closed_rate_no_replay')
                    form = page.locator('#poc-rate-form')
                    form.locator('[name=version]').fill(version)
                    form.locator('[name=currency]').select_option('INR')
                    form.locator('[name=effective_from]').fill(state['start'][:10])
                    for field, (metric, rate) in RATES.items():
                        form.locator('[name=' + field + ']').fill(rate)
                    task.intent(name, state, 'closed-rate', 'native_create_new_eight_meter_closed_demo_rate')
                    form.get_by_role('button', name='Save demo rates', exact=True).click()
                    page.wait_for_load_state('domcontentloaded')
                    safety.require('Demo rate version saved' in page.locator('body').inner_text(), 'closed_rate_save_failed')
                    state['closed_rate_created'] = version
                    task.save(name, state)
                if not state.get('closed_statement'):
                    before = set(page.get_by_role('link', name='JSON', exact=True).evaluate_all('xs=>xs.map(x=>x.getAttribute("href"))'))
                    form = page.locator('#poc-statement-form')
                    form.locator('[name=project_id]').select_option(project)
                    form.locator('[name=rate_version]').select_option(version)
                    form.locator('[name=month]').fill('')
                    form.locator('[name=period_start]').fill(state['start'])
                    form.locator('[name=period_end]').fill(state['end'])
                    if 'closed-statement' not in state['intents']:
                        task.intent(name, state, 'closed-statement', 'native_issue_only_verified_closed_demo_period')
                    else:
                        with sqlite3.connect('file:/var/lib/openstack-poc-billing/billing.sqlite?mode=ro', uri=True) as db:
                            count = db.execute('SELECT count(*) FROM statements WHERE project_id=? AND period_start=? AND period_end=?',
                                               (project, state['start'], state['end'])).fetchone()[0]
                        safety.require(count == 0, 'existing_closed_statement_requires_reconciliation')
                        state.setdefault('reconciliations', []).append({'operation': 'closed_statement',
                            'exact_period_ledger_rows_before_retry': 0, 'prior_rejection': 'calendar_month_domain_guard',
                            'at': safety.now()})
                        task.save(name, state)
                    form.get_by_role('button', name='Collect and issue statement', exact=True).click()
                    page.wait_for_load_state('domcontentloaded')
                    after = set(page.get_by_role('link', name='JSON', exact=True).evaluate_all('xs=>xs.map(x=>x.getAttribute("href"))'))
                    paths = list(after - before)
                    if not paths:
                        body = page.locator('body').inner_text()
                        print(json.dumps({'phase': 'closed_statement_submission_diagnostic',
                            'errors': page.locator('.errorlist,.alert-danger').all_inner_texts(),
                            'plain_response': body if len(body) < 400 else None}), flush=True)
                    safety.require(len(paths) == 1 and '/' + project + '/' in paths[0], 'closed_statement_identity_ambiguous')
                    data = context.request.get(browser.URL + paths[0]).json()
                    safety.require(data['status'] == 'complete' and data['total'] is not None
                                   and not data['missing_rates'] and not data['incomplete_reasons'], 'closed_statement_incomplete')
                    prices = {metric: Decimal(rate) for metric, rate in RATES.values()}
                    total = Decimal(0)
                    for line in data['lines']:
                        quantity = expected[(line['resource_id'], line['metric'])]
                        amount = (quantity * prices[line['metric']]).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                        safety.require(Decimal(line['quantity']) == quantity and Decimal(line['rate_per_unit']) == prices[line['metric']]
                                       and Decimal(line['amount']) == amount, 'closed_bill_independent_line_mismatch')
                        total += amount
                    safety.require(Decimal(data['total']) == total and {line['metric'] for line in data['lines']} == set(prices),
                                   'closed_bill_independent_total_or_meter_mismatch')
                    state['closed_statement'] = {'id': data['statement_id'], 'status': 'complete', 'total': str(total),
                        'currency': data['currency'], 'lines': len(data['lines']), 'eight_meters_verified': True,
                        'decimal_lines_and_total_verified': True, 'admin_json_path': paths[0]}
                    task.save(name, state)
                # Reverify the persisted immutable result on every readback,
                # including resumed checks after a service deployment.
                response = context.request.get(browser.URL + state['closed_statement']['admin_json_path'])
                safety.require(response.status == 200, 'saved_closed_statement_read_failed')
                saved = response.json()
                prices = {metric: Decimal(rate) for metric, rate in RATES.values()}
                total = Decimal(0)
                for line in saved['lines']:
                    quantity = expected[(line['resource_id'], line['metric'])]
                    amount = (quantity * prices[line['metric']]).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                    safety.require(Decimal(line['quantity']) == quantity and Decimal(line['rate_per_unit']) == prices[line['metric']]
                                   and Decimal(line['amount']) == amount, 'saved_closed_line_mismatch')
                    total += amount
                safety.require(saved['status'] == 'complete' and Decimal(saved['total']) == total,
                               'saved_closed_total_mismatch')
                state['rated_lines'] = saved['lines']
                row = page.locator('tr').filter(has=page.locator('a[href*="' + saved['statement_id'] + '"]')).first
                safety.require(state['start'] in row.inner_text() and state['end'] in row.inner_text()
                               and 'complete' in row.inner_text(), 'admin_closed_interval_not_visible')
                history = task.read('verified-billing', source)
                for old in history['statements'].values():
                    data = context.request.get(browser.URL + old['admin_json_path']).json()
                    safety.require(data['status'] == 'provisional' and data['total'] is None, 'historical_statement_changed')
                context.close()
                identifier = state['closed_statement']['id']
                for label in ('a', 'b'):
                    context = browser.local_context(br)
                    page = context.new_page()
                    login_dom(page, private['tenants'][label]['username'], private['tenants'][label]['password'])
                    if label == 'a':
                        page.goto(browser.URL + '/horizon/project/poc_billing/', wait_until='domcontentloaded')
                        row = page.locator('tr').filter(has=page.locator('a[href*="' + identifier + '"]')).first
                        safety.require(state['start'] in row.inner_text() and state['end'] in row.inner_text()
                            and 'complete' in row.inner_text(), 'tenant_closed_interval_not_visible')
                        for format in ('json', 'csv'):
                            response = context.request.get(browser.URL + '/horizon/project/poc_billing/statements/' + identifier + '/' + format + '/')
                            safety.require(response.status == 200, 'closed_tenant_download_failed')
                    else:
                        response = context.request.get(browser.URL + '/horizon/project/poc_billing/statements/' + identifier + '/json/')
                        safety.require(response.status == 404, 'closed_foreign_statement_leaked')
                    context.close()
                state['closed_statement'].update(tenant_a_csv_json_http=200, tenant_b_foreign_http=404,
                    historical_statements_unchanged=True, exact_interval_visible_in_admin_and_tenant_browser=True)
                task.save(name, state)
            finally:
                br.close()
        summary = {'schema': 1, 'start': state['start'], 'end': state['end'], 'sealed_period': state['sealed_period'],
            'measurements': state['checks'], 'statement': {key: value for key, value in state['closed_statement'].items()
                if key not in ('id', 'admin_json_path')}, 'pricing': {metric: rate for metric, rate in RATES.values()},
            'policy': measured['measurement_policy'], 'historical_backfill': False, 'payment_executed': False}
        summary['source_evidence'] = measured['closed_evidence']
        summary['rated_lines'] = state['rated_lines']
        output = Path(__file__).resolve().parents[2] / 'docs/evidence/closed-billing-period-20261028.json'
        output.write_text(json.dumps(summary, indent=2) + '\n')
        output.chmod(0o644)
        print(json.dumps({'phase': 'verified_closed_demo_browser_statement', 'start': state['start'], 'end': state['end'],
            'statement': summary['statement'], 'evidence_file': str(output)}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'closed_billing_browser_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
