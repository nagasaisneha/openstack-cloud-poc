"""Narrow fixture-backed management adapter for identity and catalog reads.

This module has no cloud, VM-manager, credential, or network integration.
All adapter-level errors describe fixture behavior, not remote service state.
"""

from dataclasses import dataclass
from typing import Mapping, Protocol

from .catalog import Catalog, CatalogCategory, CatalogProvider
from .catalog_fixtures import FixtureCatalogProvider
from .identity import (
    SIMULATED,
    AuthenticationResult,
    AuthorizationResult,
    FixtureIdentityProvider,
    IdentityProvider,
    authorize as authorize_identity,
)


@dataclass(frozen=True)
class AdapterError:
    """Safe fixture error; messages never include request values."""

    code: str
    message: str
    status: str = SIMULATED


@dataclass(frozen=True)
class ScopedCatalogResult:
    """Project-authorized catalog response; denied responses contain no data."""

    authorization: AuthorizationResult
    catalog: Catalog | None
    error: AdapterError | None = None

    @property
    def status(self) -> str:
        """Expose the evidence status of the authorization decision."""
        return self.authorization.status


AdapterValue = (
    AuthenticationResult
    | AuthorizationResult
    | AdapterError
    | ScopedCatalogResult
)


class ManagementAdapter(Protocol):
    """Only identity and catalog operations are part of this boundary."""

    def read_catalog(
        self,
        authentication: AuthenticationResult,
        project_id: str,
    ) -> ScopedCatalogResult:
        """Return catalog records only after project-scoped catalog.read authorization."""

    def authenticate(self, identity_reference: str) -> AuthenticationResult:
        """Return a fixture identity result; never real Keystone authentication."""

    def authorize(
        self,
        authentication: AuthenticationResult,
        project_id: str,
        operation: str,
    ) -> AuthorizationResult:
        """Apply the existing fixture project's role policy."""

    def request(
        self,
        operation: str,
        parameters: Mapping[str, object] | None = None,
    ) -> AdapterValue:
        """Dispatch only the explicitly supported identity/catalog operations."""


class FixtureManagementAdapter:
    """Compose the existing deterministic identity and catalog providers."""

    def __init__(
        self,
        identity_provider: IdentityProvider | None = None,
        catalog_provider: CatalogProvider | None = None,
    ) -> None:
        self._identity_provider = identity_provider or FixtureIdentityProvider()
        self._catalog_provider = catalog_provider or FixtureCatalogProvider()

    def _get_catalog_fixture(self) -> Catalog:
        """Internal provider access; callers must use the scoped read method."""
        return self._catalog_provider.get_catalog()

    def read_catalog(
        self,
        authentication: AuthenticationResult,
        project_id: str,
    ) -> ScopedCatalogResult:
        """Authorize catalog.read before returning any fixture catalog records."""
        decision = self.authorize(authentication, project_id, "catalog.read")
        if not decision.allowed:
            return ScopedCatalogResult(authorization=decision, catalog=None)
        return ScopedCatalogResult(authorization=decision, catalog=self._get_catalog_fixture())

    def authenticate(self, identity_reference: str) -> AuthenticationResult:
        return self._identity_provider.authenticate(identity_reference)

    def authorize(
        self,
        authentication: AuthenticationResult,
        project_id: str,
        operation: str,
    ) -> AuthorizationResult:
        return authorize_identity(authentication, project_id, operation)

    def request(
        self,
        operation: str,
        parameters: Mapping[str, object] | None = None,
    ) -> AdapterValue:
        """Dispatch a small allowlist and return non-sensitive errors otherwise."""
        params = {} if parameters is None else parameters
        if not isinstance(params, Mapping):
            return self._invalid_request()

        if operation == "catalog.list":
            authentication, project_id = self._scoped_read_parameters(params)
            if (
                authentication is None
                or project_id is None
                or set(params) != {"authentication", "project_id"}
            ):
                return self._invalid_request()
            return self.read_catalog(authentication, project_id)

        if operation == "catalog.get":
            authentication, project_id = self._scoped_read_parameters(params)
            category = params.get("category")
            identifier = params.get("identifier")
            if (
                authentication is None
                or project_id is None
                or not isinstance(category, CatalogCategory)
                or not isinstance(identifier, str)
            ):
                return self._invalid_request()
            if set(params) != {"authentication", "project_id", "category", "identifier"}:
                return self._invalid_request()
            result = self.read_catalog(authentication, project_id)
            if not result.authorization.allowed:
                return result
            if result.catalog is None:
                return ScopedCatalogResult(
                    authorization=result.authorization,
                    catalog=None,
                    error=AdapterError(
                        code="catalog_read_failed",
                        message="The authorized fixture catalog could not be read.",
                    ),
                )
            record = result.catalog.get(category, identifier)
            if record is None:
                return ScopedCatalogResult(
                    authorization=result.authorization,
                    catalog=None,
                    error=AdapterError(
                        code="fixture_record_missing",
                        message="No matching fixture record exists; remote availability is unverified.",
                    ),
                )
            return ScopedCatalogResult(
                authorization=result.authorization,
                catalog=Catalog((record,)),
            )

        if operation == "identity.authenticate":
            identity_reference = params.get("identity_reference")
            if not isinstance(identity_reference, str) or set(params) != {"identity_reference"}:
                return self._invalid_request()
            return self.authenticate(identity_reference)

        if operation == "identity.authorize":
            authentication = params.get("authentication")
            project_id = params.get("project_id")
            action = params.get("operation")
            if (
                not isinstance(authentication, AuthenticationResult)
                or not isinstance(project_id, str)
                or not isinstance(action, str)
                or set(params) != {"authentication", "project_id", "operation"}
            ):
                return self._invalid_request()
            return self.authorize(authentication, project_id, action)

        return AdapterError(
            code="unsupported_operation",
            message="This fixture adapter supports identity and catalog operations only.",
        )

    @staticmethod
    def _invalid_request() -> AdapterError:
        return AdapterError(
            code="invalid_request",
            message="The fixture operation parameters are invalid.",
        )

    @staticmethod
    def _scoped_read_parameters(
        parameters: Mapping[str, object],
    ) -> tuple[AuthenticationResult | None, str | None]:
        authentication = parameters.get("authentication")
        project_id = parameters.get("project_id")
        allowed_keys = {"authentication", "project_id"}
        if set(parameters) - allowed_keys - {"category", "identifier"}:
            return None, None
        if not isinstance(authentication, AuthenticationResult) or not isinstance(project_id, str):
            return None, None
        return authentication, project_id
