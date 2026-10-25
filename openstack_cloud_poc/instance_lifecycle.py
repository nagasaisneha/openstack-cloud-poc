"""In-memory simulated fixture lifecycle; no cloud or network integration."""

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Callable, Mapping, Protocol

from .adapter import FixtureManagementAdapter
from .api_contract import ContractError, Operation, ProvisioningRequest
from .identity import AuthenticationResult, SIMULATED
from .management_service import FixtureCatalogRequestValidator, MissingFixtureRecord

SOURCE = "fixture:T16 in-memory lifecycle; simulated only"


class InstanceState(str, Enum):
    ACTIVE = "active"
    STOPPED = "stopped"
    DELETED = "deleted"


class LifecycleErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    AUTHORIZATION_DENIED = "authorization_denied"
    VALIDATION_FAILURE = "validation_failure"
    MISSING_FIXTURE_DATA = "missing_fixture_data"
    INSTANCE_NOT_FOUND = "instance_not_found"
    INSTANCE_NOT_TASK_CREATED = "instance_not_task_created"
    INVALID_TRANSITION = "invalid_transition"
    INVALID_FIXTURE_ID = "invalid_fixture_id"
    DUPLICATE_FIXTURE_ID = "duplicate_fixture_id"
    TIMEOUT = "timeout"
    PROVIDER_FAILURE = "provider_failure"


_MESSAGES = {
    LifecycleErrorCode.INVALID_REQUEST: "The lifecycle request is invalid.",
    LifecycleErrorCode.AUTHORIZATION_DENIED: "Project authorization was denied.",
    LifecycleErrorCode.VALIDATION_FAILURE: "The create request did not pass local contract validation.",
    LifecycleErrorCode.MISSING_FIXTURE_DATA: "A referenced fixture record is missing.",
    LifecycleErrorCode.INSTANCE_NOT_FOUND: "No matching fixture instance exists.",
    LifecycleErrorCode.INSTANCE_NOT_TASK_CREATED: "The fixture instance was not created by this lifecycle provider.",
    LifecycleErrorCode.INVALID_TRANSITION: "The requested lifecycle transition is invalid.",
    LifecycleErrorCode.INVALID_FIXTURE_ID: "The fixture instance identifier is invalid.",
    LifecycleErrorCode.DUPLICATE_FIXTURE_ID: "The fixture instance identifier already exists.",
    LifecycleErrorCode.TIMEOUT: "The fixture lifecycle operation timed out.",
    LifecycleErrorCode.PROVIDER_FAILURE: "The fixture lifecycle provider failed.",
}


@dataclass(frozen=True)
class InstanceRecord:
    instance_id: str
    project_id: str
    state: InstanceState
    image_id: str | None = None
    flavor_id: str | None = None
    network_id: str | None = None
    status: str = SIMULATED
    source: str = SOURCE
    provider_created: bool = True


@dataclass(frozen=True)
class LifecycleError:
    code: LifecycleErrorCode
    message: str
    status: str = SIMULATED
    source: str = SOURCE


@dataclass(frozen=True)
class LifecycleResult:
    record: InstanceRecord | None = None
    error: LifecycleError | None = None
    status: str = SIMULATED
    source: str = SOURCE

    def __post_init__(self) -> None:
        if (self.record is None) == (self.error is None):
            raise ValueError("result must contain exactly one record or error")


