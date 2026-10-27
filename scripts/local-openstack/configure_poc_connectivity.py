#!/usr/bin/python3
"""Plan/apply isolated Neutron ingress for the two marked browser guests.

Creates a logical OVS provider bridge without a physical NIC, one private
external network, scoped RBAC, tenant routers/FIPs and source-restricted rules.
Only localhost TCP forwards are exposed. No host default/Windows route, retained
guest, backup, distro, image or existing storage is replaced or deleted.
"""
import argparse
import configparser
import ipaddress
import json
import logging
import os
from pathlib import Path
import stat
import subprocess
import time

import configure_neutron as neutron
import configure_nova as common
import continue_poc as task
import install_poc_browser as install
import manage_cinder_loop as safety

NAME = 'connectivity'
BRIDGE, PHYSNET = 'br-poc-ex', 'poc-ex'
CIDR, HOST = '192.0.2.0/24', '192.0.2.1'
ML2 = Path(neutron.PLUGIN_CONFIG)


def ml2_config(text):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    drivers = [x.strip() for x in parser.get('ml2', 'type_drivers').split(',')]
    safety.require('geneve' in drivers, 'existing_geneve_driver_missing')
    if 'flat' not in drivers:
        drivers.append('flat')
    flat = [x.strip() for x in parser.get('ml2_type_flat', 'flat_networks', fallback='').split(',') if x.strip()]
    safety.require('*' not in flat, 'wildcard_flat_network_scope_requires_review')
    if PHYSNET not in flat:
        flat.append(PHYSNET)
    return common.edit_ini(text, {'ml2': {'type_drivers': ','.join(drivers)},
                                 'ml2_type_flat': {'flat_networks': ','.join(flat)}})


def mappings(value):
    pairs = [x.split(':') for x in value.split(',') if x]
    safety.require(all(len(x) == 2 for x in pairs) and len(dict(pairs)) == len(pairs), 'ambiguous_bridge_mappings')
    result = dict(pairs)
    safety.require(PHYSNET not in result or result[PHYSNET] == BRIDGE, 'provider_mapping_already_in_use')
    result[PHYSNET] = BRIDGE
    return ','.join(key + ':' + value for key, value in sorted(result.items()))


def bridge_program(marker):
    return '''#!/usr/bin/python3
import json, subprocess
def run(args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()
owner = run(['ovs-vsctl', '--timeout=5', 'get', 'Bridge', 'br-poc-ex', 'external_ids:poc_owner']).strip('"')
if owner != EXPECTED:
    raise SystemExit('STOP: provider bridge ownership changed')
ports = run(['ovs-vsctl', '--timeout=5', 'list-ports', 'br-poc-ex']).splitlines()
for name in ports:
    if run(['ovs-vsctl', '--timeout=5', 'get', 'Interface', name, 'type']).strip('"') != 'patch':
        raise SystemExit('STOP: physical NICs and non-patch provider ports are forbidden')
    peer = run(['ovs-vsctl', '--timeout=5', 'get', 'Interface', name, 'options:peer']).strip('"')
    if run(['ovs-vsctl', '--timeout=5', 'iface-to-br', peer]) != 'br-int':
        raise SystemExit('STOP: provider patch peer changed')
run(['ip', 'link', 'set', 'dev', 'br-poc-ex', 'up'])
run(['ip', 'address', 'replace', '192.0.2.1/24', 'dev', 'br-poc-ex'])
'''.replace('EXPECTED', repr(marker))


def forward_unit(address, destination, local_port):
    safety.require(ipaddress.ip_address(address) in ipaddress.ip_network(CIDR)
                   and destination in (22, 8080) and 1024 < local_port < 65536,
                   'unsafe_local_forward_target')
    return ('[Unit]\nDescription=Local-only POC guest access\nAfter=poc-ingress-bridge.service network.target\n'
            'Requires=poc-ingress-bridge.service\n\n[Service]\nUser=nobody\nGroup=nogroup\n'
            'ExecStart=/usr/bin/socat TCP4-LISTEN:' + str(local_port) + ',bind=127.0.0.1,reuseaddr,fork,max-children=16 '
            'TCP4:' + address + ':' + str(destination) + ',bind=' + HOST + ',connect-timeout=5\n'
            'Restart=on-failure\nRestartSec=2\nNoNewPrivileges=true\nCapabilityBoundingSet=\n'
            'ProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nPrivateDevices=true\n'
            'RestrictAddressFamilies=AF_INET AF_UNIX\nTasksMax=20\nMemoryMax=64M\n\n'
            '[Install]\nWantedBy=multi-user.target\n')


def ensure(state, key, find, get, create, project, *, description=True):
    saved = state['resources'].get(key)
    resource = get(saved['id']) if saved else find()
    if resource is None:
        task.intent(NAME, state, key, 'create')
        resource = create()
        state['resources'][key] = {'id': resource.id}
        task.save(NAME, state)
        resource = get(resource.id)
    safety.require(resource.project_id == project and (not description or resource.description == state['marker']),
                   'unexpected_owner_' + key)
    state['resources'][key] = {'id': resource.id}
    task.save(NAME, state)
    return resource


