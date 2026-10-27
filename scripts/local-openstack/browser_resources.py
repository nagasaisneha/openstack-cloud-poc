#!/usr/bin/python3
"""Create bounded sample resources through Horizon, with durable no-replay intents.

Only the two explicitly marked browser tenants are used. Provider reads confirm
each form submission. Existing resources, keys, guests and backups are preserved.
"""
import argparse
import json
import logging
import os
import re

import browser_poc as browser
import configure_nova as common
import manage_cinder_loop as safety
import prepare_browser_tenants as tenants


def connection(state, private, label):
    from keystoneauth1 import session
    from keystoneauth1.identity import v3
    from openstack.connection import Connection
    auth = v3.Password(auth_url=common.AUTH_URL, username=private['tenants'][label]['username'],
        password=private['tenants'][label]['password'], user_domain_id='default',
        project_id=state['projects'][label]['id'])
    return Connection(session=session.Session(auth=auth, timeout=20), region_name='RegionOne')


def intent(state, label, kind, name):
    key = label + ':' + kind
    safety.require(key not in state.get('resource_submission_intents', {}),
                   'resource_submission_uncertain_no_replay')
    state.setdefault('resource_submission_intents', {})[key] = {'name': name, 'at': safety.now()}
    tenants.save(state)


def record(state, label, kind, resource):
    safety.require(resource.project_id == state['projects'][label]['id'], 'resource_project_mismatch')
    state.setdefault('resources', {}).setdefault(label, {})[kind] = {
        'id': resource.id, 'name': resource.name, 'created_through_browser': True}
    tenants.save(state)
    print(json.dumps({'phase': 'browser_resource_created', 'label': label, 'kind': kind,
                      'id': resource.id}), flush=True)


def create_network(page, cloud, state, label):
    name = 'poc-browser-' + label + '-net'
    safety.require(cloud.network.find_network(name) is None, 'unrecorded_network_exists')
    page.goto(browser.URL + '/horizon/project/networks/create', wait_until='networkidle')
    page.locator('[name=net_name]').fill(name)
    page.get_by_role('button', name=re.compile(r'^Next')).click()
    page.locator('[name=subnet_name]').fill(name + '-subnet')
    page.locator('[name=cidr]').fill(state['network_cidrs'][label])
    page.get_by_role('button', name=re.compile(r'^Next')).click()
    # DHCP is enabled by the native wizard; no external/router network.
    safety.require(page.locator('[name=enable_dhcp]').is_checked(), 'dhcp_not_enabled')
    intent(state, label, 'network', name)
    page.get_by_role('button', name='Create', exact=True).click()
    network = browser.wait_resource(lambda: cloud.network.find_network(name))
    safety.require(not network.is_shared and not network.is_router_external, 'sample_network_not_private')
    record(state, label, 'network', network)
    subnet = browser.wait_resource(lambda: cloud.network.find_subnet(name + '-subnet'))
    safety.require(subnet.network_id == network.id and subnet.cidr == state['network_cidrs'][label]
                   and subnet.is_dhcp_enabled, 'browser_subnet_mismatch')
    record(state, label, 'subnet', subnet)


def create_volume(page, cloud, state, label):
    name = 'poc-browser-' + label + '-data'
    safety.require(cloud.block_storage.find_volume(name) is None, 'unrecorded_volume_exists')
    page.goto(browser.URL + '/horizon/project/volumes/create/', wait_until='networkidle')
    page.locator('[name=name]').fill(name)
    page.locator('[name=description]').fill(state['marker'])
    page.locator('[name=size]').fill('1')
    intent(state, label, 'volume', name)
    page.get_by_role('button', name='Create Volume', exact=True).click()
    volume = browser.wait_resource(lambda: cloud.block_storage.find_volume(name))
    volume = cloud.block_storage.wait_for_status(volume, status='available', failures=['error'], wait=90)
    # Cinder project_id is not populated in some SDK microversions.
    raw = cloud.block_storage.get('/volumes/' + volume.id, raise_exc=True).json()['volume']
    safety.require(raw.get('os-vol-tenant-attr:tenant_id', state['projects'][label]['id'])
                   == state['projects'][label]['id'] and volume.size == 1
                   and volume.description == state['marker'], 'browser_volume_mismatch')
    volume.project_id = state['projects'][label]['id']
    record(state, label, 'volume', volume)


