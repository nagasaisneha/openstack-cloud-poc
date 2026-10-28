#!/usr/bin/python3
"""Install/configure loopback Octavia with the VM-free Caracal OVN provider.

Plans by default. Package simulation must contain no upgrades/removals. New
units are masked until configured; Horizon's existing session key is moved to
an explicit protected setting before dashboard package hooks run. No source
resource, OS upgrade, extra distribution or recovery rehearsal is involved.
"""
import argparse
import grp
import hashlib
import json
import logging
from pathlib import Path
import re
import secrets
import subprocess
import time

import configure_nova as common
import continue_poc as task
import install_poc_browser as install
import manage_cinder_loop as safety

PACKAGES = {
    'octavia-api': '1:14.0.0-0ubuntu1.5~cloud0',
    'octavia-driver-agent': '1:14.0.0-0ubuntu1.5~cloud0',
    'octavia-common': '1:14.0.0-0ubuntu1.5~cloud0',
    'python3-octavia': '1:14.0.0-0ubuntu1.5~cloud0',
    'python3-ovn-octavia-provider': '6.0.0-0ubuntu2~cloud0',
    'python3-octavia-dashboard': '13.0.0-0ubuntu1~cloud0',
    'python3-octaviaclient': '3.7.0-0ubuntu1~cloud0',
}
CONFIG = Path('/etc/octavia/octavia.conf')
UNITS = ('octavia-api.service', 'octavia-driver-agent.service')


def package_command(simulate=True):
    return ['apt-get'] + (['--simulate'] if simulate else ['--assume-yes']) + [
        '--no-upgrade', '--no-install-recommends', 'install'] + [name + '=' + version for name, version in PACKAGES.items()]


def checked_plan():
    plan = safety.run(package_command(), timeout=90)
    safety.require(re.search(r'\b0 upgraded, \d+ newly installed, 0 to remove', plan)
                   and not re.search(r'^(?:Remv|Inst \S+ \[)', plan, re.M), 'package_plan_changes_existing_packages')
    return plan


def session_key():
    value = safety.run(['runuser', '-u', 'horizon', '--', '/usr/bin/python3', '-B',
        '/usr/share/openstack-dashboard/manage.py', 'shell', '--command',
        'import json; from django.conf import settings; print(json.dumps(settings.SECRET_KEY))'], timeout=90)
    result = json.loads(value.strip().splitlines()[-1])
    safety.require(isinstance(result, str) and len(result) >= 32, 'invalid_existing_horizon_session_key')
    return result


def packages(source):
    state = task.read('octavia-install', source)
    plan = checked_plan()
    safety.atomic_write(task.DIRECTORY / 'octavia-package-plan.txt', plan, 0o600)
    key = session_key()
    keypath = Path('/etc/openstack-dashboard/poc-session-key.json')
    if keypath.exists():
        safety.protected(keypath, 0o640)
        safety.require(json.loads(keypath.read_text()) == key, 'existing_explicit_horizon_key_differs')
    else:
        safety.atomic_write(keypath, json.dumps(key) + '\n', 0o640, grp.getgrnam('horizon').gr_gid)
    enabled, snippets = install.layout()
    install.write_root(snippets / '_9995_poc_session_key.py',
        'import json as _poc_key_json\nfrom pathlib import Path as _PocKeyPath\n'
        'SECRET_KEY = _poc_key_json.loads(_PocKeyPath("/etc/openstack-dashboard/poc-session-key.json").read_text())\n')
    safety.require(session_key() == key, 'explicit_horizon_session_key_not_preserved')
    for unit in UNITS:
        safety.run(['systemctl', 'mask', '--runtime', unit])
    task.intent('octavia-install', state, 'packages', 'install_only_pinned_caracal_packages_no_upgrades')
    result = subprocess.run(package_command(False), capture_output=True, text=True, timeout=300,
                            env=dict(__import__('os').environ, DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='l'))
    safety.atomic_write(task.DIRECTORY / 'octavia-package-install.log', result.stdout + result.stderr, 0o600)
    safety.require(result.returncode == 0, 'octavia_package_install_failed_inspect_private_log')
    safety.require(session_key() == key, 'horizon_session_key_changed_by_package_hook')
    state.update(phase='pinned_packages_installed_services_masked', horizon_session_key_preserved=True,
                 package_versions=PACKAGES, os_upgrade=False, backup_or_restore_rehearsal=False)
    task.save('octavia-install', state)
    print(json.dumps({'phase': state['phase'], 'horizon_session_key_preserved': True}), flush=True)


