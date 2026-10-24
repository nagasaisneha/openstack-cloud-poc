"""Framework-independent provisioning API schemas and local validation.

These types define a contract only. They do not route requests, authorize a
cloud operation, contact a provider, queue work, or create resources.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from .identity import AuthorizationResult


class Operation(str, Enum):
    COMPUTE_CREATE = "compute.create"
    COMPUTE_INSPECT = "compute.inspect"
    COMPUTE_START = "compute.start"
    COMPUTE_STOP = "compute.stop"
    COMPUTE_DELETE = "compute.delete"


class LifecycleStatus(str, Enum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class EvidenceMode(str, Enum):
    SIMULATED = "simulated"
    REAL = "real"


class ErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    PROJECT_SCOPE_REQUIRED = "project_scope_required"
    UNAUTHORIZED_PROJECT = "unauthorized_project"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_EVIDENCE_REQUIRED = "provider_evidence_required"


class ContractError(ValueError):
    """Safe validation/contract error with a stable public error code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def to_wire(self) -> dict[str, object]:
        """Return an error body that contains no request values or secrets."""
        return {"error": {"code": self.code.value, "message": self.message}}


@dataclass(frozen=True)
class ProvisioningRequest:
    """Minimal explicitly scoped compute-create request."""

    operation: Operation
    project_id: str
    image_id: str
    flavor_id: str
    network_id: str

    def __post_init__(self) -> None:
        if self.operation is not Operation.COMPUTE_CREATE:
            raise ContractError(ErrorCode.UNSUPPORTED_OPERATION, "Operation is not supported.")
        for field_name in ("project_id", "image_id", "flavor_id", "network_id"):
            _require_text(field_name, getattr(self, field_name))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "ProvisioningRequest":
        if not isinstance(payload, Mapping):
            raise ContractError(ErrorCode.INVALID_REQUEST, "Request body must be an object.")

        if "project_id" not in payload:
            raise ContractError(ErrorCode.PROJECT_SCOPE_REQUIRED, "An explicit project scope is required.")
        required = {"operation", "project_id", "image_id", "flavor_id", "network_id"}
        if set(payload) != required:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Request fields are missing or unsupported.")

        operation_value = payload["operation"]
        if not isinstance(operation_value, str):
            raise ContractError(ErrorCode.INVALID_REQUEST, "Operation must be a supported name.")
        try:
            operation = Operation(operation_value)
        except ValueError:
            raise ContractError(ErrorCode.UNSUPPORTED_OPERATION, "Operation is not supported.") from None

        values = {
            "project_id": payload["project_id"],
            "image_id": payload["image_id"],
            "flavor_id": payload["flavor_id"],
            "network_id": payload["network_id"],
        }
        if any(not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value for value in values.values()):
            raise ContractError(ErrorCode.INVALID_REQUEST, "Project and resource references must be non-empty single-line strings.")

        return cls(operation=operation, **values)

    def to_wire(self) -> dict[str, str]:
        """Serialize only the declared public request fields."""
        return {
            "operation": self.operation.value,
            "project_id": self.project_id,
            "image_id": self.image_id,
            "flavor_id": self.flavor_id,
            "network_id": self.network_id,
        }


