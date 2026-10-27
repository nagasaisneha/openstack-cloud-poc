from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest

from openstack_cloud_poc.billing import BillingError, BillingErrorCode, UsageBatch
from openstack_cloud_poc.usage import UsageProvenance, UsageSample
from openstack_cloud_poc.usage_history import SnapshotUsageSource, UsageHistory

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 1, tzinfo=timezone.utc)


def batch(quantity='1', collected=END):
    sample = UsageSample('project-a', 'instance-a', 'compute.instance_vcpu_hours', Decimal(quantity),
                          'vCPU-hour', START, END, 'openstack:nova', UsageProvenance.OBSERVED)
    return UsageBatch('project-a', START, END, 'openstack:nova', UsageProvenance.OBSERVED,
                       False, (sample,), collected, ('nova_active_interval_unverified',))


class UsageHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'usage.sqlite'
        self.history = UsageHistory(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_duplicate_snapshot_is_idempotent_and_not_double_counted(self):
        first = self.history.append(batch(), {'object_bytes': 123})
        self.assertEqual(first, self.history.append(batch(), {'object_bytes': 123}))
        with self.history.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage_snapshots').fetchone()[0], 1)

    def test_latest_cumulative_snapshot_replaces_selection_instead_of_summing(self):
        self.history.append(batch('1'), {})
        self.history.append(batch('2', END + timedelta(seconds=60)), {})
        latest, ignored = self.history.latest('project-a', START, END)
        self.assertEqual(latest.samples[0].quantity, Decimal('2'))
        reopened = UsageHistory(self.path)
        self.assertEqual(reopened.latest('project-a', START, END)[0], latest)

    def test_foreign_project_and_other_period_never_receive_a_snapshot(self):
        self.history.append(batch(), {})
        for project, start, end in (('project-b', START, END), ('project-a', END, END + timedelta(days=31))):
            with self.subTest(project=project, start=start), self.assertRaises(BillingError) as caught:
                self.history.latest(project, start, end)
            self.assertEqual(caught.exception.code, BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)

    def test_stale_or_unclosed_usage_cannot_be_complete(self):
        self.history.append(replace(batch(), complete=True, incomplete_reasons=()), {})
        stale = SnapshotUsageSource(self.history, clock=lambda: END + timedelta(minutes=5)).collect('project-a', START, END)
        self.assertFalse(stale.complete)
        self.assertIn('usage_collection_stale', stale.incomplete_reasons)
        unclosed = SnapshotUsageSource(self.history, clock=lambda: END - timedelta(hours=1)).collect('project-a', START, END)
        self.assertFalse(unclosed.complete)
        self.assertIn('period_not_closed', unclosed.incomplete_reasons)

    def test_unsafe_database_or_parent_permissions_are_rejected(self):
        os.chmod(self.path, 0o644)
        with self.assertRaises(BillingError):
            UsageHistory(self.path)
        os.chmod(self.path, 0o600)
        os.chmod(self.path.parent, 0o755)
        with self.assertRaises(BillingError):
            UsageHistory(self.path)

    def test_corrupt_snapshot_fails_closed(self):
        with self.history.connection() as db:
            db.execute('INSERT INTO usage_snapshots VALUES (?, ?, ?, ?, ?, ?)',
                       ('bad', 'project-a', START.isoformat(), END.isoformat(), END.isoformat(), '{}'))
        with self.assertRaises(BillingError) as caught:
            self.history.latest('project-a', START, END)
        self.assertEqual(caught.exception.code, BillingErrorCode.LEDGER_UNAVAILABLE)


if __name__ == '__main__':
    unittest.main()
