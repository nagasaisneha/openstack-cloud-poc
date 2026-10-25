"""Fixture-only project isolation tests for scoped catalog reads."""

import contextlib
import io
import unittest

from openstack_cloud_poc.adapter import AdapterError, FixtureManagementAdapter, ScopedCatalogResult
from openstack_cloud_poc.catalog import VerificationStatus
from openstack_cloud_poc.identity import AuthenticationResult, Identity


class ProjectIsolationTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FixtureManagementAdapter()

    def test_authorized_identity_reads_its_project_catalog(self):
        authentication = self.adapter.authenticate("fixture:alice")
        result = self.adapter.read_catalog(authentication, "fixture:project-alpha")

        self.assertTrue(result.authorization.allowed)
        self.assertEqual(result.status, "simulated")
        self.assertIsNotNone(result.catalog)
        self.assertTrue(result.catalog.records)
        self.assertTrue(
            all(record.status is VerificationStatus.SIMULATED for record in result.catalog.records)
        )

    def test_cross_project_read_is_denied_without_catalog_records(self):
        authentication = self.adapter.authenticate("fixture:alice")
        result = self.adapter.read_catalog(authentication, "fixture:project-beta")

        self.assertFalse(result.authorization.allowed)
        self.assertEqual(result.authorization.reason, "project_scope_mismatch")
        self.assertEqual(result.status, "simulated")
        self.assertIsNone(result.catalog)

    def test_unknown_identity_is_denied_without_catalog_records(self):
        authentication = self.adapter.authenticate("fixture:unknown-user-sentinel")
        result = self.adapter.read_catalog(authentication, "fixture:project-alpha")

        self.assertFalse(result.authorization.allowed)
        self.assertEqual(result.authorization.reason, "authentication_required")
        self.assertIsNone(result.catalog)
        self.assertNotIn("fixture:unknown-user-sentinel", repr(result))

    def test_identity_without_required_role_is_denied_without_data(self):
        no_catalog_role = AuthenticationResult(
            authenticated=True,
            identity=Identity(
                reference="fixture:test-no-catalog-role",
                project_id="fixture:project-alpha",
                roles=frozenset({"compute.operator"}),
            ),
            status="simulated",
        )
        result = self.adapter.read_catalog(no_catalog_role, "fixture:project-alpha")

        self.assertFalse(result.authorization.allowed)
        self.assertEqual(result.authorization.reason, "role_required")
        self.assertIsNone(result.catalog)
        self.assertEqual(result.status, "simulated")

    def test_unscoped_public_catalog_paths_are_not_exposed(self):
        self.assertFalse(hasattr(self.adapter, "get_catalog"))
        self.assertFalse(hasattr(self.adapter, "get_record"))

        result = self.adapter.request("catalog.list")
        self.assertIsInstance(result, AdapterError)
        self.assertEqual(result.code, "invalid_request")

    def test_denial_does_not_echo_sensitive_sentinel_or_write_it_to_output(self):
        sentinel = "sensitive-identity-sentinel"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            authentication = self.adapter.authenticate(sentinel)
            result = self.adapter.read_catalog(authentication, "fixture:project-alpha")

        self.assertFalse(result.authorization.allowed)
        self.assertIsNone(result.catalog)
        self.assertNotIn(sentinel, repr(result))
        self.assertNotIn(sentinel, stdout.getvalue())
        self.assertNotIn(sentinel, stderr.getvalue())

    def test_request_dispatch_keeps_authorization_and_denied_data_together(self):
        authentication = self.adapter.authenticate("fixture:alice")
        result = self.adapter.request(
            "catalog.list",
            {"authentication": authentication, "project_id": "fixture:project-beta"},
        )

        self.assertIsInstance(result, ScopedCatalogResult)
        self.assertFalse(result.authorization.allowed)
        self.assertIsNone(result.catalog)
        self.assertEqual(result.status, "simulated")


if __name__ == "__main__":
    unittest.main()
