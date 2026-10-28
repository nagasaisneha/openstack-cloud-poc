from pathlib import Path
import sqlite3
import tempfile
import unittest

from openstack_cloud_poc.swift_usage_journal import MutationJournal, SwiftMutationJournal


class SwiftJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = MutationJournal(Path(self.temp.name) / 'events.sqlite')
        self.project = 'a' * 32

    def request(self, status='201 Created', method='PUT', size='123', project=None, account=None):
        def app(environ, respond):
            respond(status, [('X-Trans-Id', 'tx-one')])
            return [b'']
        middleware = SwiftMutationJournal(app, self.journal, [self.project])
        environ = {'REQUEST_METHOD': method, 'PATH_INFO': '/v1/AUTH_' + (account or self.project) + '/c/o',
            'HTTP_X_PROJECT_ID': project or self.project, 'HTTP_X_IDENTITY_STATUS': 'Confirmed', 'CONTENT_LENGTH': size}
        list(middleware(environ, lambda *args: None))

    def rows(self):
        with self.journal.connect() as db:
            return db.execute('SELECT project_id,method,size_bytes,known FROM mutations').fetchall()

    def test_only_authenticated_successful_own_account_changes_are_metered(self):
        self.request(status='403 Forbidden')
        self.request(account='b' * 32)
        self.assertEqual(self.rows(), [])
        self.request()
        self.assertEqual(self.rows(), [(self.project, 'PUT', 123, 1)])

    def test_authorized_cross_account_write_is_billed_to_owner_not_actor(self):
        self.request(project='b' * 32)
        self.assertEqual(self.rows(), [(self.project, 'PUT', 123, 1)])

    def test_unknown_copy_and_missing_size_are_explicitly_incomplete(self):
        self.request(method='COPY', size='')
        self.assertEqual(self.rows(), [(self.project, 'COPY', None, 0)])

    def test_duplicate_is_idempotent_but_conflict_and_history_changes_fail(self):
        values = dict(event_id='tx', project_id=self.project, object_key='e' * 64, method='PUT',
                      size_bytes=100, request_started_at='2026-10-28T00:00:00+00:00', known=True)
        self.journal.append(**values)
        self.journal.append(**values)
        self.assertEqual(len(self.rows()), 1)
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.journal.append(**dict(values, size_bytes=101))
        with self.journal.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM mutations')

    def test_deletion_zero_and_metadata_post_are_distinct(self):
        self.request(method='DELETE', status='204 No Content')
        self.assertEqual(self.rows(), [(self.project, 'DELETE', 0, 1)])

    def test_pending_and_denied_requests_have_explicit_coverage(self):
        request = self.journal.begin(self.project, 'd' * 64, 'PUT', '2026-10-28T00:00:00+00:00')
        with self.journal.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM requests r LEFT JOIN completions c '
                'USING(request_id) WHERE c.request_id IS NULL').fetchone()[0], 1)
        self.journal.finish(request, 403, None, True)
        with self.journal.connect() as db:
            self.assertEqual(db.execute('SELECT status,known FROM completions').fetchone(), (403, 1))

    def test_transaction_from_outer_swift_middleware_is_trusted(self):
        def app(environ, respond):
            respond('201 Created', [])
            return [b'']
        middleware = SwiftMutationJournal(app, self.journal, [self.project])
        list(middleware({'REQUEST_METHOD': 'PUT', 'PATH_INFO': '/v1/AUTH_' + self.project + '/c/o',
            'HTTP_X_PROJECT_ID': self.project, 'HTTP_X_IDENTITY_STATUS': 'Confirmed',
            'CONTENT_LENGTH': '10', 'swift.trans_id': 'server-transaction'}, lambda *args: None))
        self.assertEqual(self.rows(), [(self.project, 'PUT', 10, 1)])

    def test_server_error_is_an_uncertain_outcome_not_zero_usage(self):
        self.request(status='503 Service Unavailable')
        self.assertEqual(self.rows(), [])
        with self.journal.connect() as db:
            self.assertEqual(db.execute('SELECT status,known FROM completions').fetchone(), (503, 0))