@dataclass(frozen=True)
class LifecycleRequest:
    """Operation-specific request shape for the fixture lifecycle contract.

    This validates request fields only. It neither looks up an instance nor
    dispatches lifecycle work to a provider or executor.
    """

    operation: Operation
    project_id: str
    image_id: str | None = None
    flavor_id: str | None = None
    network_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, Operation):
            raise ContractError(ErrorCode.UNSUPPORTED_OPERATION, "Operation is not supported.")
        _require_text("project_id", self.project_id)
        if self.operation is Operation.COMPUTE_CREATE:
            for field_name in ("image_id", "flavor_id", "network_id"):
                _require_text(field_name, getattr(self, field_name))
            if self.instance_id is not None:
                raise ContractError(ErrorCode.INVALID_REQUEST, "Request fields do not match the operation.")
        else:
            _require_text("instance_id", self.instance_id)
            if any(value is not None for value in (self.image_id, self.flavor_id, self.network_id)):
                raise ContractError(ErrorCode.INVALID_REQUEST, "Request fields do not match the operation.")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "LifecycleRequest":
        if not isinstance(payload, Mapping):
            raise ContractError(ErrorCode.INVALID_REQUEST, "Request body must be an object.")
        if "project_id" not in payload:
            raise ContractError(ErrorCode.PROJECT_SCOPE_REQUIRED, "An explicit project scope is required.")

        operation_value = payload.get("operation")
        if not isinstance(operation_value, str):
            raise ContractError(ErrorCode.INVALID_REQUEST, "Operation must be a supported name.")
        try:
            operation = Operation(operation_value)
        except ValueError:
            raise ContractError(ErrorCode.UNSUPPORTED_OPERATION, "Operation is not supported.") from None

        fields = (
            {"image_id", "flavor_id", "network_id"}
            if operation is Operation.COMPUTE_CREATE
            else {"instance_id"}
        )
        required = {"operation", "project_id", *fields}
        if set(payload) != required:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Request fields are missing or unsupported.")
        values = {field_name: payload[field_name] for field_name in fields}
        if any(
            not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value
            for value in (payload["project_id"], *values.values())
        ):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Project and resource references must be non-empty single-line strings.",
            )
        return cls(operation=operation, project_id=payload["project_id"], **values)

    def to_wire(self) -> dict[str, str]:
        result = {"operation": self.operation.value, "project_id": self.project_id}
        for field_name in ("image_id", "flavor_id", "network_id", "instance_id"):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = value
        return result


@dataclass(frozen=True)
class LifecycleAcceptedResponse:
    """Asynchronous acceptance contract; job ID is not an instance ID.

    Creating this value does not execute or enqueue work. Its caller must only
    return it after an execution mechanism has registered the operation.
    """

    operation: Operation
    project_id: str
    job_id: str
    source: str
    evidence_mode: EvidenceMode = EvidenceMode.SIMULATED
    status: LifecycleStatus = LifecycleStatus.ACCEPTED
    _authorization_passed: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.operation, Operation):
            raise ContractError(ErrorCode.UNSUPPORTED_OPERATION, "Operation is not supported.")
        if not isinstance(self.status, LifecycleStatus) or self.status is not LifecycleStatus.ACCEPTED:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Lifecycle submission status must be accepted.")
        if not isinstance(self.evidence_mode, EvidenceMode):
            raise ContractError(ErrorCode.INVALID_REQUEST, "Evidence mode is invalid.")
        _require_text("project_id", self.project_id)
        _require_text("job_id", self.job_id)
        _require_text("source", self.source)
        if not self._authorization_passed:
            raise ContractError(ErrorCode.UNAUTHORIZED_PROJECT, "Accepted status requires authorization.")

    @classmethod
    def accepted(
        cls,
        request: LifecycleRequest,
        authorization: AuthorizationResult,
        job_id: str,
        source: str,
    ) -> "LifecycleAcceptedResponse":
        if not isinstance(authorization, AuthorizationResult) or not authorization.allowed:
            raise ContractError(ErrorCode.UNAUTHORIZED_PROJECT, "Project authorization was denied.")
        return cls(
            operation=request.operation,
            project_id=request.project_id,
            job_id=job_id,
            source=source,
            evidence_mode=EvidenceMode.SIMULATED,
            _authorization_passed=True,
        )

    def to_wire(self) -> dict[str, str]:
        return {
            "operation": self.operation.value,
            "project_id": self.project_id,
            "status": self.status.value,
            "job_id": self.job_id,
            "evidence_mode": self.evidence_mode.value,
            "source": self.source,
        }


