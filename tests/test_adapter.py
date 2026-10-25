"""Offline tests for the fixture-backed management adapter."""

import socket
import sys
import unittest
from unittest.mock import patch

from openstack_cloud_poc.adapter import AdapterError, FixtureManagementAdapter
from openstack_cloud_poc.catalog import CatalogCategory, VerificationStatus
from openstack_cloud_poc.catalog_fixtures import FixtureCatalogProvider
from openstack_cloud_poc.identity import FixtureIdentityProvider, SIMULATED


class FixtureManagementAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FixtureManagementAdapter()

    def test_adapter_composes_existing_identity_and_catalog_providers(self):
        authentication = self.adapter.authenticate("fixture:alice")
        scoped = self.adapter.read_catalog(authentication, "fixture:project-alpha")
        self.assertEqual(
            scoped.catalog,
            FixtureCatalogProvider().get_catalog(),
        )
        self.assertEqual(
            self.adapter.authenticate("fixture:alice"),
            FixtureIdentityProvider().authenticate("fixture:alice"),
        )

    def test_repeated_catalog_requests_are_stable(self):
        authentication = self.adapter.authenticate("fixture:alice")
        self.assertEqual(
            self.adapter.read_catalog(authentication, "fixture:project-alpha"),
            self.adapter.read_catalog(authentication, "fixture:project-alpha"),
        )
        self.assertEqual(
            self.adapter.request(
                "catalog.list",
                {"authentication": authentication, "project_id": "fixture:project-alpha"},
            ),
            self.adapter.request(
                "catalog.list",
                {"authentication": authentication, "project_id": "fixture:project-alpha"},
            ),
        )

    def test_identity_scope_and_role_policy_are_preserved(self):
        alice = self.adapter.authenticate("fixture:alice")
        bob = self.adapter.authenticate("fixture:bob")

        allowed = self.adapter.authorize(alice, "fixture:project-alpha", "compute.create")
        cross_project = self.adapter.authorize(alice, "fixture:project-beta", "catalog.read")
        missing_role = self.adapter.authorize(bob, "fixture:project-alpha", "compute.create")

        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.status, SIMULATED)
        self.assertFalse(cross_project.allowed)
        self.assertFalse(missing_role.allowed)
        self.assertEqual(missing_role.reason, "role_required")

    def test_catalog_metadata_units_provenance_and_status_are_preserved(self):
        authentication = self.adapter.authenticate("fixture:alice")
        catalog = self.adapter.read_catalog(authentication, "fixture:project-alpha").catalog
        flavor = catalog.get(CatalogCategory.FLAVOR, "fixture:flavor.small")

        self.assertEqual(flavor.identifier, "fixture:flavor.small")
        self.assertTrue(flavor.source.startswith("fixture:"))
        self.assertEqual(flavor.status, VerificationStatus.SIMULATED)
        self.assertEqual(flavor.attributes["vcpus"], 2)
        self.assertEqual(flavor.attributes["memory_mib"], 2048)

    def test_missing_record_returns_simulated_error_not_service_unavailable(self):
        missing_id = "private-or-unknown-record-id"
        authentication = self.adapter.authenticate("fixture:alice")
        result = self.adapter.request(
            "catalog.get",
            {
                "authentication": authentication,
                "project_id": "fixture:project-alpha",
                "category": CatalogCategory.IMAGE,
                "identifier": missing_id,
            },
        )

        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "fixture_record_missing")
        self.assertEqual(result.status, SIMULATED)
        self.assertIn("remote availability is unverified", result.error.message)
        self.assertNotIn(missing_id, repr(result))
        self.assertNotIn("unavailable", result.error.message.lower())

    def test_unsupported_operation_returns_safe_simulated_error(self):
        requested = "nova.create"
        result = self.adapter.request(requested, {"image": "sensitive-request-value"})

        self.assertIsInstance(result, AdapterError)
        self.assertEqual(result.code, "unsupported_operation")
        self.assertEqual(result.status, SIMULATED)
        self.assertNotIn(requested, result.message)
        self.assertNotIn("sensitive-request-value", repr(result))

    def test_invalid_request_does_not_echo_parameters(self):
        sensitive_value = "do-not-echo-this"
        result = self.adapter.request(
            "catalog.get",
            {"category": "not-an-enum", "identifier": sensitive_value},
        )

        self.assertIsInstance(result, AdapterError)
        self.assertEqual(result.code, "invalid_request")
        self.assertNotIn(sensitive_value, repr(result))

    def test_supported_identity_and_catalog_dispatch(self):
        result = self.adapter.request("identity.authenticate", {"identity_reference": "fixture:alice"})
        authentication = result
        catalog = self.adapter.request(
            "catalog.get",
            {
                "authentication": authentication,
                "project_id": "fixture:project-alpha",
                "category": CatalogCategory.NETWORK,
                "identifier": "fixture:network.private-demo",
            },
        )

        self.assertEqual(result.status, SIMULATED)
        self.assertEqual(catalog.status, SIMULATED)
        self.assertEqual(catalog.catalog.records[0].status, VerificationStatus.SIMULATED)

    def test_fixture_adapter_uses_no_network_and_imports_no_cloud_clients(self):
        cloud_modules = ("openstack", "novaclient", "keystoneauth1", "proxmox")
        with patch.object(socket, "create_connection", side_effect=AssertionError("network call")) as connect:
            authentication = self.adapter.authenticate("fixture:alice")
            self.adapter.read_catalog(authentication, "fixture:project-alpha")
            self.adapter.authenticate("fixture:alice")

        connect.assert_not_called()
        for module_name in cloud_modules:
            self.assertNotIn(module_name, sys.modules)


if __name__ == "__main__":
    unittest.main()