def _valid_id(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("fixture-instance-"):
        return False
    suffix = value.removeprefix("fixture-instance-")
    return bool(suffix) and suffix.isascii() and suffix.isdigit()


class FixtureInstanceProvider:
    """In-memory state machine with a deterministic, injectable ID source."""

    def __init__(self, id_generator: Callable[[], str] | None = None) -> None:
        self._lock = RLock()
        self._records: dict[str, InstanceRecord] = {}
        self._next_id = 1
        self._uses_default_generator = id_generator is None
        self._id_generator = id_generator if id_generator is not None else self._default_id
        self.fail_next: BaseException | None = None

    @property
    def records(self) -> tuple[InstanceRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def _default_id(self) -> str:
        return f"fixture-instance-{self._next_id:06d}"

    def seed(self, record: InstanceRecord) -> None:
        """Load a trusted internal fixture record after validating its boundary fields."""
        if (
            not isinstance(record, InstanceRecord)
            or not _valid_id(record.instance_id)
            or not isinstance(record.state, InstanceState)
            or not isinstance(record.project_id, str)
            or not record.project_id.strip()
            or "\n" in record.project_id
            or "\r" in record.project_id
        ):
            raise ValueError(_MESSAGES[LifecycleErrorCode.INVALID_FIXTURE_ID])
        with self._lock:
            if record.instance_id in self._records:
                raise ValueError(_MESSAGES[LifecycleErrorCode.DUPLICATE_FIXTURE_ID])
            self._records[record.instance_id] = InstanceRecord(
                instance_id=record.instance_id,
                project_id=record.project_id,
                state=record.state,
                image_id=record.image_id,
                flavor_id=record.flavor_id,
                network_id=record.network_id,
                provider_created=False,
            )

    def create(self, project_id: str, image_id: str, flavor_id: str, network_id: str) -> InstanceRecord:
        with self._lock:
            candidate = self._id_generator()
            if not _valid_id(candidate):
                raise ValueError(_MESSAGES[LifecycleErrorCode.INVALID_FIXTURE_ID])
            if candidate in self._records:
                raise DuplicateFixtureId
            self._raise_injected_failure()
            record = InstanceRecord(candidate, project_id, InstanceState.ACTIVE, image_id, flavor_id, network_id)
            self._records[candidate] = record
            if self._uses_default_generator:
                self._next_id += 1
            return record

    def inspect(self, instance_id: str) -> InstanceRecord | None:
        with self._lock:
            self._validate_id(instance_id)
            self._raise_injected_failure()
            return self._records.get(instance_id)

    def transition(
        self, instance_id: str, target: InstanceState, project_id: str | None = None,
    ) -> InstanceRecord:
        with self._lock:
            self._validate_id(instance_id)
            current = self._records.get(instance_id)
            if current is None or (project_id is not None and current.project_id != project_id):
                raise LookupError
            if not current.provider_created:
                raise PermissionError
            if not isinstance(target, InstanceState):
                raise InvalidTransition
            if current.state is target:
                return current
            allowed_targets = {
                InstanceState.ACTIVE: {InstanceState.ACTIVE, InstanceState.STOPPED, InstanceState.DELETED},
                InstanceState.STOPPED: {InstanceState.STOPPED, InstanceState.ACTIVE, InstanceState.DELETED},
                InstanceState.DELETED: {InstanceState.DELETED},
            }
            if target not in allowed_targets[current.state]:
                raise InvalidTransition
            self._raise_injected_failure()
            updated = InstanceRecord(
                current.instance_id, current.project_id, target,
                current.image_id, current.flavor_id, current.network_id,
            )
            self._records[instance_id] = updated
            return updated

    @staticmethod
    def _validate_id(instance_id: str) -> None:
        if not _valid_id(instance_id):
            raise ValueError

    def _raise_injected_failure(self) -> None:
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure


class DuplicateFixtureId(Exception):
    pass


class InvalidTransition(Exception):
    pass


class InstanceProvider(Protocol):
    def create(self, project_id: str, image_id: str, flavor_id: str, network_id: str) -> InstanceRecord: ...
    def inspect(self, instance_id: str) -> InstanceRecord | None: ...
    def transition(self, instance_id: str, target: InstanceState, project_id: str | None = None) -> InstanceRecord: ...


class FixtureInstanceLifecycleService:
    """Authenticated lifecycle facade using the repository's existing auth policy."""

    def __init__(self, adapter: FixtureManagementAdapter | None = None,
                 provider: InstanceProvider | None = None):
        self._adapter = adapter or FixtureManagementAdapter()
        self._provider = provider or FixtureInstanceProvider()
        self._validator = FixtureCatalogRequestValidator(self._adapter)

    def create(self, payload: Mapping[str, object], authentication: AuthenticationResult) -> LifecycleResult:
        if not isinstance(authentication, AuthenticationResult):
            return self._error(LifecycleErrorCode.AUTHORIZATION_DENIED)
        if not isinstance(payload, Mapping):
            return self._error(LifecycleErrorCode.INVALID_REQUEST)
        try:
            operation_value = payload.get("operation")
            if isinstance(operation_value, str):
                validated_payload = dict(payload)
                validated_payload["operation"] = Operation.COMPUTE_CREATE.value
                request = ProvisioningRequest.from_mapping(validated_payload)
                if operation_value != Operation.COMPUTE_CREATE.value:
                    return self._error(LifecycleErrorCode.INVALID_REQUEST)
            else:
                request = ProvisioningRequest.from_mapping(payload)
        except ContractError:
            return self._error(LifecycleErrorCode.VALIDATION_FAILURE)
        try:
            authorization = self._adapter.authorize(authentication, request.project_id, "compute.create")
        except Exception:
            return self._error(LifecycleErrorCode.AUTHORIZATION_DENIED)
        if not authorization.allowed:
            return self._error(LifecycleErrorCode.AUTHORIZATION_DENIED)
        try:
            self._validator.validate_references(request)
            record = self._provider.create(
                request.project_id, request.image_id, request.flavor_id, request.network_id,
            )
            return LifecycleResult(record=record)
        except MissingFixtureRecord:
            return self._error(LifecycleErrorCode.MISSING_FIXTURE_DATA)
        except TimeoutError:
            return self._error(LifecycleErrorCode.TIMEOUT)
        except DuplicateFixtureId:
            return self._error(LifecycleErrorCode.DUPLICATE_FIXTURE_ID)
        except ValueError:
            return self._error(LifecycleErrorCode.INVALID_FIXTURE_ID)
        except Exception:
            return self._error(LifecycleErrorCode.PROVIDER_FAILURE)

    def inspect(self, instance_id: str, project_id: str, authentication: AuthenticationResult) -> LifecycleResult:
        return self._perform("compute.inspect", project_id, authentication, instance_id, None)

    def start(self, instance_id: str, project_id: str, authentication: AuthenticationResult) -> LifecycleResult:
        return self._perform("compute.start", project_id, authentication, instance_id, InstanceState.ACTIVE)

    def stop(self, instance_id: str, project_id: str, authentication: AuthenticationResult) -> LifecycleResult:
        return self._perform("compute.stop", project_id, authentication, instance_id, InstanceState.STOPPED)

    def delete(self, instance_id: str, project_id: str, authentication: AuthenticationResult) -> LifecycleResult:
        return self._perform("compute.delete", project_id, authentication, instance_id, InstanceState.DELETED)

    def _perform(self, operation: str, project_id: str, authentication: AuthenticationResult,
                 instance_id: str, target: InstanceState | None) -> LifecycleResult:
        if not isinstance(authentication, AuthenticationResult):
            return self._error(LifecycleErrorCode.AUTHORIZATION_DENIED)
        try:
            decision = self._adapter.authorize(authentication, project_id, operation)
        except Exception:
            return self._error(LifecycleErrorCode.AUTHORIZATION_DENIED)
        if not decision.allowed:
            return self._error(LifecycleErrorCode.AUTHORIZATION_DENIED)
        try:
            if target is None:
                record = self._provider.inspect(instance_id)
                if record is None or record.project_id != project_id:
                    return self._error(LifecycleErrorCode.INSTANCE_NOT_FOUND)
            else:
                record = self._provider.transition(instance_id, target, project_id)
            return LifecycleResult(record=record)
        except ValueError:
            return self._error(LifecycleErrorCode.INVALID_FIXTURE_ID)
        except LookupError:
            return self._error(LifecycleErrorCode.INSTANCE_NOT_FOUND)
        except PermissionError:
            return self._error(LifecycleErrorCode.INSTANCE_NOT_TASK_CREATED)
        except InvalidTransition:
            return self._error(LifecycleErrorCode.INVALID_TRANSITION)
        except TimeoutError:
            return self._error(LifecycleErrorCode.TIMEOUT)
        except Exception:
            return self._error(LifecycleErrorCode.PROVIDER_FAILURE)

    @staticmethod
    def _error(code: LifecycleErrorCode) -> LifecycleResult:
        return LifecycleResult(error=LifecycleError(code, _MESSAGES[code]))