def create_keypair(page, cloud, state, label):
    name = 'poc-browser-' + label + '-key'
    safety.require(cloud.compute.find_keypair(name) is None, 'unrecorded_keypair_exists')
    public = (tenants.DIRECTORY / ('id_ed25519_' + label + '.pub')).read_text().strip()
    page.goto(browser.URL + '/horizon/project/key_pairs/', wait_until='networkidle')
    page.get_by_text('Import Public Key', exact=True).first.click()
    dialog = page.get_by_role('dialog')
    dialog.locator('[name=name]').fill(name)
    dialog.locator('[name=key-type]').select_option(label='SSH Key', force=True)
    dialog.locator('[name=public-key]').fill(public)
    intent(state, label, 'keypair', name)
    dialog.locator('.modal-footer').get_by_text('Import Public Key', exact=True).click()
    key = browser.wait_resource(lambda: cloud.compute.find_keypair(name))
    safety.require(key.public_key.split()[:2] == public.split()[:2], 'imported_key_mismatch')
    # Keypairs are owned by the authenticated user, not a project object.
    state.setdefault('resources', {}).setdefault(label, {})['keypair'] = {
        'id': key.id, 'name': key.name, 'created_through_browser': True}
    tenants.save(state)
    print(json.dumps({'phase': 'browser_key_imported', 'label': label}), flush=True)


def create_container(page, cloud, state, label):
    name = 'poc-browser-' + label + '-private'
    def find():
        return next((item for item in cloud.object_store.containers(prefix=name) if item.name == name), None)
    safety.require(find() is None,
                   'unrecorded_container_exists')
    page.goto(browser.URL + '/horizon/project/containers/', wait_until='networkidle')
    page.get_by_text('Container', exact=True).first.click()
    dialog = page.get_by_role('dialog')
    dialog.locator('[name=name]').fill(name)
    dialog.get_by_text('Not public', exact=True).click()
    intent(state, label, 'container', name)
    dialog.locator('.modal-footer').get_by_text('Submit', exact=True).click()
    browser.wait_resource(find)
    meta = cloud.object_store.get_container_metadata(name)
    safety.require(not meta.read_ACL and not meta.write_ACL, 'sample_container_not_private')
    state.setdefault('resources', {}).setdefault(label, {})['container'] = {
        'id': name, 'name': name, 'created_through_browser': True}
    tenants.save(state)
    print(json.dumps({'phase': 'browser_private_container_created', 'label': label}), flush=True)


def allocate(dialog, name):
    row = dialog.locator('tr:visible').filter(has=dialog.page.get_by_text(name, exact=True))
    row.first.wait_for(state='visible', timeout=15000)
    safety.require(row.count() == 1, 'launch_selection_row_ambiguous')
    add = row.locator('button.fa-arrow-up')
    if add.count():
        add.click()
    safety.require(row.locator('button.fa-arrow-down').count() == 1,
                   'launch_selection_not_allocated')


def create_server(page, cloud, state, label):
    name = 'poc-browser-' + label + '-vm'
    resources = state['resources'][label]
    safety.require(all(key in resources for key in ('network', 'subnet', 'keypair')),
                   'browser_server_dependencies_missing')
    safety.require(cloud.compute.find_server(name) is None, 'unrecorded_server_exists')
    page.goto(browser.URL + '/horizon/project/instances/', wait_until='networkidle')
    page.locator('#instances__action_launch-ng').click()
    dialog = page.get_by_role('dialog')
    page.wait_for_load_state('networkidle')
    dialog.locator('[name=name]').fill(name)
    dialog.locator('[name=description]').fill(state['marker'])
    dialog.get_by_text('Source', exact=True).first.click()
    dialog.locator('[name=boot-source-type]').select_option(value='image', force=True)
    dialog.locator('label#vol-create').filter(has_text=re.compile(r'^No$')).click()
    allocate(dialog, state['catalog']['image_name'])
    dialog.get_by_text('Flavor', exact=True).first.click()
    allocate(dialog, state['catalog']['flavor_name'])
    dialog.get_by_text('Networks', exact=True).first.click()
    allocate(dialog, resources['network']['name'])
    dialog.get_by_text('Key Pair', exact=True).first.click()
    allocate(dialog, resources['keypair']['name'])
    safety.require(dialog.locator('button.finish').is_enabled(), 'launch_form_invalid')
    intent(state, label, 'server', name)
    dialog.locator('button.finish').click()
    server = browser.wait_resource(lambda: cloud.compute.find_server(name))
    record(state, label, 'server', server)
    server = cloud.compute.wait_for_server(server, status='ACTIVE', failures=['ERROR'], wait=180)
    safety.require(server.image['id'] == state['catalog']['image_id']
                   and server.key_name == resources['keypair']['name']
                   and server.description == state['marker'], 'browser_server_mismatch')
    state['resources'][label]['server'].update(status=server.status, image_id=server.image['id'],
                                             key_name=server.key_name)
    tenants.save(state)
    print(json.dumps({'phase': 'browser_vm_active', 'label': label, 'id': server.id}), flush=True)


