#!/usr/bin/python3
"""Task 11 stage 3: local Neutron ML2/OVN and metadata control plane.

No guest, tenant network, bridge, host route, NAT or physical NIC is created.
Only ovn-remote is changed in existing OVS state, to use local Southbound TCP.
"""

import fcntl
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import re
import secrets as random_secrets
import stat
import sys
import time

import configure_nova as common


PLUGIN_CONFIG = '/etc/neutron/plugins/ml2/ml2_conf.ini'
METADATA_CONFIG = '/etc/neutron/neutron_ovn_metadata_agent.ini'
DB_KEY = 'NEUTRON_DBPASS'


def progress(message):
    print(f'Task 11 Neutron configuration: {message}', flush=True)


def plugin_environment(text):
    matches = list(re.finditer(r'^\s*(?:export\s+)?NEUTRON_PLUGIN_CONFIG\s*=.*$', text, re.M))
    if len(matches) > 1:
        raise RuntimeError('multiple NEUTRON_PLUGIN_CONFIG assignments; preserving the defaults file')
    assignment = f'NEUTRON_PLUGIN_CONFIG="{PLUGIN_CONFIG}"'
    if matches:
        match = matches[0]
        return text[:match.start()] + assignment + text[match.end():]
    return text.rstrip('\n') + '\n' + assignment + '\n'


def desired_options(secrets, mtu):
    if not isinstance(mtu, int) or not 1280 <= mtu <= 65535:
        raise RuntimeError('unsupported eth0 MTU; inspect the network path before configuration')
    auth = dict(auth_url=common.AUTH_URL, auth_type='password',
                project_domain_name='Default', user_domain_name='Default',
                project_name='service', username='neutron', password=secrets['NEUTRON_PASS'])
    neutron = {
        'DEFAULT': {
            'core_plugin': 'ml2', 'service_plugins': 'ovn-router',
            'transport_url': 'rabbit://openstack:' + secrets['RABBIT_PASS'] + '@127.0.0.1:5672/',
            'auth_strategy': 'keystone', 'bind_host': '127.0.0.1',
            'api_workers': '1', 'rpc_workers': '1', 'rpc_state_report_workers': '1',
            'notify_nova_on_port_status_changes': 'true',
            'notify_nova_on_port_data_changes': 'true', 'global_physnet_mtu': str(mtu),
        },
        'database': {'connection': 'mysql+pymysql://neutron:' + secrets[DB_KEY] + '@127.0.0.1/neutron'},
        'keystone_authtoken': dict(auth, www_authenticate_uri='http://127.0.0.1:5000/',
                                  memcached_servers='127.0.0.1:11211'),
        'nova': dict(auth, username='nova', password=secrets['NOVA_PASS'], region_name='RegionOne'),
        'oslo_concurrency': {'lock_path': '/var/lib/neutron/tmp'},
    }
    ml2 = {
        'ml2': {'type_drivers': 'geneve', 'tenant_network_types': 'geneve',
                'mechanism_drivers': 'ovn', 'extension_drivers': 'port_security',
                'overlay_ip_version': '4', 'path_mtu': str(mtu)},
        'ml2_type_geneve': {'vni_ranges': '1:65536', 'max_header_size': '38'},
        'securitygroup': {'enable_security_group': 'true'},
        'ovn': {'ovn_nb_connection': 'tcp:127.0.0.1:6641',
                'ovn_sb_connection': 'tcp:127.0.0.1:6642',
                'ovn_metadata_enabled': 'true', 'ovn_l3_scheduler': 'leastloaded',
                'neutron_sync_mode': 'log'},
    }
    metadata = {
        'DEFAULT': {'nova_metadata_host': '127.0.0.1', 'nova_metadata_port': '8775',
                    'nova_metadata_protocol': 'http',
                    'metadata_proxy_shared_secret': secrets['METADATA_SECRET'],
                    'metadata_workers': '0'},
        'ovs': {'ovsdb_connection': 'unix:/run/openvswitch/db.sock'},
        'ovn': {'ovn_sb_connection': 'tcp:127.0.0.1:6642'},
    }
    return neutron, ml2, metadata


