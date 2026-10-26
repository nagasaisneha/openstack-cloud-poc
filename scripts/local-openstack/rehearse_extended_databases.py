#!/usr/bin/python3
"""Fresh eight-schema/OVSDB backup and isolated restore; no service changes.

Includes Cinder. Restores only new private datadirs/sockets, with events and
networking disabled. Existing databases, guest disks and backups are preserved.
This does not certify cross-store consistency, storage contents or WSL restore.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import backup_application as backup
import rehearse_recovery as rehearsal
from audit_upgrade_recovery import DATA, LOCK, protected


def main():
    if os.geteuid() != 0:
        print('STOP: run sudo /usr/bin/python3 -B ' + str(Path(__file__).resolve()))
        return 1
    os.umask(0o077)
    report = {'schema': 1, 'scope': 'fresh_caracal_sql_and_ovsdb_restore', 'verified': False,
              'started_at': datetime.now(timezone.utc).isoformat(), 'stages': {},
              'live_databases_overwritten': False, 'service_changes': False,
              'full_storage_or_wsl_recovery_verified': False, 'target_release_migration_verified': False}
    path = None
    try:
        protected(DATA, directory=True)
        fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            protected(LOCK)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if shutil.disk_usage(DATA).free < 3 * 1024**3:
                raise RuntimeError('rehearsal_disk_headroom_unavailable')
            memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
            if int(memory['MemAvailable'].split()[0]) < 512 * 1024:
                raise RuntimeError('rehearsal_memory_headroom_unavailable')
            directory = Path(tempfile.mkdtemp(prefix='extended-database-recovery-', dir=DATA))
            path = directory / 'report.json'
            rehearsal.DIAGNOSTIC_ROOT = directory
            bundle = rehearsal.subdirectory(directory, 'bundle')
            databases = backup.DATABASES + ('cinder',)
            backup.snapshot_databases(bundle, databases=databases)
            hashes = {item.name: backup.checksum(item) for item in bundle.iterdir()}
            report['artifact_sha256'] = hashes
            report['stages']['snapshot'] = {'verified': True, 'database_count': len(databases)}
            path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
            report['stages']['sql'] = rehearsal.restore_sql(bundle / 'cloud-databases.sql',
                rehearsal.subdirectory(directory, 'sql'), databases=databases)
            for filename, endpoint, database in backup.OVSDB:
                report['stages'][database] = rehearsal.restore_ovs(bundle / filename, database,
                    rehearsal.subdirectory(directory, database.lower()))
            if hashes != {item.name: backup.checksum(item) for item in bundle.iterdir()}:
                raise RuntimeError('source_snapshot_changed')
            report.update(verified=True, all_owned_children_stopped=True,
                          completed_at=datetime.now(timezone.utc).isoformat())
            path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
            backup.sync_bundle(directory)
            print('POC extended database recovery: ' + json.dumps({
                'status': 'PASS', 'restored_schemas': sorted(report['stages']['sql']['databases']),
                'ovsdb_restores': 3, 'all_owned_children_stopped': True,
                'live_databases_overwritten': False, 'full_storage_or_wsl_recovery_verified': False}, sort_keys=True)
                + '; report=' + str(path))
            return 0
    except Exception as error:
        report['error_type'] = type(error).__name__
        if path is not None:
            path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        print('STOP: extended database recovery unavailable: ' + type(error).__name__
              + '; original databases/backups/services preserved; report=' + str(path))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