def configuration(source):
    state = task.read('octavia-install', source)
    safety.require(state.get('phase') in ('pinned_packages_installed_services_masked', 'octavia_ovn_api_and_agent_healthy'),
                   'octavia_packages_not_ready')
    credentials = task.DIRECTORY / 'octavia-private.json'
    if credentials.exists():
        safety.protected(credentials, 0o600)
        private = json.loads(credentials.read_text())
    else:
        safety.require(common.sql("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='octavia'") == '0',
                       'unowned_octavia_database_preserved')
        private = {'database_password': secrets.token_hex(32), 'service_password': secrets.token_hex(32)}
        safety.atomic_write(credentials, json.dumps(private) + '\n', 0o600)
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        identity = cloud.identity
        service_project = identity.find_project('service', domain_id='default', ignore_missing=False)
        admin = identity.find_role('admin', ignore_missing=False)
        role = identity.find_role('service', ignore_missing=False)
        user = identity.find_user('poc-octavia', domain_id='default')
        if user is None:
            task.intent('octavia-install', state, 'service-user', 'create_dedicated_octavia_service_identity')
            user = identity.create_user(name='poc-octavia', domain_id='default', password=private['service_password'],
                                        description=state['marker'], is_enabled=True)
            state['user_id'] = user.id
            task.save('octavia-install', state)
        safety.require(user.id == state.get('user_id') and user.description == state['marker'], 'foreign_octavia_identity_preserved')
        # The OVN provider must manage VIP/health-check ports across projects.
        # Scope is the existing service project, never system/tenant admin grants.
        for item in (admin, role):
            identity.assign_project_role_to_user(service_project, user, item)
        service = identity.find_service('poc-octavia')
        if service is None:
            safety.require(not list(identity.services(type='load-balancer')), 'existing_load_balancer_catalog_preserved')
            task.intent('octavia-install', state, 'catalog', 'create_local_octavia_catalog')
            service = identity.create_service(name='poc-octavia', type='load-balancer', description=state['marker'])
            state['service_id'] = service.id
            task.save('octavia-install', state)
        safety.require(service.id == state.get('service_id') and service.description == state['marker'], 'foreign_octavia_catalog_preserved')
        for interface in ('public', 'internal', 'admin'):
            endpoints = list(identity.endpoints(service_id=service.id, interface=interface))
            safety.require(len(endpoints) <= 1 and all(e.url == 'http://127.0.0.1:9876' for e in endpoints),
                           'existing_octavia_endpoint_changed')
            if endpoints:
                safety.require(endpoints[0].region_id in (None, 'RegionOne'), 'foreign_octavia_region_preserved')
                if endpoints[0].region_id is None:
                    identity.update_endpoint(endpoints[0], region_id='RegionOne', is_enabled=True)
            if not endpoints:
                identity.create_endpoint(service_id=service.id, interface=interface, region_id='RegionOne',
                                         is_enabled=True, url='http://127.0.0.1:9876')
        member = identity.find_role('load-balancer_member') or identity.create_role(name='load-balancer_member')
        for label in ('a', 'b'):
            project_id, user_id = source['projects'][label]['id'], source['users'][label]['id']
            safety.require(identity.get_project(project_id).description == source['marker']
                           and identity.get_user(user_id).description == source['marker'], 'tenant_role_enrollment_scope_changed')
            identity.assign_project_role_to_user(project_id, user_id, member)
        state['tenant_lb_member_role_project_scoped'] = True
    common.sql("CREATE DATABASE IF NOT EXISTS octavia CHARACTER SET utf8mb4; CREATE USER IF NOT EXISTS 'poc_octavia'@'127.0.0.1' IDENTIFIED BY '" + private['database_password'] + "'; GRANT ALL PRIVILEGES ON octavia.* TO 'poc_octavia'@'127.0.0.1'")
    auth = dict(auth_url=common.AUTH_URL, auth_type='password', project_domain_name='Default',
                user_domain_name='Default', project_name='service', username='poc-octavia', password=private['service_password'])
    values = {'DEFAULT': {'transport_url': 'rabbit://openstack:' + common.read_secrets(common.SECRET_FILE)['RABBIT_PASS'] + '@127.0.0.1:5672/',
                          'log_dir': '/var/log/octavia'},
        'database': {'connection': 'mysql+pymysql://poc_octavia:' + private['database_password'] + '@127.0.0.1/octavia'},
        'api_settings': {'bind_host': '127.0.0.1', 'bind_port': '9876',
                         'auth_strategy': 'keystone', 'enabled_provider_drivers': 'ovn:OVN provider',
                         'default_provider_driver': 'ovn'},
        'keystone_authtoken': dict(auth, www_authenticate_uri='http://127.0.0.1:5000/', memcached_servers='127.0.0.1:11211'),
        'service_auth': auth,
        'driver_agent': {'enabled_provider_agents': 'ovn', 'status_max_processes': '1', 'stats_max_processes': '1', 'get_max_processes': '1'},
        'ovn': {'ovn_nb_connection': 'tcp:127.0.0.1:6641', 'ovn_sb_connection': 'tcp:127.0.0.1:6642'},
        'oslo_policy': {'enforce_new_defaults': 'true'},
        'quotas': {'default_load_balancer_quota': '1', 'default_listener_quota': '2', 'default_pool_quota': '2',
                   'default_member_quota': '4', 'default_health_monitor_quota': '2'}}
    before = CONFIG.read_text()
    safety.atomic_write(CONFIG, common.edit_ini(before, values), 0o640, grp.getgrnam('octavia').gr_gid)
    safety.run(['runuser', '-u', 'octavia', '--', 'octavia-db-manage', '--config-file', str(CONFIG), 'upgrade', 'head'], timeout=120)
    for unit in UNITS:
        override = Path('/etc/systemd/system') / (unit + '.d') / 'poc-capacity.conf'
        install.write_root(override, '[Service]\nMemoryMax=320M\nTasksMax=32\nNoNewPrivileges=true\n')
    safety.run(['systemctl', 'daemon-reload'])
    for unit in UNITS:
        safety.run(['systemctl', 'unmask', '--runtime', unit])
        safety.run(['systemctl', 'enable', '--now', unit])
    safety.run(['apache2ctl', 'configtest'])
    safety.run(['systemctl', 'reload', 'apache2.service'])
    deadline = time.monotonic() + 60
    while True:
        try:
            with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
                response = cloud.load_balancer.get('/lbaas/providers', raise_exc=False)
                safety.require(response.status_code == 200 and any(x['name'] == 'ovn' for x in response.json()['providers']), 'ovn_provider_not_ready')
            for unit in UNITS:
                safety.run(['systemctl', 'is-active', '--quiet', unit])
            break
        except Exception:
            safety.require(time.monotonic() < deadline, 'octavia_not_healthy_inspect_private_service_logs')
            time.sleep(2)
    state['phase'] = 'octavia_ovn_api_and_agent_healthy'
    task.save('octavia-install', state)
    print(json.dumps({'phase': state['phase'], 'provider': 'ovn', 'api_bind': '127.0.0.1:9876', 'extra_vm': False}), flush=True)


