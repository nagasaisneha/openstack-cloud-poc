#!/usr/bin/python3
"""Rehearse the recorded application recovery in fresh, isolated destinations.

Run in the actual Ubuntu terminal with sudo. No live cloud/database restore,
service restart, network attachment, Nova resource creation, or file deletion.
Only child processes started by this invocation are stopped. Diagnostics and
restored copies remain private; source recovery points are never modified.
"""
import contextlib
import datetime
import fcntl
import json
import logging
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

import backup_application as backup
import configure_nova as common
import launch_test_vm as launch
import validate_guest as guest

SOURCE_SERVER = 'db0d12b8-3bf4-4e53-9a63-4bbea9971831'
SOURCE_BUNDLE = 'recovery-' + SOURCE_SERVER + '-lu4cuzr1'
COMMANDS = ('qemu-img', 'qemu-system-x86_64', 'mariadb-install-db', 'mariadbd',
            'mariadb', 'ovsdb-tool', 'ovsdb-server', 'ovsdb-client')
DIAGNOSTIC_ROOT = None


class RehearsalError(RuntimeError):
    """Only fixed, credential-free messages may use this exception type."""


ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}


def progress(message):
    print('POC recovery rehearsal: ' + message, flush=True)


def run(command, *, input_file=None, input_text=None, timeout=60):
    """Capture diagnostics without putting database/configuration text in errors."""
    result = subprocess.run(command, stdin=input_file, input=input_text, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, env=ENV, check=False)
    if result.returncode:
        if DIAGNOSTIC_ROOT is not None:
            with tempfile.NamedTemporaryFile(mode='w', prefix='command-failure-', suffix='.log',
                    dir=DIAGNOSTIC_ROOT, delete=False) as diagnostic:
                diagnostic.write(result.stdout + '\n' + result.stderr)
        raise RehearsalError(Path(command[0]).name + ' failed; private rehearsal artifacts retained')
    return result.stdout


@contextlib.contextmanager
def child(command, directory, *, identity=None):
    """Never detach, use a shared pidfile, or kill a process by name."""
    options = {}
    if identity is not None:
        options = dict(user=identity.pw_uid, group=identity.pw_gid,
                       extra_groups=[Path('/dev/kvm').stat().st_gid])
    with (directory / 'process.log').open('xb') as diagnostic:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=diagnostic,
            stderr=subprocess.STDOUT, cwd=directory, env=ENV, start_new_session=True, **options)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def wait_ready(process, action, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RehearsalError('isolated child exited before readiness; inspect private process.log')
        try:
            return action()
        except (RuntimeError, OSError):
            time.sleep(0.5)
    raise RehearsalError('isolated child readiness timed out')


def subdirectory(parent, name):
    directory = parent / name
    directory.mkdir(mode=0o700)
    return directory


def restore_journal(source, destination):
    with destination.open('xb'):
        pass
    with contextlib.closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as original, \
         contextlib.closing(sqlite3.connect(destination)) as restored:
        original.backup(restored)
        if restored.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RehearsalError('restored journal integrity check failed')
        # Compare the backed-up journal, never the current application's jobs.
        tables = original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        if ('jobs',) not in tables:
            raise RehearsalError('backup has no application jobs table')
        for (name,) in tables:
            if not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_]*', name):
                raise RehearsalError('unexpected backup table identifier')
            query = 'SELECT * FROM "' + name + '"'
            if sorted(original.execute(query).fetchall(), key=repr) != sorted(restored.execute(query).fetchall(), key=repr):
                raise RehearsalError('restored journal differs from its backup')
        count = restored.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    return dict(verified=True, jobs=count, exact_table_contents=True)


