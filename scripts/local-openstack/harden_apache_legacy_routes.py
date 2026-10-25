#!/usr/bin/python3
"""Restrict legacy Apache Cinder/Glance aliases to IPv4/IPv6 loopback.

Backs up the exact route configuration files, config-tests before Apache's
graceful reload, verifies only the two requested routes changed access, and
checks the authenticated OpenStack/retained-guest path. It never edits the
main document root, Keystone/Placement aliases, vhosts, listeners, or upstreams.
"""
import datetime
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

import configure_nova as common
import launch_test_vm as launch
import review_operations as review
import validate_guest as guest


DATA_DIR = launch.DATA_DIR
STATE = DATA_DIR / 'apache-legacy-route-hardening.json'
TARGETS = {
    Path('/etc/apache2/sites-available/cinder-api.conf'): {
        'enabled': Path('/etc/apache2/sites-enabled/cinder-api.conf'),
        'link': '../sites-available/cinder-api.conf',
        'route': '/volume',
        'proxy': 'ProxyPass "/volume" "unix:/var/run/uwsgi/cinder-api.socket|uwsgi://uwsgi-uds-cinder-api" retry=0 acquire=1',
        'upstream': 'unix:/var/run/uwsgi/cinder-api.socket (uWSGI Cinder API)',
    },
    Path('/etc/apache2/sites-available/glance-api.conf'): {
        'enabled': Path('/etc/apache2/sites-enabled/glance-api.conf'),
        'link': '../sites-available/glance-api.conf',
        'route': '/image',
        'proxy': 'ProxyPass "/image" "http://127.0.0.1:60998" retry=0 acquire=1',
        'upstream': 'http://127.0.0.1:60998 (legacy Glance proxy target)',
    },
}
ALIASES = ('/volume', '/image')
PRESERVE_PATHS = ('/', '/identity', '/placement')
HTTP_APIS = {
    'keystone': ('127.0.0.1', 5000, '/v3'),
    'placement': ('127.0.0.1', 8778, '/'),
    'glance': ('127.0.0.1', 9292, '/'),
    'nova': ('127.0.0.1', 8774, '/'),
    'neutron': ('127.0.0.1', 9696, '/'),
}
ERROR_LOGS = (Path('/var/log/apache2/error.log'),
              Path('/var/log/apache2/keystone.log'),
              Path('/var/log/apache2/placement_api_error.log'))


