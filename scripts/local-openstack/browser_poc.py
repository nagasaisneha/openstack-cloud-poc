#!/usr/bin/python3
"""Real local Horizon browser checks with private runtime credentials.

Only --create-identities submits the two marked project/user creation forms.
No credentials, cookie values, console tokens or private keys are printed.
Chromium pins the already CA-verified local server key for isolated testing;
Windows/browser trust is checked separately. Browser egress is localhost-only.
"""
import argparse
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit

import configure_nova as common
import manage_cinder_loop as safety
import prepare_browser_tenants as tenants

URL = 'https://localhost:8443'


def browser_launch(playwright):
    public = subprocess.run(['openssl', 'x509', '-in', '/etc/ssl/certs/horizon-poc-local.crt',
                              '-pubkey', '-noout'], capture_output=True, check=True).stdout
    der = subprocess.run(['openssl', 'pkey', '-pubin', '-outform', 'DER'], input=public,
                          capture_output=True, check=True).stdout
    pin = base64.b64encode(hashlib.sha256(der).digest()).decode()
    return playwright.chromium.launch(args=['--ignore-certificate-errors-spki-list=' + pin])


def local_context(browser):
    context = browser.new_context(accept_downloads=True)
    def route(handler):
        target = urlsplit(handler.request.url)
        if target.scheme == 'https' and target.hostname in ('localhost', '127.0.0.1') and target.port == 8443:
            handler.continue_()
        else:
            handler.abort()
    context.route('**/*', route)
    return context


def login(page, username, password):
    page.goto(URL + '/horizon/auth/login/', wait_until='networkidle')
    page.locator('input[name=username]').fill(username)
    page.locator('input[name=password]').fill(password)
    if page.locator('input[name=domain]').count():
        page.locator('input[name=domain]').fill('Default')
    page.locator('button[type=submit]').click()
    page.wait_for_url(lambda url: '/auth/login' not in str(url), timeout=45000)
    page.wait_for_load_state('networkidle')


def fields(page):
    return page.locator('input, textarea, select').evaluate_all('''items => items.map(x => ({
        name: x.name, type: x.type, required: x.required,
        options: x.tagName === 'SELECT' ? Array.from(x.options).map(o => ({text:o.text, value:o.value})) : undefined
    }))''')


def inspect_identity_forms(page):
    for path in ('/horizon/identity/create', '/horizon/identity/users/create/'):
        response = page.goto(URL + path, wait_until='networkidle')
        print(json.dumps({'path': path, 'http_status': response.status, 'title': page.title(),
                          'fields': fields(page),
                          'buttons': page.get_by_role('button').all_text_contents()}, sort_keys=True))


def inspect_resource_forms(browser, private):
    context = local_context(browser)
    try:
        page = context.new_page()
        login(page, private['tenants']['a']['username'], private['tenants']['a']['password'])
        for path in ('/horizon/project/networks/create', '/horizon/project/volumes/create/',
                     '/horizon/project/key_pairs/', '/horizon/project/containers/', '/horizon/project/instances/'):
            response = page.goto(URL + path, wait_until='networkidle')
            print(json.dumps({'path': path, 'http_status': response.status, 'title': page.title(),
                              'fields': fields(page), 'buttons': page.get_by_role('button').all_text_contents(),
                              'actions': page.locator('a.btn').evaluate_all('items => items.map(x => ({text: x.innerText.trim(), id: x.id, href: x.getAttribute("href")}))')}, sort_keys=True))
            if path.endswith('/instances/'):
                page.get_by_text('Launch Instance', exact=True).first.click()
                page.get_by_role('dialog').wait_for()
                print(json.dumps({'phase': 'launch_form', 'fields': fields(page),
                                  'text': page.get_by_role('dialog').inner_text()[:5000]}))
    finally:
        context.close()


def wait_resource(find):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        resource = find()
        if resource is not None:
            return resource
        time.sleep(1)
    raise safety.GuardError('browser_submission_not_confirmed_by_provider')


