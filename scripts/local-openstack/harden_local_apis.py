#!/usr/bin/python3
"""Backed-up local API bindings and bounded logrotate hook validation.

Restarts Apache and Glance only. Preserves legacy Apache configurations/port
80, RabbitMQ/EPMD, VM resources and networking. Keeps logrotate.timer stopped.
"""
import argparse
import datetime
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
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

import backup_application as backup
import configure_nova as common
import launch_test_vm as launch
import review_operations as review
import validate_guest as guest

STATE = launch.DATA_DIR / 'api-hardening.json'
DROPIN = Path('/etc/systemd/system/logrotate.service.d/50-openstack-poc-timeout.conf')
TIMEOUT_CONFIG = '[Service]\nTimeoutStartSec=5min\nTimeoutStopSec=15s\n'
TARGETS = (Path('/etc/apache2/sites-available/keystone.conf'),
           Path('/etc/apache2/sites-available/placement-api.conf'), Path('/etc/glance/glance-api.conf'),
           Path('/etc/logrotate.d/openvswitch-switch'), DROPIN)


def progress(message):
    print('Task 11 API/logrotate hardening: ' + message, flush=True)


def apache_config(text, port, daemon, alias):
    text, count = re.subn(r'^Listen (?:127\.0\.0\.1:)?' + str(port) + r'\s*$',
                         'Listen 127.0.0.1:' + str(port), text, flags=re.M)
    if count != 1:
        raise RuntimeError('unexpected Apache Listen declaration')
    text, count = re.subn(r'<VirtualHost (?:\*|127\.0\.0\.1):' + str(port) + '>',
                         '<VirtualHost 127.0.0.1:' + str(port) + '>', text)
    if count != 1:
        raise RuntimeError('unexpected Apache virtual host declaration')
    pattern = r'^(\s*WSGIDaemonProcess ' + re.escape(daemon) + r'\s+[^\n]*)$'
    def workers(match):
        value, changed = re.subn(r'\bprocesses=[1-9][0-9]*\b', 'processes=1', match.group(1))
        if changed != 1:
            raise RuntimeError('unexpected WSGI worker declaration')
        return value
    text, count = re.subn(pattern, workers, text, flags=re.M)
    if count != 1:
        raise RuntimeError('unexpected WSGI daemon scope')
    location = r'(<Location ' + re.escape(alias) + r'>)(.*?)(</Location>)'
    def local_only(match):
        body = match.group(2)
        rules = re.findall(r'^\s*Require\s+([^\n]+)', body, re.M)
        if rules and rules != ['ip 127.0.0.1 ::1']:
            raise RuntimeError('existing alias authorization requires review')
        if not rules:
            body = '\n    Require ip 127.0.0.1 ::1' + body
        return match.group(1) + body + match.group(3)
    text, count = re.subn(location, local_only, text, flags=re.S)
    if count != 1:
        raise RuntimeError('unexpected global API alias scope')
    return text


def desired(path, text):
    if path == TARGETS[0]:
        return apache_config(text, 5000, 'keystone-public', '/identity')
    if path == TARGETS[1]:
        return apache_config(text, 8778, 'placement-api', '/placement')
    if path == TARGETS[2]:
        return common.edit_ini(text, {'DEFAULT': {'bind_host': '127.0.0.1', 'workers': '1'}})
    if path == TARGETS[3]:
        calls = re.findall(r'^.*ovs-appctl[^\n]*$', text, re.M)
        if len(calls) != 1 or ('--timeout' in calls[0] and '--timeout=5' not in calls[0]):
            raise RuntimeError('unexpected OVS logrotate call')
        return text if '--timeout=5' in calls[0] else text.replace('ovs-appctl ', 'ovs-appctl --timeout=5 ', 1)
    if path == DROPIN and text in ('', TIMEOUT_CONFIG):
        return TIMEOUT_CONFIG
    raise RuntimeError('unknown target or existing timeout override requires review')


def digest(content):
    return hashlib.sha256(content).hexdigest()


def protected_regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('unexpected configuration ownership/type/mode')
    return info


