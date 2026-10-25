#!/usr/bin/python3
"""Configure and validate the local, loop-backed Cinder POC service.

This is scoped to the existing Ubuntu 22.04 WSL POC. It uses the protected
Task 11 credentials file, creates only Cinder-owned identity/database/catalog
assets, binds API/iSCSI to loopback, and retains recovery metadata. It does not
create or delete tenant volumes; those require the separately recorded volume
acceptance scope.
"""

import configparser
import fcntl
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
from urllib.parse import urlsplit

import configure_nova as common
import launch_test_vm as launch
import prepare_cinder_storage as storage


DATA_DIR = launch.DATA_DIR
STATE = DATA_DIR / 'cinder-setup.json'
SECRET_FILE = common.SECRET_FILE
LOCK = Path('/root/.openstack-poc-nova-prerequisites.lock')
CINDER_CONF = Path('/etc/cinder/cinder.conf')
NOVA_CONF = Path('/etc/nova/nova.conf')
TGT_CONF = Path('/etc/tgt/conf.d/cinder.conf')
DB_NAME = 'cinder'
DB_USER = 'cinder'
DB_HOST = '127.0.0.1'
API_URL = 'http://127.0.0.1:8776/v3/%(project_id)s'
REGION = 'RegionOne'
SERVICE_ROLE = 'service'
BASE_KEYS = common.REQUIRED_KEYS
CINDER_KEYS = ('CINDER_DBPASS', 'CINDER_PASS')


def progress(message):
    print(f'POC Cinder configure: {message}', flush=True)


def secrets_from_file():
    values = common.read_secrets(SECRET_FILE)
    info = SECRET_FILE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('protected credential file must remain root-owned mode 0600')
    extras = {}
    for line in SECRET_FILE.read_text().splitlines():
        key, sep, value = line.partition('=')
        if key not in CINDER_KEYS:
            continue
        if not sep or key in extras or not re.fullmatch(r'[0-9a-f]{64}', value):
            raise RuntimeError(f'invalid or duplicate retained credential entry: {key}')
        extras[key] = value
    values.update(extras)
    return values


