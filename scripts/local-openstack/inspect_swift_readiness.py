#!/usr/bin/python3
"""T19 observations for a guarded local Swift deployment; no object mutations.

Uses the existing root-only POC credentials. Writes only a new protected report
and the shared lock. Catalogued Swift accounts are probed with HEAD only, after
an exact loopback/account-path check; redirects and remote endpoints are refused.
"""
import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
from urllib.parse import urlsplit
import uuid

from keystoneauth1.exceptions.catalog import EndpointNotFound

import configure_nova as common
import inspect_cinder_readiness as cinder
import launch_test_vm as launch


PORTS = (8080, 6200, 6201, 6202)
UNITS = ('swift-proxy.service', 'swift-account.service',
         'swift-container.service', 'swift-object.service',
         'memcached.service', 'rsync.service')
SERVER_PACKAGES = ('swift', 'swift-proxy', 'swift-account',
                   'swift-container', 'swift-object', 'python3-swift')
PACKAGES = SERVER_PACKAGES + ('python3-swiftclient', 'memcached', 'rsync')
PATHS = ('/etc/swift', '/srv/node', '/srv/swift-poc', '/var/lib/swift', '/var/lib/swift-poc',
         '/var/cache/swift', '/var/log/swift', '/run/swift', '/run/lock/swift')
MIN_AVAILABLE_KIB = 1536 * 1024
MIN_FREE_BYTES = 12 * 1024**3  # New 8-GiB filesystem image plus host reserve.


def package_inventory():
    result = subprocess.run(
        ['dpkg-query', '-W', '-f=${Package}\t${db:Status-Abbrev}\t${Version}\n',
         *PACKAGES], text=True, capture_output=True, timeout=20, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError('package inventory failed')
    packages = {name: {'installed': False} for name in PACKAGES}
    for line in result.stdout.splitlines():
        fields = line.split('\t')
        if len(fields) != 3 or fields[0] not in packages:
            continue
        name, status, version = fields
        packages[name]['installed'] = status.startswith('ii')
        if re.fullmatch(r'[A-Za-z0-9.+:~_-]{1,100}', version):
            packages[name]['version'] = version
    return packages


def package_candidates():
    output = common.run(['apt-cache', 'policy', *SERVER_PACKAGES], timeout=20)
    candidates = {name: False for name in SERVER_PACKAGES}
    current = None
    for line in output.splitlines():
        if line.endswith(':') and not line.startswith(' '):
            current = line[:-1] if line[:-1] in candidates else None
        elif current and re.fullmatch(r'\s*Candidate:\s+[A-Za-z0-9.+:~_-]{1,100}', line):
            candidates[current] = True
    return candidates


def listener_inventory(output):
    result = {str(port): {'count': 0, 'loopback_only': False} for port in PORTS}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0] != 'LISTEN':
            raise RuntimeError('unrecognized listener inventory')
        host, separator, port_text = fields[3].rpartition(':')
        if not separator or not port_text.isdigit():
            raise RuntimeError('unrecognized listening address')
        if int(port_text) not in PORTS:
            continue
        item = result[str(int(port_text))]
        local = host in {'127.0.0.1', '[::1]', '::1'}
        item['loopback_only'] = local if item['count'] == 0 else item['loopback_only'] and local
        item['count'] += 1
    return result


def path_observation(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'present': False}
    directory = stat.S_ISDIR(info.st_mode)
    result = {'present': True,
              'kind': 'directory' if directory else 'symlink' if stat.S_ISLNK(info.st_mode) else 'other',
              'root_owned': info.st_uid == 0,
              'group_or_other_writable': bool(info.st_mode & 0o022)}
    if directory and info.st_uid == 0 and not info.st_mode & 0o022:
        # Report only emptiness; existing names/content may belong to other work.
        result['empty'] = next(path.iterdir(), None) is None
    return result


def filesystem_observation():
    output = common.run(['findmnt', '--json', '--target', '/var/lib',
                         '--output', 'FSTYPE,OPTIONS'], timeout=10)
    rows = json.loads(output)['filesystems']
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError('unrecognized storage filesystem')
    fs_type = rows[0].get('fstype')
    if not isinstance(fs_type, str) or not re.fullmatch(r'[a-z0-9_]{1,32}', fs_type):
        raise RuntimeError('unrecognized storage filesystem type')
    options = rows[0].get('options', '').split(',')
    usage = shutil.disk_usage('/var/lib')
    vfs = os.statvfs('/var/lib')
    return {'type': fs_type, 'writable_mount': 'rw' in options and 'ro' not in options,
            'xattrs_explicitly_disabled': 'nouser_xattr' in options,
            'free_bytes': usage.free, 'free_inodes': vfs.f_favail,
            'xattr_write_probe': 'pending_guarded_setup'}


