"""Append-only recovery bundles for application-owned SQLite state.

Uses SQLite's online backup API, including committed WAL contents. Rehearsal
restores into a new private directory only; it never replaces a live database.
This covers the supplied application databases, not service SQL, storage or WSL.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time


class ApplicationRecoveryError(RuntimeError):
    """Safe error code; raw database contents are never included."""


ROLE_TABLES = {'jobs': {'jobs'}, 'billing': {'rate_cards', 'statements', 'billing_audit_events'}}


def private_directory(path):
    path = Path(path).absolute()
    info = path.lstat()
    if (path.resolve() != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise ApplicationRecoveryError('unsafe_directory')
    return path


def private_file(path):
    path = Path(path).absolute()
    private_directory(path.parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ApplicationRecoveryError('unsafe_file')
    return path


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def _new_file(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, 'wb')


def _persist_json(path, value):
    with _new_file(path) as stream:
        stream.write((json.dumps(value, sort_keys=True, indent=2) + '\n').encode())
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def database_summary(path):
    """Inspect a frozen backup, never an immutable view of a live WAL DB."""
    with closing(sqlite3.connect(private_file(path).as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ApplicationRecoveryError('database_integrity_failed')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ApplicationRecoveryError('database_foreign_keys_failed')
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {table: db.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
                  for table in tables}
        logical = hashlib.sha256()
        for statement in db.iterdump():
            logical.update(statement.encode())
            logical.update(b'\n')
        return {'table_rows': counts, 'logical_sha256': logical.hexdigest(), 'integrity': 'ok'}


def create_backup(sources, destination, *, timeout_seconds=60):
    """Snapshot named jobs/billing databases without opening missing sources."""
    if (not isinstance(sources, dict) or not sources or set(sources) - {'jobs', 'billing'}
            or not 0 < timeout_seconds <= 300):
        raise ApplicationRecoveryError('invalid_snapshot_scope')
    selected = {role: private_file(path) for role, path in sources.items()}
    if len(set(selected.values())) != len(selected):
        raise ApplicationRecoveryError('duplicate_database_source')
    destination = Path(destination).absolute()
    private_directory(destination.parent)
    # Reserve room for DB plus WAL; no deletion or overwrite on low space.
    required = sum(path.stat().st_size + (Path(str(path) + '-wal').stat().st_size
                   if Path(str(path) + '-wal').is_file() else 0) for path in selected.values())
    if shutil.disk_usage(destination.parent).free < required * 2 + 1024 ** 2:
        raise ApplicationRecoveryError('snapshot_capacity_unavailable')
    destination.mkdir(mode=0o700, exist_ok=False)
    manifest = {'schema': 1, 'purpose': 'application_sqlite_recovery',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'cloud_or_wsl_recovery_verified': False, 'distributed_consistency': False,
                'components': {}}
    for role, source in sorted(selected.items()):
        output = destination / (role + '.sqlite')
        with _new_file(output):
            pass
        deadline = time.monotonic() + timeout_seconds
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise ApplicationRecoveryError('snapshot_timeout')
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as live, \
                closing(sqlite3.connect(output)) as saved:
            live.execute('PRAGMA query_only=ON')
            live.backup(saved, pages=128, progress=progress, sleep=0.01)
        with output.open('rb') as stream:
            os.fsync(stream.fileno())
        summary = database_summary(output)
        if not ROLE_TABLES[role] <= set(summary['table_rows']):
            raise ApplicationRecoveryError('application_schema_mismatch')
        manifest['components'][role] = {'filename': output.name, 'sha256': digest(output),
                                        'bytes': output.stat().st_size, **summary}
    _persist_json(destination / 'manifest.json', manifest)
    _sync_directory(destination)
    _sync_directory(destination.parent)
    return verify_backup(destination)


def verify_backup(directory):
    directory = private_directory(directory)
    manifest = json.loads(private_file(directory / 'manifest.json').read_text())
    if (manifest.get('schema') != 1 or manifest.get('purpose') != 'application_sqlite_recovery'
            or not isinstance(manifest.get('components'), dict) or not manifest['components']
            or set(manifest['components']) - {'jobs', 'billing'}):
        raise ApplicationRecoveryError('invalid_manifest')
    for role, record in manifest['components'].items():
        if (not isinstance(record, dict) or record.get('filename') != role + '.sqlite'
                or not re.fullmatch('[0-9a-f]{64}', str(record.get('sha256', '')))):
            raise ApplicationRecoveryError('invalid_artifact_identity')
        path = private_file(directory / record['filename'])
        if digest(path) != record['sha256'] or path.stat().st_size != record.get('bytes'):
            raise ApplicationRecoveryError('artifact_checksum_mismatch')
        summary = database_summary(path)
        if not ROLE_TABLES[role] <= set(summary['table_rows']):
            raise ApplicationRecoveryError('application_schema_mismatch')
        if any(record.get(key) != value for key, value in summary.items()):
            raise ApplicationRecoveryError('database_contents_mismatch')
    return manifest


def rehearse_restore(bundle, destination):
    """Verify, restore, and inspect independent copies; never overwrite state."""
    bundle = private_directory(bundle)
    manifest = verify_backup(bundle)
    destination = Path(destination).absolute()
    private_directory(destination.parent)
    destination.mkdir(mode=0o700, exist_ok=False)
    for role, record in manifest['components'].items():
        source, target = bundle / record['filename'], destination / record['filename']
        with source.open('rb') as original, _new_file(target) as restored:
            shutil.copyfileobj(original, restored, length=1024 * 1024)
            restored.flush()
            os.fsync(restored.fileno())
        if digest(target) != record['sha256'] or database_summary(target) != database_summary(source):
            raise ApplicationRecoveryError('restored_contents_mismatch')
    # Recheck original bundle after copying. Source live DBs are not touched.
    verify_backup(bundle)
    report = {'schema': 1, 'verified': True, 'purpose': 'application_sqlite_restore_rehearsal',
              'components': sorted(manifest['components']),
              'manifest_sha256': digest(bundle / 'manifest.json'),
              'completed_at': datetime.now(timezone.utc).isoformat(),
              'live_database_overwritten': False, 'cloud_or_wsl_recovery_verified': False}
    _persist_json(destination / 'report.json', report)
    _sync_directory(destination)
    _sync_directory(destination.parent)
    return report
