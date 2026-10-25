"""Fixture management services for synchronous validation and async lifecycle.

The legacy synchronous service remains validation-only. The asynchronous
service dispatches only to the in-memory fixture lifecycle provider and local
executor; neither path contacts a cloud provider or exposes an HTTP endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Mapping, Protocol

from .adapter import FixtureManagementAdapter
from .api_contract import (
    ContractError,
    ErrorCode,
    EvidenceMode,
    LifecycleAcceptedResponse,
    LifecycleRequest,
    ProvisioningRequest,
    ProvisioningResponse,
)
from .catalog import CatalogCategory
from .execution import ExecutionError, ExecutionErrorCode, ExecutionHandle, LocalExecutionEngine
from .identity import AuthenticationResult
from .jobs import FailureCategory, FixtureJobTracker, JobErrorCode, JobRecord, JobState, JobTrackerError


SOURCE = "fixture:T13 synchronous request validation; no resource operation"
ASYNC_SOURCE = "fixture:local asynchronous lifecycle; simulated only"


class ServiceErrorCode(str, Enum):
    VALIDATION_FAILURE = "validation_failure"
    AUTHORIZATION_DENIED = "authorization_denied"
    MISSING_FIXTURE_DATA = "missing_fixture_data"
    SERVICE_UNAVAILABLE = "service_unavailable"
    SERVICE_FAILURE = "service_failure"
    TIMEOUT = "timeout"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    CONFLICT = "conflict"


_ERROR_MESSAGES = {
    ServiceErrorCode.VALIDATION_FAILURE: "The request did not pass local contract validation.",
    ServiceErrorCode.AUTHORIZATION_DENIED: "Project authorization was denied.",
    ServiceErrorCode.MISSING_FIXTURE_DATA: "A referenced fixture record is missing; remote state is unverified.",
    ServiceErrorCode.SERVICE_UNAVAILABLE: "The fixture service is unavailable.",
    ServiceErrorCode.SERVICE_FAILURE: "The fixture service failed while validating the request.",
    ServiceErrorCode.TIMEOUT: "Fixture request validation timed out.",
    ServiceErrorCode.RESOURCE_UNAVAILABLE: "The fixture instance is unavailable for this operation.",
    ServiceErrorCode.CONFLICT: "A conflicting lifecycle request is already active or the idempotency key conflicts.",
}


@dataclass(frozen=True)
class ServiceError:
    """Safe, simulated diagnostic with no submitted values or exception text."""

    code: ServiceErrorCode
    message: str
    source: str = SOURCE
    status: EvidenceMode = EvidenceMode.SIMULATED
    diagnostic_category: str = "request"


@dataclass(frozen=True)
class ManagementServiceResult:
    """Exactly one accepted response or safe error; every result is simulated."""

    response: ProvisioningResponse | None = None
    error: ServiceError | None = None

    def __post_init__(self) -> None:
        if (self.response is None) == (self.error is None):
            raise ValueError("result must contain exactly one response or error")
        if self.response is not None and self.response.evidence_mode is not EvidenceMode.SIMULATED:
            raise ValueError("fixture service responses must be simulated")
        if self.error is not None and self.error.status is not EvidenceMode.SIMULATED:
            raise ValueError("fixture service errors must be simulated")


@dataclass(frozen=True)
class AsynchronousManagementResult:
    """Local result: accepted operation, synchronous inspection, or safe error.

    ``execution_handle`` is process-local coordination for callers/tests and is
    never part of the wire response. The API contract response contains only
    simulated metadata and the fixture job ID.
    """

    response: object | None = None
    job: JobRecord | None = None
    error: ServiceError | None = None
    execution_handle: ExecutionHandle | None = None

    def __post_init__(self) -> None:
        if sum((self.response is not None, self.job is not None, self.error is not None)) != 1:
            raise ValueError("result must contain exactly one response, job, or error")
        if self.error is not None and self.error.status is not EvidenceMode.SIMULATED:
            raise ValueError("fixture service errors must be simulated")
        if self.response is not None:
            evidence_mode = getattr(self.response, "evidence_mode", None)
            lifecycle_status = getattr(self.response, "status", None)
            if evidence_mode is not EvidenceMode.SIMULATED and lifecycle_status != "simulated":
                raise ValueError("fixture service responses must be simulated")


class FixtureRequestValidator(Protocol):
    """Offline fixture lookup only; implementations must never create resources."""

    def validate_references(self, request: ProvisioningRequest | LifecycleRequest) -> None:
        """Raise a safe typed error when fixture references are not present."""


class MissingFixtureRecord(Exception):
    """A request refers to illustrative fixture data that does not exist."""


class FixtureCatalogRequestValidator:
    """Validate references after the service has authorized the project request.

    The adapter's private fixture accessor is used only for this internal
    metadata check; it is not exposed as an unscoped management operation.
    """

    def __init__(self, adapter: FixtureManagementAdapter) -> None:
        self._adapter = adapter

    def validate_references(self, request: ProvisioningRequest | LifecycleRequest) -> None:
        catalog = self._adapter._get_catalog_fixture()
        references = (
            (CatalogCategory.IMAGE, request.image_id),
            (CatalogCategory.FLAVOR, request.flavor_id),
            (CatalogCategory.NETWORK, request.network_id),
        )
        if any(catalog.get(category, identifier) is None for category, identifier in references):
            raise MissingFixtureRecord


class SynchronousManagementService:
    """Validate, authorize, and fixture-check a request without provisioning."""

    def __init__(
        self,
        adapter: FixtureManagementAdapter | None = None,
        validator: FixtureRequestValidator | None = None,
    ) -> None:
        self._adapter = adapter or FixtureManagementAdapter()
        self._validator = validator or FixtureCatalogRequestValidator(self._adapter)

    def handle(
        self,
        payload: Mapping[str, object],
        authentication: AuthenticationResult,
    ) -> ManagementServiceResult:
        """Return simulated acceptance only after validation, scope, role, and fixture checks."""
        try:
            request = ProvisioningRequest.from_mapping(payload)
        except ContractError as exc:
            return self._error(
                ServiceErrorCode.VALIDATION_FAILURE,
                "validation",
                public_code=exc.code,
            )

        if not isinstance(authentication, AuthenticationResult):
            return self._error(ServiceErrorCode.AUTHORIZATION_DENIED, "authorization")
        authorization = self._adapter.authorize(
            authentication,
            request.project_id,
            request.operation.value,
        )
        if not authorization.allowed:
            return self._error(ServiceErrorCode.AUTHORIZATION_DENIED, "authorization")

        try:
            self._validator.validate_references(request)
        except MissingFixtureRecord:
            return self._error(ServiceErrorCode.MISSING_FIXTURE_DATA, "fixture_lookup")
        except PermissionError:
            return self._error(ServiceErrorCode.AUTHORIZATION_DENIED, "fixture_scope")
        except TimeoutError:
            return self._error(ServiceErrorCode.TIMEOUT, "fixture_validation")
        except ConnectionError:
            return self._error(ServiceErrorCode.SERVICE_UNAVAILABLE, "fixture_validation")
        except Exception:
            # Deliberately discard exception text; it may contain input values.
            return self._error(ServiceErrorCode.SERVICE_FAILURE, "fixture_validation")

        response = ProvisioningResponse.accepted(
            request,
            authorization,
            source=SOURCE,
            evidence_mode=EvidenceMode.SIMULATED,
        )
        return ManagementServiceResult(response=response)

    @staticmethod
    def _error(
        code: ServiceErrorCode,
        diagnostic_category: str,
        public_code: ErrorCode | None = None,
    ) -> ManagementServiceResult:
        # Keep the contract's validation categories diagnosable without
        # reflecting submitted values or exception messages.
        category = diagnostic_category
        if public_code is not None:
            category = f"{diagnostic_category}:{public_code.value}"
        return ManagementServiceResult(
            error=ServiceError(code, _ERROR_MESSAGES[code], diagnostic_category=category)
        )


class AsynchronousLifecycleManagementService:
    """Submit fixture VM mutations asynchronously; inspect remains synchronous.

    This service composes the existing fixture identity/catalog adapter, job
    tracker, local executor, and fixture instance provider. It is not an HTTP
    server and does not connect to Nova, Neutron, AWS, or another cloud.
    """

    def __init__(
        self,
        adapter: FixtureManagementAdapter | None = None,
        tracker: FixtureJobTracker | None = None,
        executor: LocalExecutionEngine | None = None,
        provider=None,
        validator: FixtureRequestValidator | None = None,
    ) -> None:
        # Local imports avoid the existing instance_lifecycle -> management_service
        # dependency becoming a module import cycle.
        from .instance_lifecycle import FixtureInstanceLifecycleService, FixtureInstanceProvider

        self._adapter = adapter or FixtureManagementAdapter()
        self._tracker = tracker or FixtureJobTracker()
        self._owns_executor = executor is None
        self._executor = executor or LocalExecutionEngine(self._tracker)
        if self._executor.tracker is not self._tracker:
            raise ValueError("executor and management service must share one job tracker")
        self._provider = provider or FixtureInstanceProvider()
        self._lifecycle = FixtureInstanceLifecycleService(self._adapter, self._provider)
        self._validator = validator or FixtureCatalogRequestValidator(self._adapter)
        # Serializes job registration through executor submission. This closes
        # the race where an idempotent duplicate could observe PENDING before
        # the first request had crossed the executor's submission boundary.
        self._submission_lock = RLock()

    @property
    def tracker(self) -> FixtureJobTracker:
        return self._tracker

    @property
    def provider(self):
        return self._provider

    def close(self) -> None:
        """Close only an engine owned by this service instance."""
        if self._owns_executor:
            self._executor.close()

    def __enter__(self) -> "AsynchronousLifecycleManagementService":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def handle(
        self,
        payload: Mapping[str, object],
        authentication: AuthenticationResult,
        idempotency_key: str | None = None,
    ) -> AsynchronousManagementResult:
        """Handle one simulated lifecycle request without exposing HTTP details."""
        try:
            request = LifecycleRequest.from_mapping(payload)
        except ContractError as exc:
            return self._error(ServiceErrorCode.VALIDATION_FAILURE, f"validation:{exc.code.value}")

        if not isinstance(authentication, AuthenticationResult):
            return self._error(ServiceErrorCode.AUTHORIZATION_DENIED, "authentication")
        try:
            authorization = self._adapter.authorize(
                authentication,
                request.project_id,
                request.operation.value,
            )
        except Exception:
            return self._error(ServiceErrorCode.AUTHORIZATION_DENIED, "authorization")
        if not authorization.allowed:
            return self._error(ServiceErrorCode.AUTHORIZATION_DENIED, "authorization")

        if request.operation.value == "compute.inspect":
            inspected = self._lifecycle.inspect(
                request.instance_id,
                request.project_id,
                authentication,
            )
            if inspected.error is not None:
                return self._map_inspection_error(inspected.error.code)
            return AsynchronousManagementResult(response=inspected)

        with self._submission_lock:
            try:
                duplicate = self._tracker.find_idempotent(request, authentication, idempotency_key)
            except JobTrackerError as exc:
                return self._map_job_error(exc.code)
            if duplicate is not None:
                return self._duplicate_result(request, authorization, duplicate.job)

            if request.operation.value == "compute.create":
                try:
                    self._validator.validate_references(request)
                except MissingFixtureRecord:
                    return self._error(ServiceErrorCode.MISSING_FIXTURE_DATA, "fixture_lookup")
                except TimeoutError:
                    return self._error(ServiceErrorCode.TIMEOUT, "fixture_validation")
                except ConnectionError:
                    return self._error(ServiceErrorCode.SERVICE_UNAVAILABLE, "fixture_validation")
                except Exception:
                    return self._error(ServiceErrorCode.SERVICE_FAILURE, "fixture_validation")
            else:
                preflight = self._preflight_instance(request, authentication)
                if preflight is not None:
                    return preflight

            try:
                registration = self._tracker.register(request, authentication, idempotency_key)
            except JobTrackerError as exc:
                return self._map_job_error(exc.code)

            if not registration.created:
                return self._duplicate_result(request, authorization, registration.job)

            try:
                handle = self._executor.submit(
                    registration.job.job_id,
                    self._operation_for(request),
                )
            except ExecutionError as exc:
                return self._map_execution_error(exc.code)

            # Do not construct/return accepted until the executor confirms that
            # the callable was submitted successfully.
            response = LifecycleAcceptedResponse.accepted(
                request,
                authorization,
                job_id=registration.job.job_id,
                source=ASYNC_SOURCE,
            )
            return AsynchronousManagementResult(response=response, execution_handle=handle)

    def _duplicate_result(self, request, authorization, job: JobRecord) -> AsynchronousManagementResult:
        # Executor rejection is not acceptance, even on an idempotent retry.
        # Preserve the failed job record for diagnosis.
        if job.state is JobState.FAILED:
            if job.failure_code is FailureCategory.EXECUTION_REJECTED:
                return self._error(ServiceErrorCode.SERVICE_UNAVAILABLE, "executor_rejected")
            if job.failure_code is FailureCategory.CONFLICT:
                return self._error(ServiceErrorCode.CONFLICT, "resource_busy")
        response = LifecycleAcceptedResponse.accepted(
            request,
            authorization,
            job_id=job.job_id,
            source=ASYNC_SOURCE,
        )
        return AsynchronousManagementResult(response=response)

    def get_job(
        self,
        job_id: str,
        project_id: str,
        authentication: AuthenticationResult,
    ) -> AsynchronousManagementResult:
        """Return job status only within the authenticated project scope."""
        with self._submission_lock:
            try:
                record = self._tracker.get(job_id, project_id, authentication)
            except JobTrackerError:
                # Not-found, unauthorized, and cross-project cases remain identical.
                return self._error(ServiceErrorCode.RESOURCE_UNAVAILABLE, "job_lookup")
        return AsynchronousManagementResult(job=record)

    def _preflight_instance(
        self,
        request: LifecycleRequest,
        authentication: AuthenticationResult,
    ) -> AsynchronousManagementResult | None:
        inspected = self._lifecycle.inspect(
            request.instance_id,
            request.project_id,
            authentication,
        )
        if inspected.error is not None:
            return self._map_inspection_error(inspected.error.code)
        if inspected.record is None or not inspected.record.provider_created:
            return self._error(ServiceErrorCode.RESOURCE_UNAVAILABLE, "fixture_instance_scope")
        return None

    def _operation_for(self, request: LifecycleRequest):
        from .instance_lifecycle import InstanceState
        from .execution import FixtureOperationFailure

        def operation(job):
            if job.project_id != request.project_id or job.operation != request.operation.value:
                raise FixtureOperationFailure(FailureCategory.AUTHORIZATION)
            if job.operation == "compute.create":
                record = self._provider.create(
                    job.project_id,
                    request.image_id,
                    request.flavor_id,
                    request.network_id,
                )
                return record.instance_id

            instance_id = job.instance_id
            if instance_id is None:
                raise FixtureOperationFailure(FailureCategory.VALIDATION)
            target = {
                "compute.start": InstanceState.ACTIVE,
                "compute.stop": InstanceState.STOPPED,
                "compute.delete": InstanceState.DELETED,
            }.get(job.operation)
            if target is None:
                raise FixtureOperationFailure(FailureCategory.VALIDATION)
            record = self._provider.transition(instance_id, target, job.project_id)
            return record.instance_id

        return operation

    def _map_inspection_error(self, lifecycle_code) -> AsynchronousManagementResult:
        code_value = getattr(lifecycle_code, "value", "")
        if code_value == "authorization_denied":
            return self._error(ServiceErrorCode.AUTHORIZATION_DENIED, "authorization")
        if code_value == "timeout":
            return self._error(ServiceErrorCode.TIMEOUT, "fixture_inspection")
        if code_value in {"invalid_fixture_id", "invalid_request"}:
            return self._error(ServiceErrorCode.VALIDATION_FAILURE, "instance_reference")
        return self._error(ServiceErrorCode.RESOURCE_UNAVAILABLE, "fixture_inspection")

    def _map_job_error(self, code: JobErrorCode) -> AsynchronousManagementResult:
        if code is JobErrorCode.IDEMPOTENCY_CONFLICT:
            return self._error(ServiceErrorCode.CONFLICT, "idempotency_conflict")
        if code is JobErrorCode.AUTHORIZATION_DENIED:
            return self._error(ServiceErrorCode.AUTHORIZATION_DENIED, "authorization")
        if code is JobErrorCode.INVALID_OPERATION:
            return self._error(ServiceErrorCode.VALIDATION_FAILURE, "operation")
        return self._error(ServiceErrorCode.SERVICE_FAILURE, "job_registration")

    def _map_execution_error(self, code: ExecutionErrorCode) -> AsynchronousManagementResult:
        if code is ExecutionErrorCode.RESOURCE_BUSY:
            return self._error(ServiceErrorCode.CONFLICT, "resource_busy")
        if code in {
            ExecutionErrorCode.CAPACITY_EXHAUSTED,
            ExecutionErrorCode.SHUTDOWN,
            ExecutionErrorCode.SUBMISSION_FAILED,
        }:
            return self._error(ServiceErrorCode.SERVICE_UNAVAILABLE, "executor_rejected")
        if code is ExecutionErrorCode.INVALID_JOB:
            return self._error(ServiceErrorCode.SERVICE_FAILURE, "job_submission")
        return self._error(ServiceErrorCode.SERVICE_FAILURE, "executor_failure")

    @staticmethod
    def _error(code: ServiceErrorCode, diagnostic_category: str) -> AsynchronousManagementResult:
        return AsynchronousManagementResult(
            error=ServiceError(
                code,
                _ERROR_MESSAGES[code],
                source=ASYNC_SOURCE,
                diagnostic_category=diagnostic_category,
            )
        )
