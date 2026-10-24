#!/usr/bin/python3
"""Task 11 operational review: observations and a bounded guest CPU smoke test.

No service/configuration/network/resource mutations. Writes one new protected
JSON report. Does not rotate logs, reopen log files, or enable the timer.
"""
import datetime
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import uuid

import configure_nova as common
import launch_test_vm as launch
import validate_guest as guest

UNITS = ('mariadb.service', 'rabbitmq-server.service', 'apache2.service', 'memcached.service',
         'glance-api.service', 'nova-api.service', 'nova-conductor.service', 'nova-scheduler.service',
         'nova-compute.service', 'neutron-server.service', 'neutron-ovn-metadata-agent.service',
         'libvirtd.service', 'openvswitch-switch.service', 'ovn-central.service', 'ovn-northd.service',
         'ovn-controller.service', 'ovn-controller-vtep.service', 'kvm-device-permissions.service',
         'logrotate.service', 'logrotate.timer')
PACKAGES = ('keystone', 'glance-api', 'placement-api', 'nova-api', 'nova-compute', 'neutron-server',
            'neutron-ovn-metadata-agent', 'mariadb-server', 'rabbitmq-server', 'apache2',
            'libapache2-mod-wsgi-py3', 'memcached', 'openvswitch-switch', 'ovn-central', 'ovn-host',
            'libvirt-daemon-system', 'qemu-system-x86', 'python3-openstackclient', 'python3-openstacksdk')
PORTS = {80, 443, 3306, 4369, 5000, 5672, 6081, 6640, 6641, 6642, 8774, 8775, 8778,
         9292, 9696, 11211, 15672, 16509, 25672}
CPU_COMMAND = "awk 'BEGIN{s=0;for(i=1;i<=100000;i++)s+=i;printf \"%.0f\\n\",s}'\n"


def listeners(text, protocol):
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            raise RuntimeError('unrecognized socket inventory format')
        address, separator, port_text = parts[3].rpartition(':')
        if not separator or not port_text.isdecimal():
            raise RuntimeError('unrecognized socket endpoint')
        port = int(port_text)
        if port not in PORTS:
            continue
        address = address.strip('[]')
        loopback = False if address == '*' else ipaddress.ip_address(address.split('%')[0]).is_loopback
        rows.append(dict(protocol=protocol, address=address, port=port, loopback=loopback))
    return rows


def systemd_state(unit):
    fields = {'LoadState', 'ActiveState', 'SubState', 'Result', 'UnitFileState', 'ExecMainStatus', 'TimeoutStartUSec'}
    text = common.run(['systemctl', '--no-pager', 'show', unit,
                       '--property=' + ','.join(sorted(fields))], timeout=15)
    result = {}
    for line in text.splitlines():
        key, separator, value = line.partition('=')
        if key not in fields or not separator or not re.fullmatch(r'[a-zA-Z0-9 ._-]*', value):
            raise RuntimeError('unexpected service property format')
        result[key] = value
    if not {'LoadState', 'ActiveState', 'SubState'} <= set(result):
        raise RuntimeError('incomplete service state')
    if unit not in {'logrotate.service', 'logrotate.timer', 'ovn-controller-vtep.service'}:
        result['expected_profile_match'] = result['LoadState'] == 'loaded' and result['ActiveState'] == 'active'
    return result


def package_versions():
    text = common.run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${db:Status-Status}\n', *PACKAGES], timeout=15)
    records = []
    for line in text.splitlines():
        parts = line.split('\t')
        if len(parts) != 3 or any(not re.fullmatch('[A-Za-z0-9.+:~_-]+', value) for value in parts):
            raise RuntimeError('unexpected package metadata')
        records.append(dict(package=parts[0], version=parts[1], status=parts[2]))
    return records


def logrotate_review():
    records = {}
    for name in ('ovn-common', 'openvswitch-switch'):
        path = Path('/etc/logrotate.d') / name
        info = path.lstat()
        if not path.is_file() or path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('unexpected logrotate configuration ownership/type/mode')
        content = path.read_text()
        calls = [line for line in content.splitlines() if not line.lstrip().startswith('#') and 'ovs-appctl' in line]
        records[name] = dict(sha256=hashlib.sha256(content.encode()).hexdigest(), appctl_call_count=len(calls),
            all_appctl_calls_bounded=bool(calls) and all(re.search(r'--timeout[= ]+[1-9][0-9]*\b', line) for line in calls))
    # Debug mode checks parsing only; captured configuration output is not printed.
    try:
        common.run(['logrotate', '--debug', '/etc/logrotate.conf'], timeout=30)
        records['debug_configuration_check'] = 'PASS'
    except Exception:
        records['debug_configuration_check'] = 'FAILED_OR_UNAVAILABLE'
    return records


def cpu_smoke(state):
    for filename in ('id_ed25519', 'known_hosts'):
        guest.protected_file(launch.DATA_DIR / filename, 0o600)
    if not (launch.DATA_DIR / 'known_hosts').stat().st_size:
        raise RuntimeError('existing pinned SSH trust required')
    command = list(guest.ssh_command(state))
    index = command.index('StrictHostKeyChecking=accept-new')
    command[index] = 'StrictHostKeyChecking=yes'
    output = common.run(command, input_text=CPU_COMMAND, timeout=20).strip()
    if output != '5000050000':
        raise RuntimeError('guest arithmetic result differs from the expected sum')
    return dict(verified=True, server_id=state['ids']['server'], workload='sum of integers 1..100000',
                result='5000050000', access='pinned-key SSH', limitation='CPU arithmetic smoke test, not AI inference or a benchmark')