def progress(message):
    print('Task 11 Apache legacy-route hardening: ' + message, flush=True)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def private_file(path, mode=0o600):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != mode:
        raise RuntimeError('protected file metadata check failed')
    return info


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def write_atomic(path, content, uid, gid, mode):
    if path.is_symlink():
        raise RuntimeError('configuration target became a symlink')
    descriptor, temporary = tempfile.mkstemp(prefix='.task11-apache-route-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_state(record):
    launch.atomic_state(STATE, record)
    os.chmod(STATE, 0o600)
    private_file(STATE)


def route_edit(text, route, proxy):
    proxy_lines = [line.strip() for line in text.splitlines()
                   if re.match(r'^\s*ProxyPass\s+', line)]
    if not proxy_lines or any(line != proxy for line in proxy_lines):
        raise RuntimeError('legacy ProxyPass mapping differs from the reviewed upstream')
    if re.search(r'^\s*ProxyPassReverse\s+', text, re.M):
        raise RuntimeError('unexpected reverse-proxy mapping requires review')

    location_pattern = re.compile(
        r'^<Location\s+["\']?' + re.escape(route) + r'["\']?\s*>\s*'
        r'(.*?)^</Location>\s*$', re.M | re.S)
    matches = list(location_pattern.finditer(text))
    any_location = re.search(r'^\s*<Location(?:Match)?\b', text, re.M)
    if any_location and not matches:
        raise RuntimeError('existing Apache Location policy requires review')
    if len(matches) > 1:
        raise RuntimeError('duplicate route authorization blocks require review')
    if matches:
        body = [line.strip() for line in matches[0].group(1).splitlines() if line.strip()]
        if body != ['Require ip 127.0.0.1 ::1']:
            raise RuntimeError('existing route authorization differs from the requested loopback policy')
        return text

    if re.search(r'^\s*<Location(?:Match)?\s+[^>]*' + re.escape(route), text, re.M):
        raise RuntimeError('overlapping route Location requires review')
    block = ('<Location "' + route + '">\n'
             '    Require ip 127.0.0.1 ::1\n'
             '</Location>\n')
    return text.rstrip('\r\n') + '\n\n' + block


def desired(path, text):
    if path not in TARGETS:
        raise RuntimeError('unexpected Apache target')
    item = TARGETS[path]
    return route_edit(text, item['route'], item['proxy'])


def check_enabled_target(path, item):
    link = item['enabled']
    info = link.lstat()
    if (not stat.S_ISLNK(info.st_mode) or os.readlink(link) != item['link']
            or link.resolve(strict=True) != path.resolve(strict=True)):
        raise RuntimeError('unexpected Apache enabled-site symlink')


def read_targets():
    result = {}
    for path, item in TARGETS.items():
        check_enabled_target(path, item)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Apache route configuration ownership/type/mode requires review')
        before = path.read_bytes()
        try:
            text = before.decode('utf-8')
        except UnicodeDecodeError:
            raise RuntimeError('Apache route configuration encoding requires review') from None
        after = desired(path, text).encode()
        result[path] = dict(before=before, after=after, uid=info.st_uid, gid=info.st_gid,
                            mode=stat.S_IMODE(info.st_mode),
                            enabled_link=item['link'], route=item['route'], upstream=item['upstream'])
    return result


def run(argv, timeout=30):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=timeout, check=False,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'})
    if result.returncode:
        raise RuntimeError('command failed: ' + Path(argv[0]).name)
    return result.stdout, result.stderr


def configtest():
    stdout, stderr = run(['apache2ctl', 'configtest'], timeout=30)
    if 'Syntax OK' not in stdout + stderr:
        raise RuntimeError('Apache configtest did not report Syntax OK')


def underlay_ipv4():
    stdout, _ = run(['ip', '-j', '-4', 'addr', 'show', 'dev', 'eth0'], timeout=10)
    try:
        records = json.loads(stdout)
        addresses = [item['local'] for interface in records for item in interface['addr_info']
                     if item.get('scope') == 'global' and item.get('family') == 'inet']
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('host IPv4 inventory malformed') from None
    if len(addresses) != 1:
        raise RuntimeError('expected one global IPv4 address on eth0')
    return addresses[0]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def http_result(host, port, path, timeout=5):
    host_part = '[' + host + ']' if ':' in host else host
    request = urllib.request.Request('http://' + host_part + ':' + str(port) + path,
                                     method='GET', headers={'Connection': 'close'})
    try:
        with HTTP.open(request, timeout=timeout) as response:
            return dict(status=response.status)
    except urllib.error.HTTPError as error:
        return dict(status=error.code)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, 'reason', error)
        return dict(status=None, error_type=type(reason).__name__)


def ipv6_loopback_listener():
    stdout, _ = run(['ss', '-6', '-H', '-lnt'], timeout=10)
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].rsplit(':', 1)[-1] == '80':
            return True
    return False


def web_snapshot(underlay, include_ipv6):
    hosts = {'loopback_v4': '127.0.0.1', 'eth0_v4': underlay}
    if include_ipv6:
        hosts['loopback_v6'] = '::1'
    result = {}
    for label, host in hosts.items():
        result[label] = {path: http_result(host, 80, path) for path in (*ALIASES, *PRESERVE_PATHS)}
    for label, (host, port, path) in HTTP_APIS.items():
        result['api_' + label] = {'/': http_result(host, port, path)}
    return result


def result_status(snapshot, label, path):
    value = snapshot.get(label, {}).get(path)
    return value.get('status') if isinstance(value, dict) else None


def compare_access(before, after):
    for label in ('loopback_v4', 'eth0_v4', 'loopback_v6'):
        if label not in before or label not in after:
            continue
        for path in PRESERVE_PATHS:
            if before[label][path] != after[label][path]:
                raise RuntimeError('unrelated Apache route behavior changed: ' + path)
    for label in ('loopback_v4', 'loopback_v6'):
        if label not in before or label not in after:
            continue
        for path in ALIASES:
            old = before[label][path]
            new = after[label][path]
            if new.get('status') == 403 and old.get('status') != 403:
                raise RuntimeError('loopback client was denied on ' + path)
            if old.get('status') is None or new.get('status') is None:
                raise RuntimeError('loopback alias probe unavailable')
    for path in ALIASES:
        if after['eth0_v4'][path].get('status') != 403:
            raise RuntimeError('non-loopback IPv4 request was not denied on ' + path)
    for label in HTTP_APIS:
        key = 'api_' + label
        if before[key] != after[key]:
            raise RuntimeError('API HTTP discovery status changed: ' + label)


