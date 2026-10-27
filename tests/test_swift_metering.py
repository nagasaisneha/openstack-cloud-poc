import unittest
from unittest.mock import Mock
from openstack_cloud_poc.swift_metering import CollectorAccountHeadOnly

PROJECT = 'a' * 32


class CollectorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock(return_value=[b'provider'])
        self.middleware = CollectorAccountHeadOnly(self.app)
        self.start = Mock()

    def environment(self, **changes):
        return dict({'HTTP_X_IDENTITY_STATUS': 'Confirmed', 'HTTP_X_PROJECT_ID': PROJECT,
                     'HTTP_X_ROLES': 'reader,billing_collector', 'REQUEST_METHOD': 'HEAD',
                     'PATH_INFO': '/v1/AUTH_' + PROJECT}, **changes)

    def test_authenticated_collector_can_only_head_its_own_account(self):
        env = self.environment()
        self.assertEqual(self.middleware(env, self.start), [b'provider'])
        self.app.assert_called_once_with(env, self.start)

    def test_account_head_canonical_trailing_slash_is_supported(self):
        self.middleware(self.environment(PATH_INFO='/v1/AUTH_' + PROJECT + '/'), self.start)
        self.app.assert_called_once()

    def test_mutations_and_object_content_reads_are_denied_before_swift(self):
        for method in ('GET', 'PUT', 'POST', 'DELETE', 'COPY'):
            with self.subTest(method=method):
                self.assertEqual(self.middleware(self.environment(REQUEST_METHOD=method), self.start), [b''])
                self.assertEqual(self.start.call_args.args[0], '403 Forbidden')
        self.app.assert_not_called()

    def test_foreign_account_object_and_encoded_paths_are_denied(self):
        for path in ('/v1/AUTH_' + 'b' * 32, '/v1/AUTH_' + PROJECT + '/container',
                     '/v1/AUTH_' + PROJECT + '//', '/v1/AUTH_%61' + 'a' * 31):
            with self.subTest(path=path):
                self.middleware(self.environment(PATH_INFO=path), self.start)
                self.assertEqual(self.start.call_args.args[0], '403 Forbidden')
        self.app.assert_not_called()

    def test_unconfirmed_identity_cannot_use_the_collector_role(self):
        self.middleware(self.environment(HTTP_X_IDENTITY_STATUS='Invalid'), self.start)
        self.app.assert_not_called()
        self.assertEqual(self.start.call_args.args[0], '403 Forbidden')

    def test_normal_tenant_authorization_remains_with_swift(self):
        env = self.environment(HTTP_X_ROLES='member,billing_viewer', REQUEST_METHOD='PUT')
        self.middleware(env, self.start)
        self.app.assert_called_once_with(env, self.start)


if __name__ == '__main__':
    unittest.main()
