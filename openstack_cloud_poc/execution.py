"""Bounded standard-library execution for local simulated lifecycle jobs.

This module has no HTTP or cloud-provider integration. It accepts a previously
registered fixture job, rechecks authorization from the immutable job record,
and runs a supplied local callable on a bounded thread pool.
"""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from threading import BoundedSemaphore, Lock, RLock
from typing import Callable

from .identity import FixtureIdentityProvider, authorize
from .jobs import (
    FailureCategory,
    FixtureJobTracker,
    JobRecord,
    JobState,
    JobTrackerError,
    _execution_path,
)


class ExecutionErrorCode(str, Enum):
    INVALID_JOB = "invalid_job"
    INVALID_CALLABLE = "invalid_callable"
    JOB_NOT_PENDING = "job_not_pending"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    RESOURCE_BUSY = "resource_busy"
    DUPLICATE_SUBMISSION = "duplicate_submission"
    SHUTDOWN = "executor_shutdown"
    SUBMISSION_FAILED = "submission_failed"


_ERROR_MESSAGES = {
    ExecutionErrorCode.INVALID_JOB: "No pending simulated job is available for execution.",
    ExecutionErrorCode.INVALID_CALLABLE: "The local operation is not executable.",
    ExecutionErrorCode.JOB_NOT_PENDING: "The simulated job is not pending.",
    ExecutionErrorCode.CAPACITY_EXHAUSTED: "The local execution capacity is full.",
    ExecutionErrorCode.RESOURCE_BUSY: "A lifecycle operation for this fixture instance is already running.",
    ExecutionErrorCode.DUPLICATE_SUBMISSION: "The simulated job has already been submitted.",
    ExecutionErrorCode.SHUTDOWN: "The local execution engine is shut down.",
    ExecutionErrorCode.SUBMISSION_FAILED: "The local execution engine could not accept the job.",
}


class ExecutionError(RuntimeError):
    """Sanitized failure before an operation can be reported as accepted."""

    def __init__(self, code: ExecutionErrorCode) -> None:
        self.code = code
        self.message = _ERROR_MESSAGES[code]
        super().__init__(self.message)


class FixtureOperationFailure(Exception):
    """Safe structured failure for a callable that knows a partial fixture ID."""

    def __init__(
        self,
        failure_code: FailureCategory = FailureCategory.PROVIDER,
        instance_id: str | None = None,
    ) -> None:
        if not isinstance(failure_code, FailureCategory):
            raise ValueError("failure_code must be an allowlisted category")
        self.failure_code = failure_code
        self.instance_id = instance_id
        super().__init__("The simulated fixture operation failed.")


@dataclass(frozen=True)
class ExecutionHandle:
    """Non-cancellable view of accepted background work."""

    _future: Future[JobRecord]

    def result(self, timeout: float | None = None) -> JobRecord:
        return self._future.result(timeout=timeout)

    def done(self) -> bool:
        return self._future.done()

    def running(self) -> bool:
        return self._future.running()


OperationCallable = Callable[[JobRecord], str | None]
ExecutionAuthorizer = Callable[[JobRecord], bool]


class FixtureExecutionAuthorizer:
    """Reauthorize using fixture identity references stored on the job.

    No user bearer token or request credential is retained or replayed. This
    default authorizer is simulated and may be replaced by a future internal
    authorization adapter without changing the executor contract.
    """

    def __init__(self) -> None:
        self._identities = FixtureIdentityProvider()

    def __call__(self, job: JobRecord) -> bool:
        authentication = self._identities.authenticate(job.actor_id)
        return authorize(authentication, job.project_id, job.operation).allowed


