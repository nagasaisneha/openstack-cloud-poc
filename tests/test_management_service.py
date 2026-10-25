"""Offline tests for synchronous fixture request handling; no resource is created."""

import unittest

from openstack_cloud_poc.api_contract import EvidenceMode, LifecycleStatus
from openstack_cloud_poc.identity import FixtureIdentityProvider
from openstack_cloud_poc.management_service import (
    ManagementServiceResult,
    ServiceErrorCode,
    SynchronousManagementService,
)


def valid_payload():
    return {
        "operation": "compute.create",
        "project_id": "fixture:project-alpha",
        "image_id": "fixture:image.linux-example",
        "flavor_id": "fixture:flavor.small",
        "network_id": "fixture:network.private-demo",
    }


class RaisingValidator:
    def __init__(self, error):
        self.error = error

    def validate_references(self, request):
        raise self.error


class ManagementServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = SynchronousManagementService()
        self.identities = FixtureIdentityProvider()
        self.alice = self.identities.authenticate("fixture:alice")

    def test_success_is_simulated_acceptance_without_creation_claim(self):
        result = self.service.handle(valid_payload(), self.alice)

        self.assertIsInstance(result, ManagementServiceResult)
        self.assertIsNotNone(result.response)
        self.assertIsNone(result.error)
        self.assertEqual(result.response.status, LifecycleStatus.ACCEPTED)
        self.assertEqual(result.response.evidence_mode, EvidenceMode.SIMULATED)
        self.assertIn("fixture:", result.response.source)
        self.assertNotIn("resource_id", result.response.to_wire())
        self.assertNotIn("provider_evidence", result.response.to_wire())

    def test_invalid_request_has_safe_validation_error(self):
        payload = valid_payload()
        payload["image_id"] = "sensitive-image-sentinel"
        payload.pop("flavor_id")
        result = self.service.handle(payload, self.alice)

        self.assertEqual(result.error.code, ServiceErrorCode.VALIDATION_FAILURE)
        self.assertEqual(result.error.status, EvidenceMode.SIMULATED)
        self.assertNotIn("sensitive-image-sentinel", repr(result))

    def test_unknown_identity_and_cross_project_requests_are_denied(self):
        unknown = self.identities.authenticate("unknown-identity-sentinel")
        result = self.service.handle(valid_payload(), unknown)
        self.assertEqual(result.error.code, ServiceErrorCode.AUTHORIZATION_DENIED)
        self.assertNotIn("unknown-identity-sentinel", repr(result))

        payload = valid_payload()
        payload["project_id"] = "fixture:project-beta"
        result = self.service.handle(payload, self.alice)
        self.assertEqual(result.error.code, ServiceErrorCode.AUTHORIZATION_DENIED)
        self.assertIsNone(result.response)

    def test_missing_required_role_is_denied(self):
        bob = self.identities.authenticate("fixture:bob")
        result = self.service.handle(valid_payload(), bob)
        self.assertEqual(result.error.code, ServiceErrorCode.AUTHORIZATION_DENIED)
        self.assertEqual(result.error.diagnostic_category, "authorization")

    def test_missing_fixture_reference_is_not_remote_unavailability(self):
        payload = valid_payload()
        payload["image_id"] = "fixture:image-missing-sentinel"
        result = self.service.handle(payload, self.alice)

        self.assertEqual(result.error.code, ServiceErrorCode.MISSING_FIXTURE_DATA)
        self.assertIn("remote state is unverified", result.error.message)
        self.assertNotIn("fixture:image-missing-sentinel", repr(result))

    def test_injected_service_unavailable_and_timeout_are_mapped(self):
        unavailable = SynchronousManagementService(
            validator=RaisingValidator(ConnectionError("secret connection detail"))
        ).handle(valid_payload(), self.alice)
        self.assertEqual(unavailable.error.code, ServiceErrorCode.SERVICE_UNAVAILABLE)
        self.assertNotIn("secret connection detail", repr(unavailable))

        timed_out = SynchronousManagementService(
            validator=RaisingValidator(TimeoutError("timeout sensitive detail"))
        ).handle(valid_payload(), self.alice)
        self.assertEqual(timed_out.error.code, ServiceErrorCode.TIMEOUT)
        self.assertNotIn("timeout sensitive detail", repr(timed_out))

    def test_injected_provider_error_is_sanitized_but_diagnosable(self):
        failure = RuntimeError("provider error secret-sentinel")
        result = SynchronousManagementService(
            validator=RaisingValidator(failure)
        ).handle(valid_payload(), self.alice)

        self.assertEqual(result.error.code, ServiceErrorCode.SERVICE_FAILURE)
        self.assertEqual(result.error.diagnostic_category, "fixture_validation")
        self.assertNotIn("secret-sentinel", repr(result))

    def test_all_outcomes_are_simulated_and_never_completed(self):
        outcomes = [
            self.service.handle(valid_payload(), self.alice),
            self.service.handle({}, self.alice),
            SynchronousManagementService(
                validator=RaisingValidator(RuntimeError("safe"))
            ).handle(valid_payload(), self.alice),
        ]
        for outcome in outcomes:
            if outcome.response is not None:
                self.assertEqual(outcome.response.evidence_mode, EvidenceMode.SIMULATED)
                self.assertNotEqual(outcome.response.status, LifecycleStatus.COMPLETED)
            else:
                self.assertEqual(outcome.error.status, EvidenceMode.SIMULATED)


if __name__ == "__main__":
    unittest.main()
