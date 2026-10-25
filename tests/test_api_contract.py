"""Offline contract tests; no API server or provider is involved."""

import unittest

from openstack_cloud_poc.api_contract import (
    ContractError,
    ErrorCode,
    EvidenceMode,
    LifecycleAcceptedResponse,
    LifecycleRequest,
    LifecycleStatus,
    Operation,
    ProvisioningRequest,
    ProvisioningResponse,
)
from openstack_cloud_poc.identity import AuthorizationResult, FixtureIdentityProvider, authorize


def valid_payload():
    return {
        "operation": "compute.create",
        "project_id": "fixture:project-alpha",
        "image_id": "fixture:image.linux-example",
        "flavor_id": "fixture:flavor.small",
        "network_id": "fixture:network.private-demo",
    }


class ProvisioningContractTests(unittest.TestCase):
    def test_valid_request_requires_and_preserves_explicit_project_scope(self):
        request = ProvisioningRequest.from_mapping(valid_payload())

        self.assertEqual(request.operation, Operation.COMPUTE_CREATE)
        self.assertEqual(request.project_id, "fixture:project-alpha")
        self.assertEqual(request.to_wire(), valid_payload())

    def test_missing_required_fields_and_project_scope_have_safe_errors(self):
        payload = valid_payload()
        payload.pop("image_id")
        with self.assertRaises(ContractError) as missing_field:
            ProvisioningRequest.from_mapping(payload)
        self.assertEqual(missing_field.exception.code, ErrorCode.INVALID_REQUEST)

        payload = valid_payload()
        payload.pop("project_id")
        with self.assertRaises(ContractError) as missing_scope:
            ProvisioningRequest.from_mapping(payload)
        self.assertEqual(missing_scope.exception.code, ErrorCode.PROJECT_SCOPE_REQUIRED)

    def test_unsupported_operation_has_stable_error_without_echoing_input(self):
        payload = valid_payload()
        payload["operation"] = "nova.delete-with-sensitive-sentinel"
        with self.assertRaises(ContractError) as raised:
            ProvisioningRequest.from_mapping(payload)

        self.assertEqual(raised.exception.code, ErrorCode.UNSUPPORTED_OPERATION)
        self.assertNotIn("sensitive-sentinel", str(raised.exception))
        self.assertEqual(
            raised.exception.to_wire(),
            {"error": {"code": "unsupported_operation", "message": "Operation is not supported."}},
        )

    def test_unknown_fields_and_invalid_references_are_rejected(self):
        payload = valid_payload()
        payload["token"] = "do-not-echo-this"
        with self.assertRaises(ContractError) as extra:
            ProvisioningRequest.from_mapping(payload)
        self.assertEqual(extra.exception.code, ErrorCode.INVALID_REQUEST)
        self.assertNotIn("do-not-echo-this", str(extra.exception))

        payload = valid_payload()
        payload["flavor_id"] = " "
        with self.assertRaises(ContractError):
            ProvisioningRequest.from_mapping(payload)

        with self.assertRaises(ContractError):
            ProvisioningRequest(Operation.COMPUTE_CREATE, " ", "image", "flavor", "network")

    def test_accepted_means_validated_and_authorized_not_created_or_queued(self):
        request = ProvisioningRequest.from_mapping(valid_payload())
        authorization = authorize(
            FixtureIdentityProvider().authenticate("fixture:alice"),
            request.project_id,
            "compute.create",
        )
        response = ProvisioningResponse.accepted(
            request, authorization, source="fixture:T12 contract test"
        )
        wire = response.to_wire()

        self.assertEqual(response.status, LifecycleStatus.ACCEPTED)
        self.assertEqual(response.evidence_mode, EvidenceMode.SIMULATED)
        self.assertNotIn("resource_id", wire)
        self.assertNotIn("queued", wire)
        self.assertNotIn("identity", wire)

    def test_lifecycle_request_validates_operation_specific_fields(self):
        create = LifecycleRequest.from_mapping(valid_payload())
        self.assertEqual(create.operation, Operation.COMPUTE_CREATE)
        self.assertEqual(create.to_wire(), valid_payload())

        for operation in (
            Operation.COMPUTE_INSPECT,
            Operation.COMPUTE_START,
            Operation.COMPUTE_STOP,
            Operation.COMPUTE_DELETE,
        ):
            with self.subTest(operation=operation):
                payload = {
                    "operation": operation.value,
                    "project_id": "fixture:project-alpha",
                    "instance_id": "fixture:instance-001",
                }
                request = LifecycleRequest.from_mapping(payload)
                self.assertEqual(request.operation, operation)
                self.assertEqual(request.to_wire(), payload)

        legacy_payload = valid_payload()
        legacy_payload["operation"] = "compute.inspect"
        with self.assertRaises(ContractError) as legacy_type:
            ProvisioningRequest.from_mapping(legacy_payload)
        self.assertEqual(legacy_type.exception.code, ErrorCode.UNSUPPORTED_OPERATION)

    def test_lifecycle_request_rejects_missing_extra_or_mismatched_fields(self):
        with self.assertRaises(ContractError) as missing_instance:
            LifecycleRequest.from_mapping({
                "operation": "compute.stop",
                "project_id": "fixture:project-alpha",
            })
        self.assertEqual(missing_instance.exception.code, ErrorCode.INVALID_REQUEST)

        with self.assertRaises(ContractError) as unexpected_image:
            LifecycleRequest.from_mapping({
                "operation": "compute.delete",
                "project_id": "fixture:project-alpha",
                "instance_id": "fixture:instance-001",
                "image_id": "fixture:image-1",
            })
        self.assertEqual(unexpected_image.exception.code, ErrorCode.INVALID_REQUEST)

        with self.assertRaises(ContractError) as missing_project:
            LifecycleRequest.from_mapping({
                "operation": "compute.inspect",
                "instance_id": "fixture:instance-001",
            })
        self.assertEqual(missing_project.exception.code, ErrorCode.PROJECT_SCOPE_REQUIRED)

    def test_async_accepted_response_contains_job_id_not_instance_id(self):
        request = LifecycleRequest.from_mapping({
            "operation": "compute.create",
            "project_id": "fixture:project-alpha",
            "image_id": "fixture:image.linux-example",
            "flavor_id": "fixture:flavor.small",
            "network_id": "fixture:network.private-demo",
        })
        authorization = authorize(
            FixtureIdentityProvider().authenticate("fixture:alice"),
            request.project_id,
            request.operation.value,
        )
        response = LifecycleAcceptedResponse.accepted(
            request,
            authorization,
            job_id="fixture-job-001",
            source="fixture:lifecycle contract test",
        )
        wire = response.to_wire()

        self.assertEqual(wire["status"], "accepted")
        self.assertEqual(wire["job_id"], "fixture-job-001")
        self.assertEqual(wire["evidence_mode"], "simulated")
        self.assertNotIn("instance_id", wire)
        self.assertNotIn("resource_id", wire)

    def test_denied_lifecycle_authorization_cannot_produce_accepted_response(self):
        request = LifecycleRequest.from_mapping({
            "operation": "compute.delete",
            "project_id": "fixture:project-alpha",
            "instance_id": "fixture:instance-001",
        })
        authorization = authorize(
            FixtureIdentityProvider().authenticate("fixture:carol"),
            request.project_id,
            request.operation.value,
        )
        with self.assertRaises(ContractError) as denied:
            LifecycleAcceptedResponse.accepted(
                request,
                authorization,
                job_id="fixture-job-002",
                source="fixture:lifecycle contract test",
            )
        self.assertEqual(denied.exception.code, ErrorCode.UNAUTHORIZED_PROJECT)

    def test_denied_authorization_cannot_produce_accepted_response(self):
        request = ProvisioningRequest.from_mapping(valid_payload())
        denied = AuthorizationResult(False, "simulated", "project_scope_mismatch")

        with self.assertRaises(ContractError) as raised:
            ProvisioningResponse.accepted(request, denied, source="fixture:T12 test")
        self.assertEqual(raised.exception.code, ErrorCode.UNAUTHORIZED_PROJECT)
        self.assertNotIn("project_scope_mismatch", str(raised.exception))

    def test_simulated_authorization_cannot_be_labelled_real(self):
        request = ProvisioningRequest.from_mapping(valid_payload())
        authorization = authorize(
            FixtureIdentityProvider().authenticate("fixture:alice"),
            request.project_id,
            "compute.create",
        )
        with self.assertRaises(ContractError) as raised:
            ProvisioningResponse.accepted(
                request,
                authorization,
                source="fixture:T12 test",
                evidence_mode=EvidenceMode.REAL,
            )
        self.assertEqual(raised.exception.code, ErrorCode.UNAUTHORIZED_PROJECT)

    def test_completed_result_requires_resource_id_and_provider_evidence(self):
        request = ProvisioningRequest.from_mapping(valid_payload())
        with self.assertRaises(ContractError) as missing_evidence:
            ProvisioningResponse(
                operation=request.operation,
                project_id=request.project_id,
                status=LifecycleStatus.COMPLETED,
                evidence_mode=EvidenceMode.SIMULATED,
                source="test:synthetic",
                resource_id="synthetic:resource-1",
            )
        self.assertEqual(missing_evidence.exception.code, ErrorCode.PROVIDER_EVIDENCE_REQUIRED)

    def test_synthetic_completion_separates_lifecycle_and_evidence_mode(self):
        request = ProvisioningRequest.from_mapping(valid_payload())
        response = ProvisioningResponse.completed(
            request,
            resource_id="synthetic:resource-1",
            provider_evidence="synthetic test evidence; no cloud resource exists",
            source="test:synthetic provider fixture",
            evidence_mode=EvidenceMode.SIMULATED,
        )

        self.assertEqual(response.status, LifecycleStatus.COMPLETED)
        self.assertEqual(response.evidence_mode, EvidenceMode.SIMULATED)
        self.assertEqual(response.to_wire()["resource_id"], "synthetic:resource-1")
        self.assertIn("no cloud resource exists", response.to_wire()["provider_evidence"])

    def test_accepted_response_cannot_carry_resource_or_completion_evidence(self):
        request = ProvisioningRequest.from_mapping(valid_payload())
        with self.assertRaises(ContractError):
            ProvisioningResponse(
                operation=request.operation,
                project_id=request.project_id,
                status=LifecycleStatus.ACCEPTED,
                evidence_mode=EvidenceMode.SIMULATED,
                source="test:synthetic",
                resource_id="synthetic:resource-1",
            )

        with self.assertRaises(ContractError):
            ProvisioningResponse(
                operation=request.operation,
                project_id=request.project_id,
                status=LifecycleStatus.ACCEPTED,
                evidence_mode=EvidenceMode.SIMULATED,
                source="test:synthetic",
            )


if __name__ == "__main__":
    unittest.main()