def prepare_database(secrets):
    path = common.SECRET_FILE
    rows = [line.partition('=')[2] for line in path.read_text().splitlines()
            if line.startswith(DB_KEY + '=')]
    if not rows:
        assets = common.sql("SELECT (SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='neutron') + (SELECT COUNT(*) FROM mysql.user WHERE User='neutron')")
        if assets != '0':
            raise RuntimeError('existing Neutron database/account without task credentials; preserving it')
        with path.open('a') as stream:
            stream.write(f'{DB_KEY}={random_secrets.token_hex(32)}\n')
            stream.flush()
            os.fsync(stream.fileno())
    # Reload into the same dictionary so even later errors redact the new secret.
    secrets.update(common.read_secrets(path, common.REQUIRED_KEYS + (DB_KEY,)))
    password = secrets[DB_KEY]  # read_secrets restricts this value to 64 hex chars.
    common.sql(f"""
CREATE DATABASE IF NOT EXISTS neutron CHARACTER SET utf8 COLLATE utf8_general_ci;
CREATE USER IF NOT EXISTS 'neutron'@'127.0.0.1' IDENTIFIED BY '{password}';
GRANT ALL PRIVILEGES ON neutron.* TO 'neutron'@'127.0.0.1'
""")
    import pymysql
    with pymysql.connect(host='127.0.0.1', user='neutron', password=password,
                         database='neutron', connect_timeout=5, read_timeout=5,
                         write_timeout=5) as database:
        with database.cursor() as cursor:
            cursor.execute('SELECT 1')
            if cursor.fetchone() != (1,):
                raise RuntimeError('unexpected Neutron database authentication response')
    progress('database creation and Neutron account authentication: PASS')


def read_ovs_external_ids():
    result = json.loads(common.run(['ovs-vsctl', '--timeout=5', '--format=json',
                                   '--columns=external_ids', 'list', 'Open_vSwitch']))
    if len(result['data']) != 1 or result['data'][0][0][0] != 'map':
        raise RuntimeError('unexpected OVS external-ID inventory')
    return dict(result['data'][0][0][1])


def verify_backend(host_ip):
    common.run(['ovs-vsctl', '--timeout=5', 'br-exists', 'br-int'])
    values = read_ovs_external_ids()
    if values.get('ovn-encap-ip') != host_ip or 'geneve' not in values.get('ovn-encap-type', '').split(','):
        raise RuntimeError('existing OVN Geneve endpoint differs from current eth0; preserving it for review')
    if not values.get('system-id'):
        raise RuntimeError('existing OVS system-id is missing; no identity will be generated automatically')
    for utility, endpoint in [('ovn-nbctl', 'ptcp:6641:127.0.0.1'),
                              ('ovn-sbctl', 'ptcp:6642:127.0.0.1')]:
        listeners = common.run([utility, '--timeout=5', 'get-connection'])
        if endpoint not in listeners or '0.0.0.0' in listeners:
            raise RuntimeError('OVN listener differs from the verified loopback topology; preserving it')
    return values


def network_readiness(cloud):
    networks = list(cloud.network.networks())
    aliases = {extension.alias for extension in cloud.network.extensions()}
    required = {'router', 'security-group', 'port-security'}
    if not required <= aliases:
        raise RuntimeError('Neutron is missing required router/security/port-security extensions')
    agents = list(cloud.network.agents())
    healthy = [agent for agent in agents if agent.is_alive and agent.is_admin_state_up]
    controller_hosts = {agent.host for agent in healthy if 'OVN Controller' in agent.agent_type}
    metadata_hosts = {agent.host for agent in healthy if 'OVN Metadata' in agent.agent_type}
    if not controller_hosts.intersection(metadata_hosts):
        return None
    return networks, healthy


def verify_api(secrets):
    with common.connection(secrets) as cloud:
        last_error = 'OVN controller/metadata agents not both alive yet'
        for attempt in range(30):
            try:
                ready = network_readiness(cloud)
                if ready:
                    networks, agents = ready
                    progress(f'authenticated network-list query: PASS ({len(networks)} networks)')
                    progress('router, security-group and port-security extensions: PASS')
                    for agent in agents:
                        if 'OVN' in agent.agent_type:
                            progress(f'{agent.agent_type}: host={agent.host} alive/admin-up')
                    return
            except Exception as exc:
                last_error = f'{type(exc).__name__}: {exc}'
            if attempt % 5 == 0:
                progress('waiting for Neutron API and OVN controller/metadata agent registration')
            time.sleep(2)
        raise RuntimeError('Neutron readiness timed out: ' + last_error)


