"""Offline tests for the simulated identity and authorization fixture."""

import socket
import unittest
from unittest.mock import patch

from openstack_cloud_poc.identity import (
    SIMULATED,
    FixtureIdentityProvider,
    authorize,
)


class FixtureIdentityTests(unittest.TestCase):
    def setUp(self):
        self.provider = FixtureIdentityProvider()

    def test_known_identity_returns_simulated_project_and_roles(self):
        result = self.provider.authenticate("fixture:alice")

        self.assertTrue(result.authenticated)
        self.assertEqual(result.status, SIMULATED)
        self.assertEqual(result.identity.project_id, "fixture:project-alpha")
        self.assertEqual(result.identity.roles, {"catalog.viewer", "compute.operator"})

    def test_unknown_identity_fails_without_echoing_input(self):
        unknown_reference = "fixture:unknown-value"
        result = self.provider.authenticate(unknown_reference)

        self.assertFalse(result.authenticated)
        self.assertIsNone(result.identity)
        self.assertEqual(result.status, SIMULATED)
        self.assertEqual(result.error, "identity_not_found")
        self.assertNotIn(unknown_reference, repr(result))

    def test_required_role_allows_explicit_operation(self):
        result = authorize(
            self.provider.authenticate("fixture:alice"),
            "fixture:project-alpha",
            "compute.create",
        )

        self.assertTrue(result.allowed)
        self.assertEqual(result.status, SIMULATED)
        self.assertEqual(result.reason, "authorized_by_fixture_policy")

    def test_compute_operator_can_submit_each_lifecycle_operation(self):
        authentication = self.provider.authenticate("fixture:alice")
        for operation in (
            "compute.create",
            "compute.inspect",
            "compute.start",
            "compute.stop",
            "compute.delete",
        ):
            with self.subTest(operation=operation):
                result = authorize(authentication, "fixture:project-alpha", operation)
                self.assertTrue(result.allowed)
                self.assertEqual(result.status, SIMULATED)

    def test_compute_viewer_can_inspect_but_cannot_mutate(self):
        authentication = self.provider.authenticate("fixture:carol")

        inspect = authorize(authentication, "fixture:project-alpha", "compute.inspect")
        self.assertTrue(inspect.allowed)

        for operation in ("compute.create", "compute.start", "compute.stop", "compute.delete"):
            with self.subTest(operation=operation):
                result = authorize(authentication, "fixture:project-alpha", operation)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, "role_required")

    def test_each_lifecycle_operation_denies_cross_project_access(self):
        authentication = self.provider.authenticate("fixture:alice")
        for operation in (
            "compute.create",
            "compute.inspect",
            "compute.start",
            "compute.stop",
            "compute.delete",
        ):
            with self.subTest(operation=operation):
                result = authorize(authentication, "fixture:project-beta", operation)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason, "project_scope_mismatch")

    def test_missing_role_denies_operation(self):
        result = authorize(
            self.provider.authenticate("fixture:bob"),
            "fixture:project-alpha",
            "compute.create",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.status, SIMULATED)
        self.assertEqual(result.reason, "role_required")

    def test_cross_project_access_is_denied(self):
        result = authorize(
            self.provider.authenticate("fixture:alice"),
            "fixture:project-beta",
            "catalog.read",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.status, SIMULATED)
        self.assertEqual(result.reason, "project_scope_mismatch")

    def test_unknown_operation_is_denied(self):
        result = authorize(
            self.provider.authenticate("fixture:alice"),
            "fixture:project-alpha",
            "admin.delete_everything",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "operation_not_allowed")

    def test_unknown_identity_cannot_be_authorized(self):
        result = authorize(
            self.provider.authenticate("fixture:unknown-value"),
            "fixture:project-alpha",
            "catalog.read",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "authentication_required")

    def test_fixture_flow_makes_no_socket_connection(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError("network call")) as connect:
            result = self.provider.authenticate("fixture:alice")
            decision = authorize(result, "fixture:project-alpha", "catalog.read")

        self.assertTrue(decision.allowed)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
