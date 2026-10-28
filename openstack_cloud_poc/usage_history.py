"""Protected provider-usage snapshots; snapshots are never summed as deltas."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import stat

from .billing import BillingError, BillingErrorCode, UsageBatch
from .usage import UsageProvenance, UsageSample


def batch_wire(batch):
    return {
        'project_id': batch.project_id, 'interval_start': batch.interval_start.isoformat(),
        'interval_end': batch.interval_end.isoformat(), 'source': batch.source,
        'provenance': batch.provenance.value, 'complete': batch.complete,
        'collected_at': batch.collected_at.isoformat() if batch.collected_at else None,
        'incomplete_reasons': list(batch.incomplete_reasons),
        'samples': [{
            'resource_id': item.resource_id, 'metric': item.metric, 'quantity': str(item.quantity),
            'unit': item.unit, 'interval_start': item.interval_start.isoformat(),
            'interval_end': item.interval_end.isoformat(), 'source': item.source,
        } for item in batch.samples],
    }


def batch_from_wire(data):
    project = data['project_id']
    provenance = UsageProvenance(data['provenance'])
    samples = tuple(UsageSample(
        project, row['resource_id'], row['metric'], Decimal(row['quantity']), row['unit'],
        datetime.fromisoformat(row['interval_start']), datetime.fromisoformat(row['interval_end']),
        row['source'], provenance,
    ) for row in data['samples'])
    return UsageBatch(project, datetime.fromisoformat(data['interval_start']),
                      datetime.fromisoformat(data['interval_end']), data['source'], provenance,
                      data['complete'], samples,
                      datetime.fromisoformat(data['collected_at']) if data['collected_at'] else None,
                      tuple(data['incomplete_reasons']))


class UsageHistory:
    def __init__(self, path):
        self.path = Path(path)
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE)
        finally:
            os.close(descriptor)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS usage_snapshots (
                    snapshot_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    interval_start TEXT NOT NULL, interval_end TEXT NOT NULL,
                    collected_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS usage_snapshot_project_period
                    ON usage_snapshots(project_id, interval_start, interval_end, collected_at DESC);
                CREATE TRIGGER IF NOT EXISTS usage_snapshots_immutable_update
                    BEFORE UPDATE ON usage_snapshots BEGIN SELECT RAISE(ABORT, 'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS usage_snapshots_immutable_delete
                    BEFORE DELETE ON usage_snapshots BEGIN SELECT RAISE(ABORT, 'immutable'); END;
            ''')

    @contextmanager
    def connection(self):
        try:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            try:
                with db:
                    yield db
            finally:
                db.close()
        except sqlite3.Error:
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE) from None

    def append(self, batch, inventory):
        if not isinstance(batch, UsageBatch) or batch.collected_at is None:
            raise BillingError(BillingErrorCode.INVALID_REQUEST)
        payload = json.dumps({'batch': batch_wire(batch), 'inventory': inventory},
                             sort_keys=True, separators=(',', ':'))
        identifier = hashlib.sha256(payload.encode()).hexdigest()
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO usage_snapshots VALUES (?, ?, ?, ?, ?, ?)',
                       (identifier, batch.project_id, batch.interval_start.isoformat(),
                        batch.interval_end.isoformat(), batch.collected_at.isoformat(), payload))
        return identifier

    def latest(self, project_id, start, end):
        try:
            with self.connection() as db:
                row = db.execute('''SELECT payload FROM usage_snapshots
                    WHERE project_id=? AND interval_start=? AND interval_end=?
                    ORDER BY collected_at DESC, snapshot_id DESC LIMIT 1''',
                    (project_id, start.isoformat(), end.isoformat())).fetchone()
            if row is None:
                raise BillingError(BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)
            data = json.loads(row['payload'])
            batch = batch_from_wire(data['batch'])
            if batch.project_id != project_id or batch.interval_start != start or batch.interval_end != end:
                raise ValueError
            return batch, data['inventory']
        except BillingError:
            raise
        except (TypeError, ValueError, KeyError):
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE) from None


class SnapshotUsageSource:
    def __init__(self, history, clock=None):
        self.history = history
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def certifies_closed_period(self, project_id, interval_start, interval_end):
        batch, inventory = self.history.latest(project_id, interval_start, interval_end)
        sealed = inventory.get('measurement_policy', {}).get('sealed_period', {})
        return (batch.complete and not batch.incomplete_reasons and batch.provenance is UsageProvenance.OBSERVED
            and batch.source == 'openstack:provider-events+libvirt-readonly'
            and re.fullmatch('[0-9a-f]{64}', sealed.get('id', '')) is not None
            and sealed.get('project_id') == project_id and sealed.get('start') == interval_start.isoformat()
            and sealed.get('end') == interval_end.isoformat() and interval_end <= self.clock())

    def collect(self, project_id, interval_start, interval_end):
        batch, inventory = self.history.latest(project_id, interval_start, interval_end)
        reasons = set(batch.incomplete_reasons)
        sealed = inventory.get('measurement_policy', {}).get('sealed_period', {})
        certified = (batch.complete and batch.source == 'openstack:provider-events+libvirt-readonly'
            and re.fullmatch('[0-9a-f]{64}', sealed.get('id', '')) is not None
            and sealed.get('project_id') == project_id and sealed.get('start') == interval_start.isoformat()
            and sealed.get('end') == interval_end.isoformat() and interval_end <= self.clock())
        if batch.collected_at is None or not certified and self.clock() - batch.collected_at > timedelta(minutes=3):
            reasons.add('usage_collection_stale')
        if interval_end > self.clock():
            reasons.add('period_not_closed')
        return replace(batch, complete=batch.complete and not reasons,
                       incomplete_reasons=tuple(sorted(reasons)))