def expected_account_endpoint(endpoint, project_id):
    if not isinstance(endpoint, str) or not re.fullmatch(r'[0-9a-f]{32}', project_id or ''):
        return False
    try:
        parsed = urlsplit(endpoint)
        return (parsed.scheme == 'http' and parsed.netloc == '127.0.0.1:8080'
                and parsed.path == '/v1/AUTH_' + project_id
                and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def cloud_inventory(cloud):
    result = {'keystone_auth': False, 'endpoint_present': False,
              'account_head': 'not_attempted'}
    try:
        project_id = cloud.session.get_project_id()
        if not isinstance(project_id, str) or not re.fullmatch(r'[0-9a-f]{32}', project_id):
            raise RuntimeError('valid project scope required')
        result['keystone_auth'] = True
        try:
            endpoint = cloud.session.get_endpoint(service_type='object-store',
                                                  interface='internal', region_name='RegionOne')
        except EndpointNotFound:
            result['endpoint_status'] = 'not_in_catalog'
            return result
        result['endpoint_present'] = bool(endpoint)
        if not endpoint:
            result['endpoint_status'] = 'not_in_catalog'
            return result
        result['expected_loopback_account'] = expected_account_endpoint(endpoint, project_id)
        if not result['expected_loopback_account']:
            result['endpoint_status'] = 'outside_proposed_local_scope'
            return result
        # Account HEAD returns no object/container names or data. Never follow
        # a redirect with this authenticated session to another destination.
        response = cloud.session.head(endpoint, allow_redirects=False,
                                      raise_exc=False, timeout=10)
        result['account_head'] = response.status_code
        result['endpoint_status'] = 'responding' if response.status_code in (200, 204) else 'not_ready'
    except Exception as error:
        result['failure'] = cinder.fixed_api_failure(error)
    return result


def installation_readiness(report):
    host = report['host']
    fs = host['filesystem']
    cloud = report['cloud']
    checks = {
        'keystone_authenticated': cloud.get('keystone_auth') is True,
        'catalog_absence_verified': cloud.get('endpoint_status') == 'not_in_catalog',
        'memory_headroom': host['mem_available_kib'] >= MIN_AVAILABLE_KIB,
        'disk_headroom': fs['free_bytes'] >= MIN_FREE_BYTES and fs['free_inodes'] >= 4096,
        'local_storage_filesystem': fs['type'] in {'ext4', 'xfs'} and fs['writable_mount']
                                    and not fs['xattrs_explicitly_disabled'],
        'safe_storage_parents': all(item.get('kind') == 'directory'
                                   and item.get('root_owned') is True
                                   and item.get('group_or_other_writable') is False
                                   for item in host['storage_parents'].values()),
        'loop_control_present': host['loop_control_present'] is True,
        'packages_available': all(host['package_candidates'].values()),
        'memcached_active': host['units']['memcached.service']['active'] == 'active',
    }
    existing = (cloud.get('endpoint_present') is True
                or any(value['present'] for value in host['paths'].values())
                or any(host['packages'][name]['installed'] for name in SERVER_PACKAGES)
                or any(value['count'] for value in host['listeners'].values())
                or any(host['units'][name]['load'] not in {'not-found', 'masked'}
                       or host['units'][name]['active'] != 'inactive'
                       for name in UNITS[:4]))
    status = ('review_existing_state' if existing else
              'ready_for_guarded_setup' if all(checks.values()) else 'blocked_prerequisites')
    return {'status': status, 'checks': checks, 'existing_swift_state_or_port_conflict': existing,
            'object_round_trip': 'not_tested',
            'limits': {'min_mem_available_kib': MIN_AVAILABLE_KIB, 'min_free_bytes': MIN_FREE_BYTES}}


def main():
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the Ubuntu terminal.', file=sys.stderr)
        return 1
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    release = Path('/etc/os-release').read_text()
    if (not re.search(r'^ID=ubuntu$', release, re.M)
            or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
            or 'microsoft' not in platform.release().lower()):
        print('STOP: this inspector is scoped to the existing Ubuntu 22.04 WSL POC.', file=sys.stderr)
        return 1
    report = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'mode': 'observations_only_no_object_or_service_changes'}
    path = None
    try:
        descriptor = os.open(cinder.LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError('protected regular lock required')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = launch.DATA_DIR.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise RuntimeError('protected acceptance directory required')
            path = launch.DATA_DIR / ('swift-readiness-' + uuid.uuid4().hex + '.json')
            report['host'] = {
                'mem_available_kib': int(re.search(r'^MemAvailable:\s+(\d+)',
                    Path('/proc/meminfo').read_text(), re.M).group(1)),
                'filesystem': filesystem_observation(),
                'storage_parents': {name: path_observation(Path(name)) for name in ('/srv', '/var/lib')},
                'loop_control_present': stat.S_ISCHR(Path('/dev/loop-control').lstat().st_mode),
                'paths': {name: path_observation(Path(name)) for name in PATHS},
                'packages': package_inventory(), 'package_candidates': package_candidates(),
                'units': {name: cinder.systemd_observation(name) for name in UNITS},
                'listeners': listener_inventory(common.run(['ss', '-H', '-ltn'], timeout=15)),
            }
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as cloud:
                report['cloud'] = cloud_inventory(cloud)
            report['installation'] = installation_readiness(report)
            launch.atomic_state(path, report)
            summary = {'installation': report['installation'], 'cloud': report['cloud'],
                       'mem_available_kib': report['host']['mem_available_kib'],
                       'filesystem': report['host']['filesystem'],
                       'listeners': report['host']['listeners'],
                       'existing_paths': [name for name, value in report['host']['paths'].items() if value['present']],
                       'installed_server_packages': [name for name in SERVER_PACKAGES if report['host']['packages'][name]['installed']]}
            print('POC Swift preflight: summary=' + json.dumps(summary, sort_keys=True), flush=True)
            print('POC Swift preflight: ' + report['installation']['status'], flush=True)
            print('POC Swift preflight: report=' + str(path), flush=True)
            return 0 if report['installation']['status'] == 'ready_for_guarded_setup' else 2
    except Exception as error:
        report['failure'] = cinder.fixed_api_failure(error)
        if path:
            launch.atomic_state(path, report)
            print('POC Swift preflight: protected incomplete report=' + str(path), flush=True)
        print('STOP: Swift preflight incomplete: ' + json.dumps(report['failure'], sort_keys=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
