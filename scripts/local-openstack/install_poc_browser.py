#!/usr/bin/python3
"""Plan/install the local Horizon usage/billing extension and read-only meter.

Uses the existing Ubuntu, Horizon TLS listener, Keystone and Swift. Preserves
the verified backup and existing resources. No OS/package upgrade or rehearsal.
Runtime code is copied into a content-addressed root-owned release directory;
credentials and mutable state stay in a separate Horizon-owned private directory.
"""
import argparse
import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sysconfig
import tempfile
import time

import configure_nova as common
import configure_swift as swift
import launch_test_vm as launch
import manage_cinder_loop as safety

REPO = Path(__file__).resolve().parents[2]
STATE_DIR = Path('/var/lib/openstack-poc-billing')
RELEASES = Path('/opt/openstack-poc/releases')
PROXY = Path('/etc/swift/proxy-server.conf')
CINDER = Path('/etc/cinder/cinder.conf')
SNIPPET = '''# Managed by openstack-cloud-poc T48
POC_BILLING_STATE_DIR = "/var/lib/openstack-poc-billing"
POC_BILLING_COLLECTOR_CONFIG = "/var/lib/openstack-poc-billing/collector.json"
ANGULAR_FEATURES = dict(globals().get("ANGULAR_FEATURES", {}))
ANGULAR_FEATURES["users_panel"] = False
'''


def collector_units(release):
    service = ('[Unit]\nDescription=Read-only project usage collection for the local POC\n'
               'After=apache2.service swift-proxy.service cinder-volume.service\n\n'
               '[Service]\nType=oneshot\nUser=horizon\nGroup=horizon\n'
               'WorkingDirectory=/var/lib/openstack-poc-billing\n'
               'ExecStart=/usr/bin/python3 -B ' + str(release / 'collect_poc_usage.py') + '\n'
               'TimeoutStartSec=90\nNoNewPrivileges=true\nPrivateTmp=true\n'
               'ProtectSystem=strict\nProtectHome=true\n'
               'ReadWritePaths=/var/lib/openstack-poc-billing\n')
    timer = ('[Unit]\nDescription=Collect local POC project usage every minute\n\n'
             '[Timer]\nOnCalendar=*-*-* *:*:00\nAccuracySec=5\nPersistent=true\n\n'
             '[Install]\nWantedBy=timers.target\n')
    return service, timer


def swift_metering_config(text, journal=False):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    pipeline = parser.get('pipeline:main', 'pipeline').split()
    safety.require(pipeline.count('authtoken') == 1 and pipeline.count('keystoneauth') == 1
                   and pipeline.index('authtoken') < pipeline.index('keystoneauth'),
                   'unsupported_swift_auth_pipeline')
    if 'poc_metering' in pipeline:
        safety.require(pipeline.index('authtoken') < pipeline.index('poc_metering') < pipeline.index('keystoneauth'),
                       'collector_restriction_outside_authenticated_pipeline')
    else:
        pipeline.insert(pipeline.index('keystoneauth'), 'poc_metering')
    roles = [name.strip() for name in parser.get('filter:keystoneauth', 'operator_roles').split(',') if name.strip()]
    if 'billing_collector' not in roles:
        roles.append('billing_collector')
    filter_values = {'paste.filter_factory': 'openstack_cloud_poc.swift_metering:filter_factory'}
    if journal:
        filter_values.update(byte_journal_path='/var/lib/openstack-poc-swift-meter/events.sqlite',
                             meter_projects_path='/etc/swift/poc-meter-projects.json')
    return common.edit_ini(text, {
        'pipeline:main': {'pipeline': ' '.join(pipeline)},
        'filter:poc_metering': filter_values,
        'filter:keystoneauth': {'operator_roles': ','.join(roles)},
    })


def cinder_reader_policy(text):
    # Caracal's deprecated admin_or_owner fallback admits owner readers to
    # volume writes. Use the registered member/reader rules instead.
    return common.edit_ini(text, {'oslo_policy': {'enforce_new_defaults': 'true'}})


def configure_cinder_reader(directory):
    info = safety.protected(CINDER)
    before = CINDER.read_text()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(before)
    policy = Path(parser.get('oslo_policy', 'policy_file', fallback='policy.yaml'))
    if not policy.is_absolute():
        policy = CINDER.parent / policy
    safety.require(not policy.exists() or policy.read_text().strip() in ('', '{}', '---'),
                   'custom_cinder_policy_requires_review')
    after = cinder_reader_policy(before)
    if before != after:
        (directory / 'cinder.conf.before').write_text(before)
        safety.atomic_write(CINDER, after, stat.S_IMODE(info.st_mode), info.st_gid)
    # This POC serves Cinder's WSGI API in Apache, alongside Horizon.
    # Reload even on a retry after configuration was written successfully.
    safety.run(['apache2ctl', 'configtest'])
    safety.run(['systemctl', 'reload', 'apache2.service'], timeout=90)
    from configure_cinder import cloud_ready
    deadline = time.monotonic() + 45
    while True:
        try:
            cloud_ready(common.read_secrets(common.SECRET_FILE))
            return
        except Exception:
            safety.require(time.monotonic() < deadline, 'cinder_not_healthy_after_reader_policy')
            time.sleep(2)