def create_identities(page, state, private):
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        for label, name in tenants.PROJECTS.items():
            existing = cloud.identity.find_project(name, domain_id='default')
            if label in state['projects']:
                tenants.owned(existing, name, state['marker'], state['projects'][label]['id'])
                continue
            safety.require(existing is None, 'unrecorded_existing_browser_project')
            safety.require(label not in state.get('project_submission_intents', {}),
                           'project_submission_uncertain_no_replay')
            state.setdefault('project_submission_intents', {})[label] = safety.now()
            tenants.save(state)
            page.goto(URL + '/horizon/identity/create', wait_until='networkidle')
            page.locator('input[name="name"], input[name$="-name"]').fill(name)
            page.locator('textarea[name="description"], textarea[name$="-description"]').fill(state['marker'])
            page.get_by_role('button', name=re.compile(r'^Create Project$')).click()
            resource = wait_resource(lambda: cloud.identity.find_project(name, domain_id='default'))
            tenants.owned(resource, name, state['marker'])
            state['projects'][label] = {'id': resource.id, 'name': resource.name,
                                        'created_through_browser': True}
            tenants.save(state)
            print(json.dumps({'phase': 'browser_project_created', 'label': label, 'project_id': resource.id}))
        for label, name in tenants.USERS.items():
            existing = cloud.identity.find_user(name, domain_id='default')
            if label in state['users']:
                tenants.owned(existing, name, state['marker'], state['users'][label]['id'])
                continue
            safety.require(existing is None, 'unrecorded_existing_browser_user')
            safety.require(label not in state.get('user_submission_intents', {}),
                           'user_submission_uncertain_no_replay')
            state.setdefault('user_submission_intents', {})[label] = safety.now()
            tenants.save(state)
            page.goto(URL + '/horizon/identity/users/create/', wait_until='networkidle')
            page.locator('input[name=name]').fill(name)
            page.locator('textarea[name=description]').fill(state['marker'])
            page.locator('input[name=password]').fill(private['tenants'][label]['password'])
            page.locator('input[name=confirm_password]').fill(private['tenants'][label]['password'])
            page.locator('select[name=project]').select_option(value=state['projects'][label]['id'], force=True)
            page.locator('select[name=role_id]').select_option(label='member', force=True)
            page.get_by_role('button', name=re.compile(r'^Create User$')).click()
            resource = wait_resource(lambda: cloud.identity.find_user(name, domain_id='default'))
            tenants.owned(resource, name, state['marker'])
            safety.require(resource.default_project_id == state['projects'][label]['id'],
                           'browser_user_default_project_changed')
            state['users'][label] = {'id': resource.id, 'name': resource.name,
                                     'created_through_browser': True}
            tenants.save(state)
            print(json.dumps({'phase': 'browser_user_created', 'label': label, 'user_id': resource.id}))
    state['phase'] = 'browser_projects_and_users_created'
    tenants.save(state)


def verify_logins(browser, state, private):
    results = []
    for label in tenants.PROJECTS:
        context = local_context(browser)
        try:
            page = context.new_page()
            login(page, private['tenants'][label]['username'], private['tenants'][label]['password'])
            response = page.goto(URL + '/horizon/project/poc_billing/', wait_until='networkidle')
            print(json.dumps({'phase': 'tenant_billing_page', 'label': label,
                              'http_status': response.status, 'path': urlsplit(page.url).path,
                              'title': page.title()}))
            safety.require(response.status == 200, 'tenant_' + label + '_billing_http_' + str(response.status))
            safety.require(state['projects'][label]['id'] in page.locator('body').inner_text(),
                           'browser_project_scope_not_displayed')
            foreign = state['projects']['b' if label == 'a' else 'a']['id']
            response = page.goto(URL + '/horizon/project/poc_billing/?project_id=' + foreign)
            safety.require(response.status == 403, 'foreign_billing_project_not_denied')
            response = page.goto(URL + '/horizon/admin/poc_billing/', wait_until='networkidle')
            safety.require(response.status in (302, 401, 403) or '/admin/' not in urlsplit(page.url).path,
                           'tenant_admin_billing_access_not_denied')
            results.append({'label': label, 'login_verified': True, 'own_billing_http': 200,
                             'foreign_billing_http': 403, 'admin_billing_denied': True})
        finally:
            context.close()
    context = local_context(browser)
    try:
        page = context.new_page()
        login(page, 'admin', common.read_secrets(common.SECRET_FILE)['ADMIN_PASS'])
        response = page.goto(URL + '/horizon/admin/poc_billing/', wait_until='networkidle')
        safety.require(response.status == 200, 'admin_billing_http_' + str(response.status))
        safety.require(page.locator('#poc-rate-form').count() == 1,
                       'admin_rate_form_missing')
        results.append({'label': 'admin', 'login_verified': True, 'billing_admin_http': 200})
    finally:
        context.close()
    state['browser_login_checks'] = results
    tenants.save(state)
    print(json.dumps({'phase': 'tenant_browser_logins_and_scope_verified', 'results': results}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--inspect-identity-forms', action='store_true')
    mode.add_argument('--create-identities', action='store_true')
    mode.add_argument('--verify-logins', action='store_true')
    mode.add_argument('--inspect-resource-forms', action='store_true')
    args = parser.parse_args()
    safety.host_guard()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    state, private = tenants.load()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = browser_launch(playwright)
        try:
            if args.inspect_resource_forms:
                inspect_resource_forms(browser, private)
            elif args.verify_logins:
                verify_logins(browser, state, private)
            else:
                context = local_context(browser)
                page = context.new_page()
                login(page, 'admin', common.read_secrets(common.SECRET_FILE)['ADMIN_PASS'])
                if args.inspect_identity_forms:
                    inspect_identity_forms(page)
                else:
                    create_identities(page, state, private)
                context.close()
        finally:
            browser.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # Playwright call logs can include form arguments: never echo them.
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print(json.dumps({'phase': 'browser_check_failed', 'reason': reason,
                          'automatic_submission_replay': False}))
        raise SystemExit(2)