@dataclass(frozen=True)
class ProvisioningResponse:
    """Contract response; lifecycle state is independent of evidence mode."""

    operation: Operation
    project_id: str
    status: LifecycleStatus
    evidence_mode: EvidenceMode
    source: str
    resource_id: str | None = None
    provider_evidence: str | None = None
    _authorization_passed: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.operation, Operation):
            raise ContractError(ErrorCode.UNSUPPORTED_OPERATION, "Operation is not supported.")
        if not isinstance(self.status, LifecycleStatus):
            raise ContractError(ErrorCode.INVALID_REQUEST, "Lifecycle status is invalid.")
        if not isinstance(self.evidence_mode, EvidenceMode):
            raise ContractError(ErrorCode.INVALID_REQUEST, "Evidence mode is invalid.")
        _require_text("project_id", self.project_id)
        _require_text("source", self.source)
        if self.resource_id is not None:
            _require_text("resource_id", self.resource_id)
        if self.provider_evidence is not None:
            _require_text("provider_evidence", self.provider_evidence)

        if self.status is LifecycleStatus.ACCEPTED:
            if not self._authorization_passed:
                raise ContractError(
                    ErrorCode.UNAUTHORIZED_PROJECT,
                    "Accepted status requires an authorization result.",
                )
            if self.resource_id is not None or self.provider_evidence is not None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "An accepted request cannot claim resource creation.",
                )
        elif self.status is LifecycleStatus.COMPLETED:
            if self.resource_id is None or self.provider_evidence is None:
                raise ContractError(
                    ErrorCode.PROVIDER_EVIDENCE_REQUIRED,
                    "Completion requires a resource reference and provider-supplied evidence.",
                )
        elif self.resource_id is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Rejected or failed requests cannot claim a created resource.",
            )

    @classmethod
    def accepted(
        cls,
        request: ProvisioningRequest,
        authorization: AuthorizationResult,
        source: str,
        evidence_mode: EvidenceMode = EvidenceMode.SIMULATED,
    ) -> "ProvisioningResponse":
        """Build an accepted response only after an allowed authorization result."""
        if not isinstance(authorization, AuthorizationResult):
            raise ContractError(ErrorCode.UNAUTHORIZED_PROJECT, "Project authorization was not supplied.")
        if not authorization.allowed:
            raise ContractError(ErrorCode.UNAUTHORIZED_PROJECT, "Project authorization was denied.")
        if evidence_mode is EvidenceMode.REAL and authorization.status == "simulated":
            raise ContractError(
                ErrorCode.UNAUTHORIZED_PROJECT,
                "Simulated authorization cannot be represented as real evidence.",
            )
        return cls(
            operation=request.operation,
            project_id=request.project_id,
            status=LifecycleStatus.ACCEPTED,
            evidence_mode=evidence_mode,
            source=source,
            _authorization_passed=True,
        )

    @classmethod
    def completed(
        cls,
        request: ProvisioningRequest,
        resource_id: str,
        provider_evidence: str,
        source: str,
        evidence_mode: EvidenceMode = EvidenceMode.SIMULATED,
    ) -> "ProvisioningResponse":
        """Build a completion representation only from supplied provider evidence."""
        return cls(
            operation=request.operation,
            project_id=request.project_id,
            status=LifecycleStatus.COMPLETED,
            evidence_mode=evidence_mode,
            source=source,
            resource_id=resource_id,
            provider_evidence=provider_evidence,
        )

    def to_wire(self) -> dict[str, str]:
        """Serialize lifecycle and evidence labels as distinct fields."""
        result = {
            "operation": self.operation.value,
            "project_id": self.project_id,
            "status": self.status.value,
            "evidence_mode": self.evidence_mode.value,
            "source": self.source,
        }
        if self.resource_id is not None:
            result["resource_id"] = self.resource_id
        if self.provider_evidence is not None:
            result["provider_evidence"] = self.provider_evidence
        return result


def _require_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a non-empty single-line string.")
