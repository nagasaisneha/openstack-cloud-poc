#!/usr/bin/python3
"""Bootstrap only the new marked secondary backend using a pinned tenant key."""
import json
import logging
import os
import subprocess
import time

import browser_lifecycle as life
import browser_resources as controls
import configure_nova as common
import continue_poc as task
import launch_test_vm as launch
import manage_cinder_loop as safety
import verify_poc_guest_access as guest


def command(source, state):
    saved = task.read('lifecycle-a-octavia-backend', source)
    safety.require(saved['resources']['server']['id'] == state['secondary']['id'], 'secondary_backend_scope_changed')
    hosts = task.DIRECTORY / ('known_hosts_' + saved['prefix'])
    if not hosts.exists():
        descriptor = os.open(hosts, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
    safety.protected(hosts, 0o600)
    return ['ip', 'netns', 'exec', state['namespace'], 'ssh', '-F', '/dev/null',
        '-i', str(task.tenants.DIRECTORY / 'id_ed25519_a'), '-o', 'BatchMode=yes',
        '-o', 'IdentitiesOnly=yes', '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
        '-o', 'ProxyCommand=none', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
        '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(hosts),
        '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=5',
        'cirros@' + state['secondary']['address'], 'sh', '-s']


def ssh(source, state, script, *, first=False):
    args = command(source, state)
    if first:
        args[args.index('StrictHostKeyChecking=yes')] = 'StrictHostKeyChecking=accept-new'
    result = subprocess.run(args, input=script, text=True, capture_output=True, timeout=25)
    safety.require(result.returncode == 0, 'secondary_backend_ssh_not_ready')
    return result.stdout


def apply(source, private):
    name, state = 'octavia-backends', task.read('octavia-backends', source)
    saved = task.read('lifecycle-a-octavia-backend', source)
    connectivity = task.read('connectivity', source)
    with common.connection(common.read_secrets(common.SECRET_FILE)) as operator, controls.connection(source, private, 'a') as cloud:
        vm = life.disposable(operator.compute.get_server(saved['resources']['server']['id']), source, saved, 'a', 'server')
        primary, network, primary_port = task.validate_tenant(cloud, source, 'a')
        subnet = cloud.network.get_subnet(source['resources']['a']['subnet']['id'])
        ports = [p for p in cloud.network.ports(device_id=vm.id) if p.network_id == network.id]
        safety.require(vm.status == 'ACTIVE' and len(ports) == 1 and ports[0].project_id == network.project_id,
                       'secondary_backend_port_scope_changed')
        address = next(x['ip_address'] for x in ports[0].fixed_ips if x['subnet_id'] == subnet.id)
        source_ip = launch.metadata_address(operator, network, subnet)
        group_name = saved['prefix'] + '-bootstrap'
        group = cloud.network.find_security_group(group_name)
        if group is None:
            task.intent(name, state, 'bootstrap-security-group', 'create_new_secondary_only_metadata_ssh_group')
            group = cloud.network.create_security_group(name=group_name, description=state['marker'])
            state['bootstrap_group_id'] = group.id
            task.save(name, state)
        safety.require(group.id == state.get('bootstrap_group_id') and group.project_id == network.project_id
                       and group.description == state['marker'], 'unowned_backend_bootstrap_group')
        rules = list(cloud.network.security_group_rules(security_group_id=group.id))
        if not any(r.direction == 'ingress' and r.protocol == 'tcp' and r.port_range_min == r.port_range_max == 22
                   and r.remote_ip_prefix == source_ip + '/32' for r in rules):
            cloud.network.create_security_group_rule(security_group_id=group.id, direction='ingress', ether_type='IPv4',
                protocol='tcp', port_range_min=22, port_range_max=22, remote_ip_prefix=source_ip + '/32', description=state['marker'])
        access = connectivity['resources']['a_access_group']['id']
        old_groups = set(ports[0].security_group_ids)
        cloud.network.update_port(ports[0], security_group_ids=sorted(old_groups | {access, group.id}))
        primary_ip = next(x['ip_address'] for x in primary_port.fixed_ips if x['subnet_id'] == subnet.id)
        state.update(namespace='ovnmeta-' + network.id, project_id=network.project_id, network_id=network.id,
                     subnet_id=subnet.id, primary={'id': primary.id, 'address': primary_ip},
                     secondary={'id': vm.id, 'address': address}, app_port=8080)
        task.save(name, state)
    deadline = time.monotonic() + 60
    while True:
        try:
            identity = json.loads(ssh(source, state,
                'wget -q -T 8 -O - http://169.254.169.254/openstack/latest/meta_data.json\n', first=True))
            safety.require(identity['uuid'] == vm.id, 'secondary_backend_guest_uuid_mismatch')
            break
        except Exception:
            safety.require(time.monotonic() < deadline, 'secondary_backend_ssh_unavailable')
            time.sleep(2)
    expected = {'tenant': 'a', 'backend': 'secondary-a', 'run': connectivity['marker'], 'server_id': vm.id}
    ssh(source, state, guest.application_script(expected))
    received = json.loads(ssh(source, state, 'wget -q -T 5 -O - http://127.0.0.1:8080/\n'))
    safety.require(received == expected, 'secondary_backend_application_mismatch')
    state.update(phase='two_real_owned_backends_ready', secondary_guest_uuid_verified=True, pinned_ssh=True,
                 existing_guest_services_changed=False)
    task.save(name, state)
    print(json.dumps({'phase': state['phase'], 'secondary_guest_uuid_verified': True,
                      'primary_id': state['primary']['id'], 'secondary_id': state['secondary']['id']}), flush=True)


if __name__ == '__main__':
    try:
        logging.disable(logging.CRITICAL)
        source, private = task.initialize()
        with task.locked():
            apply(source, private)
    except Exception as error:
        print(json.dumps({'phase': 'octavia_backend_prepare_stopped', 'reason': str(error) if isinstance(error, safety.GuardError)
                          else type(error).__name__}), flush=True)
        raise SystemExit(2)