def ui(source):
    state = task.read('octavia-install', source)
    safety.require(state.get('phase') == 'octavia_ovn_api_and_agent_healthy', 'octavia_not_ready_for_ui')
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        providers = cloud.load_balancer.get('/lbaas/providers', raise_exc=True).json()['providers']
        safety.require([p['name'] for p in providers] == ['ovn'], 'ui_adapter_requires_exclusively_ovn_provider')
    # Caracal's packaged dashboard omits SOURCE_IP_PORT. A separate Angular
    # decorator offers only this deployed provider's supported TCP features.
    # Never edit a vendor asset or expose an unsupported algorithm to customers.
    safety.require((Path(__file__).resolve().parents[2] / 'openstack_cloud_poc/horizon/static/poc_ovn/driver-compat.js').is_file(),
                   'packaged_ovn_browser_adapter_missing')
    obsolete = {
        install.layout()[0] / '_3195_project_poc_ovn.py':
            "PANEL_GROUP = 'network'\nPANEL_DASHBOARD = 'project'\nADD_JS_FILES = ['poc_ovn/driver-compat.js']\n",
        install.layout()[1] / '_9996_poc_ovn_static.py':
            'STATICFILES_DIRS = tuple(globals().get("STATICFILES_DIRS", ())) + (\'/opt/openstack-poc/octavia-ui/37c3c4d255ae5d73e5f1c65a07f92635ed341a8d71b8ae27a5869a3387ae655e\',)\n',
    }
    for path, expected in obsolete.items():
        if path.exists():
            safety.protected(path)
            safety.require(path.read_text() == expected, 'unexpected_obsolete_ovn_dashboard_configuration')
            path.unlink()
    runtime_snippet = Path('/usr/share/openstack-dashboard/openstack_dashboard/local/local_settings.d/_9996_poc_ovn_static.py')
    if runtime_snippet.exists():
        safety.protected(runtime_snippet)
        expected_prefix = 'STATICFILES_DIRS = tuple(globals().get("STATICFILES_DIRS", ())) + ('
        safety.require(runtime_snippet.read_text().startswith(expected_prefix), 'unexpected_obsolete_runtime_static_configuration')
        runtime_snippet.unlink()
    install.apply()
    state['ovn_ui_adapter_sha256'] = hashlib.sha256(
        (Path(__file__).resolve().parents[2] / 'openstack_cloud_poc/horizon/static/poc_ovn/driver-compat.js').read_bytes()).hexdigest()
    task.save('octavia-install', state)
    print(json.dumps({'phase': 'native_ovn_browser_choices_enabled', 'algorithm': 'SOURCE_IP_PORT',
                      'protocol': 'TCP', 'vendor_files_changed': False}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', choices=('packages', 'configuration', 'ui'))
    args = parser.parse_args()
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    if not args.apply:
        plan = checked_plan()
        print(json.dumps({'phase': 'octavia_plan', 'packages': PACKAGES, 'package_summary': next(l for l in plan.splitlines() if 'newly installed' in l),
                          'provider': 'ovn', 'api_bind': '127.0.0.1:9876', 'extra_vm': False, 'os_upgrade': False}))
    else:
        with task.locked():
            {'packages': packages, 'configuration': configuration, 'ui': ui}[args.apply](source)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'octavia_configuration_stopped', 'reason': str(error) if isinstance(error, safety.GuardError)
                          else type(error).__name__}), flush=True)
        raise SystemExit(2)