def plan(source, state, cloud):
    occupied = [x.cidr for x in cloud.network.subnets() if x.id != state['resources'].get('external_subnet', {}).get('id')]
    routes = json.loads(safety.run(['ip', '-j', '-4', 'route', 'show', 'table', 'all']))
    occupied += [x['dst'] for x in routes if x.get('dst') not in (None, 'default') and x.get('dev') != BRIDGE]
    safety.require(not any(ipaddress.ip_network(CIDR).overlaps(ipaddress.ip_network(x, strict=False)) for x in occupied),
                   'provider_cidr_overlaps_existing_network')
    values = neutron.read_ovs_external_ids()
    mappings(values.get('ovn-bridge-mappings', ''))
    safety.require(values.get('ovn-cms-options') == 'enable-chassis-as-gw', 'gateway_chassis_not_enabled')
    for label in ('a', 'b'):
        with task.browser.connection(source, task.tenants.load()[1], label) as tenant:
            task.validate_tenant(tenant, source, label)
    print(json.dumps({'phase': 'connectivity_plan', 'provider_bridge': BRIDGE, 'provider_cidr': CIDR,
                      'physical_interface_added': False, 'windows_route_changed': False,
                      'local_ports': {'a': {'ssh': 2221, 'http': 8081}, 'b': {'ssh': 2222, 'http': 8082}},
                      'guest_ingress_source': HOST + '/32', 'retained_vm_changed': False}), flush=True)