def restore_archive(source, directory):
    """Materialize regular files; record links without following/restoring them."""
    count = total = links = 0
    with tarfile.open(source, 'r:gz') as archive:
        members = archive.getmembers()
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or not path.parts or '..' in path.parts
                    or str(path) != member.name or member.name in names
                    or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
                raise RehearsalError('unsafe or duplicate operational archive member')
            names.add(member.name)
            total += member.size if member.isfile() else 0
        if total > 2 * 1024 ** 3:
            raise RehearsalError('operational archive exceeds the 2 GiB rehearsal budget')
        # Validate the whole archive before the first extraction. No extractall,
        # ownership restoration, devices, FIFOs or symlink traversal.
        for member in members:
            target = directory.joinpath(*PurePosixPath(member.name).parts)
            if member.issym() or member.islnk():
                links += 1
                continue
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(mode=0o700, exist_ok=True)
            else:
                with archive.extractfile(member) as original, target.open('xb') as restored:
                    shutil.copyfileobj(original, restored)
                target.chmod(0o600)
                if target.stat().st_size != member.size:
                    raise RehearsalError('restored archive member has an unexpected size')
                count += 1
    return dict(verified=True, regular_files=count, restored_bytes=total,
                links_recorded_not_followed=links, live_paths_replaced=False)


def sql_client(directory):
    # --no-defaults MUST be the first option. The explicit socket is always
    # supplied; no path can fall back to the system MariaDB socket.
    return ['mariadb', '--no-defaults', '--protocol=socket', '--socket=' + str(directory / 'sql.sock'),
            '--user=' + pwd.getpwuid(os.geteuid()).pw_name,
            '--batch', '--raw', '--skip-column-names', '--skip-reconnect']


def restore_sql(source, directory, *, databases=None):
    databases = backup.database_scope(databases)
    datadir = subdirectory(directory, 'data')
    account = pwd.getpwuid(os.geteuid()).pw_name
    run(['mariadb-install-db', '--no-defaults', '--datadir=' + str(datadir),
         '--auth-root-authentication-method=socket', '--skip-test-db', '--user=' + account], timeout=90)
    command = ['mariadbd', '--no-defaults', '--user=' + account, '--datadir=' + str(datadir),
        '--socket=' + str(directory / 'sql.sock'), '--pid-file=' + str(directory / 'sql.pid'),
        '--log-error=' + str(directory / 'sql.log'), '--tmpdir=' + str(directory),
        '--skip-networking', '--skip-log-bin', '--skip-slave-start', '--event-scheduler=OFF',
        '--performance-schema=OFF', '--innodb-buffer-pool-size=64M', '--max-connections=5',
        '--local-infile=0', '--secure-file-priv=' + str(directory)]
    with child(command, directory) as process:
        client = sql_client(directory)
        wait_ready(process, lambda: run(client, input_text='SELECT 1;\n', timeout=5))
        with source.open() as stream:
            run(client, input_file=stream, timeout=180)
        actual = run(client, input_text='SHOW DATABASES;\n').splitlines()
        if set(actual) != set(databases) | {'information_schema', 'mysql', 'performance_schema', 'sys'}:
            raise RehearsalError('restored SQL database inventory differs from the selected recovery scope')
        inventories = {}
        for name in databases:
            # Names come only from the fixed legacy/extended database allowlist.
            tables = run(client, input_text=f"SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='{name}' AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME;\n").splitlines()
            if not tables or any(not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_]*', table) for table in tables):
                raise RehearsalError('missing or unexpected restored SQL table inventory')
            checks = run(client, input_text='CHECK TABLE ' + ','.join('`' + name + '`.`' + table + '`' for table in tables) + ';\n', timeout=90).splitlines()
            if len(checks) != len(tables) or any(row.rsplit('\t', 2)[-2:] != ['status', 'OK'] for row in checks):
                raise RehearsalError('restored SQL table integrity check failed')
            inventories[name] = len(tables)
        flags = run(client, input_text='SELECT @@skip_networking, @@event_scheduler, @@log_bin;\n').strip()
        if flags != '1\tOFF\t0':
            raise RehearsalError('isolated SQL networking/events/binlog guard failed')
    return dict(verified=True, databases=inventories, private_socket_only=True, child_stopped=True,
                limitation='Logical import and table integrity; no live service rollback or cross-service atomicity')