def apache_state():
    stdout, _ = run(['systemctl', '--no-pager', 'show', 'apache2.service',
                     '--property=LoadState,ActiveState,SubState,Result,CanReload'], timeout=15)
    fields = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition('=')
        if not separator or key not in {'LoadState', 'ActiveState', 'SubState', 'Result', 'CanReload'}:
            raise RuntimeError('unexpected Apache systemd properties')
        fields[key] = value
    if fields.get('LoadState') != 'loaded' or fields.get('ActiveState') != 'active' or fields.get('SubState') != 'running':
        raise RuntimeError('Apache is not loaded and running')
    if fields.get('CanReload') != 'yes':
        raise RuntimeError('Apache does not advertise a safe reload operation')
    return fields


def openstack_preflight(secrets):
    with common.connection(secrets) as admin:
        state, _ = guest.load_run(admin, require_ssh=False)
        observed = review.cloud_state(admin, state)
        guest_observed = review.cpu_smoke(state)
        services = list(admin.identity.services())
        endpoints = list(admin.identity.endpoints())
        service_types = {service.id: service.type for service in services}
        catalog = []
        for endpoint in endpoints:
            kind = service_types.get(endpoint.service_id)
            url = endpoint.url
            if not isinstance(url, str) or any(token in url for token in ('\n', '\r', '\x00')):
                raise RuntimeError('unexpected catalog endpoint metadata')
            from urllib.parse import urlsplit
            parsed = urlsplit(url)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise RuntimeError('credential-bearing or malformed catalog URL')
            catalog.append(dict(service=kind, interface=endpoint.interface, path=parsed.path or '/'))
        aliases = [item for item in catalog if any(
            item['path'] == path or item['path'].startswith(path + '/') for path in ALIASES)]
        if aliases or 'volume' in set(service_types.values()):
            raise RuntimeError('catalog exposes a legacy route or Cinder service; client impact requires review')
    return dict(state=state, cloud=observed, guest_cpu=guest_observed,
                catalog_service_types=sorted(set(service_types.values())),
                volume_service_in_catalog=False, catalog_alias_endpoints=aliases)


def smoke_openstack(secrets, state):
    deadline = time.monotonic() + 120
    while True:
        try:
            with common.connection(secrets) as admin:
                observed = review.cloud_state(admin, state)
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError('authenticated cloud/provider recovery timed out') from None
            time.sleep(2)
    guest_result = review.cpu_smoke(state)
    return dict(authenticated_cloud='PASS', guest_cpu='PASS',
                compute=observed['compute_up'], network_mtu=observed['mtu'],
                ovn_agents=observed['ovn_agents_up'], placement_inventory=observed['placement_inventory'],
                glance_image_status='active', retained_server_status=observed['status'],
                guest_result=guest_result['result'])


def log_snapshots():
    result = {}
    for path in ERROR_LOGS:
        try:
            info = path.lstat()
        except FileNotFoundError:
            if path == ERROR_LOGS[0]:
                raise RuntimeError('Apache main error log is absent') from None
            result[str(path)] = None
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Apache error log metadata requires review')
        result[str(path)] = dict(inode=info.st_ino, size=info.st_size)
    return result


