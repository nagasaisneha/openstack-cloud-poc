"""Real SQLite/app-domain recovery tests; no cloud or WSL recovery claims."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from openstack_cloud_poc.application_recovery import (
    ApplicationRecoveryError, create_backup, rehearse_restore, verify_backup,
)
from openstack_cloud_poc.billing import BillingLedger, BillingService, BillingError
from tests.test_billing import START, END, Source, admin, collector, project_user, make_sample, make_card, make_rule


class ApplicationStateRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def jobs(self):
        path = self.root / 'application-jobs.sqlite'
        path.touch(mode=0o600)
        db = sqlite3.connect(path)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE jobs (job_id TEXT PRIMARY KEY, state TEXT)')
        db.execute("INSERT INTO jobs VALUES ('owned-job', 'succeeded')")
        db.commit()
        return path, db

    def test_snapshot_includes_committed_wal_and_restore_is_independent(self):
        path, live = self.jobs()
        try:
            bundle = self.root / 'bundle'
            manifest = create_backup({'jobs': path}, bundle)
            self.assertFalse(manifest['cloud_or_wsl_recovery_verified'])
            self.assertEqual(manifest['components']['jobs']['table_rows'], {'jobs': 1})
            live.execute("INSERT INTO jobs VALUES ('later-job', 'running')")
            live.commit()
            report = rehearse_restore(bundle, self.root / 'restored')
            self.assertTrue(report['verified'])
            self.assertFalse(report['live_database_overwritten'])
            with sqlite3.connect(self.root / 'restored/jobs.sqlite') as restored:
                self.assertEqual(restored.execute('SELECT * FROM jobs').fetchall(), [('owned-job', 'succeeded')])
            self.assertEqual(live.execute('SELECT COUNT(*) FROM jobs').fetchone(), (2,))
        finally:
            live.close()

    def test_restored_billing_preserves_rates_statement_csv_denials_and_immutable_history(self):
        path = self.root / 'billing.sqlite'
        ledger = BillingLedger(path)
        service = BillingService(ledger, Source((make_sample(),)))
        service.configure_rate_card(admin(), make_card(make_rule()))
        statement = service.generate_statement(collector(), 'project-a', START, END, 'demo-v1')
        csv = service.project_statement_csv(project_user('project-a', 'billing_viewer'), 'project-a', statement.statement_id)
        audit_before = len(ledger.list_audit_events())
        try:
            create_backup({'billing': path}, self.root / 'bundle')
            rehearse_restore(self.root / 'bundle', self.root / 'restored')
            restored = BillingLedger(self.root / 'restored/billing.sqlite')
            try:
                self.assertEqual(len(restored.list_audit_events()), audit_before)
                recovered = BillingService(restored, Source())
                self.assertEqual(recovered.project_statement_csv(project_user('project-a', 'billing_viewer'),
                                                                 'project-a', statement.statement_id), csv)
                with self.assertRaises(BillingError):
                    recovered.project_statement_csv(project_user('project-b', 'billing_viewer'),
                                                    'project-a', statement.statement_id)
                with sqlite3.connect(restored.path) as db:
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute('DELETE FROM statements')
            finally:
                restored.close()
            self.assertEqual(len(ledger.list_audit_events()), audit_before)
        finally:
            ledger.close()

    def test_corrupt_bundle_and_existing_destination_never_overwrite_live_state(self):
        path, live = self.jobs()
        live.close()
        create_backup({'jobs': path}, self.root / 'bundle')
        existing = self.root / 'existing'
        existing.mkdir(mode=0o700)
        marker = existing / 'preserved'
        marker.write_text('keep')
        with self.assertRaises(FileExistsError):
            rehearse_restore(self.root / 'bundle', existing)
        self.assertEqual(marker.read_text(), 'keep')
        with (self.root / 'bundle/jobs.sqlite').open('ab') as stream:
            stream.write(b'corruption')
        with self.assertRaisesRegex(ApplicationRecoveryError, 'checksum'):
            rehearse_restore(self.root / 'bundle', self.root / 'corrupt-restore')
        self.assertFalse((self.root / 'corrupt-restore').exists())

    def test_missing_unsafe_or_wrong_source_cannot_become_a_successful_backup(self):
        path, live = self.jobs()
        live.close()
        with self.assertRaises(FileNotFoundError):
            create_backup({'jobs': self.root / 'missing.sqlite'}, self.root / 'missing-bundle')
        self.assertFalse((self.root / 'missing.sqlite').exists())
        link = self.root / 'link.sqlite'
        link.symlink_to(path)
        with self.assertRaises(ApplicationRecoveryError):
            create_backup({'jobs': link}, self.root / 'symlink-bundle')
        with self.assertRaisesRegex(ApplicationRecoveryError, 'schema'):
            create_backup({'billing': path}, self.root / 'wrong-role')
        self.assertFalse((self.root / 'wrong-role/manifest.json').exists())
        path.chmod(0o644)
        with self.assertRaises(ApplicationRecoveryError):
            create_backup({'jobs': path}, self.root / 'unsafe-bundle')

    def test_a_later_source_change_cannot_change_a_frozen_verified_backup(self):
        path, live = self.jobs()
        try:
            before = create_backup({'jobs': path}, self.root / 'bundle')
            live.execute("DELETE FROM jobs")
            live.commit()
            self.assertEqual(verify_backup(self.root / 'bundle'), before)
            self.assertEqual(os.stat(self.root / 'bundle/manifest.json').st_mode & 0o777, 0o600)
        finally:
            live.close()