class LocalExecutionEngine:
    """Bounded background executor for local fixture jobs.

    ``max_workers`` bounds active threads and ``max_pending`` bounds queued
    work. Therefore at most their sum can be accepted at once. Rejected jobs
    are marked failed when possible and ``submit`` raises instead of returning
    an execution handle, so callers cannot mistake rejection for acceptance.
    """

    def __init__(
        self,
        tracker: FixtureJobTracker,
        max_workers: int = 2,
        max_pending: int = 8,
        authorizer: ExecutionAuthorizer | None = None,
    ) -> None:
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending < 0:
            raise ValueError("max_pending must be a non-negative integer")
        if not isinstance(tracker, FixtureJobTracker):
            raise TypeError("tracker must be a FixtureJobTracker")

        self.max_workers = max_workers
        self.max_pending = max_pending
        self.max_in_flight = max_workers + max_pending
        self._tracker = tracker
        self._execution = _execution_path(tracker)
        self._authorizer = authorizer or FixtureExecutionAuthorizer()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="fixture-lifecycle",
        )
        self._capacity = BoundedSemaphore(self.max_in_flight)
        self._lock = RLock()
        self._shutdown_lock = Lock()
        self._submitted: set[str] = set()
        self._resource_reservations: dict[tuple[str, str], str] = {}
        self._reservation_by_job: dict[str, tuple[str, str]] = {}
        self._closed = False

    @property
    def tracker(self) -> FixtureJobTracker:
        """Expose the associated tracker for dependency consistency checks."""
        return self._tracker

    def submit(self, job_id: str, operation: OperationCallable) -> ExecutionHandle:
        """Submit a registered pending job or raise without false acceptance.

        A returned handle means the underlying executor accepted the callable.
        The job becomes RUNNING only inside the worker, after it starts.
        """
        if not callable(operation):
            self._fail_pending(job_id, FailureCategory.VALIDATION)
            raise ExecutionError(ExecutionErrorCode.INVALID_CALLABLE)

        with self._lock:
            if job_id in self._submitted:
                raise ExecutionError(ExecutionErrorCode.DUPLICATE_SUBMISSION)
            if self._closed:
                self._fail_pending(job_id, FailureCategory.EXECUTION_REJECTED)
                raise ExecutionError(ExecutionErrorCode.SHUTDOWN)
            try:
                pending = self._execution.get_pending(job_id)
            except JobTrackerError:
                raise ExecutionError(ExecutionErrorCode.INVALID_JOB) from None
            resource_key = (
                (pending.project_id, pending.instance_id)
                if pending.instance_id is not None
                else None
            )
            if resource_key is not None and resource_key in self._resource_reservations:
                self._fail_pending(job_id, FailureCategory.CONFLICT)
                raise ExecutionError(ExecutionErrorCode.RESOURCE_BUSY)
            if not self._capacity.acquire(blocking=False):
                self._fail_pending(job_id, FailureCategory.EXECUTION_REJECTED)
                raise ExecutionError(ExecutionErrorCode.CAPACITY_EXHAUSTED)
            if resource_key is not None:
                self._resource_reservations[resource_key] = job_id
                self._reservation_by_job[job_id] = resource_key
            try:
                future = self._pool.submit(self._run, job_id, operation)
            except JobTrackerError:
                self._release_reservation(job_id)
                self._capacity.release()
                raise ExecutionError(ExecutionErrorCode.JOB_NOT_PENDING) from None
            except Exception:
                self._release_reservation(job_id)
                self._capacity.release()
                self._fail_pending(job_id, FailureCategory.EXECUTION_REJECTED)
                raise ExecutionError(ExecutionErrorCode.SUBMISSION_FAILED) from None

            self._submitted.add(job_id)
            future.add_done_callback(lambda completed: self._release_slot(job_id, completed))
            return ExecutionHandle(future)

    @property
    def is_shutdown(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        """Stop accepting work and wait for all accepted work to finish."""
        with self._shutdown_lock:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
            # ThreadPoolExecutor.shutdown(wait=True) drains accepted queued work.
            self._pool.shutdown(wait=True)

    def __enter__(self) -> "LocalExecutionEngine":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _run(self, job_id: str, operation: OperationCallable) -> JobRecord:
        """Worker body. Exceptions are converted to safe terminal job states."""
        try:
            pending = self._execution.get_pending(job_id)
            running = self._execution.transition(job_id, JobState.RUNNING)
        except JobTrackerError as exc:
            raise ExecutionError(ExecutionErrorCode.JOB_NOT_PENDING) from None

        try:
            try:
                authorized = self._authorizer(pending)
            except Exception:
                authorized = False
            if not authorized:
                return self._execution.transition(
                    job_id,
                    JobState.FAILED,
                    FailureCategory.AUTHORIZATION,
                )

            instance_id = operation(running)
            if instance_id is not None:
                self._execution.associate_instance_id(job_id, instance_id)
            return self._execution.transition(
                job_id,
                JobState.SUCCEEDED,
            )
        except FixtureOperationFailure as exc:
            try:
                if exc.instance_id is not None:
                    self._execution.associate_instance_id(job_id, exc.instance_id)
                return self._fail_running(job_id, exc.failure_code)
            except JobTrackerError:
                return self._fail_running(job_id, FailureCategory.PROVIDER)
        except TimeoutError:
            return self._fail_running(job_id, FailureCategory.TIMEOUT)
        except ConnectionError:
            return self._fail_running(job_id, FailureCategory.SERVICE_UNAVAILABLE)
        except Exception:
            return self._fail_running(job_id, FailureCategory.PROVIDER)

    def _fail_pending(self, job_id: str, failure_code: FailureCategory) -> None:
        try:
            self._execution.transition(job_id, JobState.FAILED, failure_code)
        except JobTrackerError:
            # The submission error remains the public result. Never expose
            # tracker internals or turn a rejected submission into acceptance.
            pass

    def _fail_running(self, job_id: str, failure_code: FailureCategory) -> JobRecord:
        return self._execution.transition(job_id, JobState.FAILED, failure_code)

    def _release_slot(self, job_id: str, _future: Future[JobRecord]) -> None:
        with self._lock:
            self._submitted.discard(job_id)
            self._release_reservation(job_id)
            self._capacity.release()

    def _release_reservation(self, job_id: str) -> None:
        resource_key = self._reservation_by_job.pop(job_id, None)
        if resource_key is not None:
            self._resource_reservations.pop(resource_key, None)