def ovs_server(directory, database):
    return ['ovsdb-server', str(database), '--no-chdir', '--remote=punix:' + str(directory / 'db.sock'),
            '--unixctl=' + str(directory / 'control.sock'), '--pidfile=' + str(directory / 'db.pid'),
            '-vconsole:err', '-vsyslog:off']


def ovs_counts(endpoint, schema):
    tables = sorted(schema['tables'])
    query = [schema['name']] + [dict(op='select', table=table, where=[], columns=['_uuid']) for table in tables]
    results = json.loads(run(['ovsdb-client', '--timeout=10', 'query', endpoint, json.dumps(query)]))
    if len(results) != len(tables) or any(set(result) != {'rows'} for result in results):
        raise RehearsalError('isolated OVSDB row inventory failed')
    return {table: len(result['rows']) for table, result in zip(tables, results)}


def restore_ovs(source, database, directory):
    seed = subdirectory(directory, 'seed')
    copied = seed / 'snapshot.db'
    shutil.copyfile(source, copied)
    endpoint = 'unix:' + str(seed / 'db.sock')
    with child(ovs_server(seed, copied), seed) as process:
        wait_ready(process, lambda: run(['ovsdb-client', '--timeout=2', 'list-dbs', endpoint], timeout=5))
        schema = json.loads(run(['ovsdb-client', '--timeout=10', 'get-schema', endpoint, database]))
        if schema['name'] != database:
            raise RehearsalError('snapshot OVSDB schema name differs from the expected service')
        expected = ovs_counts(endpoint, schema)
    schema_path = directory / 'schema.json'
    schema_path.write_text(json.dumps(schema))
    restored = subdirectory(directory, 'restored')
    target = restored / 'restored.db'
    run(['ovsdb-tool', 'create', str(target), str(schema_path)])
    endpoint = 'unix:' + str(restored / 'db.sock')
    with child(ovs_server(restored, target), restored) as process:
        wait_ready(process, lambda: run(['ovsdb-client', '--timeout=2', 'list-dbs', endpoint], timeout=5))
        with source.open() as stream:
            run(['ovsdb-client', '--timeout=20', 'restore', endpoint, database], input_file=stream)
        if ovs_counts(endpoint, schema) != expected:
            raise RehearsalError('restored OVSDB table counts differ from the snapshot')
    return dict(verified=True, tables=len(expected), rows=sum(expected.values()),
                private_socket_only=True, children_stopped=True,
                limitation='Schema and per-table row counts verified; controllers not attached')


def restore_disk(source, directory):
    target = directory / 'restored.qcow2'
    if target.exists() or target.is_symlink():
        raise RehearsalError('restored disk destination exists')
    run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', str(source), str(target)], timeout=120)
    run(['qemu-img', 'check', '-f', 'qcow2', '--output=json', str(target)])
    info = json.loads(run(['qemu-img', 'info', '--output=json', str(target)]))
    if info.get('format') != 'qcow2' or info.get('backing-filename') or info.get('virtual-size') != 1024 ** 3:
        raise RehearsalError('restored disk is not a self-contained 1 GiB qcow2')
    run(['qemu-img', 'compare', '-f', 'qcow2', '-F', 'qcow2', str(source), str(target)], timeout=120)
    return target


def qemu_command(directory, identity):
    return ['qemu-system-x86_64', '-no-user-config', '-nodefaults', '-machine', 'pc',
            '-accel', 'kvm', '-cpu', 'host', '-m', '256', '-smp', '1',
            '-uuid', identity, '-name', 'poc-isolated-restore-' + identity,
            '-display', 'none', '-monitor', 'none', '-nic', 'none', '-no-reboot',
            '-serial', 'file:' + str(directory / 'console.log'),
            '-qmp', 'unix:' + str(directory / 'qmp.sock') + ',server=on,wait=off',
            '-drive', 'file=' + str(directory / 'boot.qcow2') + ',format=qcow2,if=virtio',
            '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny']