def package_files():
    result = {}
    for path in sorted((REPO / 'openstack_cloud_poc').rglob('*')):
        if path.is_file() and path.suffix in ('.py', '.html', '.js'):
            safety.require(not path.is_symlink(), 'source_symlink_refused')
            result[str(path.relative_to(REPO))] = path.read_bytes()
    result['collect_poc_usage.py'] = (REPO / 'scripts/local-openstack/collect_poc_usage.py').read_bytes()
    return result


def release_hash(files):
    algorithm = hashlib.sha256()
    for name, content in sorted(files.items()):
        algorithm.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
    return algorithm.hexdigest()


def layout():
    spec = importlib.util.find_spec('openstack_dashboard')
    safety.require(spec is not None, 'horizon_not_installed')
    root = Path(spec.origin).resolve().parent
    return root / 'local/enabled', root / 'local/local_settings.d'


def plan():
    safety.protected(PROXY)
    swift_state = swift.protected_json(swift.STATE)
    swift.verify_owned_files(swift_state)
    updated = swift_metering_config(PROXY.read_text(), journal=Path('/etc/swift/poc-meter-projects.json').exists())
    files = package_files()
    digest = release_hash(files)
    enabled, snippets = layout()
    account = pwd.getpwnam('horizon')
    for path in (enabled, snippets):
        info = path.lstat()
        safety.require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                       'unsafe_horizon_extension_directory')
    if STATE_DIR.exists():
        info = STATE_DIR.lstat()
        safety.require(stat.S_ISDIR(info.st_mode) and info.st_uid == account.pw_uid
                       and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_billing_state_directory')
    return {'schema': 1, 'action': 'install_existing_horizon_poc_extension',
            'release_sha256': digest, 'source_file_count': len(files),
            'state_directory': str(STATE_DIR), 'horizon_user': account.pw_name,
            'swift_change': 'authenticated_collector_own_account_HEAD_only',
            'os_upgrade': False, 'backup_or_restore_rehearsal': False,
            'full_wsl_restore_verified': False}, files, updated, enabled, snippets, account


def write_root(path, content, mode=0o644):
    public_code_directory(path.parent)
    safety.atomic_write(path, content, mode)


def public_code_directory(path):
    # The install process has umask 077 for credentials and receipts. Code
    # directories need explicit traversal permissions for service accounts.
    if not path.parent.exists() or path != RELEASES.parent and path.is_relative_to(RELEASES.parent):
        public_code_directory(path.parent)
    path.mkdir(exist_ok=True, mode=0o755)
    info = path.lstat()
    safety.require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                   and not info.st_mode & 0o022, 'unsafe_code_directory')
    os.chmod(path, 0o755)