def log_deltas(before):
    report = {}
    for name, offset in before.items():
        if offset is None:
            report[name] = dict(present=False, error_lines=0, proxy_errors=0, authz_denials=0, other_errors=0)
            continue
        path = Path(name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError('Apache error log changed type')
        rotated = info.st_ino != offset['inode'] or info.st_size < offset['size']
        start = 0 if rotated else offset['size']
        with path.open('rb') as stream:
            stream.seek(start)
            lines = stream.read().decode('utf-8', errors='replace').splitlines()
        errors = [line for line in lines if re.search(
            r'\[(?:[^]]+:)?(?:error|crit|alert|emerg)\]|\bAH[0-9]{5}\b', line, re.I)]
        proxy = [line for line in errors if re.search(r'\[proxy(?::|\])|\bAH01[0-9]{3}\b', line)]
        authz = [line for line in errors if re.search(r'\[authz_core:error\]|\bAH01630\b', line)]
        codes = {}
        for line in errors:
            match = re.search(r'\bAH[0-9]{5}\b', line)
            if match:
                codes[match.group()] = codes.get(match.group(), 0) + 1
        report[name] = dict(present=True, rotated_during_check=rotated, error_lines=len(errors),
                            proxy_errors=len(proxy), authz_denials=len(authz),
                            other_errors=max(0, len(errors) - len(proxy) - len(authz)),
                            apache_error_codes=dict(sorted(codes.items())))
    return report


def make_recovery(originals, run_id):
    DATA_DIR_INFO = DATA_DIR.lstat()
    if (not stat.S_ISDIR(DATA_DIR_INFO.st_mode) or DATA_DIR_INFO.st_uid != 0
            or stat.S_IMODE(DATA_DIR_INFO.st_mode) != 0o700):
        raise RuntimeError('protected acceptance directory metadata requires review')
    directory = Path(tempfile.mkdtemp(prefix='apache-legacy-route-recovery-', dir=DATA_DIR))
    os.chmod(directory, 0o700)
    entries = {}
    for index, (path, item) in enumerate(originals.items()):
        backup_name = 'before-' + str(index) + '-' + path.name
        write_private(directory / backup_name, item['before'])
        entries[str(path)] = dict(backup=backup_name, old_sha256=sha256(item['before']),
                                  new_sha256=sha256(item['after']), uid=item['uid'], gid=item['gid'],
                                  mode=item['mode'], enabled_link=item['enabled_link'],
                                  route=item['route'], upstream=item['upstream'])
    plan = dict(run_id=run_id, files=entries)
    write_private(directory / 'change-plan.json', (json.dumps(plan, indent=2, sort_keys=True) + '\n').encode())
    checksums = {path.name: sha256(path.read_bytes()) for path in directory.iterdir() if path.is_file()}
    write_private(directory / 'checksums.json', (json.dumps(checksums, indent=2, sort_keys=True) + '\n').encode())
    os.chmod(directory, 0o700)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    record = dict(run_id=run_id, recovery_directory=str(directory), files=entries,
                  checksums={**checksums, 'checksums.json': sha256((directory / 'checksums.json').read_bytes())})
    verify_recovery(record)
    return record


def verify_recovery(record):
    directory = Path(record['recovery_directory'])
    info = directory.lstat()
    if (directory.parent != DATA_DIR or not directory.name.startswith('apache-legacy-route-recovery-')
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError('Apache recovery directory metadata failed')
    if set(record.get('files', {})) != {str(path) for path in TARGETS}:
        raise RuntimeError('Apache recovery target scope differs')
    checksums = record.get('checksums')
    if not isinstance(checksums, dict) or 'checksums.json' not in checksums:
        raise RuntimeError('Apache recovery checksum manifest is incomplete')
    required_checksums = {item['backup'] for item in record['files'].values()} | {'change-plan.json', 'checksums.json'}
    if set(checksums) != required_checksums:
        raise RuntimeError('Apache recovery artifact inventory differs')
    for name, expected in checksums.items():
        if Path(name).name != name:
            raise RuntimeError('unexpected Apache recovery artifact')
        artifact = directory / name
        private_file(artifact)
        if sha256(artifact.read_bytes()) != expected:
            raise RuntimeError('Apache recovery checksum mismatch')
    plan = json.loads((directory / 'change-plan.json').read_text())
    if plan != dict(run_id=record['run_id'], files=record['files']):
        raise RuntimeError('Apache recovery plan differs from state')
    checks_file = json.loads((directory / 'checksums.json').read_text())
    if checks_file != {key: value for key, value in checksums.items() if key != 'checksums.json'}:
        raise RuntimeError('Apache recovery checksum list differs from state')
    for filename, item in record['files'].items():
        before = (directory / item['backup']).read_bytes()
        if sha256(before) != item['old_sha256']:
            raise RuntimeError('Apache original configuration hash mismatch')
        if sha256(desired(Path(filename), before.decode()).encode()) != item['new_sha256']:
            raise RuntimeError('Apache planned configuration hash mismatch')


def restore_originals(record, runtime_reloaded):
    verify_recovery(record)
    directory = Path(record['recovery_directory'])
    for filename, item in record['files'].items():
        path = Path(filename)
        current_info = path.lstat()
        if not stat.S_ISREG(current_info.st_mode) or current_info.st_uid != 0:
            raise RuntimeError('Apache target drift prevents safe rollback')
        current_hash = sha256(path.read_bytes())
        if current_hash == item['old_sha256']:
            continue
        if current_hash != item['new_sha256']:
            raise RuntimeError('Apache target drift prevents safe rollback')
        write_atomic(path, (directory / item['backup']).read_bytes(),
                     item['uid'], item['gid'], item['mode'])
    configtest()
    if runtime_reloaded:
        run(['systemctl', 'reload', 'apache2.service'], timeout=60)
        apache_state()


def log_summary(logs):
    proxy = sum(item['proxy_errors'] for item in logs.values())
    authz = sum(item['authz_denials'] for item in logs.values())
    other = sum(item['other_errors'] for item in logs.values())
    return dict(proxy_error_lines=proxy, authorization_denials=authz,
                other_error_lines=other,
                upstream_http_503_remains=None)


def apply():
    if os.geteuid() != 0:
        raise RuntimeError('run with sudo /usr/bin/python3 -B in the Ubuntu terminal')
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    if not Path('/etc/os-release').exists():
        raise RuntimeError('Ubuntu host is unavailable')
    if STATE.exists() or STATE.is_symlink():
        private_file(STATE)
        prior = json.loads(STATE.read_text())
        if prior.get('phase') != 'verified':
            raise RuntimeError('prior Apache route change is incomplete; inspect protected evidence')
        verify_recovery(prior)
        for filename, item in prior['files'].items():
            path = Path(filename)
            private_info = path.lstat()
            if (not stat.S_ISREG(private_info.st_mode) or private_info.st_uid != 0
                    or sha256(path.read_bytes()) != item['new_sha256']):
                raise RuntimeError('verified Apache route configuration has drifted')
        progress('existing verified route change and recovery point: PASS; no files rewritten')
        return 0

    with os.fdopen(os.open('/root/.openstack-poc-nova-prerequisites.lock',
            os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if apache_state()['ActiveState'] != 'active':
            raise RuntimeError('Apache must already be active')
        progress('BEGIN; inspecting enabled legacy route mappings and authorization')
        originals = read_targets()
        progress('upstreams: /volume -> ' + TARGETS[next(path for path in TARGETS if TARGETS[path]['route'] == '/volume')]['upstream']
                 + '; /image -> ' + TARGETS[next(path for path in TARGETS if TARGETS[path]['route'] == '/image')]['upstream'])

        secrets = common.read_secrets(common.SECRET_FILE)
        cloud = openstack_preflight(secrets)
        progress('supported-client/catalog review: no Cinder catalog service or /volume-/image-based endpoint; POC uses Glance catalog endpoint')
        progress('authenticated Keystone/Nova/Neutron/Glance/Placement and retained guest baseline: PASS')

        ip = underlay_ipv4()
        ipv6_enabled = ipv6_loopback_listener()
        before_web = web_snapshot(ip, ipv6_enabled)
        if result_status(before_web, 'loopback_v4', '/') != 200 or result_status(before_web, 'eth0_v4', '/') != 200:
            raise RuntimeError('port-80 main-server baseline differs from the reviewed HTTP 200 behavior')
        for label in ('loopback_v4', 'eth0_v4'):
            for path in PRESERVE_PATHS:
                if before_web[label][path].get('status') is None:
                    raise RuntimeError('preserved Apache route baseline is unavailable')
        for alias in ALIASES:
            if before_web['eth0_v4'][alias].get('status') == 403:
                raise RuntimeError('legacy route is already access-controlled; inspect before changing')
            if before_web['loopback_v4'][alias].get('status') == 403:
                raise RuntimeError('legacy route is denied to loopback; inspect before changing')
        progress('route baseline captured; existing /, /identity and /placement behavior recorded')

        api_before = {key: value for key, value in before_web.items() if key.startswith('api_')}
        configtest()
        log_before = log_snapshots()
        record = make_recovery(originals, uuid.uuid4().hex)
        record.update(phase='backed_up', verified=False, checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      pre_change_routes=before_web, api_http_before=api_before,
                      cloud_catalog_review={key: value for key, value in cloud.items() if key != 'state'},
                      recovery_upstreams={item['route']: item['upstream'] for item in record['files'].values()})
        atomic_state(record)
        progress('root-only route configuration recovery bundle: PASS; path=' + record['recovery_directory'])

        runtime_reloaded = False
        try:
            for filename, item in record['files'].items():
                path = Path(filename)
                current = path.lstat()
                if not stat.S_ISREG(current.st_mode) or current.st_uid != item['uid']:
                    raise RuntimeError('Apache target changed after backup')
                if sha256(path.read_bytes()) != item['old_sha256']:
                    raise RuntimeError('Apache target drifted after backup')
                # Build solely from the protected original, then replace atomically.
                original = (Path(record['recovery_directory']) / item['backup']).read_bytes()
                updated = desired(path, original.decode()).encode()
                if sha256(updated) != item['new_sha256']:
                    raise RuntimeError('staged Apache route content differs from reviewed plan')
                write_atomic(path, updated, item['uid'], item['gid'], item['mode'])
            record['phase'] = 'files_staged'
            atomic_state(record)
            progress('only /volume and /image received Require ip 127.0.0.1 ::1; running apache2ctl configtest')
            configtest()
            record['phase'] = 'configtest_passed'
            atomic_state(record)
            progress('apache2ctl configtest: PASS; gracefully reloading Apache')
            run(['systemctl', 'reload', 'apache2.service'], timeout=60)
            runtime_reloaded = True
            apache_state()
            record['phase'] = 'reloaded'
            atomic_state(record)

            # Authenticated operations and the pinned-key guest check cover Keystone,
            # Glance, Nova, Neutron, Placement inventory, OVN agents and KVM recovery.
            after_cloud = smoke_openstack(secrets, cloud['state'])
            if cloud['cloud'].get('status') != 'ACTIVE' or not after_cloud['authenticated_cloud'] == 'PASS':
                raise RuntimeError('authenticated cloud/provider recovery check failed')
            after_web = web_snapshot(ip, ipv6_enabled)
            compare_access(before_web, after_web)
            logs = log_deltas(log_before)
            summary = log_summary(logs)
            summary['upstream_http_503_remains'] = any(
                after_web['loopback_v4'][alias].get('status') == 503 for alias in ALIASES)
            summary['loopback_statuses'] = {alias: after_web['loopback_v4'][alias].get('status') for alias in ALIASES}
            summary['nonloopback_statuses'] = {alias: after_web['eth0_v4'][alias].get('status') for alias in ALIASES}
            record.update(phase='verified', verified=True, post_change_routes=after_web,
                          api_http_after={key: value for key, value in after_web.items() if key.startswith('api_')},
                          api_http_unchanged=True, cloud_guest_validation=after_cloud,
                          apache_error_log_delta=logs, result_summary=summary,
                          checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            record['ipv6_loopback_listener'] = ipv6_enabled
            atomic_state(record)
            progress('nonloopback /volume and /image denied (HTTP 403); localhost access preserved: PASS')
            progress('/, /identity, /placement and API discovery statuses unchanged: PASS')
            progress('authenticated Keystone/Placement/Glance/Nova/Neutron, provider inventory and retained guest: PASS')
            progress('upstream HTTP 503 remains: ' + ('yes' if summary['upstream_http_503_remains'] else 'no'))
            progress('new Apache error-log counts: ' + json.dumps({key: {name: value[name] for name in
                ('error_lines', 'proxy_errors', 'authz_denials', 'other_errors')}
                for key, value in logs.items()}, sort_keys=True))
            progress('protected evidence=' + str(STATE))
            progress('COMPLETE (legacy /volume and /image source-address restriction only)')
            return 0
        except Exception:
            try:
                record['phase'] = 'rollback_started'
                atomic_state(record)
                restore_originals(record, runtime_reloaded)
                record['phase'] = 'rolled_back'
                atomic_state(record)
                progress('verification failed; exact originals restored and checked; recovery retained at '
                         + record['recovery_directory'])
            except Exception:
                record['phase'] = 'rollback_failed'
                atomic_state(record)
                progress('rollback could not be confirmed; stop and inspect protected recovery at '
                         + record['recovery_directory'])
            raise


def main():
    try:
        return apply()
    except Exception as error:
        print('STOP: Apache legacy-route hardening failed (' + type(error).__name__ + '); recovery point preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