def qmp_kvm(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(path))
        with client.makefile('rwb') as stream:
            def receive():
                line = stream.readline(65537)
                if not line or len(line) > 65536:
                    raise RehearsalError('invalid QMP response')
                return json.loads(line)
            if 'QMP' not in receive():
                raise RehearsalError('missing QMP greeting')
            for operation in ('qmp_capabilities', 'query-kvm'):
                stream.write((json.dumps({'execute': operation}) + '\n').encode())
                stream.flush()
                for _ in range(20):
                    response = receive()
                    if 'error' in response:
                        raise RehearsalError('isolated QMP check failed')
                    if 'return' in response:
                        break
                else:
                    raise RehearsalError('QMP response budget exceeded')
            if response['return'] != {'enabled': True, 'present': True}:
                raise RehearsalError('restored guest is not KVM accelerated')
    return True


def boot_disk(target, directory):
    identity = pwd.getpwnam('libvirt-qemu')
    if identity.pw_uid == 0:
        raise RehearsalError('unprivileged libvirt-qemu account required')
    # QEMU gets access only to a fresh copy and overlay in its own private
    # scratch directory. The protected backup directory stays root-only.
    base = directory / 'base.qcow2'
    shutil.copyfile(target, base)
    before = backup.checksum(base)
    run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(base), str(directory / 'boot.qcow2')])
    for path in (base, directory / 'boot.qcow2'):
        path.chmod(0o600)
        os.chown(path, identity.pw_uid, identity.pw_gid)
    os.chown(directory, identity.pw_uid, identity.pw_gid)
    try:
        with child(qemu_command(directory, str(uuid.uuid4())), directory, identity=identity) as process:
            wait_ready(process, lambda: qmp_kvm(directory / 'qmp.sock'), timeout=20)
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RehearsalError('restored guest exited before its login prompt')
                console = directory / 'console.log'
                if console.exists():
                    if console.stat().st_size > 1024 ** 2:
                        raise RehearsalError('restored guest console exceeded the bounded log budget')
                    text = console.read_text(errors='replace')
                    if re.search(r'(?:^|\n)[^\r\n]{0,80}\blogin:\s*$', text, re.M):
                        break
                time.sleep(1)
            else:
                raise RehearsalError('restored guest login prompt was not observed within 240 seconds')
        if backup.checksum(base) != before:
            raise RehearsalError('restored base disk changed despite using a disposable overlay')
    finally:
        # No deletion. Reclaim this invocation's scratch files after its child
        # has stopped so saved guest output remains root-only.
        for path in directory.iterdir():
            os.chown(path, 0, 0, follow_symlinks=False)
            if path.is_file() and not path.is_symlink():
                path.chmod(0o600)
        os.chown(directory, 0, 0)
        directory.chmod(0o700)
    return dict(verified=True, kvm_enabled=True, serial_login_prompt=True,
                network_devices=0, nova_resource_created=False, child_stopped=True,
                base_disk_unchanged=True, limitation='Isolated disk boot only; no Nova/Neutron restore')


def source_bundle():
    root = launch.DATA_DIR
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or root.is_symlink()
            or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700):
        raise RehearsalError('protected acceptance directory required')
    path = root / 'application.json'
    guest.protected_file(path, 0o600)
    application = json.loads(path.read_text())
    lifecycle = application['results']['application_lifecycle']
    cleanup = application['results']['dependency_cleanup']
    source = root / SOURCE_BUNDLE
    if (application['ids']['server'] != SOURCE_SERVER or lifecycle.get('phase') != 'deleted'
            or not lifecycle.get('verified') or lifecycle.get('backup_directory') != str(source)
            or cleanup.get('verified') is not True or set(cleanup['done']) != set(range(len(cleanup['plan'])))):
        raise RehearsalError('verified original recovery and completed temporary cleanup required')
    backup.verify_bundle(source, SOURCE_SERVER)
    return source


