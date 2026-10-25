"""Thread-safe, process-local records for simulated lifecycle operations.

The tracker registers and inspects jobs; it does not queue or execute work,
persist records, or claim that a cloud resource was created. State transitions
are exposed only through the module-internal execution path used by a future
worker integration.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from threading import RLock
from typing import Callable
from uuid import uuid4

from .api_contract import LifecycleRequest, Operation
from .identity import AuthenticationResult, authorize


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FailureCategory(str, Enum):
    VALIDATION = "validation_error"
    AUTHORIZATION = "authorization_denied"
    CONFLICT = "conflict"
    EXECUTION_REJECTED = "execution_rejected"
    TIMEOUT = "timeout"
    SERVICE_UNAVAILABLE = "service_unavailable"
    PROVIDER = "provider_error"
    UNKNOWN = "unknown_error"


class JobErrorCode(str, Enum):
    INVALID_OPERATION = "invalid_operation"
    INVALID_JOB_ID = "invalid_job_id"
    NOT_FOUND = "job_not_found"
    INVALID_TRANSITION = "invalid_transition"
    INVALID_FAILURE_CODE = "invalid_failure_code"
    INVALID_CLOCK = "invalid_clock"
    DUPLICATE_ID = "duplicate_job_id"
    AUTHORIZATION_DENIED = "authorization_denied"
    INVALID_REQUEST = "invalid_request"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"


_ERROR_MESSAGES = {
    JobErrorCode.INVALID_OPERATION: "The operation is not supported by the simulated job tracker.",
    JobErrorCode.INVALID_JOB_ID: "The synthetic job identifier is invalid.",
    JobErrorCode.NOT_FOUND: "No matching simulated job exists.",
    JobErrorCode.INVALID_TRANSITION: "The requested job state transition is not allowed.",
    JobErrorCode.INVALID_FAILURE_CODE: "A supported failure code is required for failed jobs.",
    JobErrorCode.INVALID_CLOCK: "The job clock returned an invalid or earlier timestamp.",
    JobErrorCode.DUPLICATE_ID: "The synthetic job identifier is already in use.",
    JobErrorCode.AUTHORIZATION_DENIED: "Project authorization was denied.",
    JobErrorCode.INVALID_REQUEST: "The job registration request is invalid.",
    JobErrorCode.IDEMPOTENCY_CONFLICT: "The idempotency key was used with a different request.",
}

_TRACKABLE_OPERATIONS = frozenset({
    Operation.COMPUTE_CREATE.value,
    Operation.COMPUTE_START.value,
    Operation.COMPUTE_STOP.value,
    Operation.COMPUTE_DELETE.value,
})


class JobTrackerError(ValueError):
    """Stable, safe error that never includes identifiers or submitted values."""

    def __init__(self, code: JobErrorCode) -> None:
        self.code = code
        self.message = _ERROR_MESSAGES[code]
        super().__init__(self.message)


@dataclass(frozen=True)
class JobRecord:
    """Immutable simulated job snapshot with an optional fixture instance ID."""

    job_id: str
    project_id: str
    actor_id: str
    operation: str
    state: JobState
    created_at: datetime
    updated_at: datetime
    instance_id: str | None = None
    failure_code: FailureCategory | None = None
    evidence_mode: str = "simulated"
    source: str = "fixture:T14 simulated lifecycle; no cloud resource operation"

    def __post_init__(self) -> None:
        if not _safe_job_id(self.job_id):
            raise ValueError("job record requires a synthetic fixture ID")
        if not _safe_text(self.project_id) or not _safe_text(self.actor_id):
            raise ValueError("job record requires project and actor references")
        if self.operation not in _TRACKABLE_OPERATIONS or not isinstance(self.state, JobState):
            raise ValueError("job record operation or state is invalid")
        if not _aware_datetime(self.created_at) or not _aware_datetime(self.updated_at):
            raise ValueError("job record timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("job record timestamps are out of order")
        if self.evidence_mode != "simulated" or not self.source.startswith("fixture:"):
            raise ValueError("fixture job records must have simulated fixture provenance")
        if self.instance_id is not None and not _safe_instance_id(self.instance_id):
            raise ValueError("fixture job records require a synthetic instance ID")
        if self.state is JobState.FAILED:
            if not isinstance(self.failure_code, FailureCategory):
                raise ValueError("failed job records require a safe failure code")
        elif self.failure_code is not None:
            raise ValueError("non-failed job records cannot contain a failure code")

    @property
    def failure_category(self) -> FailureCategory | None:
        """Compatibility alias for the former ``failure_category`` field."""
        return self.failure_code

    def to_wire(self) -> dict[str, str]:
        """Serialize safe scoped metadata; fixture IDs are not Nova IDs."""
        result = {
            "job_id": self.job_id,
            "project_id": self.project_id,
            "actor_id": self.actor_id,
            "operation": self.operation,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "evidence_mode": self.evidence_mode,
            "source": self.source,
        }
        if self.instance_id is not None:
            result["instance_id"] = self.instance_id
        if self.failure_code is not None:
            # Preserve the prior serialized field while using the clearer
            # failure_code name in the internal record schema.
            result["failure_category"] = self.failure_code.value
        return result


@dataclass(frozen=True)
class JobRegistration:
    """Registration result; ``created`` tells an executor whether to dispatch."""

    job: JobRecord
    created: bool


Clock = Callable[[], datetime]
JobIdFactory = Callable[[], str]
IdempotencyKey = tuple[str, str, str]


class FixtureJobTracker:
    """Thread-safe, process-local registration and lookup for simulated jobs."""

    _ALLOWED_TRANSITIONS = {
        JobState.PENDING: frozenset({JobState.RUNNING, JobState.FAILED}),
        JobState.RUNNING: frozenset({JobState.SUCCEEDED, JobState.FAILED}),
        JobState.SUCCEEDED: frozenset(),
        JobState.FAILED: frozenset(),
    }

    def __init__(
        self,
        clock: Clock | None = None,
        job_id_factory: JobIdFactory | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._job_id_factory = job_id_factory or (lambda: f"fixture-job-{uuid4().hex}")
        self._jobs: dict[str, JobRecord] = {}
        self._idempotency: dict[IdempotencyKey, tuple[str, str]] = {}
        self._lock = RLock()
        self.__execution_path = _FixtureJobExecutionPath(self)

    def register(
        self,
        request: LifecycleRequest,
        authentication: AuthenticationResult,
        idempotency_key: str | None = None,
    ) -> JobRegistration:
        """Authorize and register a job, returning an existing duplicate safely.

        Idempotency is scoped to project, operation, and caller-supplied key.
        The request fingerprint is computed locally from canonical request JSON.
        """
        if not isinstance(request, LifecycleRequest) or request.operation.value not in _TRACKABLE_OPERATIONS:
            raise JobTrackerError(JobErrorCode.INVALID_OPERATION)
        if idempotency_key is not None and not _valid_idempotency_key(idempotency_key):
            raise JobTrackerError(JobErrorCode.INVALID_REQUEST)

        if not isinstance(authentication, AuthenticationResult):
            raise JobTrackerError(JobErrorCode.AUTHORIZATION_DENIED)
        identity = authentication.identity
        decision = authorize(authentication, request.project_id, request.operation.value)
        if not decision.allowed or identity is None or not _safe_text(identity.reference):
            raise JobTrackerError(JobErrorCode.AUTHORIZATION_DENIED)

        try:
            canonical = json.dumps(request.to_wire(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            raise JobTrackerError(JobErrorCode.INVALID_REQUEST) from None
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        index_key = (request.project_id, request.operation.value, idempotency_key) if idempotency_key else None
        instance_id = request.instance_id
        if instance_id is not None and not _safe_instance_id(instance_id):
            raise JobTrackerError(JobErrorCode.INVALID_REQUEST)

        with self._lock:
            if index_key is not None:
                existing = self._idempotency.get(index_key)
                if existing is not None:
                    existing_job_id, existing_fingerprint = existing
                    if existing_fingerprint != fingerprint:
                        raise JobTrackerError(JobErrorCode.IDEMPOTENCY_CONFLICT)
                    return JobRegistration(self._jobs[existing_job_id], created=False)

            job_id = self._job_id_factory()
            if not _safe_job_id(job_id):
                raise JobTrackerError(JobErrorCode.INVALID_JOB_ID)
            if job_id in self._jobs:
                raise JobTrackerError(JobErrorCode.DUPLICATE_ID)
            timestamp = self._timestamp()
            record = JobRecord(
                job_id=job_id,
                project_id=request.project_id,
                actor_id=identity.reference,
                operation=request.operation.value,
                state=JobState.PENDING,
                created_at=timestamp,
                updated_at=timestamp,
                instance_id=instance_id,
            )
            self._jobs[job_id] = record
            if index_key is not None:
                self._idempotency[index_key] = (job_id, fingerprint)
            return JobRegistration(record, created=True)

    def find_idempotent(
        self,
        request: LifecycleRequest,
        authentication: AuthenticationResult,
        idempotency_key: str | None,
    ) -> JobRegistration | None:
        """Find an existing keyed request before downstream resource checks.

        This read-only lookup lets a management service return/reject a
        duplicate before revalidating mutable fixture catalog state. It does
        not register a new job; callers still use ``register`` atomically.
        """
        if idempotency_key is None:
            return None
        if not isinstance(request, LifecycleRequest) or request.operation.value not in _TRACKABLE_OPERATIONS:
            raise JobTrackerError(JobErrorCode.INVALID_OPERATION)
        if not _valid_idempotency_key(idempotency_key):
            raise JobTrackerError(JobErrorCode.INVALID_REQUEST)
        if not isinstance(authentication, AuthenticationResult):
            raise JobTrackerError(JobErrorCode.AUTHORIZATION_DENIED)
        identity = authentication.identity
        decision = authorize(authentication, request.project_id, request.operation.value)
        if not decision.allowed or identity is None or not _safe_text(identity.reference):
            raise JobTrackerError(JobErrorCode.AUTHORIZATION_DENIED)
        try:
            canonical = json.dumps(request.to_wire(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            raise JobTrackerError(JobErrorCode.INVALID_REQUEST) from None
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        key = (request.project_id, request.operation.value, idempotency_key)
        with self._lock:
            existing = self._idempotency.get(key)
            if existing is None:
                return None
            job_id, existing_fingerprint = existing
            if existing_fingerprint != fingerprint:
                raise JobTrackerError(JobErrorCode.IDEMPOTENCY_CONFLICT)
            return JobRegistration(self._jobs[job_id], created=False)

    def get(
        self,
        job_id: str,
        project_id: str,
        authentication: AuthenticationResult,
    ) -> JobRecord:
        """Inspect only a job in an authorized project; all denial is not-found."""
        if not isinstance(authentication, AuthenticationResult):
            raise JobTrackerError(JobErrorCode.NOT_FOUND)
        decision = authorize(authentication, project_id, Operation.COMPUTE_INSPECT.value)
        if not decision.allowed:
            raise JobTrackerError(JobErrorCode.NOT_FOUND)
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.project_id != project_id:
                raise JobTrackerError(JobErrorCode.NOT_FOUND)
            return record

    def _transition(
        self,
        job_id: str,
        state: JobState,
        failure_code: FailureCategory | None = None,
        instance_id: str | None = None,
    ) -> JobRecord:
        """Internal atomic transition called through the execution path only."""
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise JobTrackerError(JobErrorCode.NOT_FOUND)
            if not isinstance(state, JobState) or state not in self._ALLOWED_TRANSITIONS[record.state]:
                raise JobTrackerError(JobErrorCode.INVALID_TRANSITION)
            if state is JobState.FAILED:
                if not isinstance(failure_code, FailureCategory):
                    raise JobTrackerError(JobErrorCode.INVALID_FAILURE_CODE)
            elif failure_code is not None:
                raise JobTrackerError(JobErrorCode.INVALID_FAILURE_CODE)
            if instance_id is not None and not _safe_instance_id(instance_id):
                raise JobTrackerError(JobErrorCode.INVALID_REQUEST)
            if record.instance_id is not None and instance_id not in (None, record.instance_id):
                raise JobTrackerError(JobErrorCode.INVALID_REQUEST)

            timestamp = self._timestamp()
            if timestamp < record.updated_at:
                raise JobTrackerError(JobErrorCode.INVALID_CLOCK)
            updated = JobRecord(
                job_id=record.job_id,
                project_id=record.project_id,
                actor_id=record.actor_id,
                operation=record.operation,
                state=state,
                created_at=record.created_at,
                updated_at=timestamp,
                instance_id=instance_id if instance_id is not None else record.instance_id,
                failure_code=failure_code,
            )
            self._jobs[job_id] = updated
            return updated

    def _timestamp(self) -> datetime:
        value = self._clock()
        if not _aware_datetime(value):
            raise JobTrackerError(JobErrorCode.INVALID_CLOCK)
        return value


class _FixtureJobExecutionPath:
    """Module-internal capability used by a future worker and tracker tests."""

    def __init__(self, tracker: FixtureJobTracker) -> None:
        self.__tracker = tracker

    def get_pending(self, job_id: str) -> JobRecord:
        """Read authoritative job metadata for an internal execution caller."""
        with self.__tracker._lock:
            record = self.__tracker._jobs.get(job_id)
            if record is None:
                raise JobTrackerError(JobErrorCode.NOT_FOUND)
            if record.state is not JobState.PENDING:
                raise JobTrackerError(JobErrorCode.INVALID_TRANSITION)
            return record

    def associate_instance_id(self, job_id: str, instance_id: str) -> JobRecord:
        """Persist a known fixture ID before a later operation step can fail."""
        if not _safe_instance_id(instance_id):
            raise JobTrackerError(JobErrorCode.INVALID_REQUEST)
        with self.__tracker._lock:
            record = self.__tracker._jobs.get(job_id)
            if record is None:
                raise JobTrackerError(JobErrorCode.NOT_FOUND)
            if record.state is not JobState.RUNNING:
                raise JobTrackerError(JobErrorCode.INVALID_TRANSITION)
            if record.instance_id is not None:
                if record.instance_id != instance_id:
                    raise JobTrackerError(JobErrorCode.INVALID_REQUEST)
                return record
            timestamp = self.__tracker._timestamp()
            if timestamp < record.updated_at:
                raise JobTrackerError(JobErrorCode.INVALID_CLOCK)
            updated = JobRecord(
                job_id=record.job_id,
                project_id=record.project_id,
                actor_id=record.actor_id,
                operation=record.operation,
                state=record.state,
                created_at=record.created_at,
                updated_at=timestamp,
                instance_id=instance_id,
            )
            self.__tracker._jobs[job_id] = updated
            return updated

    def transition(
        self,
        job_id: str,
        state: JobState,
        failure_code: FailureCategory | None = None,
        instance_id: str | None = None,
    ) -> JobRecord:
        return self.__tracker._transition(job_id, state, failure_code, instance_id)


def _execution_path(tracker: FixtureJobTracker) -> _FixtureJobExecutionPath:
    """Return the private execution capability for trusted internal callers."""
    return tracker._FixtureJobTracker__execution_path


def _safe_job_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("fixture-job-")
        and len(value) > len("fixture-job-")
        and all(character.isalnum() or character == "-" for character in value)
    )


def _safe_instance_id(value: object) -> bool:
    prefix = "fixture-instance-"
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    suffix = value.removeprefix(prefix)
    return bool(suffix) and suffix.isascii() and suffix.isdigit()


def _valid_idempotency_key(value: object) -> bool:
    return _safe_text(value) and len(value) <= 255


def _safe_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value and "\r" not in value


def _aware_datetime(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
