#!/usr/bin/python3
"""Native Horizon lifecycle acceptance on newly marked disposable resources.

All source/retained VMs, volumes and objects are protected from lifecycle
mutations. Creates have durable no-replay intentions; provider reads confirm
results. Ordinary resource snapshots may be tested, never backup/restore drills.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import re
import time
import subprocess
from uuid import uuid4

import browser_poc as browser
import browser_resources as controls
import cinder_loopback_chap as chap
import configure_nova as common
import accept_cinder_volume as volume_guard
import continue_poc as task
import manage_cinder_loop as safety


def disposable(resource, source, state, label, kind):
    protected = {source['retained_vm_id']}
    protected.update(item['id'] for resources in source['resources'].values() for item in resources.values())
    safety.require(resource is not None and resource.id not in protected
                   and resource.id == state['resources'][kind]['id']
                   and resource.project_id == source['projects'][label]['id']
                   and resource.description == state['marker']
                   and resource.name.startswith(state['prefix']), 'disposable_resource_scope_changed')
    return resource


def remember(name, state, kind, resource):
    state['resources'][kind] = {'id': resource.id, 'name': resource.name, 'created_through_browser': True}
    task.save(name, state)
    print(json.dumps({'phase': 'browser_lifecycle_created', 'kind': kind, 'id': resource.id}), flush=True)


def create_vm(page, cloud, source, state, label, name):
    title = state['prefix'] + '-vm'
    safety.require(not state['resources'].get('server') and cloud.compute.find_server(title) is None,
                   'disposable_vm_already_exists_preserve')
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:')))
    safety.require(available >= 768 * 1024, 'insufficient_control_plane_memory_headroom')
    task.validate_tenant(cloud, source, label)
    page.goto(browser.URL + '/horizon/project/instances/', wait_until='networkidle')
    page.locator('#instances__action_launch-ng').click()
    dialog = page.get_by_role('dialog')
    page.wait_for_load_state('networkidle')
    dialog.locator('[name=name]').fill(title)
    dialog.locator('[name=description]').fill(state['marker'])
    dialog.get_by_text('Source', exact=True).first.click()
    dialog.locator('[name=boot-source-type]').select_option('image', force=True)
    dialog.locator('label#vol-create').filter(has_text=re.compile('^No$')).click()
    controls.allocate(dialog, source['catalog']['image_name'])
    dialog.get_by_text('Flavor', exact=True).first.click()
    controls.allocate(dialog, source['catalog']['flavor_name'])
    dialog.get_by_text('Networks', exact=True).first.click()
    controls.allocate(dialog, source['resources'][label]['network']['name'])
    dialog.get_by_text('Key Pair', exact=True).first.click()
    controls.allocate(dialog, source['resources'][label]['keypair']['name'])
    from playwright.sync_api import expect
    expect(dialog.locator('button.finish')).to_be_enabled()
    task.intent(name, state, 'server-create', 'native_launch_one_tiny_disposable_vm')
    dialog.locator('button.finish').click()
    vm = browser.wait_resource(lambda: cloud.compute.find_server(title))
    remember(name, state, 'server', vm)
    disposable(vm, source, state, label, 'server')
    cloud.compute.wait_for_server(vm, status='ACTIVE', failures=['ERROR'], wait=180)
    state['checks']['server_create'] = {'status': 'ACTIVE', 'through_browser': True}
    task.save(name, state)


def row_action(page, table, resource_id, action):
    row = page.locator('tr[data-object-id="' + resource_id + '"]')
    row.wait_for(state='visible')
    selected = row.locator('[id$="__action_' + action + '"]')
    safety.require(selected.count() == 1, 'browser_row_action_ambiguous_' + action)
    if not selected.is_visible():
        row.locator('.dropdown-toggle').click()
    selected.click()
    # Native table actions can require a confirmation modal. Its icon is part
    # of the accessible name, so select the semantic primary/danger control.
    try:
        # Horizon's native confirmation template has .modal but no ARIA
        # dialog role (the Angular launch wizard does have that role).
        dialog = page.locator('.modal:visible').last
        dialog.wait_for(state='visible', timeout=2500)
        submit = dialog.locator('.modal-footer .btn-danger, .modal-footer .btn-primary')
        safety.require(submit.count() == 1, 'browser_confirmation_ambiguous')
        submit.click()
    except Exception as error:
        from playwright.sync_api import TimeoutError
        if not isinstance(error, TimeoutError):
            raise


def vm_transition(page, cloud, source, state, label, name, action):
    vm = disposable(cloud.compute.get_server(state['resources']['server']['id']), source, state, label, 'server')
    expected_before, target = {'stop': ('ACTIVE', 'SHUTOFF'), 'start': ('SHUTOFF', 'ACTIVE')}[action]
    safety.require(vm.status == expected_before and vm.task_state is None, 'vm_not_settled_before_' + action)
    page.goto(browser.URL + '/horizon/project/instances/', wait_until='networkidle')
    task.intent(name, state, 'server-' + action, 'native_' + action + '_only_disposable_vm')
    row_action(page, 'instances', vm.id, action)
    vm = cloud.compute.wait_for_server(vm, status=target, failures=['ERROR'], wait=120)
    page.reload(wait_until='networkidle')
    row = page.locator('tr[data-object-id="' + vm.id + '"]')
    from playwright.sync_api import expect
    expect(row).to_contain_text('Shutoff' if target == 'SHUTOFF' else 'Active', ignore_case=True, timeout=20000)
    state['checks']['server_' + action] = {'provider_status': vm.status, 'browser_status_matched': True,
                                          'checked_at': safety.now()}
    capture_actions(cloud, vm.id, state)
    task.save(name, state)
    print(json.dumps({'phase': 'browser_lifecycle_' + action + '_verified', 'status': vm.status}), flush=True)


def capture_actions(cloud, server_id, state):
    response = cloud.compute.get('/servers/' + server_id + '/os-instance-actions',
                                  microversion='2.66', raise_exc=True).json()
    actions = []
    for item in response['instanceActions']:
        detail = cloud.compute.get('/servers/' + server_id + '/os-instance-actions/' + item['request_id'],
                                     microversion='2.66', raise_exc=True).json()['instanceAction']
        actions.append({key: detail.get(key) for key in ('action', 'request_id', 'start_time', 'instance_uuid')})
        actions[-1]['events'] = [{key: event.get(key) for key in ('event', 'start_time', 'finish_time', 'result')}
                                  for event in detail.get('events', [])]
    state['provider_actions'] = actions


def create_volume(page, cloud, source, state, label, name):
    title = state['prefix'] + '-volume'
    safety.require(not state['resources'].get('volume') and cloud.block_storage.find_volume(title) is None,
                   'disposable_volume_already_exists_preserve')
    page.goto(browser.URL + '/horizon/project/volumes/create/', wait_until='networkidle')
    page.locator('[name=name]').fill(title)
    page.locator('[name=description]').fill(state['marker'])
    page.locator('[name=size]').fill('1')
    task.intent(name, state, 'volume-create', 'native_create_empty_one_gib_disposable_volume')
    page.get_by_role('button', name='Create Volume').click()
    volume = browser.wait_resource(lambda: cloud.block_storage.find_volume(title))
    remember(name, state, 'volume', volume)
    volume = cloud.block_storage.wait_for_status(volume, status='available', failures=['error'], wait=120)
    safety.require(volume.size == 1 and volume.description == state['marker'], 'disposable_volume_drift')
    state['checks']['volume_create'] = {'provider_status': 'available', 'through_browser': True}
    task.save(name, state)


def prepare_volume_node(source, state, label):
    # Inspect privileged owner attributes before the sole host-side storage
    # exception. Never prepare a node for an original/sample volume.
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        volume = disposable(cloud.block_storage.get_volume(state['resources']['volume']['id']),
                            source, state, label, 'volume')
        safety.require(volume.status == 'available' and volume.size == 1 and not volume.attachments,
                       'disposable_volume_not_empty_detached_scope')
        cloud.block_storage.set_volume_metadata(volume, task49_run_id=state['prefix'])
        volume = cloud.block_storage.get_volume(volume.id)
        node_name = 'chap-' + label
        node = task.read(node_name, source)
        node.update(volume_id=volume.id, project_id=source['projects'][label]['id'],
                    run_id=state['prefix'], ownership_metadata_key='task49_run_id',
                    loopback_chap_md5=True, loopback_chap_algorithm='MD5')
        chap.prepare_node(volume, node, lambda data: task.save(node_name, data))


def attach_volume(page, cloud, source, state, label, name):
    vm = disposable(cloud.compute.get_server(state['resources']['server']['id']), source, state, label, 'server')
    prepare_volume_node(source, state, label)
    volume_id = state['resources']['volume']['id']
    page.goto(browser.URL + '/horizon/project/volumes/' + volume_id + '/attach/', wait_until='networkidle')
    page.locator('[name=instance]').select_option(vm.id, force=True)
    task.intent(name, state, 'volume-attach', 'native_attach_only_new_disposable_volume_to_new_disposable_vm')
    page.locator('input[type=submit]').click()
    volume = cloud.block_storage.wait_for_status(cloud.block_storage.get_volume(volume_id),
                                                status='in-use', failures=['error'], wait=120)
    attachments = list(cloud.compute.volume_attachments(vm))
    safety.require(len([x for x in attachments if x.volume_id == volume_id]) == 1
                   and any(x['server_id'] == vm.id for x in volume.attachments), 'browser_volume_attachment_mismatch')
    state['checks']['volume_attach'] = {'provider_status': 'in-use', 'through_browser': True,
                                        'loopback_chap_scope': 'only_new_volume_node', 'global_iscsi_defaults_changed': False}
    task.save(name, state)
    print(json.dumps({'phase': 'browser_disposable_volume_attached', 'status': volume.status}), flush=True)


def access_and_io(page, cloud, source, state, label, name):
    vm = disposable(cloud.compute.get_server(state['resources']['server']['id']), source, state, label, 'server')
    connectivity = task.read('connectivity', source)
    group = connectivity['resources'][label + '_access_group']['id']
    if not state['checks'].get('security_group_edit'):
        page.goto(browser.URL + '/horizon/project/instances/' + vm.id + '/update?step=update_security_groups',
                  wait_until='networkidle')
        selector = page.locator('[name=update_security_groups_role_member]')
        prior_ports = list(cloud.network.ports(device_id=vm.id))
        safety.require(len(prior_ports) == 1, 'disposable_vm_port_ambiguous')
        selected = prior_ports[0].security_group_ids
        selector.select_option(sorted(set(selected) | {group}), force=True)
        task.intent(name, state, 'guest-security-group-edit', 'native_add_only_owned_source_restricted_access_group')
        page.locator('input[type=submit]').click()
        page.wait_for_load_state('networkidle')
        ports = list(cloud.network.ports(device_id=vm.id))
        safety.require(len(ports) == 1 and set(selected) | {group} <= set(ports[0].security_group_ids),
                       'browser_security_group_edit_not_applied')
        state['checks']['security_group_edit'] = {'through_browser': True, 'existing_groups_preserved': True}
        task.save(name, state)
    description = state['marker'] + '/' + state['prefix']
    if 'floating_ip' not in state['resources']:
        safety.require(not any(item.description == description for item in cloud.network.ips()), 'unrecorded_test_floating_ip')
        page.goto(browser.URL + '/horizon/project/floating_ips/allocate/', wait_until='networkidle')
        page.locator('[name=pool]').select_option(connectivity['resources']['external_network']['id'], force=True)
        page.locator('[name=description]').fill(description)
        task.intent(name, state, 'floating-ip-create', 'native_allocate_only_disposable_floating_ip')
        page.locator('input[type=submit]').click()
        floating = browser.wait_resource(lambda: next((item for item in cloud.network.ips() if item.description == description), None))
        safety.require(floating.project_id == source['projects'][label]['id'], 'test_floating_ip_owner_mismatch')
        state['resources']['floating_ip'] = {'id': floating.id, 'address': floating.floating_ip_address,
                                             'description': description, 'created_through_browser': True}
        task.save(name, state)
    floating = cloud.network.get_ip(state['resources']['floating_ip']['id'])
    safety.require(floating.description == description and floating.project_id == source['projects'][label]['id'],
                   'test_floating_ip_scope_changed')
    ports = list(cloud.network.ports(device_id=vm.id))
    safety.require(len(ports) == 1, 'disposable_vm_port_ambiguous')
    if not floating.port_id:
        page.goto(browser.URL + '/horizon/project/floating_ips/associate/?instance_id=' + vm.id, wait_until='networkidle')
        page.locator('[name=ip_id]').select_option(floating.id, force=True)
        target = ports[0].id + '_' + ports[0].fixed_ips[0]['ip_address']
        page.locator('[name=port_id]').select_option(target, force=True)
        task.intent(name, state, 'floating-ip-associate', 'native_associate_only_disposable_guest_port')
        page.locator('input[type=submit]').click()
        page.wait_for_load_state('networkidle')
    safety.require(cloud.network.get_ip(floating.id).port_id == ports[0].id, 'browser_floating_ip_association_mismatch')
    hosts = task.DIRECTORY / ('known_hosts_' + state['prefix'])
    if not hosts.exists():
        descriptor = os.open(hosts, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
    safety.protected(hosts, 0o600)
    command = ['ssh', '-F', '/dev/null', '-b', '192.0.2.1', '-i',
        str(task.tenants.DIRECTORY / ('id_ed25519_' + label)), '-o', 'BatchMode=yes',
        '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
        '-o', 'IdentitiesOnly=yes', '-o', 'ProxyCommand=none', '-o', 'ForwardAgent=no',
        '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=' + str(hosts),
        '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=5',
        'cirros@' + floating.floating_ip_address, 'sh', '-s']
    deadline = time.monotonic() + 60
    while True:
        result = subprocess.run(command, input='wget -q -T 8 -O - http://169.254.169.254/openstack/latest/meta_data.json\n',
                                text=True, capture_output=True, timeout=20)
        if result.returncode == 0:
            safety.require(json.loads(result.stdout)['uuid'] == vm.id, 'disposable_ssh_guest_identity_mismatch')
            break
        safety.require(time.monotonic() < deadline, 'disposable_guest_ssh_not_ready')
        time.sleep(2)
    command[command.index('StrictHostKeyChecking=accept-new')] = 'StrictHostKeyChecking=yes'
    command[-2:] = ['sudo', '-n', 'sh', '-s']
    volume_id = state['resources']['volume']['id']
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        volume = disposable(operator.block_storage.get_volume(volume_id), source, state, label, 'volume')
        safety.require(volume.status == 'in-use' and len(volume.attachments) == 1
                       and volume.attachments[0]['server_id'] == vm.id, 'disposable_volume_attachment_changed')
    safety.require(not state['checks'].get('volume_write_read'), 'disposable_volume_write_already_verified')
    task.intent(name, state, 'volume-data-write', 'write_read_4096_random_bytes_only_to_uuid_guarded_new_vdb')
    script = volume_guard.guest_volume_guard('vdb', volume_id) + '''
umask 077
scratch=$(mktemp -d /tmp/poc-t49-data.XXXXXX)
trap 'rm -f "$scratch/pattern" "$scratch/readback"; rmdir "$scratch"' 0
dd if=/dev/urandom of="$scratch/pattern" bs=4096 count=1 2>/dev/null
dd if="$scratch/pattern" of="$dev" bs=4096 count=1 2>/dev/null
sync
dd if="$dev" of="$scratch/readback" bs=4096 count=1 2>/dev/null
cmp "$scratch/pattern" "$scratch/readback"
sha256sum "$scratch/readback" | cut -d ' ' -f 1
'''
    result = subprocess.run(command, input=script, text=True, capture_output=True, timeout=30)
    safety.require(result.returncode == 0 and re.fullmatch('[0-9a-f]{64}\\s*', result.stdout), 'disposable_volume_write_read_failed')
    state['checks']['volume_write_read'] = {'bytes': 4096, 'sha256': result.stdout.strip(),
        'uuid_serial_guard': True, 'ssh_pinned': True, 'existing_data_changed': False}
    state['checks']['floating_ip_associate'] = {'through_browser': True, 'ssh_guest_uuid_verified': True}
    task.save(name, state)
    print(json.dumps({'phase': 'browser_guest_access_and_volume_io_verified', 'bytes': 4096,
                      'guest_uuid_verified': True, 'original_data_preserved': True}), flush=True)


def detach_and_snapshot(page, cloud, source, state, label, name):
    safety.require(state['checks'].get('volume_write_read'), 'disposable_volume_io_not_yet_verified')
    volume_id = state['resources']['volume']['id']
    vm = disposable(cloud.compute.get_server(state['resources']['server']['id']), source, state, label, 'server')
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        volume = disposable(operator.block_storage.get_volume(volume_id), source, state, label, 'volume')
        safety.require(volume.status == 'in-use' and len(volume.attachments) == 1
                       and volume.attachments[0]['server_id'] == vm.id, 'disposable_volume_attachment_changed')
    page.goto(browser.URL + '/horizon/project/volumes/' + volume_id + '/attach/', wait_until='networkidle')
    row = page.locator('#attachments tr[data-object-id]')
    safety.require(row.count() == 1 and vm.name in row.inner_text(), 'browser_attachment_target_ambiguous')
    attachment = row.get_attribute('data-object-id')
    task.intent(name, state, 'volume-detach', 'native_detach_only_uuid_guarded_disposable_data_volume')
    row_action(page, 'attachments', attachment, 'detach')
    volume = cloud.block_storage.wait_for_status(cloud.block_storage.get_volume(volume_id),
                                                status='available', failures=['error'], wait=120)
    safety.require(not volume.attachments and not any(x.volume_id == volume_id for x in cloud.compute.volume_attachments(vm)),
                   'detached_volume_still_attached')
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        volume = disposable(operator.block_storage.get_volume(volume_id), source, state, label, 'volume')
        node = task.read('chap-' + label, source)
        chap.verify_node_removed(volume, node, lambda data: task.save('chap-' + label, data))
    state['checks']['volume_detach'] = {'provider_status': 'available', 'through_browser': True,
                                       'iscsi_node_removed': True, 'global_defaults_preserved': True}
    task.save(name, state)
    print(json.dumps({'phase': 'browser_disposable_volume_detached'}), flush=True)
    create_snapshot(page, cloud, source, state, label, name)


def create_snapshot(page, cloud, source, state, label, name):
    volume_id = state['resources']['volume']['id']
    safety.require(state['checks'].get('volume_detach'), 'volume_not_detached_for_snapshot')
    title = state['prefix'] + '-snapshot'
    safety.require(cloud.block_storage.find_snapshot(title) is None, 'unrecorded_disposable_snapshot')
    page.goto(browser.URL + '/horizon/project/volumes/' + volume_id + '/create_snapshot/', wait_until='networkidle')
    page.locator('[name=name]').fill(title)
    page.locator('[name=description]').fill(state['marker'])
    task.intent(name, state, 'snapshot-create', 'native_create_resource_snapshot_of_disposable_volume_no_restore')
    page.locator('input[type=submit]').click()
    snap = browser.wait_resource(lambda: cloud.block_storage.find_snapshot(title))
    remember(name, state, 'snapshot', snap)
    snap = cloud.block_storage.wait_for_status(snap, status='available', failures=['error'], wait=120)
    safety.require(snap.volume_id == volume_id and snap.size == 1 and snap.description == state['marker'],
                   'disposable_snapshot_mismatch')
    state['checks']['snapshot_create'] = {'through_browser': True, 'provider_status': 'available',
                                         'restore_performed': False}
    task.save(name, state)
    print(json.dumps({'phase': 'browser_disposable_snapshot_available', 'restore_performed': False}), flush=True)


def reserve_snapshot_capacity(page, source, state, label, name):
    project = source['projects'][label]['id']
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        quota = operator.block_storage.get('/os-quota-sets/' + project + '?usage=True', raise_exc=True).json()['quota_set']
        safety.require(quota['gigabytes'] == {'limit': 2, 'in_use': 2, 'reserved': 0}
                       and not list(operator.block_storage.snapshots(all_projects=True, project_id=project)),
                       'snapshot_capacity_plan_changed')
    page.goto(browser.URL + '/horizon/identity/' + project + '/update_quotas/', wait_until='networkidle')
    page.locator('a[data-toggle=tab]').filter(has_text=re.compile(r'^\s*Volume\s*$')).click()
    page.locator('[name=gigabytes]').fill('3')
    state['original_gigabytes_limit'] = 2
    task.intent(name, state, 'temporary-snapshot-quota', 'native_raise_only_test_project_gib_2_to_3_for_one_snapshot_then_restore')
    page.locator('input[type=submit]').click()
    page.wait_for_load_state('networkidle')
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        safety.require(operator.block_storage.get_quota_set(project).gigabytes == 3,
                       'snapshot_capacity_change_not_applied')
    state['checks']['temporary_snapshot_capacity'] = {'from_gib': 2, 'to_gib': 3, 'through_browser': True,
                                                     'must_restore_after_cleanup': True}
    task.save(name, state)


def wait_absent(getter, resource_id):
    from openstack import exceptions
    deadline = time.monotonic() + 120
    while True:
        try:
            found = getter(resource_id)
            if found is None:
                return
        except exceptions.ResourceNotFound:
            return
        safety.require(time.monotonic() < deadline, 'disposable_resource_deletion_not_confirmed')
        time.sleep(2)


def cleanup(page, cloud, source, state, label, name):
    safety.require(state['checks'].get('snapshot_create') and state['checks'].get('volume_write_read'),
                   'disposable_storage_acceptance_incomplete')
    for kind, path, table, getter in (
        ('snapshot', '/horizon/project/snapshots/', 'volume_snapshots', cloud.block_storage.get_snapshot),
        ('volume', '/horizon/project/volumes/', 'volumes', cloud.block_storage.get_volume)):
        with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
            privileged_get = operator.block_storage.get_snapshot if kind == 'snapshot' else operator.block_storage.get_volume
            resource = disposable(privileged_get(state['resources'][kind]['id']), source, state, label, kind)
        safety.require(resource.status == 'available', 'disposable_storage_not_available_for_cleanup')
        page.goto(browser.URL + path, wait_until='networkidle')
        task.intent(name, state, kind + '-delete', 'native_delete_only_confirmed_disposable_' + kind)
        row_action(page, table, resource.id, 'delete')
        wait_absent(getter, resource.id)
        state['checks'][kind + '_delete'] = {'through_browser': True, 'provider_absence_verified': True}
        task.save(name, state)
        print(json.dumps({'phase': 'browser_disposable_' + kind + '_deleted'}), flush=True)
    vm = disposable(cloud.compute.get_server(state['resources']['server']['id']), source, state, label, 'server')
    safety.require(not list(cloud.compute.volume_attachments(vm)), 'disposable_vm_still_has_volume_attachment')
    page.goto(browser.URL + '/horizon/project/instances/' + vm.id + '/auto_console', wait_until='networkidle')
    page.locator('#status').filter(has_text='Connected').wait_for(timeout=30000)
    safety.require(page.locator('canvas').count() == 1, 'disposable_browser_console_missing')
    state['checks']['vm_console'] = {'https_wss_connected': True}
    task.save(name, state)
    floating_id = state['resources']['floating_ip']['id']
    floating = cloud.network.get_ip(floating_id)
    safety.require(floating.project_id == source['projects'][label]['id']
                   and floating.description == state['marker'] + '/' + state['prefix'], 'unowned_floating_ip_cleanup')
    page.goto(browser.URL + '/horizon/project/floating_ips/', wait_until='networkidle')
    task.intent(name, state, 'floating-ip-disassociate', 'native_disassociate_only_disposable_floating_ip')
    row_action(page, 'floating_ips', floating_id, 'disassociate')
    safety.require(not cloud.network.get_ip(floating_id).port_id, 'floating_ip_not_disassociated')
    state['checks']['floating_ip_disassociate'] = {'through_browser': True, 'provider_port_cleared': True}
    task.save(name, state)
    page.goto(browser.URL + '/horizon/project/floating_ips/', wait_until='networkidle')
    task.intent(name, state, 'floating-ip-release', 'native_release_only_disposable_floating_ip')
    row_action(page, 'floating_ips', floating_id, 'release')
    wait_absent(cloud.network.get_ip, floating_id)
    state['checks']['floating_ip_release'] = {'through_browser': True, 'provider_absence_verified': True}
    task.save(name, state)
    capture_actions(cloud, vm.id, state)
    task.save(name, state)
    page.goto(browser.URL + '/horizon/project/instances/', wait_until='networkidle')
    task.intent(name, state, 'server-delete', 'native_delete_only_disposable_vm_after_detached_storage')
    row_action(page, 'instances', vm.id, 'delete')
    wait_absent(cloud.compute.get_server, vm.id)
    domain = subprocess.run(['virsh', '-c', 'qemu:///system', 'dominfo', vm.id], capture_output=True, text=True)
    safety.require(domain.returncode != 0, 'deleted_disposable_domain_still_registered')
    state['checks']['server_delete'] = {'through_browser': True, 'nova_absent': True, 'libvirt_absent': True}
    state['phase'] = 'browser_disposable_vm_storage_and_ip_lifecycle_verified_quota_restore_pending'
    task.save(name, state)
    print(json.dumps({'phase': state['phase'], 'source_resources_deleted': 0}), flush=True)


def restore_snapshot_capacity(page, source, state, label, name):
    safety.require(state['checks'].get('snapshot_delete') and state['checks'].get('volume_delete')
                   and state.get('original_gigabytes_limit') == 2, 'storage_cleanup_not_ready_for_quota_restore')
    project = source['projects'][label]['id']
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        quota = operator.block_storage.get('/os-quota-sets/' + project + '?usage=True', raise_exc=True).json()['quota_set']
        safety.require(quota['gigabytes']['in_use'] <= 2 and quota['gigabytes']['reserved'] == 0
                       and quota['gigabytes']['limit'] == 3, 'quota_restore_would_conflict_with_current_usage')
    page.goto(browser.URL + '/horizon/identity/' + project + '/update_quotas/', wait_until='networkidle')
    page.locator('a[data-toggle=tab]').filter(has_text=re.compile(r'^\s*Volume\s*$')).click()
    page.locator('[name=gigabytes]').fill('2')
    task.intent(name, state, 'snapshot-quota-restore', 'native_restore_original_two_gib_test_project_limit')
    page.locator('input[type=submit]').click()
    page.wait_for_load_state('networkidle')
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator:
        safety.require(operator.block_storage.get_quota_set(project).gigabytes == 2, 'original_storage_quota_not_restored')
    state['checks']['snapshot_quota_restored'] = {'gigabytes': 2, 'through_browser': True}
    state['phase'] = 'browser_disposable_vm_storage_and_ip_lifecycle_verified'
    task.save(name, state)
    print(json.dumps({'phase': state['phase'], 'storage_quota_restored_gib': 2}), flush=True)


def inspect(page, state):
    page.goto(browser.URL + '/horizon/project/instances/', wait_until='networkidle')
    print(json.dumps({'phase': 'native_instance_actions', 'actions': page.locator('tr[data-object-id] [id*="__action_"]').evaluate_all(
        'xs=>xs.map(x=>({id:x.id,text:x.innerText.trim(),href:x.getAttribute("href")}))')}), flush=True)
    if 'volume' in state['resources']:
        resource = state['resources']['volume']['id']
        for action in ('attach', 'create_snapshot'):
            page.goto(browser.URL + '/horizon/project/volumes/' + resource + '/' + action + '/', wait_until='networkidle')
            print(json.dumps({'phase': 'native_volume_' + action, 'fields': browser.fields(page),
                              'buttons': page.locator('button,input[type=submit]').evaluate_all(
                                  'xs=>xs.map(x=>({text:x.innerText,value:x.type==="submit"?x.value:undefined}))')}), flush=True)


def inspect_access(page, state):
    vm = state['resources']['server']['id']
    for route in ('/horizon/project/floating_ips/allocate/',
                  '/horizon/project/floating_ips/associate/?instance_id=' + vm,
                  '/horizon/project/instances/' + vm + '/update?step=update_security_groups'):
        page.goto(browser.URL + route, wait_until='networkidle')
        print(json.dumps({'phase': 'inspect_lifecycle_access', 'path': route, 'fields': browser.fields(page),
                          'buttons': page.locator('button,input[type=submit]').evaluate_all(
                              'xs=>xs.map(x=>({text:x.innerText,value:x.type==="submit"?x.value:undefined}))')}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', choices=('a', 'b'), required=True)
    parser.add_argument('--run-id', default='', help='Fresh short identifier for a separate sealed acceptance run')
    parser.add_argument('--phase', choices=('create-vm', 'stop-vm', 'start-vm', 'create-volume', 'attach-volume', 'access-and-io', 'detach-and-snapshot', 'create-snapshot', 'reserve-snapshot-capacity', 'restore-snapshot-capacity', 'cleanup', 'vm-cycle-and-volume', 'inspect', 'inspect-access'), required=True)
    args = parser.parse_args()
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    safety.require(not args.run_id or re.fullmatch('[a-z0-9][a-z0-9-]{0,31}', args.run_id), 'unsafe_lifecycle_run_id')
    name = 'lifecycle-' + args.label + ('-' + args.run_id if args.run_id else '')
    with task.locked():
        state = task.read(name, source)
        if 'prefix' not in state:
            state['prefix'] = 'poc-t49-' + args.label + '-' + uuid4().hex[:10]
            task.save(name, state)
        from playwright.sync_api import sync_playwright
        with controls.connection(source, private, args.label) as cloud, sync_playwright() as pw:
            br = browser.browser_launch(pw)
            try:
                context = browser.local_context(br)
                page = context.new_page()
                if args.phase in ('reserve-snapshot-capacity', 'restore-snapshot-capacity'):
                    browser.login(page, 'admin', common.read_secrets(common.SECRET_FILE)['ADMIN_PASS'])
                else:
                    browser.login(page, private['tenants'][args.label]['username'], private['tenants'][args.label]['password'])
                if args.phase == 'inspect':
                    inspect(page, state)
                elif args.phase == 'inspect-access':
                    inspect_access(page, state)
                elif args.phase == 'create-vm':
                    create_vm(page, cloud, source, state, args.label, name)
                elif args.phase == 'create-volume':
                    create_volume(page, cloud, source, state, args.label, name)
                elif args.phase == 'attach-volume':
                    attach_volume(page, cloud, source, state, args.label, name)
                elif args.phase == 'access-and-io':
                    access_and_io(page, cloud, source, state, args.label, name)
                elif args.phase == 'detach-and-snapshot':
                    detach_and_snapshot(page, cloud, source, state, args.label, name)
                elif args.phase == 'create-snapshot':
                    create_snapshot(page, cloud, source, state, args.label, name)
                elif args.phase == 'reserve-snapshot-capacity':
                    reserve_snapshot_capacity(page, source, state, args.label, name)
                elif args.phase == 'restore-snapshot-capacity':
                    restore_snapshot_capacity(page, source, state, args.label, name)
                elif args.phase == 'cleanup':
                    cleanup(page, cloud, source, state, args.label, name)
                elif args.phase == 'vm-cycle-and-volume':
                    vm_transition(page, cloud, source, state, args.label, name, 'stop')
                    vm_transition(page, cloud, source, state, args.label, name, 'start')
                    create_volume(page, cloud, source, state, args.label, name)
                    inspect(page, state)
                else:
                    vm_transition(page, cloud, source, state, args.label, name,
                                  'stop' if args.phase == 'stop-vm' else 'start')
                context.close()
            finally:
                br.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print(json.dumps({'phase': 'browser_lifecycle_stopped', 'reason': reason,
                          'automatic_mutation_replay': False}), flush=True)
        raise SystemExit(2)