def main():
    secrets = {}
    try:
        if os.geteuid() != 0:
            raise RuntimeError('run with sudo /usr/bin/python3 in the actual Ubuntu 22.04 WSL terminal')
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M):
            raise RuntimeError('Ubuntu 22.04 is required')
        if 'microsoft' not in platform.release().lower():
            raise RuntimeError('this script is scoped to the approved WSL host')
        lock_fd = os.open('/root/.openstack-poc-nova-prerequisites.lock',
                          os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            progress('BEGIN')
            secrets = common.read_secrets(common.SECRET_FILE)
            if common.sql('SELECT COUNT(*) FROM nova.instances WHERE deleted=0') != '0':
                raise RuntimeError('live Nova instances exist; do not reconfigure networking around workloads')
            neutron_exists = common.sql("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='neutron'") == '1'
            if neutron_exists and common.sql("SHOW TABLES FROM neutron LIKE 'networks'"):
                if common.sql('SELECT COUNT(*) FROM neutron.networks') != '0':
                    raise RuntimeError('Neutron networks already exist; preserve them and inspect before reconfiguration')
            for unit in ('ovn-controller', 'ovn-northd', 'rabbitmq-server', 'memcached'):
                common.run(['systemctl', 'is-active', '--quiet', unit], timeout=10)
            addresses = json.loads(common.run(['ip', '-j', '-4', 'addr', 'show', 'dev', 'eth0']))
            candidates = [a['local'] for row in addresses for a in row['addr_info'] if a['scope'] == 'global']
            if len(candidates) != 1:
                raise RuntimeError('expected one global IPv4 address on WSL eth0')
            host_ip = str(ipaddress.IPv4Address(candidates[0]))
            link = json.loads(common.run(['ip', '-j', 'link', 'show', 'dev', 'eth0']))
            mtu = link[0]['mtu']
            previous = verify_backend(host_ip)
            with common.connection(secrets, 'neutron') as service:
                service.session.get_token()
            progress('existing Neutron service identity authentication: PASS')
            prepare_database(secrets)
            options = desired_options(secrets, mtu)
            for path, backup, updates in (
                ('/etc/neutron/neutron.conf', '/root/neutron.conf.before-poc', options[0]),
                (PLUGIN_CONFIG, '/root/ml2_conf.ini.before-poc', options[1]),
                (METADATA_CONFIG, '/root/neutron_ovn_metadata_agent.ini.before-poc', options[2]),
            ):
                common.update_config(Path(path), Path(backup), updates)
            common.update_text_file(Path('/etc/default/neutron-server'),
                                    Path('/root/neutron-server.defaults.before-poc'), plugin_environment)
            progress(f'configuration files backed up and updated; underlay MTU={mtu}')
            common.run(['runuser', '-u', 'neutron', '--', 'neutron-db-manage',
                        '--config-file', '/etc/neutron/neutron.conf',
                        '--config-file', PLUGIN_CONFIG, 'upgrade', 'head'])
            progress('Neutron database schema migration: PASS')
            remote_backup = Path('/root/neutron-ovn-remote.before-poc.json')
            if not remote_backup.exists() and not remote_backup.is_symlink():
                with remote_backup.open('x') as stream:
                    json.dump({'ovn-remote': previous.get('ovn-remote')}, stream)
            elif (remote_backup.is_symlink() or not stat.S_ISREG(remote_backup.stat().st_mode)
                  or remote_backup.stat().st_uid != 0 or remote_backup.stat().st_mode & 0o077):
                raise RuntimeError('unsafe existing OVN remote backup; preserving it')
            common.run(['ovs-vsctl', '--timeout=5', 'set', 'Open_vSwitch', '.',
                        'external_ids:ovn-remote=tcp:127.0.0.1:6642'])
            common.run(['systemctl', 'restart', 'neutron-server', 'neutron-ovn-metadata-agent'])
            progress('Neutron API/metadata services restarted; checking actual API and OVN registration')
            verify_api(secrets)
            progress('COMPLETE (tenant network, packet isolation, metadata and VM validation remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr, flush=True)
        print('Existing backups and credentials retained. Share this sanitized output to resume.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