def verify_backup(record):
    directory = Path(record['backup_directory'])
    info = directory.lstat()
    if (directory.parent != launch.DATA_DIR or not directory.name.startswith('api-hardening-recovery-')
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError('unsafe API recovery directory')
    if set(record['files']) != {str(path) for path in TARGETS}:
        raise RuntimeError('persisted API change scope differs')
    required = {'cloud-databases.sql', 'operational-files.tar.gz', 'RECOVERY.md', 'change-plan.json', 'checksums.json'} | {item[0] for item in backup.OVSDB}
    required |= {item['before'] for item in record['files'].values() if item['existed']}
    if set(record['sha256']) != required:
        raise RuntimeError('API recovery point incomplete')
    for name, value in record['sha256'].items():
        if Path(name).name != name:
            raise RuntimeError('unexpected recovery artifact path')
        path = directory / name
        guest.protected_file(path, 0o600)
        if backup.checksum(path) != value:
            raise RuntimeError('API recovery integrity check failed')
    for filename, item in record['files'].items():
        before = (directory / item['before']).read_bytes() if item['existed'] else b''
        if digest(before) != item['old_sha256'] or digest(desired(Path(filename), before.decode()).encode()) != item['new_sha256']:
            raise RuntimeError('persisted change metadata differs from recovery/configuration')
    if json.loads((directory / 'change-plan.json').read_text()) != {'run_id': record['run_id'], 'files': record['files']}:
        raise RuntimeError('self-contained recovery change plan differs')
    if json.loads((directory / 'checksums.json').read_text()) != {key: value for key, value in record['sha256'].items() if key != 'checksums.json'}:
        raise RuntimeError('self-contained recovery checksums differ')


def prepare(run_id):
    if STATE.exists() or STATE.is_symlink():
        guest.protected_file(STATE, 0o600)
        existing = json.loads(STATE.read_text())
        if existing['run_id'] != run_id:
            raise RuntimeError('API recovery record belongs to another run')
        if existing['phase'] != 'preparing':
            verify_backup(existing)
            return existing
    directory = Path(tempfile.mkdtemp(prefix='api-hardening-recovery-', dir=launch.DATA_DIR))
    record = dict(run_id=run_id, backup_directory=str(directory), phase='preparing', verified=False, files={})
    launch.atomic_state(STATE, record)
    # Plan every edit before any live configuration write.
    for index, path in enumerate(TARGETS):
        existed = path.exists() or path.is_symlink()
        if not existed and path != DROPIN:
            raise RuntimeError('required configuration is absent')
        info = protected_regular(path) if existed else None
        before = path.read_bytes() if existed else b''
        target = desired(path, before.decode()).encode()
        name = 'before-' + str(index)
        if existed:
            (directory / name).write_bytes(before)
        record['files'][str(path)] = dict(existed=existed, before=name,
            uid=info.st_uid if info else 0, gid=info.st_gid if info else 0,
            mode=stat.S_IMODE(info.st_mode) if info else 0o644,
            old_sha256=digest(before), new_sha256=digest(target))
    backup.snapshot_databases(directory)
    backup.snapshot_operational_files(directory / 'operational-files.tar.gz')
    (directory / 'change-plan.json').write_text(json.dumps({'run_id': record['run_id'], 'files': record['files']}, indent=2) + '\n')
    (directory / 'RECOVERY.md').write_text('''# API/logrotate configuration recovery
This protected point contains individually consistent SQL/live OVSDB snapshots,
operational files and exact per-file originals. Read api-hardening.json for
paths, original existence/uid/gid/mode and hashes, also retained independently
in change-plan.json and checksums.json here. Full restore is untested.
For configuration-only rollback, verify every hash, then restore only a listed
file whose current hash still matches this task's desired hash. Use a temporary
file, saved ownership/mode and atomic replacement. For the originally absent
timeout drop-in, remove only that exact task file after matching its hash;
preserve other overrides/directories. Validate Apache/logrotate configurations,
daemon-reload, and restart only Apache/Glance if their files were restored.
Do not restore SQL/OVSDB into running services or overwrite VM disks. Keep all
recovery points; whole-cloud rollback needs separate quiescence/recovery scope.
''')
    record['sha256'] = {item.name: backup.checksum(item) for item in directory.iterdir() if item.is_file()}
    (directory / 'checksums.json').write_text(json.dumps(record['sha256'], indent=2) + '\n')
    record['sha256']['checksums.json'] = backup.checksum(directory / 'checksums.json')
    backup.sync_bundle(directory)
    verify_backup(record)
    record['phase'] = 'prepared'
    launch.atomic_state(STATE, record)
    progress('consistent metadata/configuration backup checks: PASS; path=' + str(directory))
    return record


def write_target(path, item, directory):
    before = (directory / item['before']).read_bytes() if item['existed'] else b''
    target = desired(path, before.decode()).encode()
    exists = path.exists() or path.is_symlink()
    if exists:
        protected_regular(path)
        current = digest(path.read_bytes())
        if current == item['new_sha256']:
            return
        if not item['existed'] or current != item['old_sha256']:
            raise RuntimeError('live configuration drift; no overwrite performed')
    elif item['existed']:
        raise RuntimeError('original configuration disappeared; preserving recovery point')
    if not path.parent.exists():
        path.parent.mkdir(mode=0o755)
    if path.parent.is_symlink():
        raise RuntimeError('unexpected configuration directory symlink')
    descriptor, temporary = tempfile.mkstemp(prefix='.task11-api-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(target)
            os.fchown(stream.fileno(), item['uid'], item['gid'])
            os.fchmod(stream.fileno(), item['mode'])
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
            os.unlink(temporary)  # Only this invocation's temporary file.


def postrotate(text):
    blocks = re.findall(r'^\s*postrotate\s*\n(.*?)^\s*endscript\s*$', text, re.M | re.S)
    if len(blocks) != 1:
        raise RuntimeError('ambiguous postrotate script; not executed')
    calls = [line for line in blocks[0].splitlines() if 'ovs-appctl' in line and not line.lstrip().startswith('#')]
    if not calls or any('--timeout=5' not in line for line in calls):
        raise RuntimeError('unbounded postrotate call; not executed')
    return blocks[0]


def verify_hooks():
    targets = set()
    required = ('ovn-controller', 'ovn-northd', 'ovnnb_db', 'ovnsb_db', 'ovs-vswitchd', 'ovsdb-server')
    for folder in ('/run/ovn', '/run/openvswitch'):
        for candidate in Path(folder).glob('*.ctl'):
            if not any(candidate.name.startswith(name + '.') for name in required):
                continue
            target = candidate.resolve(strict=True)
            if target.parent != Path(folder) or not stat.S_ISSOCK(target.stat().st_mode) or target.stat().st_uid != 0:
                raise RuntimeError('unexpected control socket metadata')
            targets.add(target)
    if not all(any(path.name.startswith(name + '.') for path in targets) for name in required):
        raise RuntimeError('required current control socket not found')
    for target in sorted(targets):
        common.run(['ovs-appctl', '--timeout=5', '-t', str(target), 'vlog/reopen'], timeout=8)
    for path in (TARGETS[3], Path('/etc/logrotate.d/ovn-common')):
        protected_regular(path)
        script = postrotate(path.read_text())
        result = subprocess.run(['/bin/sh', '-eu', '-c', script],
            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        if result.returncode:
            raise RuntimeError('runtime postrotate execution failed; private diagnostics require review')
    common.run(['logrotate', '--debug', '/etc/logrotate.conf'], timeout=30)
    return dict(individual_control_probes=len(targets), ovs_postrotate='PASS', ovn_postrotate='PASS',
                parser_debug='PASS', limitation='log reopen hooks only; no forced log rotation or timer activation')


def rabbit_preflight():
    names = common.run(['epmd', '-names'], timeout=15)
    entries = [dict(name=match[0], port=int(match[1])) for match in re.findall(r'^name (\S+) at port ([0-9]+)$', names, re.M)]
    cluster = json.loads(common.run(['rabbitmqctl', '--quiet', '--formatter', 'json', 'cluster_status'], timeout=30))
    connections = json.loads(common.run(['rabbitmqctl', '--quiet', '--formatter', 'json', 'list_connections', 'user', 'peer_host'], timeout=30))
    status = json.loads(common.run(['rabbitmq-diagnostics', '--quiet', '--formatter', 'json', 'status'], timeout=30))
    hosts = socket.gethostbyname_ex(socket.gethostname())[2]
    return dict(hostname=socket.gethostname(), hostname_ipv4=hosts, epmd_nodes=entries,
        running_nodes=cluster['running_nodes'], disk_nodes=cluster['nodes']['disc'],
        ram_nodes=cluster['nodes'].get('ram', []), connection_count=len(connections),
        all_connections_loopback=all(ipaddress.ip_address(item['peer_host']).is_loopback for item in connections),
        only_poc_connection_users=all(item['user'] == 'openstack' for item in connections),
        config_files=status['config_files'])


def verify_apis(secrets, state):
    deadline = time.monotonic() + 120
    while True:
        try:
            with common.connection(secrets) as admin:
                observed = review.cloud_state(admin, state)
            workload = review.cpu_smoke(state)
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError('API/guest readiness did not recover within the deadline') from None
            time.sleep(2)
    bound = review.listeners(common.run(['ss', '-H', '-lnt'], timeout=10), 'tcp')
    for port in (5000, 8778, 9292):
        matches = [entry for entry in bound if entry['port'] == port]
        if not matches or not all(entry['loopback'] for entry in matches):
            raise RuntimeError('target API listener is absent or not loopback-only')
    addresses = json.loads(common.run(['ip', '-j', '-4', 'addr', 'show', 'dev', 'eth0'], timeout=10))
    underlay = [item['local'] for interface in addresses for item in interface['addr_info'] if item['scope'] == 'global']
    if len(underlay) != 1:
        raise RuntimeError('ambiguous host IPv4 for scoped alias verification')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    aliases = {}
    for path in ('/identity/v3', '/placement/'):
        try:
            with opener.open('http://' + underlay[0] + ':80' + path, timeout=5) as response:
                code = response.status
        except urllib.error.HTTPError as error:
            code = error.code
        if code != 403:
            raise RuntimeError('API alias not denied from the host non-loopback address')
        aliases[path] = code
    return dict(cloud=observed, guest_cpu=workload, api_listeners=[entry for entry in bound if entry['port'] in (5000, 8778, 9292)],
                nonloopback_alias_http=aliases)


def apply_configuration(record):
    verify_backup(record)
    if record.get('verified'):
        for filename, item in record['files'].items():
            protected_regular(Path(filename))
            if digest(Path(filename).read_bytes()) != item['new_sha256']:
                raise RuntimeError('verified configuration drift; no automatic reapply')
        return
    for path in TARGETS:
        write_target(path, record['files'][str(path)], Path(record['backup_directory']))
    record['phase'] = 'files_written'
    launch.atomic_state(STATE, record)
    progress('reviewed API/hook files staged; validating Apache syntax')
    common.run(['apache2ctl', 'configtest'], timeout=20)
    common.run(['systemctl', 'daemon-reload'], timeout=30)
    progress('Apache syntax: PASS; timeout override loaded; restarting Apache and Glance')
    common.run(['systemctl', 'restart', 'apache2.service'], timeout=110)
    common.run(['systemctl', 'restart', 'glance-api.service'], timeout=110)
    record['phase'] = 'services_restarted'
    launch.atomic_state(STATE, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', required=True, action='store_true')
    parser.parse_args()
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    try:
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M) or 'microsoft' not in platform.release().lower():
            raise RuntimeError('existing Ubuntu 22.04 WSL required')
        with os.fdopen(os.open('/root/.openstack-poc-nova-prerequisites.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if review.systemd_state('logrotate.timer')['ActiveState'] != 'inactive' or review.systemd_state('logrotate.service')['ActiveState'] != 'inactive':
                raise RuntimeError('timer/rotation job must remain inactive before this stage')
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as admin:
                state, _ = guest.load_run(admin, require_ssh=False)
                review.cloud_state(admin, state)
            record = prepare(state['run_id'])
            progress('BEGIN; metadata backup verified; timer remains inactive')
            apply_configuration(record)
            progress('awaiting actual API/provider and guest recovery')
            record['api_validation'] = verify_apis(secrets, state)
            progress('loopback API bindings, non-loopback alias denial and recovered cloud/guest: PASS')
            record['logrotate_validation'] = verify_hooks()
            if review.systemd_state('logrotate.service').get('TimeoutStartUSec') != '5min':
                raise RuntimeError('total rotation job timeout differs from the reviewed limit')
            if review.systemd_state('logrotate.timer')['ActiveState'] != 'inactive':
                raise RuntimeError('timer unexpectedly active; no activation requested')
            progress('bounded OVN/OVS direct controls and actual postrotate hook execution: PASS; timer inactive')
            review.observe(record, 'rabbitmq_preflight', rabbit_preflight)
            record.update(phase='verified', verified=True, checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            launch.atomic_state(STATE, record)
            progress('protected evidence=' + str(STATE))
            progress('COMPLETE (API/logrotate scope; RabbitMQ/EPMD and timer reactivation remain pending)')
            return 0
    except Exception as exc:
        print('STOP: API/logrotate stage failed (' + type(exc).__name__ + '). Protected backups/state retained; timer not enabled.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