def main():
    global DIAGNOSTIC_ROOT
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    report = {'verified': False, 'stages': {}, 'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    destination = None
    try:
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RehearsalError('existing Ubuntu 22.04 WSL required')
        for name in COMMANDS:
            if not shutil.which(name, path=ENV['PATH']):
                raise RehearsalError('required command unavailable: ' + name)
        if shutil.disk_usage('/var/tmp').free < 3 * 1024 ** 3:
            raise RehearsalError('3 GiB free scratch space required')
        memory = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M).group(1))
        if memory < 768 * 1024:
            raise RehearsalError('768 MiB available memory required')
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            source = source_bundle()
            destination = Path(tempfile.mkdtemp(prefix='restore-rehearsal-', dir=launch.DATA_DIR))
            DIAGNOSTIC_ROOT = destination
            report['source_bundle'] = str(source)
            report['source_server'] = SOURCE_SERVER
            report['scratch_directories'] = []
            progress('BEGIN; source backups and live services preserved')
            stages = (
                ('journal', lambda: restore_journal(source / 'application-jobs.sqlite', destination / 'restored-jobs.sqlite')),
                ('operational_files', lambda: restore_archive(source / 'operational-files.tar.gz', subdirectory(destination, 'files'))),
            )
            for name, action in stages:
                report['stages'][name] = action()
                launch.atomic_state(destination / 'report.json', report)
                progress(name + ': PASS')
            # Short /var/tmp paths keep private UNIX sockets below Linux's
            # sockaddr_un path limit; none are system service sockets.
            sql = Path(tempfile.mkdtemp(prefix='poc-sql-', dir='/var/tmp'))
            report['scratch_directories'].append(str(sql))
            launch.atomic_state(destination / 'report.json', report)
            report['stages']['sql'] = restore_sql(source / 'cloud-databases.sql', sql)
            launch.atomic_state(destination / 'report.json', report)
            progress('seven isolated SQL databases: PASS')
            for filename, _, database in backup.OVSDB:
                ovs = Path(tempfile.mkdtemp(prefix='poc-ovs-', dir='/var/tmp'))
                report['scratch_directories'].append(str(ovs))
                launch.atomic_state(destination / 'report.json', report)
                report['stages'][database] = restore_ovs(source / filename, database, ovs)
                launch.atomic_state(destination / 'report.json', report)
                progress(database + ' isolated restore: PASS')
            restored = restore_disk(source / 'guest-root.qcow2', destination)
            report['stages']['disk'] = dict(verified=True, contents_match=True)
            boot = Path(tempfile.mkdtemp(prefix='poc-boot-', dir='/var/tmp'))
            report['scratch_directories'].append(str(boot))
            launch.atomic_state(destination / 'report.json', report)
            progress('disk contents: PASS; waiting up to 240s for isolated guest login')
            report['stages']['guest_boot'] = boot_disk(restored, boot)
            # Recheck every original artifact after all operations.
            backup.verify_bundle(source, SOURCE_SERVER)
            report['source_bundle_unchanged'] = True
            report['verified'] = True
            report['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            launch.atomic_state(destination / 'report.json', report)
            progress('PASS; all owned child processes stopped; report=' + str(destination / 'report.json'))
            return 0
    except Exception as exc:
        if destination is not None:
            report['error_type'] = type(exc).__name__
            report['failure_reason'] = str(exc) if isinstance(exc, RehearsalError) else type(exc).__name__
            launch.atomic_state(destination / 'report.json', report)
            progress('protected incomplete report=' + str(destination / 'report.json'))
        # SQL, archive and serial output may contain secrets: never echo an
        # exception, subprocess output or full report here.
        reason = str(exc) if isinstance(exc, RehearsalError) else type(exc).__name__
        print('STOP: recovery rehearsal incomplete: ' + reason + '; existing backups/live state preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