def create_object(page, cloud, state, label):
    name = 'hello-' + label + '.txt'
    container = state['resources'][label]['container']['name']
    safety.require(not any(item.name == name for item in cloud.object_store.objects(container)),
                   'unrecorded_object_exists')
    content = ('OpenStack POC sample data for tenant ' + label + '.\n').encode()
    page.goto(browser.URL + '/horizon/project/containers/', wait_until='networkidle')
    page.get_by_text(container, exact=True).first.click()
    # Horizon's compact upload action uses an icon, rather than a text label.
    page.locator('button:has(.fa-upload), a.btn:has(.fa-upload)').first.click()
    dialog = page.get_by_role('dialog')
    dialog.locator('[name=file]').set_input_files({'name': name, 'mimeType': 'text/plain', 'buffer': content})
    dialog.locator('[name=name]').fill(name)
    page.wait_for_function("() => { const x = document.querySelector('.modal-footer .btn-primary'); return x && !x.disabled; }")
    print(json.dumps({'phase': 'object_upload_form_valid', 'label': label}), flush=True)
    intent(state, label, 'object', name)
    dialog.locator('.modal-footer .btn-primary').click()
    print(json.dumps({'phase': 'object_upload_submitted', 'label': label}), flush=True)
    browser.wait_resource(lambda: next((item for item in cloud.object_store.objects(container)
                                       if item.name == name), None))
    received = cloud.object_store.download_object(name, container=container)
    safety.require(received == content, 'browser_object_content_mismatch')
    state['resources'][label]['object'] = {'id': name, 'name': name,
        'bytes': len(content), 'created_through_browser': True, 'round_trip_verified': True}
    tenants.save(state)
    print(json.dumps({'phase': 'browser_object_round_trip_verified', 'label': label, 'bytes': len(content)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', choices=('a', 'b'), required=True)
    parser.add_argument('--resource', choices=('network', 'volume', 'keypair', 'container', 'server', 'object', 'all'), required=True)
    args = parser.parse_args()
    safety.host_guard(); os.umask(0o077); logging.disable(logging.CRITICAL)
    state, private = tenants.load()
    safety.require(args.resource == 'all' or args.resource not in state.get('resources', {}).get(args.label, {}),
                   'resource_already_recorded_no_replay')
    from playwright.sync_api import sync_playwright
    with connection(state, private, args.label) as cloud, sync_playwright() as pw:
        safety.require(cloud.session.get_project_id() == state['projects'][args.label]['id'], 'tenant_scope_mismatch')
        br = browser.browser_launch(pw)
        try:
            context = browser.local_context(br); page = context.new_page()
            browser.login(page, private['tenants'][args.label]['username'], private['tenants'][args.label]['password'])
            actions = {'network': create_network, 'volume': create_volume, 'keypair': create_keypair,
                       'container': create_container, 'server': create_server, 'object': create_object}
            for kind in actions if args.resource == 'all' else (args.resource,):
                if kind not in state.get('resources', {}).get(args.label, {}):
                    actions[kind](page, cloud, state, args.label)
            context.close()
        finally:
            br.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print(json.dumps({'phase': 'resource_browser_failed', 'reason': reason,
                          'automatic_submission_replay': False}), flush=True)
        raise SystemExit(2)
