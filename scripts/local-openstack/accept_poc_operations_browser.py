#!/usr/bin/python3
"""Verify admin-only operations dashboard and tenant-side denial in a real browser."""
import json
import logging

import browser_poc as browser
import continue_poc as task
import configure_nova as common
import manage_cinder_loop as safety


def login_dom(page, username, password):
    response = page.goto(browser.URL + '/horizon/auth/login/', wait_until='domcontentloaded', timeout=60000)
    safety.require(response is not None and response.status == 200, 'operations_browser_login_page_failed')
    page.locator('input[name=username]').fill(username)
    page.locator('input[name=password]').fill(password)
    if page.locator('input[name=domain]').count():
        page.locator('input[name=domain]').fill('Default')
    page.locator('button[type=submit]').click()
    page.wait_for_url(lambda url: '/auth/login' not in str(url), timeout=45000)
    page.wait_for_load_state('domcontentloaded', timeout=30000)


def apply(source, private):
    from playwright.sync_api import expect, sync_playwright
    with sync_playwright() as pw:
        br = browser.browser_launch(pw)
        try:
            admin = browser.local_context(br)
            page = admin.new_page()
            secrets = common.read_secrets(common.SECRET_FILE)
            login_dom(page, 'admin', secrets['ADMIN_PASS'])
            print(json.dumps({'phase': 'operations_admin_login_complete'}), flush=True)
            response = page.goto(browser.URL + '/horizon/admin/poc_billing/operations/',
                                 wait_until='domcontentloaded', timeout=60000)
            print(json.dumps({'phase': 'operations_admin_page_loaded', 'http_status': response.status if response else None,
                              'title': page.title()}), flush=True)
            safety.require(response is not None and response.status == 200, 'admin_operations_dashboard_http_failed')
            expect(page.locator('#active-alerts')).to_be_visible()
            rows = page.locator('tbody tr').count()
            print(json.dumps({'phase': 'operations_admin_dashboard_rendered', 'probe_rows': rows}), flush=True)
            safety.require(rows >= 20, 'operations_dashboard_probe_rows_missing')
            safety.require('90 daily rotations' in page.locator('body').inner_text(), 'operations_retention_text_missing')
            admin.close()

            tenant = browser.local_context(br)
            page = tenant.new_page()
            login_dom(page, private['tenants']['a']['username'], private['tenants']['a']['password'])
            print(json.dumps({'phase': 'operations_tenant_login_complete'}), flush=True)
            response = page.goto(browser.URL + '/horizon/admin/poc_billing/operations/',
                                 wait_until='domcontentloaded', timeout=60000)
            print(json.dumps({'phase': 'operations_tenant_page_loaded', 'http_status': response.status if response else None}), flush=True)
            safety.require(response is not None and response.status == 403,
                           'tenant_access_to_admin_operations_not_denied')
            tenant.close()
        finally:
            br.close()
    state = task.read('operations-install', source)
    state['admin_browser_dashboard'] = {'http_status': 200, 'probe_rows': rows,
                                        'tenant_a_admin_access_status': 403}
    task.save('operations-install', state)
    print(json.dumps({'phase': 'browser_admin_operations_view_and_tenant_denial_verified',
                      'admin_http_status': 200, 'probe_rows': rows, 'tenant_http_status': 403}), flush=True)


if __name__ == '__main__':
    try:
        logging.disable(logging.CRITICAL)
        source, private = task.initialize()
        with task.locked():
            apply(source, private)
    except Exception as error:
        print(json.dumps({'phase': 'operations_browser_acceptance_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