def append_secret(key, value):
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError('protected credential file changed ownership or mode')
        os.write(fd, f'{key}={value}\n'.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def save_state(state):
    launch.atomic_state(STATE, state)
    os.chmod(STATE, 0o600)


def load_or_begin_state(cloud, db_schema_count, db_user_count):
    cinder_user = cloud.identity.find_user('cinder', domain_id='default')
    cinder_service = cloud.identity.find_service('cinderv3')
    volume_services = [service for service in cloud.identity.services()
                       if getattr(service, 'type', None) == 'volumev3']
    if STATE.exists() or STATE.is_symlink():
        info = STATE.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError('existing Cinder state file is not protected')
        state = json.loads(STATE.read_text())
        if state.get('schema') != 1 or state.get('task') != 'T18-local-cinder':
            raise RuntimeError('existing Cinder state file has an unexpected schema or owner')
        return state
    if db_schema_count or db_user_count or cinder_user or cinder_service or volume_services:
        raise RuntimeError('pre-existing Cinder database/account/identity/catalog asset; preserve and review before adoption')
    state = {'schema': 1, 'task': 'T18-local-cinder', 'phase': 'initializing',
             'database': DB_NAME, 'database_user': DB_USER,
             'keystone_user': 'cinder', 'service_name': 'cinderv3',
             'endpoint_url': API_URL, 'region': REGION,
             'volume_group': storage.VG, 'loop_device': str(storage.LOOP),
             'created_assets': [], 'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    save_state(state)
    return state


def validate_partial_retry_state():
    """Recognize only this script's protected, identity-complete partial run."""
    if not STATE.exists() or STATE.is_symlink():
        return False
    info = STATE.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise RuntimeError('existing Cinder state file is not protected')
    state = json.loads(STATE.read_text())
    required = {'database', 'keystone_user_cinder', 'keystone_service_cinderv3',
                'endpoint_public', 'endpoint_internal', 'endpoint_admin'}
    return (state.get('schema') == 1 and state.get('task') == 'T18-local-cinder'
            and state.get('phase') == 'initializing'
            and required.issubset(set(state.get('created_assets', []))))


def database_inventory():
    schema_count = int(common.sql("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='cinder'"))
    rows = common.sql("SELECT Host FROM mysql.user WHERE User='cinder' ORDER BY Host")
    hosts = [value.strip() for value in rows.splitlines() if value.strip()]
    return schema_count, hosts


def ensure_secrets(values, schema_count, db_hosts, cinder_user):
    if schema_count and 'CINDER_DBPASS' not in values:
        raise RuntimeError('Cinder database exists without a retained database password; preserving it')
    if db_hosts and 'CINDER_DBPASS' not in values:
        raise RuntimeError('Cinder database account exists without a retained password; preserving it')
    if cinder_user and 'CINDER_PASS' not in values:
        raise RuntimeError('Keystone cinder user exists without a retained password; preserving it')
    for key in CINDER_KEYS:
        if key not in values:
            value = random_secrets.token_hex(32)
            append_secret(key, value)
            values[key] = value
    return values


def verify_database_password(password):
    import pymysql
    try:
        with pymysql.connect(host=DB_HOST, user=DB_USER, password=password,
                             database=DB_NAME, connect_timeout=5,
                             read_timeout=5, write_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                return cursor.fetchone() == (1,)
    except Exception:
        return False


def configure_database(password, existing_hosts):
    if existing_hosts and existing_hosts != [DB_HOST]:
        raise RuntimeError('unexpected MariaDB cinder account host; preserving it')
    if existing_hosts and not verify_database_password(password):
        raise RuntimeError('retained Cinder database password does not authenticate; preserving database/account')
    common.sql(f"CREATE DATABASE IF NOT EXISTS cinder CHARACTER SET utf8 COLLATE utf8_general_ci")
    common.sql(f"CREATE USER IF NOT EXISTS 'cinder'@'{DB_HOST}' IDENTIFIED BY '{password}'")
    common.sql(f"GRANT ALL PRIVILEGES ON cinder.* TO 'cinder'@'{DB_HOST}'")
    if not verify_database_password(password):
        raise RuntimeError('Cinder database account authentication failed')
    progress('database/account authentication: PASS')


def config_options(values):
    cinder_auth = dict(auth_url=common.AUTH_URL, auth_type='password',
                       project_domain_name='Default', user_domain_name='Default',
                       project_name='service', username='cinder', password=values['CINDER_PASS'])
    return {
        'DEFAULT': {
            'transport_url': 'rabbit://openstack:' + values['RABBIT_PASS'] + '@127.0.0.1:5672/',
            'auth_strategy': 'keystone',
            'my_ip': '127.0.0.1',
            'enabled_backends': 'lvm',
            'glance_api_servers': 'http://127.0.0.1:9292',
            'osapi_volume_listen': '127.0.0.1',
            'osapi_volume_listen_port': '8776',
        },
        'database': {'connection': 'mysql+pymysql://cinder:' + values['CINDER_DBPASS'] + '@127.0.0.1/cinder'},
        'keystone_authtoken': dict(cinder_auth, www_authenticate_uri='http://127.0.0.1:5000/v3',
                                  memcached_servers='127.0.0.1:11211',
                                  service_token_roles=SERVICE_ROLE,
                                  service_token_roles_required='true'),
        'service_user': dict(cinder_auth, send_service_user_token='true'),
        'lvm': {
            'volume_driver': 'cinder.volume.drivers.lvm.LVMVolumeDriver',
            'volume_group': storage.VG,
            'volume_backend_name': 'CINDER_POC_LOOP',
            'target_protocol': 'iscsi',
            'target_helper': 'tgtadm',
            'target_ip_address': '127.0.0.1',
            'target_port': '3260',
            'lvm_conf_file': str(storage.LVM_CONFIG),
            'lvm_type': 'default',
        },
        'oslo_concurrency': {'lock_path': '/var/lib/cinder/tmp'},
    }


def require_nova_service_tokens(secrets, cloud, service_project, service_role):
    parser = configparser.ConfigParser(interpolation=None)
    if not NOVA_CONF.is_file() or NOVA_CONF.is_symlink():
        raise RuntimeError('Nova configuration is missing or symlinked')
    parser.read(NOVA_CONF)
    if (not parser.has_section('service_user')
            or parser.getboolean('service_user', 'send_service_user_token', fallback=False) is not True
            or parser.get('service_user', 'username', fallback='') != 'nova'
            or parser.get('service_user', 'project_name', fallback='') != 'service'):
        raise RuntimeError('Nova is not configured to send its service token as the service-project nova user')
    nova = cloud.identity.find_user('nova', domain_id='default', ignore_missing=False)
    with common.connection(secrets, 'nova', scoped=False) as candidate:
        if candidate.session.get_user_id() != nova.id:
            raise RuntimeError('retained nova service credentials do not match Keystone identity')
    assignments = list(cloud.identity.role_assignments(user_id=nova.id,
                                                       scope_project_id=service_project.id))
    if service_role.id not in role_ids_from_assignments(assignments):
        cloud.identity.assign_project_role_to_user(service_project, nova, service_role)
    progress('Nova service-token sender and service role: PASS')


def role_ids_from_assignments(assignments):
    """Extract role IDs from openstacksdk Keystone RoleAssignment resources.

    The SDK exposes the nested Keystone ``role`` reference as a mapping; it
    does not guarantee a flattened ``role_id`` attribute.
    """
    role_ids = set()
    for assignment in assignments:
        role = getattr(assignment, 'role', None)
        if isinstance(role, dict):
            role_id = role.get('id')
        else:
            role_id = getattr(role, 'id', None)
        if role_id is None:
            role_id = getattr(assignment, 'role_id', None)
        if role_id:
            role_ids.add(role_id)
    return role_ids


def ensure_identity_and_catalog(secrets, cloud, state):
    identity = cloud.identity
    service_project = identity.find_project('service', domain_id='default', ignore_missing=False)
    admin_role = identity.find_role('admin', ignore_missing=False)
    service_role = identity.find_role(SERVICE_ROLE)
    if service_role is None:
        service_role = identity.create_role(name=SERVICE_ROLE)
        state['created_assets'].append('keystone_role_service')
        save_state(state)
    require_nova_service_tokens(secrets, cloud, service_project, service_role)

    user = identity.find_user('cinder', domain_id='default')
    if user is None:
        user = identity.create_user(name='cinder', domain_id='default',
                                    password=secrets['CINDER_PASS'], is_enabled=True)
        state['created_assets'].append('keystone_user_cinder')
        save_state(state)
    with common.connection(secrets, 'cinder', scoped=False) as candidate:
        if candidate.session.get_user_id() != user.id:
            raise RuntimeError('retained cinder service credentials do not match Keystone identity')
    assignments = list(identity.role_assignments(user_id=user.id, scope_project_id=service_project.id))
    assigned = role_ids_from_assignments(assignments)
    for role in (admin_role, service_role):
        if role.id not in assigned:
            identity.assign_project_role_to_user(service_project, user, role)
    with common.connection(secrets, 'cinder', scoped=True) as candidate:
        candidate.session.get_token()

    service = identity.find_service('cinderv3')
    if service is None:
        conflicts = [item for item in identity.services()
                     if getattr(item, 'type', None) == 'volumev3']
        if conflicts:
            raise RuntimeError('a differently named volumev3 service already exists; preserving catalog')
        service = identity.create_service(name='cinderv3', type='volumev3',
                                          description='OpenStack Block Storage local POC',
                                          is_enabled=True)
        state['created_assets'].append('keystone_service_cinderv3')
        save_state(state)
    if service.type != 'volumev3' or not service.is_enabled:
        raise RuntimeError('existing cinderv3 service has conflicting type or disabled state')
    endpoints = list(identity.endpoints(service_id=service.id))
    for interface in ('public', 'internal', 'admin'):
        matches = [item for item in endpoints if item.interface == interface and item.region_id == REGION]
        if len(matches) > 1 or (matches and (matches[0].url != API_URL or not matches[0].is_enabled)):
            raise RuntimeError(f'conflicting Cinder {interface} endpoint; preserving it')
        if not matches:
            identity.create_endpoint(service_id=service.id, interface=interface,
                                     url=API_URL, region_id=REGION, is_enabled=True)
            state['created_assets'].append('endpoint_' + interface)
            save_state(state)
    progress('Cinder identity, service role, and volumev3 endpoints: PASS')


def verify_storage():
    state = json.loads(storage.STATE.read_text())
    if (state.get('status') != 'prepared' or state.get('volume_group') != storage.VG
            or state.get('loop_device') != str(storage.LOOP)):
        raise RuntimeError('dedicated Cinder storage state does not match expected loop/VG')
    if storage.loop_inventory() != str(storage.IMAGE):
        raise RuntimeError('Cinder backing image is not attached to the expected loop device')
    pv = storage.pv_inventory()
    groups = storage.vg_inventory()
    group = [item for item in groups if item.get('vg_name', '').strip() == storage.VG]
    if (not pv or pv.get('vg_name', '').strip() != storage.VG
            or len(group) != 1 or group[0].get('pv_count', '').strip() != '1'):
        raise RuntimeError('Cinder dedicated PV/VG failed current-state verification')
    if storage.listen_addresses('3260') != ['127.0.0.1:3260']:
        raise RuntimeError('TGT listener is not loopback-only')
    if storage.listen_addresses('8776') != ['127.0.0.1:8776']:
        raise RuntimeError('Cinder API listener is not loopback-only')


def expected_cinder_endpoint(endpoint, project_id):
    """Accept the catalog template or its project-scoped resolved form."""
    if not isinstance(endpoint, str):
        return False
    try:
        parsed = urlsplit(endpoint)
        expected = urlsplit(API_URL)
    except ValueError:
        return False
    path = parsed.path.rstrip('/')
    allowed_paths = {expected.path.rstrip('/'), f'/v3/{project_id}'}
    return (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
            and parsed.port == 8776 and path in allowed_paths
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment)


def ensure_tgt_config():
    expected = 'include /var/lib/cinder/volumes/*\n'
    if TGT_CONF.exists() or TGT_CONF.is_symlink():
        info = TGT_CONF.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or TGT_CONF.read_text() != expected):
            raise RuntimeError('existing TGT Cinder include differs; preserving it')
    else:
        fd = os.open(TGT_CONF, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, expected.encode())
            os.fchown(fd, 0, 0)
            os.fchmod(fd, 0o644)
            os.fsync(fd)
        finally:
            os.close(fd)


def cloud_ready(secrets):
    with common.connection(secrets) as cloud:
        endpoint = cloud.session.get_endpoint(service_type='volumev3', interface='internal', region_name=REGION)
        if not expected_cinder_endpoint(endpoint, cloud.session.get_project_id()):
            raise RuntimeError('authenticated catalog lookup did not return the expected loopback Cinder endpoint')
        services = list(cloud.block_storage.services())
        scheduler = any(item.binary == 'cinder-scheduler' and item.state == 'up'
                        and item.status == 'enabled' for item in services)
        volume = any(item.binary == 'cinder-volume' and item.state == 'up'
                     and item.status == 'enabled' for item in services)
        if not scheduler or not volume:
            raise RuntimeError('Cinder scheduler or volume service has not registered up/enabled')
        response = cloud.block_storage.get('/scheduler-stats/get_pools?detail=True',
                                            raise_exc=True, microversion='3.0').json()
        pools = response.get('pools', []) if isinstance(response, dict) else []
        matching = [pool.get('capabilities', {}) for pool in pools
                    if isinstance(pool, dict)
                    and isinstance(pool.get('capabilities'), dict)
                    and pool['capabilities'].get('volume_backend_name') == 'CINDER_POC_LOOP']
        try:
            has_capacity = any(float(item.get('free_capacity_gb') or 0) > 0 for item in matching)
        except (TypeError, ValueError):
            has_capacity = False
        if not matching or not has_capacity:
            raise RuntimeError('Cinder scheduler reports no positive free capacity for the dedicated LVM backend')
        progress('authenticated Cinder API, scheduler, volume service, and positive backend capacity: PASS')


def main():
    values = {}
    stage = 'host_preflight'
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    try:
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M)
                or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('this setup is scoped to the existing Ubuntu 22.04 WSL host')
        DATA_DIR_INFO = DATA_DIR.lstat()
        if (not stat.S_ISDIR(DATA_DIR_INFO.st_mode) or DATA_DIR_INFO.st_uid != 0
                or stat.S_IMODE(DATA_DIR_INFO.st_mode) != 0o700):
            raise RuntimeError('protected acceptance directory must be root-owned mode 0700')
        fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            progress('BEGIN')
            verify_storage()
            partial_retry = validate_partial_retry_state()
            for unit in ('cinder-scheduler.service', 'cinder-volume.service'):
                load_state = common.run(['systemctl', 'show', unit, '--property=LoadState']).strip()
                if load_state != 'LoadState=masked' and not partial_retry:
                    raise RuntimeError(f'{unit} must remain masked until the service configuration is complete')
            stage = 'credentials_and_baseline_inventory'
            values = secrets_from_file()
            schema_count, db_hosts = database_inventory()
            with common.connection(values) as cloud:
                cloud.session.get_project_id()
                state = load_or_begin_state(cloud, schema_count, len(db_hosts))
                cinder_user = cloud.identity.find_user('cinder', domain_id='default')
                values = ensure_secrets(values, schema_count, db_hosts, cinder_user)
                if 'CINDER_DBPASS' not in values or 'CINDER_PASS' not in values:
                    raise RuntimeError('Cinder credentials were not retained securely')

                stage = 'database'
                configure_database(values['CINDER_DBPASS'], db_hosts)
                if 'database' not in state['created_assets']:
                    state['created_assets'].append('database')
                    save_state(state)

                stage = 'keystone_identity_and_catalog'
                ensure_identity_and_catalog(values, cloud, state)

            stage = 'configuration_and_schema'
            common.update_config(CINDER_CONF, Path('/root/cinder.conf.before-poc'), config_options(values))
            common.update_config(NOVA_CONF, Path('/root/nova.conf.before-cinder'),
                                 {'cinder': {'os_region_name': REGION}})
            ensure_tgt_config()
            common.run(['systemctl', 'restart', 'tgt.service'], timeout=60)
            if storage.listen_addresses('3260') != ['127.0.0.1:3260']:
                raise RuntimeError('TGT did not return with its loopback-only listener')
            common.run(['/usr/sbin/runuser', '-u', 'cinder', '--',
                        '/usr/bin/cinder-manage', '--config-file', str(CINDER_CONF), 'db', 'sync'], timeout=240)
            common.run(['apache2ctl', 'configtest'], timeout=30)

            stage = 'service_activation'
            common.run(['systemctl', 'restart', 'apache2.service'], timeout=120)
            common.run(['systemctl', 'restart', 'nova-api.service'], timeout=120)
            common.run(['systemctl', 'unmask', '--runtime', 'cinder-scheduler.service', 'cinder-volume.service'])
            common.run(['systemctl', 'enable', 'cinder-scheduler.service', 'cinder-volume.service'])
            common.run(['systemctl', 'start', 'cinder-scheduler.service', 'cinder-volume.service'], timeout=120)
            for unit in ('cinder-scheduler.service', 'cinder-volume.service'):
                common.run(['systemctl', 'is-active', '--quiet', unit], timeout=15)

            stage = 'cloud_validation'
            deadline = time.monotonic() + 120
            last_error = 'services have not registered yet'
            while time.monotonic() < deadline:
                try:
                    cloud_ready(values)
                    break
                except Exception as error:
                    last_error = f'{type(error).__name__}: {error}'
                    time.sleep(4)
            else:
                raise RuntimeError('Cinder cloud validation timed out: ' + last_error)
            state['phase'] = 'ready_for_scoped_volume_test'
            state['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            save_state(state)
            progress(f'PASS (backend ready; no volume created); report={STATE}')
        return 0
    except Exception as error:
        details = common.redact(f'{type(error).__name__}: {error}', values)
        details = ' '.join(details.split())
        details = re.sub(r'[^A-Za-z0-9 _./:=+-]', '?', details)[:240]
        print(f'STOP: Cinder configure incomplete at {stage}: {details}; preserve current assets and inspect before retry.',
              file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
