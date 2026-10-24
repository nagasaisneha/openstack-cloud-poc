#!/usr/bin/python3
"""Task 11 stage 2: configure the approved Ubuntu 22.04 WSL Nova node.

Run manually with sudo /usr/bin/python3. No VM creation or Neutron deployment.
Credentials stay in the existing root-only file and service configuration.
"""

import configparser
import fcntl
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tempfile
import time


SECRET_FILE = Path('/root/openstack-poc-keystone-secrets')
AUTH_URL = 'http://127.0.0.1:5000/v3'
REQUIRED_KEYS = (
    'ADMIN_PASS', 'NOVA_DBPASS', 'NOVA_PASS', 'NEUTRON_PASS',
    'RABBIT_PASS', 'METADATA_SECRET', 'PLACEMENT_PASS',
)


def progress(message):
    print(f'Task 11 Nova configuration: {message}', flush=True)


def read_secrets(path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
        raise RuntimeError('secret file must be a regular root-owned file')
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError('secret file must have mode 0600')
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition('=')
        if key not in REQUIRED_KEYS:
            continue
        if not separator or key in values or not re.fullmatch('[0-9a-f]{64}', value):
            raise RuntimeError(f'invalid or duplicate credential entry: {key}')
        values[key] = value
    if set(values) != set(REQUIRED_KEYS):
        raise RuntimeError('required credentials are missing; run the prerequisite stage')
    return values


def redact(message, secrets):
    for value in secrets.values():
        message = message.replace(value, '[REDACTED]')
    message = re.sub(r'(\b[\w+.-]+://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', message)
    return re.sub(r'(?i)((?:x-auth-token|x-subject-token|authorization)\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+',
                  r'\1[REDACTED]', message)


def edit_ini(text, updates):
    """Edit selected options, preserving unrelated settings and comments."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)  # Reject ambiguous duplicate sections/options first.
    lines = text.splitlines(keepends=True)
    for section, options in updates.items():
        header = re.compile(r'^\s*\[' + re.escape(section) + r'\]\s*(?:[#;].*)?$')
        start = next((i for i, line in enumerate(lines) if header.match(line)), None)
        if start is None:
            if lines and not lines[-1].endswith('\n'):
                lines[-1] += '\n'
            lines.append(f'\n[{section}]\n')
            start = len(lines) - 1
        end = next((i for i in range(start + 1, len(lines))
                    if re.match(r'^\s*\[[^]]+\]', lines[i])), len(lines))
        for key, value in options.items():
            match = re.compile(r'^\s*' + re.escape(key) + r'\s*[=:]', re.IGNORECASE)
            indices = [i for i in range(start + 1, end) if match.match(lines[i])]
            if value is None:
                for index in reversed(indices):
                    del lines[index]
                    end -= 1
            elif indices:
                lines[indices[0]] = f'{key} = {value}\n'
            else:
                if end and not lines[end - 1].endswith('\n'):
                    lines[end - 1] += '\n'
                lines.insert(end, f'{key} = {value}\n')
                end += 1
    result = ''.join(lines)
    parser.read_string(result)
    return result


def update_config(path, backup, updates):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f'{path} must be a regular file, not a symlink')
    original = path.read_text()
    updated = edit_ini(original, updates)
    if backup.exists() or backup.is_symlink():
        backup_stat = backup.lstat()
        if not stat.S_ISREG(backup_stat.st_mode) or backup_stat.st_uid != 0 or stat.S_IMODE(backup_stat.st_mode) != 0o600:
            raise RuntimeError(f'unsafe existing backup: {backup}')
    else:
        with backup.open('x') as stream:
            stream.write(original)
        backup.chmod(0o600)
        with Path(str(backup) + '.metadata.json').open('x') as stream:
            json.dump({'uid': metadata.st_uid, 'gid': metadata.st_gid,
                       'mode': stat.S_IMODE(metadata.st_mode)}, stream)
    if updated == original:
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.task11-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(updated)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)  # Only the temporary file created above.


def run(command, *, input_text=None, timeout=180):
    result = subprocess.run(command, input=input_text, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f'{command[0]} failed (exit {result.returncode}): {result.stdout[-6000:]}')
    return result.stdout


def sql(statement):
    return run(['mysql', '--protocol=SOCKET', '--host=localhost', '--batch', '--raw', '--skip-column-names'],
               input_text=statement + ';\n', timeout=30).strip()


def connection(secrets, username='admin', scoped=True):
    # Explicit password sessions avoid loading a clouds.yaml or passing secrets
    # as subprocess command arguments. No debug logging or token persistence.
    from keystoneauth1 import session
    from keystoneauth1.identity import v3
    from openstack.connection import Connection
    password_key = 'ADMIN_PASS' if username == 'admin' else username.upper() + '_PASS'
    attrs = dict(auth_url=AUTH_URL, username=username,
                 password=secrets[password_key], user_domain_id='default')
    if scoped:
        attrs.update(project_name='admin' if username == 'admin' else 'service',
                     project_domain_id='default')
    return Connection(session=session.Session(auth=v3.Password(**attrs), timeout=10),
                      region_name='RegionOne', interface='internal')


def configure_identity(secrets):
    with connection(secrets) as cloud:
        identity = cloud.identity
        project = identity.find_project('service', domain_id='default', ignore_missing=False)
        admin = identity.find_role('admin', ignore_missing=False)
        service_role = identity.find_role('service') or identity.create_role(name='service')
        for name in ('nova', 'neutron'):
            user = identity.find_user(name, domain_id='default')
            if user is None:
                user = identity.create_user(name=name, domain_id='default',
                                            password=secrets[name.upper() + '_PASS'],
                                            is_enabled=True)
            # Never reset an existing identity password. Check retained credentials
            # with unscoped auth before assigning any project privilege.
            with connection(secrets, name, scoped=False) as candidate:
                if candidate.session.get_user_id() != user.id:
                    raise RuntimeError(f'existing {name} identity does not match retained credentials')
            for role in (admin, service_role):
                identity.assign_project_role_to_user(project, user, role)
            with connection(secrets, name) as candidate:
                candidate.session.get_token()
            progress(f'{name} service identity authentication: PASS')
        for name, kind, url in (
            ('nova', 'compute', 'http://127.0.0.1:8774/v2.1'),
            ('neutron', 'network', 'http://127.0.0.1:9696'),
        ):
            service = identity.find_service(name)
            if service is None:
                service = identity.create_service(name=name, type=kind,
                                                  description='Task 11 local OpenStack POC',
                                                  is_enabled=True)
            if service.type != kind or not service.is_enabled:
                raise RuntimeError(f'conflicting existing service catalog entry: {name}')
            endpoints = list(identity.endpoints(service_id=service.id))
            for interface in ('public', 'internal', 'admin'):
                matches = [e for e in endpoints if e.interface == interface and e.region_id == 'RegionOne']
                if len(matches) > 1 or (matches and (matches[0].url != url or not matches[0].is_enabled)):
                    raise RuntimeError(f'conflicting {name} {interface} endpoint; preserving it')
                if not matches:
                    identity.create_endpoint(service_id=service.id, interface=interface,
                                             url=url, region_id='RegionOne', is_enabled=True)
    progress('compute catalog configured; network catalog registered for the pending Neutron stage')


def desired_options(secrets, host_ip):
    nova_auth = dict(auth_url=AUTH_URL, auth_type='password',
                     project_domain_name='Default', user_domain_name='Default',
                     project_name='service', username='nova', password=secrets['NOVA_PASS'])
    return {
        'DEFAULT': {
            'enabled_apis': 'osapi_compute,metadata',
            'transport_url': 'rabbit://openstack:' + secrets['RABBIT_PASS'] + '@127.0.0.1:5672/',
            'my_ip': host_ip, 'osapi_compute_listen': '127.0.0.1',
            'osapi_compute_workers': '1', 'metadata_listen': '127.0.0.1',
            'metadata_workers': '1', 'log_dir': None,
        },
        'api_database': {'connection': 'mysql+pymysql://nova:' + secrets['NOVA_DBPASS'] + '@127.0.0.1/nova_api'},
        'database': {'connection': 'mysql+pymysql://nova:' + secrets['NOVA_DBPASS'] + '@127.0.0.1/nova'},
        'api': {'auth_strategy': 'keystone'},
        'keystone_authtoken': dict(nova_auth, www_authenticate_uri='http://127.0.0.1:5000/',
                                  memcached_servers='127.0.0.1:11211'),
        'service_user': dict(nova_auth, send_service_user_token='true'),
        'placement': dict(nova_auth, username='placement', password=secrets['PLACEMENT_PASS'], region_name='RegionOne'),
        'glance': {'api_servers': 'http://127.0.0.1:9292'},
        'neutron': dict(nova_auth, username='neutron', password=secrets['NEUTRON_PASS'],
                        region_name='RegionOne', service_metadata_proxy='true',
                        metadata_proxy_shared_secret=secrets['METADATA_SECRET']),
        'vnc': {'enabled': 'false'},
        'oslo_concurrency': {'lock_path': '/var/lib/nova/tmp'},
        'scheduler': {'discover_hosts_in_cells_interval': '300'},
    }


def validate_cells(rows, secrets):
    """Reject conflicting existing cells instead of remapping their state."""
    base = 'mysql+pymysql://nova:' + secrets['NOVA_DBPASS'] + '@127.0.0.1/'
    rabbit = 'rabbit://openstack:' + secrets['RABBIT_PASS'] + '@127.0.0.1:5672/'
    names = set()
    for name, uuid, transport, database, disabled in rows:
        if name in names or name not in ('cell0', 'cell1') or disabled != '0':
            raise RuntimeError('unexpected, duplicate, or disabled existing cell; preserving it')
        names.add(name)
        if name == 'cell0':
            valid = (uuid == '00000000-0000-0000-0000-000000000000'
                     and transport.rstrip('/') == 'none:' and database == base + 'nova_cell0')
        else:
            valid = transport == rabbit and database == base + 'nova'
        if not valid:
            raise RuntimeError(f'{name} connection differs from the approved local configuration; preserving it')
    return names


def initialize_cells(secrets):
    manage = ['runuser', '-u', 'nova', '--', 'nova-manage']
    run(manage + ['api_db', 'sync'])

    def inspect():
        raw = sql('SELECT name,uuid,transport_url,database_connection,disabled FROM nova_api.cell_mappings')
        return [line.split('\t') for line in raw.splitlines()] if raw else []

    names = validate_cells(inspect(), secrets)
    if 'cell0' not in names:
        run(manage + ['cell_v2', 'map_cell0'])
    if 'cell1' not in names:
        run(manage + ['cell_v2', 'create_cell', '--name=cell1'])
    run(manage + ['db', 'sync'])
    rows = inspect()
    if validate_cells(rows, secrets) != {'cell0', 'cell1'}:
        raise RuntimeError('expected cell0 and cell1 were not registered')
    for name, uuid, _, _, _ in rows:
        progress(f'cell initialized: {name} {uuid}')


def observed_compute(cloud):
    healthy = [s for s in cloud.compute.services(binary='nova-compute')
               if s.state == 'up' and s.status == 'enabled']
    if not healthy:
        return None
    providers = list(cloud.placement.resource_providers())
    for hypervisor in cloud.compute.hypervisors(details=True):
        if hypervisor.state != 'up' or hypervisor.status != 'enabled':
            continue
        for provider in providers:
            if provider.name != hypervisor.name:
                continue
            inventories = cloud.placement.resource_provider_inventories(provider)
            by_class = {inventory.resource_class: inventory for inventory in inventories}
            if not {'VCPU', 'MEMORY_MB', 'DISK_GB'} <= set(by_class):
                continue
            if by_class['MEMORY_MB'].total - by_class['MEMORY_MB'].reserved < 256:
                raise RuntimeError('compute reports less than 256 MiB schedulable RAM after host reservation')
            return healthy, hypervisor, provider, by_class
    return None


def verify_compute(secrets):
    # API startup, compute reporting and Placement inventory can become ready at
    # different times. Check all three before reporting stage completion.
    with connection(secrets) as cloud:
        last_error = 'no healthy hypervisor with matching Placement inventory yet'
        for attempt in range(30):
            try:
                observed = observed_compute(cloud)
                if observed:
                    break
            except Exception as exc:
                last_error = f'{type(exc).__name__}: {exc}'
            if attempt % 5 == 0:
                progress('waiting for Nova API, compute registration and Placement inventory')
            time.sleep(2)
        else:
            raise RuntimeError('Nova compute readiness timed out: ' + last_error)
        run(['runuser', '-u', 'nova', '--', 'nova-manage', 'cell_v2', 'discover_hosts'])
        healthy, hypervisor, provider, by_class = observed
        mapped_hosts = set(sql('SELECT host FROM nova_api.host_mappings').splitlines())
        if not any(service.host in mapped_hosts for service in healthy):
            raise RuntimeError('compute service is not mapped to a Nova cell')
        for service in healthy:
            progress(f'compute service: {service.host} {service.state}/{service.status}')
        progress(f'hypervisor: {hypervisor.name} type={hypervisor.hypervisor_type}')
        progress(f'Placement compute provider: {provider.id} (inventory present)')
        for resource_class in sorted(by_class):
            inventory = by_class[resource_class]
            progress(f'inventory {resource_class}: total={inventory.total} reserved={inventory.reserved} ratio={inventory.allocation_ratio}')


def main():
    secrets = {}
    try:
        if os.geteuid() != 0:
            raise RuntimeError('run with sudo /usr/bin/python3 in the actual Ubuntu 22.04 WSL terminal')
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        os_release = Path('/etc/os-release').read_text()
        if not re.search(r'^ID=ubuntu$', os_release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', os_release, re.M):
            raise RuntimeError('Ubuntu 22.04 is required')
        if 'microsoft' not in platform.release().lower():
            raise RuntimeError('this script is scoped to the approved WSL host')
        lock_path = Path('/root/.openstack-poc-nova-prerequisites.lock')
        lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            progress('BEGIN')
            secrets = read_secrets(SECRET_FILE)
            # Do not reconfigure a node that has already acquired live workload.
            if sql("SHOW TABLES FROM nova LIKE 'instances'"):
                if sql('SELECT COUNT(*) FROM nova.instances WHERE deleted=0') != '0':
                    raise RuntimeError('live Nova instances exist; configuration stage is no longer safe to rerun')
            addresses = json.loads(run(['ip', '-j', '-4', 'addr', 'show', 'dev', 'eth0']))
            candidates = [a['local'] for row in addresses for a in row['addr_info'] if a['scope'] == 'global']
            if len(candidates) != 1:
                raise RuntimeError('expected one global IPv4 address on WSL eth0')
            host_ip = str(ipaddress.IPv4Address(candidates[0]))
            configure_identity(secrets)
            update_config(Path('/etc/nova/nova.conf'), Path('/root/nova.conf.before-poc'), desired_options(secrets, host_ip))
            update_config(Path('/etc/nova/nova-compute.conf'), Path('/root/nova-compute.conf.before-poc'), {
                'DEFAULT': {'compute_driver': 'libvirt.LibvirtDriver',
                            'reserved_host_memory_mb': '6144',
                            'cpu_allocation_ratio': '1.0', 'ram_allocation_ratio': '1.0',
                            'disk_allocation_ratio': '1.0'},
                'libvirt': {'virt_type': 'kvm', 'connection_uri': 'qemu:///system'},
            })
            progress('Nova configuration backed up and updated; current eth0 address=' + host_ip)
            initialize_cells(secrets)
            run(['systemctl', 'restart', 'nova-api', 'nova-conductor', 'nova-scheduler', 'nova-compute'])
            progress('Nova services restarted; checking actual API/provider registration')
            verify_compute(secrets)
            progress('COMPLETE (Neutron and real VM/network validation remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr, flush=True)
        print('Existing backups and credentials retained. Share this sanitized output to resume.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