def apply(source, private, state):
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        plan(source, state, cloud)
        exists = subprocess.run(['ovs-vsctl', '--timeout=5', 'br-exists', BRIDGE]).returncode == 0
        if exists:
            owner = safety.run(['ovs-vsctl', '--timeout=5', 'get', 'Bridge', BRIDGE, 'external_ids:poc_owner']).strip().strip('"')
            safety.require(owner == state['marker'], 'unowned_provider_bridge_exists')
        else:
            task.intent(NAME, state, 'provider_bridge', 'create_logical_bridge_without_physical_interface')
            safety.run(['ovs-vsctl', '--timeout=5', 'add-br', BRIDGE, '--', 'set', 'Bridge', BRIDGE,
                        'external_ids:poc_owner=' + state['marker']])
        values = neutron.read_ovs_external_ids()
        state.setdefault('original_bridge_mappings', values.get('ovn-bridge-mappings', ''))
        safety.run(['ovs-vsctl', '--timeout=5', 'set', 'Open_vSwitch', '.',
                    'external_ids:ovn-bridge-mappings=' + mappings(values.get('ovn-bridge-mappings', ''))])
        helper = Path('/usr/local/sbin/poc-ingress-bridge')
        install.write_root(helper, bridge_program(state['marker']), mode=0o700)
        install.write_root(Path('/etc/systemd/system/poc-ingress-bridge.service'),
            '[Unit]\nDescription=Isolated logical POC provider bridge\nAfter=openvswitch-switch.service\n'
            'Requires=openvswitch-switch.service\nBefore=neutron-server.service\n\n[Service]\nType=oneshot\n'
            'ExecStart=/usr/local/sbin/poc-ingress-bridge\nRemainAfterExit=yes\n\n[Install]\nWantedBy=multi-user.target\n')
        safety.run(['systemctl', 'daemon-reload'])
        safety.run(['systemctl', 'enable', '--now', 'poc-ingress-bridge.service'])
        before = ML2.read_text()
        after = ml2_config(before)
        if before != after:
            safety.atomic_write(task.DIRECTORY / 'ml2.before', before, 0o600)
            info = safety.protected(ML2)
            safety.atomic_write(ML2, after, stat.S_IMODE(info.st_mode), info.st_gid)
            safety.run(['systemctl', 'restart', 'neutron-server.service'], timeout=90)
        deadline = time.monotonic() + 60
        while True:
            try:
                list(cloud.network.networks()); break
            except Exception:
                safety.require(time.monotonic() < deadline, 'neutron_api_not_ready')
                time.sleep(2)
        operator = cloud.session.get_project_id()
        net = ensure(state, 'external_network', lambda: cloud.network.find_network('poc-local-external'),
            cloud.network.get_network, lambda: cloud.network.create_network(name='poc-local-external',
            description=state['marker'], is_router_external=True, is_shared=False,
            provider_network_type='flat', provider_physical_network=PHYSNET), operator)
        safety.require(net.is_router_external and not net.is_shared and net.provider_physical_network == PHYSNET,
                       'provider_network_drift')
        subnet = ensure(state, 'external_subnet', lambda: cloud.network.find_subnet('poc-local-external-subnet'),
            cloud.network.get_subnet, lambda: cloud.network.create_subnet(name='poc-local-external-subnet',
            description=state['marker'], network_id=net.id, cidr=CIDR, ip_version=4,
            gateway_ip=HOST, is_dhcp_enabled=False,
            allocation_pools=[{'start': '192.0.2.100', 'end': '192.0.2.149'}]), operator)
        safety.require(subnet.cidr == CIDR and subnet.network_id == net.id and not subnet.is_dhcp_enabled,
                       'external_subnet_changed')
        policies = list(cloud.network.rbac_policies(object_id=net.id))
        for label in ('a', 'b'):
            target = source['projects'][label]['id']
            if not any(x.action == 'access_as_external' and x.target_project_id == target for x in policies):
                policy = cloud.network.create_rbac_policy(object_type='network', object_id=net.id,
                    action='access_as_external', target_project_id=target)
                policies.append(policy)
            # Grant only the intended clients, then remove the server-created
            # wildcard from this newly owned external network (no other object).
            cloud.network.update_quota(target, floatingip=2)
        for policy in policies:
            if policy.action == 'access_as_external' and policy.target_project_id == '*':
                safety.require(policy.object_id == net.id and policy.project_id == operator,
                               'unowned_external_network_policy')
                cloud.network.delete_rbac_policy(policy.id)
        for label in ('a', 'b'):
            project = source['projects'][label]['id']
            with task.browser.connection(source, private, label) as tenant:
                vm, network, port = task.validate_tenant(tenant, source, label)
                router = ensure(state, label + '_router', lambda: tenant.network.find_router('poc-browser-' + label + '-router'),
                    tenant.network.get_router, lambda: tenant.network.create_router(name='poc-browser-' + label + '-router',
                    description=state['marker'], external_gateway_info={'network_id': net.id}), project)
                subnet_id = source['resources'][label]['subnet']['id']
                attached = [p for p in tenant.network.ports(device_id=router.id)
                            if any(x['subnet_id'] == subnet_id for x in p.fixed_ips)]
                if not attached:
                    task.intent(NAME, state, label + '_router_interface', 'attach_owned_subnet')
                    tenant.network.add_interface_to_router(router, subnet_id=subnet_id)
                group = ensure(state, label + '_access_group', lambda: tenant.network.find_security_group('poc-browser-' + label + '-access'),
                    tenant.network.get_security_group, lambda: tenant.network.create_security_group(
                    name='poc-browser-' + label + '-access', description=state['marker']), project)
                for protocol, low, high in [('tcp', 22, 22), ('tcp', 8080, 8080), ('icmp', 8, 0)]:
                    rules = list(tenant.network.security_group_rules(security_group_id=group.id))
                    if not any(x.direction == 'ingress' and x.protocol == protocol and x.port_range_min == low
                               and x.port_range_max == high and x.remote_ip_prefix == HOST + '/32' for x in rules):
                        tenant.network.create_security_group_rule(security_group_id=group.id, direction='ingress',
                            ethertype='IPv4', protocol=protocol, port_range_min=low, port_range_max=high,
                            remote_ip_prefix=HOST + '/32', description=state['marker'])
                tenant.network.update_port(port, security_group_ids=sorted(set(port.security_group_ids) | {group.id}))
                def find_ip():
                    found = [x for x in tenant.network.ips(port_id=port.id) if x.description == state['marker']]
                    safety.require(len(found) <= 1, 'duplicate_owned_floating_ips')
                    return found[0] if found else None
                floating = ensure(state, label + '_floating_ip', find_ip, tenant.network.get_ip,
                    lambda: tenant.network.create_ip(floating_network_id=net.id, port_id=port.id,
                                                     description=state['marker']), project)
                safety.require(floating.port_id == port.id and floating.floating_network_id == net.id,
                               'floating_ip_target_changed')
                state.setdefault('access', {})[label] = {'guest_id': vm.id, 'port_id': port.id,
                    'fixed_ip': port.fixed_ips[0]['ip_address'], 'floating_ip': floating.floating_ip_address,
                    'ssh_port': 2221 if label == 'a' else 2222, 'http_port': 8081 if label == 'a' else 8082}
                task.save(NAME, state)
                for kind, destination in [('ssh', 22), ('http', 8080)]:
                    unit = 'poc-guest-' + label + '-' + kind + '.service'
                    install.write_root(Path('/etc/systemd/system') / unit,
                        forward_unit(floating.floating_ip_address, destination, state['access'][label][kind + '_port']))
        safety.run(['systemctl', 'daemon-reload'])
        for label in ('a', 'b'):
            for kind in ('ssh', 'http'):
                safety.run(['systemctl', 'enable', '--now', 'poc-guest-' + label + '-' + kind + '.service'])
        state['phase'] = 'routed_guest_access_configured_not_yet_verified'
        task.save(NAME, state)
        print(json.dumps({'phase': state['phase'], 'access': state['access'], 'physical_nic_added': False}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    state = task.read(NAME, source)
    with task.locked():
        if args.apply:
            apply(source, private, state)
        else:
            with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
                plan(source, state, cloud)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print(json.dumps({'phase': 'connectivity_stopped', 'reason': reason, 'automatic_create_replay': False}), flush=True)
        raise SystemExit(2)
