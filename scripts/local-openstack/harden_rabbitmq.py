#!/usr/bin/python3
"""Back up and restrict the approved single-node laptop RabbitMQ/EPMD."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

import backup_application as backup
import configure_nova as common
import harden_local_apis as api
import launch_test_vm as launch
import review_operations as review
import validate_guest as guest

DATA_DIR = launch.DATA_DIR
STATE = DATA_DIR / 'rabbitmq-hardening.json'
RABBIT_DIR = Path('/etc/rabbitmq')
ENV_CONFIG = RABBIT_DIR / 'rabbitmq-env.conf'
RABBIT_CONFIG = RABBIT_DIR / 'rabbitmq.conf'
SOCKET_DROPIN_DIR = Path('/etc/systemd/system/epmd.socket.d')
SOCKET_DROPIN = SOCKET_DROPIN_DIR / '50-openstack-poc-loopback.conf'
STAGING_PARENT = Path('/tmp')
UNITS = {'epmd.socket': Path('/lib/systemd/system/epmd.socket'),
         'epmd.service': Path('/lib/systemd/system/epmd.service'),
         'rabbitmq-server.service': Path('/lib/systemd/system/rabbitmq-server.service')}
NODE = 'rabbit@DESKTOP-Sneha'
HOST = 'DESKTOP-Sneha'
HOST_IP = '127.0.1.1'
PORTS = {4369: {'127.0.0.1', '127.0.1.1'}, 5672: {'127.0.0.1'}, 25672: {'127.0.1.1'}}
RABBIT_CONFIG_TEXT = ('listeners.tcp.default = 127.0.0.1:5672\n'
                      'distribution.listener.interface = 127.0.1.1\n'
                      'distribution.listener.port_range.min = 25672\n'
                      'distribution.listener.port_range.max = 25672\n')
SOCKET_CONFIG_TEXT = ('[Socket]\nListenStream=\n'
                      'ListenStream=127.0.0.1:4369\n'
                      'ListenStream=127.0.1.1:4369\n')
ARTIFACTS = ('configuration-and-units.tar.gz', 'rabbitmq-definitions.json',
             'prechange-metadata.json', 'change-plan.json', 'RECOVERY.md', 'checksums.json')


def progress(message):
    print('Task 11 RabbitMQ hardening: ' + message, flush=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def protected_regular(path, owners=None):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
        raise RuntimeError('unexpected protected file type or writable mode')
    if owners is not None and (info.st_uid, info.st_gid) not in owners:
        raise RuntimeError('unexpected protected file ownership')
    return info


def systemd_properties(unit):
    fields = ('LoadState', 'ActiveState', 'SubState', 'UnitFileState',
              'FragmentPath', 'DropInPaths', 'Listen')
    text = common.run(['systemctl', '--no-pager', 'show', unit,
                       '--property=' + ','.join(fields)], timeout=15)
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        if not sep or key not in fields:
            raise RuntimeError('unexpected systemd metadata format')
        result[key] = value
    if not {'LoadState', 'ActiveState', 'SubState', 'FragmentPath', 'DropInPaths'} <= set(result):
        raise RuntimeError('incomplete systemd unit metadata')
    return result


def environment_keys():
    protected_regular(ENV_CONFIG)
    keys = []
    for line in ENV_CONFIG.read_text().splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
        if match:
            keys.append(match.group(1))
    return keys


def listener_inventory():
    rows = review.listeners(common.run(['ss', '-H', '-lnt'], timeout=10), 'tcp')
    result = {}
    for port in PORTS:
        matches = [row for row in rows if row['port'] == port]
        if len(matches) != 1 or matches[0]['loopback']:
            raise RuntimeError('unexpected current RabbitMQ/EPMD listener inventory')
        result[str(port)] = matches
    return result


def preflight():
    broker = api.rabbit_preflight()
    if (broker['hostname'] != HOST or broker['hostname_ipv4'] != [HOST_IP]
            or broker['running_nodes'] != [NODE] or broker['disk_nodes'] != [NODE]
            or broker['ram_nodes'] or broker['epmd_nodes'] != [{'name': 'rabbit', 'port': 25672}]
            or broker['connection_count'] < 1 or not broker['all_connections_loopback']
            or not broker['only_poc_connection_users'] or broker['config_files'] != []):
        raise RuntimeError('broker/client identity preflight differs; no changes made')
    listeners = listener_inventory()
    socket = systemd_properties('epmd.socket')
    epmd = systemd_properties('epmd.service')
    rabbit = systemd_properties('rabbitmq-server.service')
    if (socket['ActiveState'] != 'active' or socket['SubState'] != 'running'
            or socket['UnitFileState'] != 'disabled' or socket['FragmentPath'] != str(UNITS['epmd.socket'])
            or socket['DropInPaths'] or socket['Listen'] != '[::]:4369 (Stream)'):
        raise RuntimeError('EPMD socket differs from reviewed default; no changes made')
    if (epmd['ActiveState'] != 'active' or epmd['SubState'] != 'running'
            or epmd['UnitFileState'] != 'disabled'
            or epmd['FragmentPath'] != str(UNITS['epmd.service']) or epmd['DropInPaths']):
        raise RuntimeError('EPMD service differs from reviewed profile; no changes made')
    if (rabbit['ActiveState'] != 'active' or rabbit['SubState'] != 'running'
            or rabbit['UnitFileState'] != 'enabled' or rabbit['FragmentPath'] != str(UNITS['rabbitmq-server.service'])
            or rabbit['DropInPaths']):
        raise RuntimeError('RabbitMQ service differs from reviewed profile; no changes made')
    directory_info = RABBIT_DIR.lstat()
    rabbit_account = pwd.getpwnam('rabbitmq')
    if (not stat.S_ISDIR(directory_info.st_mode) or stat.S_IMODE(directory_info.st_mode) != 0o755
            or (directory_info.st_uid, directory_info.st_gid) != (rabbit_account.pw_uid, rabbit_account.pw_gid)):
        raise RuntimeError('RabbitMQ configuration directory ownership/mode differs; no changes made')
    protected_regular(ENV_CONFIG, {(directory_info.st_uid, directory_info.st_gid)})
    if environment_keys():
        raise RuntimeError('RabbitMQ environment overrides require review; no changes made')
    if (sorted(item.name for item in RABBIT_DIR.iterdir()) != ['rabbitmq-env.conf']
            or RABBIT_CONFIG.exists() or RABBIT_CONFIG.is_symlink()
            or SOCKET_DROPIN_DIR.exists() or SOCKET_DROPIN_DIR.is_symlink()):
        raise RuntimeError('RabbitMQ configuration inventory differs; no changes made')
    for path in UNITS.values():
        protected_regular(path, {(0, 0)})
    version = common.run(['dpkg-query', '-W', '-f=${Version}', 'rabbitmq-server'], timeout=15).strip()
    if version != '3.9.27-0ubuntu0.6':
        raise RuntimeError('RabbitMQ package version differs from reviewed release; no changes made')
    return dict(broker=broker, listeners=listeners,
                units={'epmd.socket': socket, 'epmd.service': epmd,
                       'rabbitmq-server.service': rabbit},
                package_version=version, env_assignment_count=0)


def queue_inventory():
    raw = common.run(['rabbitmqctl', '--quiet', '--formatter', 'json', 'list_queues',
                      'name', 'messages', 'consumers', 'durable'], timeout=45)
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise RuntimeError('RabbitMQ queue inventory format requires review')
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('name'), str)
                or type(row.get('messages')) is not int or type(row.get('consumers')) is not int
                or type(row.get('durable')) is not bool or row['messages'] < 0 or row['consumers'] < 0):
            raise RuntimeError('RabbitMQ queue inventory fields require review')
    pending = sum(row['messages'] for row in rows)
    if pending:
        raise RuntimeError('RabbitMQ has queued messages; allow the broker to drain before restart')
    return dict(queue_count=len(rows), pending_messages=0,
                durable_queue_count=sum(row['durable'] for row in rows))


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def export_definitions_backup(destination):
    parent_info = STAGING_PARENT.lstat()
    if (not stat.S_ISDIR(parent_info.st_mode) or STAGING_PARENT.is_symlink()
            or (parent_info.st_mode & 0o002 and not parent_info.st_mode & stat.S_ISVTX)):
        raise RuntimeError('unsafe RabbitMQ definitions staging parent')
    progress('definitions staging parent check: PASS')
    account = pwd.getpwnam('rabbitmq')
    staging = Path(tempfile.mkdtemp(prefix='openstack-poc-rabbitmq-', dir=STAGING_PARENT))
    source = staging / 'definitions.json'
    try:
        os.chown(staging, account.pw_uid, account.pw_gid)
        os.chmod(staging, 0o700)
        info = staging.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid
                or info.st_gid != account.pw_gid or stat.S_IMODE(info.st_mode) != 0o700):
            progress('private staging metadata rejected: uid=%d gid=%d mode=%04o' %
                     (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)))
            raise RuntimeError('private RabbitMQ definitions staging directory differs')
        progress('RabbitMQ-owned mode-0700 staging directory: PASS')
        progress('starting rabbitmqctl definitions export')
        result = subprocess.run(['rabbitmqctl', 'export_definitions', str(source)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if result.returncode:
            progress('definitions export failed; exit_status=' + str(result.returncode))
            raise RuntimeError('RabbitMQ definitions export failed; protected partial backup retained')
        progress('definitions export command exit_status=0')
        try:
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        except Exception as error:
            progress('definitions export output open failed; error_type=' + type(error).__name__)
            raise
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) not in {(0, 0), (account.pw_uid, account.pw_gid)}
                    or info.st_size <= 0 or info.st_size > 128 * 1024 * 1024):
                progress('definitions file metadata rejected: uid=%d gid=%d mode=%04o links=%d bytes=%d' %
                         (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink, info.st_size))
                raise RuntimeError('RabbitMQ definitions export file metadata requires review')
            os.fchmod(fd, 0o600)
            secured_info = os.fstat(fd)
            if stat.S_IMODE(secured_info.st_mode) != 0o600:
                progress('definitions staging mode could not be restricted to 0600')
                raise RuntimeError('RabbitMQ definitions staging file mode requires review')
            progress('definitions export file secured to mode 0600: PASS')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                data = stream.read()
        finally:
            os.close(fd)
        try:
            document = json.loads(data)
        except Exception as error:
            progress('definitions JSON parse failed; error_type=%s bytes=%d' %
                     (type(error).__name__, len(data)))
            raise RuntimeError('RabbitMQ definitions export is invalid; protected partial backup retained') from None
        if not isinstance(document, dict):
            progress('definitions JSON root type rejected: ' + type(document).__name__)
            raise RuntimeError('RabbitMQ definitions export is invalid; protected partial backup retained')
        write_private(destination, data)
        try:
            guest.protected_file(destination, 0o600)
        except Exception as error:
            progress('root-only definitions copy validation failed; error_type=' + type(error).__name__)
            raise
        progress('broker definitions exported to private staging and copied to root-only recovery storage')
        return len(data)
    finally:
        if source.exists() or source.is_symlink():
            source.unlink()
        try:
            staging.rmdir()
        except OSError:
            progress('private definitions staging directory retained for operator review')


def verify_recovery(record):
    directory = Path(record['backup_directory'])
    info = directory.lstat()
    if (directory.parent != DATA_DIR or not directory.name.startswith('rabbitmq-recovery-')
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError('unsafe RabbitMQ recovery directory')
    expected = set(ARTIFACTS) - {'checksums.json'}
    if set(record['sha256']) != expected:
        raise RuntimeError('RabbitMQ recovery artifact set differs')
    for name, value in record['sha256'].items():
        path = directory / name
        guest.protected_file(path, 0o600)
        if backup.checksum(path) != value:
            raise RuntimeError('RabbitMQ recovery artifact integrity check failed')
    checksums = directory / 'checksums.json'
    guest.protected_file(checksums, 0o600)
    if json.loads(checksums.read_text()) != record['sha256']:
        raise RuntimeError('RabbitMQ recovery checksum manifest differs')
    if (json.loads((directory / 'change-plan.json').read_text()) != record['plan']
            or json.loads((directory / 'prechange-metadata.json').read_text()) != record['prechange']):
        raise RuntimeError('RabbitMQ recovery metadata differs')


def create_recovery(prechange, queues):
    directory = Path(tempfile.mkdtemp(prefix='rabbitmq-recovery-', dir=DATA_DIR))
    os.chmod(directory, 0o700)
    progress('fresh root-only recovery directory created: ' + directory.name)
    plan = {'rabbitmq_config': {'path': str(RABBIT_CONFIG), 'existed': False,
                                'new_sha256': digest(RABBIT_CONFIG_TEXT.encode())},
            'epmd_socket_dropin': {'path': str(SOCKET_DROPIN), 'existed': False,
                                   'new_sha256': digest(SOCKET_CONFIG_TEXT.encode())},
            'rabbitmq_node_data_changed': False, 'rabbitmq_users_or_vhosts_changed': False,
            'service_enablement_changed': False}
    record = dict(run_id=uuid.uuid4().hex, backup_directory=str(directory), phase='preparing',
                  verified=False, prechange=prechange, queues=queues, plan=plan)
    progress('archiving RabbitMQ configuration and package unit files')
    with tarfile.open(directory / 'configuration-and-units.tar.gz', 'w:gz') as archive:
        archive.add(RABBIT_DIR, arcname='etc/rabbitmq', recursive=True)
        for path in UNITS.values():
            archive.add(path, arcname=str(path).lstrip('/'), recursive=False)
    guest.protected_file(directory / 'configuration-and-units.tar.gz', 0o600)
    progress('configuration and unit archive: PASS')
    definitions = directory / 'rabbitmq-definitions.json'
    progress('exporting broker definitions through private staging')
    export_definitions_backup(definitions)
    progress('broker definitions JSON and root-only copy: PASS')
    progress('writing recovery metadata and checksums')
    write_private(directory / 'prechange-metadata.json',
                  (json.dumps(prechange, indent=2, sort_keys=True) + '\n').encode())
    write_private(directory / 'change-plan.json', (json.dumps(plan, indent=2, sort_keys=True) + '\n').encode())
    readme = '''# RabbitMQ/EPMD loopback recovery point

This root-only point contains /etc/rabbitmq, the package unit files, an exported
definitions document, and pre-change metadata/listeners. Definitions may
contain password hashes. Queue/message payloads are not part of that export.
No RabbitMQ Mnesia data was copied or changed by this task.

The task creates /etc/rabbitmq/rabbitmq.conf and
/etc/systemd/system/epmd.socket.d/50-openstack-poc-loopback.conf. To roll back,
verify every checksum and confirm each live file still matches this task's
recorded new hash. Remove only these two task-created files; remove the drop-in
directory only if empty. Run systemctl daemon-reload, then restart epmd.socket,
epmd.service and rabbitmq-server.service in that order. Do not replace
/var/lib/rabbitmq, reset Mnesia, rename the node, restore definitions over
newer state, or change unit enablement. The definitions export is not a queue
data backup.
'''
    write_private(directory / 'RECOVERY.md', readme.encode())
    expected_artifacts = set(ARTIFACTS) - {'checksums.json'}
    record['sha256'] = {name: backup.checksum(directory / name) for name in expected_artifacts}
    write_private(directory / 'checksums.json',
                  (json.dumps(record['sha256'], indent=2, sort_keys=True) + '\n').encode())
    backup.sync_bundle(directory)
    verify_recovery(record)
    record.update(phase='prepared', verified=True)
    launch.atomic_state(STATE, record)
    progress('protected configuration, unit, definitions and metadata recovery checks: PASS; path=' + str(directory))
    return record


def atomic_create(path, content, uid, gid, mode):
    if path.exists() or path.is_symlink():
        raise RuntimeError('target appeared after inventory; preserving it')
    fd, temporary = tempfile.mkstemp(prefix='.task11-rabbitmq-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_configuration(record):
    verify_recovery(record)
    if record.get('phase') != 'prepared' or not record.get('verified'):
        raise RuntimeError('verified fresh recovery point required before live writes')
    if RABBIT_CONFIG.exists() or SOCKET_DROPIN_DIR.exists():
        raise RuntimeError('configuration drift after backup; no overwrite performed')
    atomic_create(RABBIT_CONFIG, RABBIT_CONFIG_TEXT.encode(), 0, pwd.getpwnam('rabbitmq').pw_gid, 0o640)
    record['phase'] = 'files_written'
    launch.atomic_state(STATE, record)
    SOCKET_DROPIN_DIR.mkdir(mode=0o755)
    os.chmod(SOCKET_DROPIN_DIR, 0o755)
    atomic_create(SOCKET_DROPIN, SOCKET_CONFIG_TEXT.encode(), 0, 0, 0o644)
    record['phase'] = 'files_written'
    launch.atomic_state(STATE, record)


def verify_files(record):
    for key, path in (('rabbitmq_config', RABBIT_CONFIG), ('epmd_socket_dropin', SOCKET_DROPIN)):
        plan = record['plan'][key]
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or digest(path.read_bytes()) != plan['new_sha256']):
            raise RuntimeError('live broker configuration differs from reviewed task result')


def verify_listeners():
    rows = review.listeners(common.run(['ss', '-H', '-lnt'], timeout=10), 'tcp')
    observed = {}
    for port, expected in PORTS.items():
        matches = [row for row in rows if row['port'] == port]
        if ({row['address'] for row in matches} != expected or len(matches) != len(expected)
                or not all(row['loopback'] for row in matches)):
            raise RuntimeError('RabbitMQ/EPMD listener is absent or not loopback-only')
        observed[str(port)] = matches
    return observed


def wait_runtime(state, secrets):
    deadline = time.monotonic() + 210
    while True:
        try:
            units = {name: systemd_properties(name) for name in
                     ('epmd.socket', 'epmd.service', 'rabbitmq-server.service')}
            if any(row['ActiveState'] != 'active' or row['SubState'] != 'running'
                   for row in units.values()):
                raise RuntimeError('service recovery pending')
            listeners = verify_listeners()
            broker = api.rabbit_preflight()
            if (broker['running_nodes'] != [NODE] or broker['disk_nodes'] != [NODE]
                    or broker['ram_nodes'] or not broker['connection_count']
                    or not broker['all_connections_loopback'] or not broker['only_poc_connection_users']):
                raise RuntimeError('broker/client recovery pending')
            with common.connection(secrets) as admin:
                cloud = review.cloud_state(admin, state)
            workload = review.cpu_smoke(state)
            return dict(units=units, listeners=listeners, broker=broker, cloud=cloud, guest_cpu=workload)
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError('RabbitMQ clients, authenticated OpenStack APIs and retained guest did not recover') from None
            time.sleep(3)


def remove_task_file(path, expected_hash):
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or digest(path.read_bytes()) != expected_hash:
        raise RuntimeError('rollback target drifted; preserve it for review')
    path.unlink()
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def rollback(record):
    remove_task_file(RABBIT_CONFIG, record['plan']['rabbitmq_config']['new_sha256'])
    remove_task_file(SOCKET_DROPIN, record['plan']['epmd_socket_dropin']['new_sha256'])
    try:
        SOCKET_DROPIN_DIR.rmdir()
    except OSError:
        pass
    common.run(['systemctl', 'daemon-reload'], timeout=30)
    common.run(['systemctl', 'restart', 'epmd.socket'], timeout=30)
    common.run(['systemctl', 'restart', 'epmd.service'], timeout=45)
    common.run(['systemctl', 'start', 'rabbitmq-server.service'], timeout=120)


def apply():
    if STATE.exists() or STATE.is_symlink():
        guest.protected_file(STATE, 0o600)
        previous = json.loads(STATE.read_text())
        if previous.get('phase') == 'verified' and previous.get('verified'):
            verify_recovery(previous)
            verify_files(previous)
            result = dict(listeners=verify_listeners(), broker=api.rabbit_preflight())
            progress('previously verified loopback state remains intact: ' + json.dumps(result, sort_keys=True))
            return
        raise RuntimeError('unfinished RabbitMQ recovery record; automatic replay refused')
    progress('BEGIN; checking single-node identity, local clients, listeners, units and configuration')
    before = preflight()
    queues = queue_inventory()
    progress('single node, local broker clients, empty queue backlog and exact listener inventory: PASS')
    before['queue_summary'] = queues
    progress('loading retained OpenStack credentials and demo metadata')
    secrets = common.read_secrets(common.SECRET_FILE)
    with common.connection(secrets) as admin:
        state, _ = guest.load_run(admin, require_ssh=False)
        review.cloud_state(admin, state)
    progress('retained cloud/provider and VM inventory: PASS')
    progress('creating and verifying a fresh RabbitMQ recovery bundle')
    record = create_recovery(before, queues)
    try:
        write_configuration(record)
        progress('verified recovery point: PASS; loopback settings staged; restarting RabbitMQ/EPMD only')
        common.run(['systemctl', 'stop', 'rabbitmq-server.service'], timeout=90)
        common.run(['systemctl', 'daemon-reload'], timeout=30)
        common.run(['systemctl', 'restart', 'epmd.socket'], timeout=30)
        common.run(['systemctl', 'restart', 'epmd.service'], timeout=45)
        common.run(['systemctl', 'start', 'rabbitmq-server.service'], timeout=120)
        record['phase'] = 'services_restarted'
        launch.atomic_state(STATE, record)
        progress('RabbitMQ/EPMD restarted; waiting for client, cloud API and retained guest recovery')
        record['runtime_validation'] = wait_runtime(state, secrets)
        verify_files(record)
        record.update(phase='verified', verified=True,
                      checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        launch.atomic_state(STATE, record)
        progress('AMQP 5672, EPMD 4369 and distribution 25672 loopback-only; broker, cloud and KVM guest recovered: PASS')
        progress('protected evidence=' + str(STATE))
        progress('COMPLETE (RabbitMQ/EPMD binding scope only; other host listeners retain their reviewed scope)')
    except Exception:
        if record.get('phase') in ('files_written', 'services_restarted'):
            progress('validation failed; attempting exact-hash configuration rollback')
            try:
                rollback(record)
                record.update(phase='rolled_back', verified=False, rollback='broker start requested after exact config removal')
                launch.atomic_state(STATE, record)
                progress('exact configuration rollback completed; recovery bundle retained')
            except Exception as error:
                record.update(phase='rollback_requires_operator_review',
                              rollback_error_type=type(error).__name__)
                launch.atomic_state(STATE, record)
                progress('rollback requires operator review; protected bundle=' + record['backup_directory'])
        raise


def main():
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    try:
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M)
                or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('existing Ubuntu 22.04 WSL required')
        os.umask(0o077)
        with os.fdopen(os.open('/root/.openstack-poc-nova-prerequisites.lock',
                               os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            apply()
        return 0
    except Exception as error:
        print('STOP: RabbitMQ hardening failed (' + type(error).__name__ + '); protected recovery retained; inspect phase before retry.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