def apply():
    summary, files, proxy_text, enabled, snippets, account = plan()
    directory = Path(tempfile.mkdtemp(prefix='browser-extension-install-', dir=launch.DATA_DIR))
    report_path = directory / 'report.json'
    report = {'schema': 1, 'plan': summary, 'created_at': safety.now(), 'phase': 'stage_release'}
    def save(phase):
        report.update(phase=phase, updated_at=safety.now())
        safety.atomic_write(report_path, json.dumps(report, indent=2, sort_keys=True) + '\n', 0o600)
    save('stage_release')
    try:
        release = RELEASES / summary['release_sha256']
        public_code_directory(RELEASES.parent)
        public_code_directory(RELEASES)
        public_code_directory(release)
        for name, content in files.items():
            target = release / name
            if target.exists():
                safety.require(target.read_bytes() == content, 'immutable_release_content_changed')
                public_code_directory(target.parent)
            else:
                write_root(target, content.decode())
        if not STATE_DIR.exists():
            STATE_DIR.mkdir(mode=0o700)
            os.chown(STATE_DIR, account.pw_uid, account.pw_gid)
        pth = Path(sysconfig.get_path('purelib')) / 'openstack_poc_browser.pth'
        previous_release = pth.read_text().strip() if pth.exists() else None
        report['code_release_changed'] = previous_release != str(release)
        snippet = snippets / '_9900_poc_billing.py'
        changes = [(pth, str(release) + '\n'), (snippet, SNIPPET)]
        for name in ('_3190_project_poc_billing.py', '_2390_admin_poc_billing.py'):
            changes.append((enabled / name, (REPO / 'deployment/horizon-local' / name).read_text()))
        for path, text in changes:
            if path.exists():
                safety.protected(path)
                (directory / (path.name + '.before')).write_bytes(path.read_bytes())
            write_root(path, text)
        safety.run(['runuser', '-u', 'swift', '--', '/usr/bin/python3', '-I', '-B', '-c',
                    'from openstack_cloud_poc.swift_metering import filter_factory'])
        safety.run(['runuser', '-u', 'horizon', '--', '/usr/bin/python3', '-I', '-B', '-c',
                    'import openstack_cloud_poc.horizon'])
        save('horizon_configuration_check')
        safety.run(['runuser', '-u', 'horizon', '--', '/usr/bin/python3', '-B',
                    '/usr/share/openstack-dashboard/manage.py', 'check'], timeout=90)
        safety.run(['runuser', '-u', 'horizon', '--', '/usr/bin/python3', '-B',
                    '/usr/share/openstack-dashboard/manage.py', 'shell', '--command',
                    'from django.template.loader import get_template; '
                    'get_template("poc_billing/admin.html"); get_template("poc_billing/project.html"); '
                    'get_template("poc_billing/operations.html")'], timeout=90)
        # Static auto-discovery changes the offline compression context. Build
        # the matching manifest before Apache serves the new release.
        for command in (['collectstatic', '--noinput'], ['compress', '--force']):
            safety.run(['runuser', '-u', 'horizon', '--', '/usr/bin/python3', '-B',
                        '/usr/share/openstack-dashboard/manage.py', *command], timeout=120)
        save('swift_metering_restriction_intent')
        (directory / 'proxy-server.conf.before').write_bytes(PROXY.read_bytes())
        (directory / 'swift-setup.json.before').write_bytes(swift.STATE.read_bytes())
        info = PROXY.lstat()
        safety.atomic_write(PROXY, proxy_text, stat.S_IMODE(info.st_mode), info.st_gid)
        state = swift.protected_json(swift.STATE)
        before = state['files'][str(PROXY)]
        after = hashlib.sha256(PROXY.read_bytes()).hexdigest()
        state['files'][str(PROXY)] = after
        state.setdefault('configuration_revisions', []).append({
            'task': 'T48', 'path': str(PROXY), 'before_sha256': before,
            'after_sha256': after, 'changed_at': safety.now(),
            'reason': 'restrict_dedicated_meter_to_own_account_HEAD'})
        safety.atomic_write(swift.STATE, json.dumps(state, sort_keys=True, indent=2) + '\n', 0o600)
        swift.verify_owned_files(state)
        proxy_active = 'ActiveState=active' in safety.run(
            ['systemctl', 'show', 'swift-proxy.service', '--property=ActiveState'])
        if before != after or not proxy_active or report['code_release_changed']:
            safety.run(['systemctl', 'restart', 'swift-proxy.service'], timeout=90)
        deadline = time.monotonic() + 60
        while True:
            try:
                report['swift_health'] = swift.service_health(common.read_secrets(common.SECRET_FILE))
                break
            except Exception:
                safety.require(time.monotonic() < deadline, 'swift_health_not_ready_after_configuration')
                time.sleep(2)
        save('reload_existing_horizon')
        safety.run(['apache2ctl', 'configtest'])
        safety.run(['systemctl', 'reload', 'apache2.service'], timeout=90)
        safety.run(['curl', '--silent', '--show-error', '--fail', '--head', '--cacert',
                    '/etc/ssl/certs/horizon-poc-local-ca.crt', 'https://localhost:8443/horizon/'], timeout=30)
        save('enforce_cinder_member_reader_boundary')
        configure_cinder_reader(directory)
        service, timer = collector_units(release)
        write_root(Path('/etc/systemd/system/poc-usage-collector.service'), service)
        write_root(Path('/etc/systemd/system/poc-usage-collector.timer'), timer)
        safety.run(['systemctl', 'daemon-reload'])
        if (STATE_DIR / 'collector.json').exists():
            safety.run(['systemctl', 'enable', '--now', 'poc-usage-collector.timer'])
            result = subprocess.run(['systemctl', 'start', 'poc-usage-collector.service'],
                                     text=True, capture_output=True, timeout=100)
            report['initial_collector_service_exit_code'] = result.returncode
        else:
            report['initial_collector_service_exit_code'] = None
        report['release'] = str(release)
        save('extension_installed')
    except Exception as error:
        report.update(failure_at_phase=report['phase'], error_type=type(error).__name__)
        if isinstance(error, safety.GuardError):
            report['guard_reason'] = str(error)
        save('failed')
        raise
    print(json.dumps({'phase': report['phase'], 'release': str(release), 'report': str(report_path)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    safety.host_guard()
    os.umask(0o077)
    import logging
    logging.disable(logging.CRITICAL)
    if args.apply:
        with safety.locked(safety.MANAGEMENT_LOCK):
            apply()
    else:
        print(json.dumps(plan()[0], sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print('STOP: POC browser extension: ' + reason + '; inspect the protected install report')
        raise SystemExit(2)