def cloud_state(admin, state):
    server = admin.compute.get_server(state['ids']['server'])
    launch.assert_owned(server, state, '', server=True)
    if server.status != 'ACTIVE' or server.task_state is not None or int(server.power_state) != 1:
        raise RuntimeError('retained demo is not settled ACTIVE/running')
    image = admin.image.get_image(state['image_id'])
    if image.status != 'active' or image.owner_id != state['project_id']:
        raise RuntimeError('retained private image is not active/owned')
    network, port = guest.validate_attachment(admin, state)
    flavor = admin.compute.get_flavor(state['ids']['flavor'])
    compute = common.observed_compute(admin)
    agents = list(admin.network.agents())
    required = {'OVN Controller Gateway agent', 'OVN Metadata agent'}
    alive = {agent.agent_type for agent in agents if agent.is_alive and agent.is_admin_state_up}
    if not required <= alive or compute is None or compute[1].name != server.hypervisor_hostname:
        raise RuntimeError('required compute/OVN agent is not up/enabled')
    catalog = []
    services = {service.id: service.type for service in admin.identity.services()}
    for endpoint in admin.identity.endpoints():
        kind = services.get(endpoint.service_id)
        if kind in {'identity', 'image', 'compute', 'placement', 'network'}:
            from urllib.parse import urlsplit
            url = urlsplit(endpoint.url)
            if url.username or url.password or url.query or url.fragment or not url.hostname:
                raise RuntimeError('unexpected catalog URL; refusing credential-bearing output')
            catalog.append(dict(service=kind, interface=endpoint.interface, url=endpoint.url))
    return dict(authenticated=True, project_id=state['project_id'], retained_server=server.id,
        status=server.status, image_id=image.id, network_id=network.id, port_id=port.id, mtu=network.mtu,
        flavor=dict(id=flavor.id, ram_mib=flavor.ram, vcpus=flavor.vcpus, disk_gib=flavor.disk),
        compute_up=True, ovn_agents_up=sorted(required), provider_id=compute[2].id,
        placement_inventory={name: dict(total=value.total, reserved=value.reserved, ratio=value.allocation_ratio)
                             for name, value in compute[3].items()}, endpoints=catalog)


def capacity():
    result = {'linux_root': dict(zip(('total_bytes', 'used_bytes', 'free_bytes'), shutil.disk_usage('/')))}
    if os.path.ismount('/mnt/c'):
        result['windows_c_mount'] = dict(zip(('total_bytes', 'used_bytes', 'free_bytes'), shutil.disk_usage('/mnt/c')))
    result['meminfo_kib'] = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        name, value = line.split(':', 1)
        if name in {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}:
            result['meminfo_kib'][name] = int(value.split()[0])
    return result


def observe(report, label, action):
    try:
        report[label] = dict(status='PASS', result=action())
    except Exception as exc:
        # No raw SDK, SQL, command or configuration diagnostics in the report.
        report[label] = dict(status='FAILED_OR_UNAVAILABLE', error_type=type(exc).__name__)
    print('Task 11 operations review: ' + label + ': ' + json.dumps(report[label], sort_keys=True), flush=True)


def main():
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    try:
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('existing Ubuntu 22.04 WSL required')
        secrets = common.read_secrets(common.SECRET_FILE)
        with common.connection(secrets) as admin:
            state, _ = guest.load_run(admin, require_ssh=False)
            path = launch.DATA_DIR / 'application.json'
            guest.protected_file(path, 0o600)
            application = json.loads(path.read_text())
            cleanup = application['results']['dependency_cleanup']
            if not cleanup.get('verified') or set(cleanup['done']) != set(range(len(cleanup['plan']))):
                raise RuntimeError('completed scoped dependency cleanup required')
            report = dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), run_id=state['run_id'])
            print('Task 11 operations review: BEGIN (no service/configuration/network changes)', flush=True)
            observe(report, 'packages', package_versions)
            for unit in UNITS:
                observe(report, unit, lambda unit=unit: systemd_state(unit))
            observe(report, 'listeners', lambda: listeners(common.run(['ss', '-H', '-lnt'], timeout=10), 'tcp')
                    + listeners(common.run(['ss', '-H', '-lnu'], timeout=10), 'udp'))
            observe(report, 'capacity', capacity)
            observe(report, 'cloud', lambda: cloud_state(admin, state))
            observe(report, 'guest_cpu_smoke', lambda: cpu_smoke(state))
            observe(report, 'logrotate', logrotate_review)
            report['backup_locations'] = dict(vm=application['results']['application_lifecycle']['backup_directory'],
                dependencies=cleanup['backup_directory'], supplemental=cleanup['recreated_default']['backup_directory'])
            report['limitations'] = ['listener reachability outside WSL not tested', 'WSL restart persistence untested',
                'full recovery restore untested', 'logrotate timer reactivation pending review',
                'external/provider routing and floating IPs not demonstrated']
            destination = launch.DATA_DIR / ('operations-review-' + uuid.uuid4().hex + '.json')
            launch.atomic_state(destination, report)
            print('Task 11 operations review: protected report=' + str(destination), flush=True)
            print('Task 11 operations review: COMPLETE (observations only; listener/logrotate review pending)', flush=True)
            return 0 if all(value.get('status') == 'PASS' for value in report.values() if isinstance(value, dict) and 'status' in value) else 1
    except Exception as exc:
        print('STOP: operations review failed (' + type(exc).__name__ + '); no service changes executed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
