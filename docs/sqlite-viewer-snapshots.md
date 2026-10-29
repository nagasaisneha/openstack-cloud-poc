# SQLite snapshots for Windows viewing

Use standalone snapshots of these live POC databases in **DB Browser for SQLite**:

| Snapshot filename | Live source in the existing Ubuntu WSL instance |
|---|---|
| `usage.sqlite` | `/var/lib/openstack-poc-billing/usage.sqlite` |
| `billing.sqlite` | `/var/lib/openstack-poc-billing/billing.sqlite` |
| `application-jobs.sqlite` | `/root/openstack-poc-acceptance/application-jobs.sqlite` |

The exporter opens the live sources with SQLite URI `mode=ro` and uses Python's
`sqlite3.Connection.backup()`. It does not copy active database files, bypass
WAL handling, instantiate the application ledger, take its lifetime advisory
lock, or stop/restart services. SQLite's [online backup API](https://www.sqlite.org/backup.html)
provides each database's consistent snapshot while allowing ongoing application
use. These separate databases have separate snapshot times; the export is not
one atomic transaction across all three files.

Snapshots go into a new private Windows folder under `%LOCALAPPDATA%`, named
`OpenStackPocSnapshots-<UTC>-<suffix>`. Prepare this directory with inherited
permissions disabled and inheritable FullControl entries only for the current
Windows user and SYSTEM. Keep database contents and runtime manifests outside
the checkout.

Run [the exporter](../scripts/local-openstack/export_sqlite_viewer_snapshots.py)
as root in the **existing Ubuntu terminal**, supplying that folder's `/mnt/c/`
path with `--destination`. It refuses existing destination contents, validates
source identity/permissions, and writes only new snapshots and export receipts.
It bounds each backup to 60 seconds and checks `PRAGMA integrity_check`. WAL
source data is included through SQLite; only the snapshot is finalized into
DELETE journal mode. Completed snapshot files require no `-wal`, `-shm`, or
`-journal` companion files. Failed exports retain clearly named partial files
for inspection and do not replace live data or previous exports.

The exporter prints the exact Windows paths after successful completion. Its
`snapshot-manifest.json` records per-file timestamps, sizes, SHA-256 checksums,
table counts, integrity results, and before/after POC service fingerprints.
Source ownership, permissions and inodes are checked for preservation; live
content may continue changing normally while the POC runs.

Run [the Windows verifier](../scripts/local-openstack/verify_sqlite_viewer_snapshots.ps1)
in Windows PowerShell with `-Directory` set to the printed Windows directory.
It checks private ACLs, normalizes ownership to the current Windows user,
compares hashes and sizes against the manifest, checks SQLite headers and
absence of sidecars, and marks the three snapshots read-only. Only the new
Windows export directory is affected.

Open the printed `.sqlite` paths in DB Browser for SQLite for viewing. These
are static snapshots; rerunning the export into another new private folder is
required to refresh them. Editing a snapshot cannot update the live POC.

The 2026-10-29 export used the online backup API for all three sources, passed
all three integrity checks and preserved the monitored service fingerprints.
The exporter also passed an isolated WAL test: committed WAL data was included,
an in-flight uncommitted row was excluded, the live writer remained usable,
the result was standalone, and overwrite attempts were rejected. See the
[sanitized export evidence](evidence/sqlite-viewer-snapshots-20261029.json).

The operator returned the completed Windows verification result at
2026-10-29 14:33:50 UTC. All three hashes matched, each file had only operator
and SYSTEM ACL entries, the directory ACL was protected, and each snapshot was
standalone and read-only. The monitored services were unchanged. See the
[sanitized Windows receipt](evidence/sqlite-viewer-windows-20261029.json).
WSL administrator/Windows process launches from the agent session timed out;
the export and Windows verification therefore ran in the operator's existing
Ubuntu and PowerShell terminals. No WSL or POC service restart was used.
