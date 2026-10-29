#!/usr/bin/python3
"""Export consistent SQLite viewing snapshots into a prepared private Windows folder.

Run as root in the existing Ubuntu WSL terminal. The destination must already
have a Windows ACL restricted to the operator and SYSTEM. Live files are opened
with mode=ro and read through Connection.backup(); no active files are copied.
Each database has its own snapshot time; this is not a cross-database transaction.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time


SOURCES = (
    Path('/var/lib/openstack-poc-billing/usage.sqlite'),
    Path('/var/lib/openstack-poc-billing/billing.sqlite'),
    Path('/root/openstack-poc-acceptance/application-jobs.sqlite'),
)
UNITS = ('apache2.service', 'mariadb.service', 'poc-usage-collector.timer')


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def source_identity(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600,
            'source must be a regular mode-0600 file: ' + path.name)
    return {'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid,
            'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}


def services():
    result = subprocess.run(['systemctl', 'show', *UNITS,
                             '--property=Id,ActiveState,SubState,MainPID,ExecMainStartTimestampMonotonic'],
                            check=True, capture_output=True, text=True, timeout=15)
    return [dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            for block in result.stdout.strip().split('\n\n')]


def backup_database(source, destination):
    started = now()
    deadline = time.monotonic() + 60

    def progress(status, remaining, total):
        require(time.monotonic() < deadline, 'backup exceeded its bounded time window')

    # Exclusive creation prevents overwriting a prior snapshot or following a link.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as live:
        live.execute('PRAGMA query_only=ON')
        source_mode = live.execute('PRAGMA journal_mode').fetchone()[0]
        with closing(sqlite3.connect(destination, timeout=5)) as snapshot:
            live.backup(snapshot, pages=64, progress=progress, sleep=0.05)
            # Make the exported file standalone even when the source uses WAL.
            require(snapshot.execute('PRAGMA journal_mode=DELETE').fetchone() == ('delete',),
                    'could not finalize standalone snapshot')
    completed = now()
    with closing(sqlite3.connect(destination.as_uri() + '?mode=ro', uri=True)) as check:
        require(check.execute('PRAGMA integrity_check').fetchall() == [('ok',)],
                'snapshot integrity check failed')
        table_count = check.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        require(table_count > 0, 'snapshot has no application tables')
    require(not any(Path(str(destination) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')),
            'snapshot still depends on a sidecar')
    digest = hashlib.sha256()
    with destination.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        os.fsync(stream.fileno())
    return {'filename': source.name, 'source': str(source), 'source_journal_mode': source_mode,
            'backup_started_at': started, 'backup_completed_at': completed,
            'bytes': destination.stat().st_size, 'sha256': digest.hexdigest(),
            'tables': table_count, 'integrity_check': 'ok', 'standalone': True}


def write_new(path, value):
    with path.open('x') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    require(os.geteuid() == 0 and 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower(),
            'run with sudo in the existing Ubuntu WSL terminal')
    directory = args.destination
    require(directory.is_absolute() and str(directory).startswith('/mnt/c/Users/')
            and re.fullmatch(r'OpenStackPocSnapshots-\d{8}T\d{6}Z-[0-9a-f]{8}', directory.name)
            and directory.parent.name == 'Local' and directory.is_dir() and not directory.is_symlink(),
            'expected the new, private Windows snapshot directory')
    require(not any(directory.iterdir()), 'destination must be empty; existing files are preserved')
    os.umask(0o077)
    before = {str(path): source_identity(path) for path in SOURCES}
    service_before = services()
    require(all(unit.get('ActiveState') == 'active' for unit in service_before),
            'a monitored POC service is not active; no service changes attempted')
    write_new(directory / 'export-started.json', {'started_at': now(), 'method': 'sqlite3.Connection.backup'})
    entries = []
    for source in SOURCES:
        pending = directory / ('.' + source.name + '.pending')
        entries.append(backup_database(source, pending))
    after = {str(path): source_identity(path) for path in SOURCES}
    require(before == after, 'source identity, ownership or permissions changed during export')
    for source in SOURCES:
        final = directory / source.name
        require(not final.exists(), 'refusing to overwrite a snapshot')
        (directory / ('.' + source.name + '.pending')).rename(final)
    service_after = services()
    report = {'completed_at': now(), 'method': 'sqlite3.Connection.backup',
              'consistency': 'one transactionally consistent snapshot per database; separate snapshot times',
              'live_sources_opened_read_only': True, 'source_identity_and_permissions_unchanged': True,
              'services_before': service_before, 'services_after': service_after,
              'monitored_services_unchanged': service_before == service_after,
              'services_stopped_or_restarted_by_export': False,
              'snapshots': entries}
    write_new(directory / 'snapshot-manifest.json', report)
    windows_directory = 'C:' + str(directory)[6:].replace('/', '\\')
    print(json.dumps({'status': 'complete', 'windows_directory': windows_directory,
                      'files': [windows_directory + '\\' + source.name for source in SOURCES],
                      'integrity_checks': 'all ok',
                      'monitored_services_unchanged': service_before == service_after}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not expose database contents or arbitrary driver error text.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        raise SystemExit('Snapshot export failed: ' + message)
