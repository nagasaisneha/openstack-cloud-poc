#!/usr/bin/python3
"""Install a loopback Nova console behind the existing Horizon TLS vhost.

Only new packages are installed (--no-upgrade --no-remove); the proxy is
runtime-masked during package installation. Existing guest data/domains are
preserved. --apply enables VNC for newly created guests and reloads only the
Nova compute worker and Apache; it does not stop/start/recreate any guest.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import time

import configure_nova as common
import install_poc_browser as browser
import launch_test_vm as launch
import manage_cinder_loop as safety
import prepare_cinder_storage as storage
import validate_guest as guest
import validate_lifecycle as lifecycle

ARCHIVE = Path('/tmp/openstack-poc-novnc/novnc-novnc-1.7.0.tgz')
INTEGRITY = 'sha512-ucEJOx4T2avIRCleodk7YobZj5O2Ga2AeLfQ69A/yjG9HHba2+PDgwSkN3FttrmG+70ZGx21sElNFouK13RzyA=='
WEB = Path('/opt/openstack-poc/novnc/1.7.0')
APACHE = Path('/etc/apache2/conf-available/horizon-local-ssl.conf')
NOVA = (Path('/etc/nova/nova.conf'), Path('/etc/nova/nova-compute.conf'))
PROXY_UNIT = 'nova-novncproxy.service'
BEGIN, END = '# BEGIN POC browser console', '# END POC browser console'
PROXY_BLOCK = '''# BEGIN POC browser console
    ProxyPreserveHost On
    ProxyPass /novnc/websockify ws://127.0.0.1:6080/websockify
    ProxyPassReverse /novnc/websockify ws://127.0.0.1:6080/websockify
    ProxyPass /novnc/ http://127.0.0.1:6080/
    ProxyPassReverse /novnc/ http://127.0.0.1:6080/
    <Location /novnc/>
        Require local
        RequestHeader set X-Forwarded-Proto "https"
        Header always set Referrer-Policy "no-referrer"
        Header always set Cache-Control "no-store"
        Header always set X-Content-Type-Options "nosniff"
        Header always set Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' wss://localhost:8443 wss://127.0.0.1:8443; img-src 'self' data:; frame-ancestors 'self'"
    </Location>
    SetEnvIf Request_URI "^/novnc/" poc_console_request
    CustomLog ${APACHE_LOG_DIR}/poc-console-access.log "%h %t \\"%m %U %H\\" %>s %b" env=poc_console_request
    # END POC browser console'''


def nova_options(text):
    return common.edit_ini(text, {
        'DEFAULT': {'web': str(WEB)},
        'vnc': {'enabled': 'true', 'server_listen': '127.0.0.1',
                'server_proxyclient_address': '127.0.0.1',
                'novncproxy_host': '127.0.0.1', 'novncproxy_port': '6080',
                'novncproxy_base_url': 'https://localhost:8443/novnc/poc-console.html'},
        'console': {'allowed_origins': 'localhost,127.0.0.1'},
        'consoleauth': {'token_ttl': '300', 'enforce_session_timeout': 'true'},
    })


def apache_options(text):
    safety.require(text.count('</VirtualHost>') == 1 and '<VirtualHost 127.0.0.1:8443>' in text,
                   'unexpected_horizon_tls_vhost')
    if BEGIN in text:
        safety.require(text.count(BEGIN) == text.count(END) == 1, 'console_vhost_markers_invalid')
        start, finish = text.index(BEGIN), text.index(END) + len(END)
        text = text[:start] + text[finish:].lstrip('\n')
    original = 'CustomLog ${APACHE_LOG_DIR}/horizon-local-access.log combined'
    if original + ' env=!poc_console_request' not in text:
        safety.require(text.count(original) == 1, 'unexpected_horizon_access_log')
        text = text.replace(original, original + ' env=!poc_console_request')
    return text.replace('</VirtualHost>', PROXY_BLOCK + '\n</VirtualHost>')


def install_packages():
    version = safety.run(['dpkg-query', '-W', '-f=${Version}', 'nova-common']).strip()
    safety.require(':29.' in version, 'existing_nova_is_not_caracal')
    packages = ['nova-novncproxy=' + version, 'novnc']
    simulation = safety.run(['env', 'LC_ALL=C', 'apt-get', '--simulate', '--no-upgrade', '--no-remove', 'install', *packages])
    safety.require('0 upgraded,' in simulation and '0 to remove' in simulation, 'package_plan_would_upgrade_or_remove')
    safety.run(['systemctl', 'mask', '--runtime', PROXY_UNIT])
    output = safety.run(['env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-y', '--no-upgrade', '--no-remove',
                          '-o', 'Dpkg::Options::=--force-confold', 'install', *packages], timeout=300)
    directory = Path(tempfile.mkdtemp(prefix='browser-console-packages-', dir=launch.DATA_DIR))
    (directory / 'install.log').write_text(output)
    print(json.dumps({'phase': 'new_console_packages_installed_proxy_masked', 'report_directory': str(directory),
                      'os_upgrade': False}))


def stage_frontend():
    actual = 'sha512-' + base64.b64encode(hashlib.sha512(ARCHIVE.read_bytes()).digest()).decode()
    safety.require(actual == INTEGRITY, 'novnc_package_integrity_mismatch')
    with tarfile.open(ARCHIVE, 'r:gz') as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            relative = Path(member.name)
            safety.require(member.isfile() and relative.parts[0] == 'package'
                           and '..' not in relative.parts and not relative.is_absolute(), 'unsafe_frontend_archive')
            target = WEB.joinpath(*relative.parts[1:])
            browser.public_code_directory(target.parent)
            with archive.extractfile(member) as stream:
                data = stream.read()
            if target.exists():
                safety.require(target.read_bytes() == data, 'immutable_frontend_changed')
            else:
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
                with os.fdopen(descriptor, 'wb') as stream:
                    os.fchmod(stream.fileno(), 0o644)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
    safety.require((WEB / 'core/rfb.js').exists(), 'novnc_browser_module_missing')
    for path in (browser.REPO / 'deployment/horizon-local/console').iterdir():
        browser.write_root(WEB / path.name, path.read_text())


def apply():
    safety.require(Path('/usr/bin/nova-novncproxy').exists(), 'console_packages_not_installed')
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        state, ignored = guest.load_run(cloud, require_ssh=False)
        server = cloud.compute.get_server(state['ids']['server'])
        safety.require(lifecycle.settled(server, 'ACTIVE', 1), 'retained_guest_not_active_before_console_change')
    directory = Path(tempfile.mkdtemp(prefix='browser-console-config-', dir=launch.DATA_DIR))
    report = {'schema': 1, 'created_at': safety.now(), 'phase': 'stage_verified_frontend',
              'frontend_version': '1.7.0', 'frontend_integrity': INTEGRITY,
              'source_upgrade_performed': False, 'full_wsl_restore_verified': False}
    path = directory / 'report.json'
    def save(phase):
        report.update(phase=phase, updated_at=safety.now())
        safety.atomic_write(path, json.dumps(report, sort_keys=True, indent=2) + '\n', 0o600)
    save(report['phase'])
    try:
        stage_frontend()
        for config in (*NOVA, APACHE):
            info = safety.protected(config)
            text = config.read_text()
            (directory / (config.name + '.before')).write_text(text)
            updated = apache_options(text) if config == APACHE else nova_options(text)
            safety.atomic_write(config, updated, stat.S_IMODE(info.st_mode), info.st_gid)
        browser.write_root(Path('/etc/systemd/system/nova-novncproxy.service.d/poc-local.conf'),
                            '[Service]\nExecStart=\nExecStart=/usr/bin/nova-novncproxy --config-file /etc/nova/nova.conf\n')
        safety.run(['a2enmod', 'proxy_http', 'proxy_wstunnel', 'headers', 'setenvif'])
        safety.run(['apache2ctl', 'configtest'])
        save('activate_local_console_proxy')
        safety.run(['systemctl', 'daemon-reload'])
        safety.run(['systemctl', 'unmask', '--runtime', PROXY_UNIT])
        safety.run(['systemctl', 'enable', '--now', PROXY_UNIT], timeout=90)
        deadline = time.monotonic() + 30
        while storage.listen_addresses('6080') != ['127.0.0.1:6080']:
            safety.require(time.monotonic() < deadline, 'console_loopback_listener_unverified')
            time.sleep(1)
        safety.run(['systemctl', 'reload', 'apache2.service'], timeout=90)
        save('reload_compute_worker_for_future_console_guests')
        safety.run(['systemctl', 'restart', 'nova-compute.service'], timeout=90)
        with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
            server = cloud.compute.get_server(state['ids']['server'])
            safety.require(lifecycle.settled(server, 'ACTIVE', 1), 'retained_guest_state_changed')
        safety.run(['curl', '--silent', '--show-error', '--fail', '--cacert',
                    '/etc/ssl/certs/horizon-poc-local-ca.crt',
                    'https://localhost:8443/novnc/poc-console.html'], timeout=30)
        report.update(retained_vm_still_active=True, proxy_listener='127.0.0.1:6080',
                      console_token_websocket_verified=False)
        save('console_endpoint_installed')
    except Exception as error:
        report.update(failure_at_phase=report['phase'], error_type=type(error).__name__)
        if isinstance(error, safety.GuardError):
            report['guard_reason'] = str(error)
        save('failed')
        raise
    print(json.dumps({'phase': report['phase'], 'report': str(path),
                      'retained_vm_still_active': True, 'console_token_websocket_verified': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--install-packages', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    safety.host_guard()
    os.umask(0o077)
    if args.install_packages or args.apply:
        with safety.locked(safety.MANAGEMENT_LOCK):
            install_packages() if args.install_packages else apply()
    else:
        print(json.dumps({'action': 'prepare_local_nova_console', 'frontend_version': '1.7.0',
                          'proxy_bind': '127.0.0.1:6080', 'external_url': 'https://localhost:8443/novnc/poc-console.html',
                          'os_upgrade': False, 'retained_vm_power_action': False}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print('STOP: local browser console: ' + reason + '; inspect protected reports')
        raise SystemExit(2)
