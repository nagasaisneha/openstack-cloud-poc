"""Manager-independent identity contract and deterministic fixture provider.

Fixture identities are nonsecret sample data. Their results are always
simulated and do not establish Keystone authentication or cloud identity.
"""

from dataclasses import dataclass
from typing import Protocol


SIMULATED = "simulated"

# These are illustrative identifiers and assignments, not credentials.
_FIXTURE_IDENTITIES = {
    "fixture:alice": ("fixture:project-alpha", frozenset({"catalog.viewer", "compute.operator"})),
    "fixture:bob": ("fixture:project-alpha", frozenset({"catalog.viewer"})),
    "fixture:carol": ("fixture:project-alpha", frozenset({"compute.viewer"})),
    "fixture:mallory": ("fixture:project-beta", frozenset({"catalog.viewer", "compute.operator"})),
    "fixture:dana": ("fixture:project-alpha", frozenset({"usage.viewer"})),
}

# Explicit fixture policy. Inspection is available to a compute viewer or
# operator; state-changing lifecycle actions require compute.operator.
# Unlisted operations remain denied.
_ALLOWED_ROLES = {
    "catalog.read": frozenset({"catalog.viewer"}),
    "compute.create": frozenset({"compute.operator"}),
    "compute.inspect": frozenset({"compute.viewer", "compute.operator"}),
    "compute.start": frozenset({"compute.operator"}),
    "compute.stop": frozenset({"compute.operator"}),
    "compute.delete": frozenset({"compute.operator"}),
    "usage.read": frozenset({"usage.viewer"}),
}


@dataclass(frozen=True)
class Identity:
    reference: str
    project_id: str
    roles: frozenset[str]


@dataclass(frozen=True)
class AuthenticationResult:
    authenticated: bool
    identity: Identity | None
    status: str
    error: str | None = None


@dataclass(frozen=True)
class AuthorizationResult:
    allowed: bool
    status: str
    reason: str


class IdentityProvider(Protocol):
    """Authentication boundary; implementations must label their evidence."""

    def authenticate(self, identity_reference: str) -> AuthenticationResult:
        """Resolve an identity reference without logging or returning secrets."""


class FixtureIdentityProvider:
    """Resolve known nonsecret fixture IDs; never contacts an external service."""

    def authenticate(self, identity_reference: str) -> AuthenticationResult:
        assignment = _FIXTURE_IDENTITIES.get(identity_reference)
        if assignment is None:
            return AuthenticationResult(
                authenticated=False,
                identity=None,
                status=SIMULATED,
                error="identity_not_found",
            )

        project_id, roles = assignment
        return AuthenticationResult(
            authenticated=True,
            identity=Identity(identity_reference, project_id, roles),
            status=SIMULATED,
        )


def authorize(
    authentication: AuthenticationResult,
    project_id: str,
    operation: str,
) -> AuthorizationResult:
    """Apply fixture project scope and the explicit operation-to-role policy."""
    if not authentication.authenticated or authentication.identity is None:
        return AuthorizationResult(False, SIMULATED, "authentication_required")

    identity = authentication.identity
    if project_id != identity.project_id:
        return AuthorizationResult(False, SIMULATED, "project_scope_mismatch")

    allowed_roles = _ALLOWED_ROLES.get(operation)
    if allowed_roles is None:
        return AuthorizationResult(False, SIMULATED, "operation_not_allowed")
    if identity.roles.isdisjoint(allowed_roles):
        return AuthorizationResult(False, SIMULATED, "role_required")

    return AuthorizationResult(True, SIMULATED, "authorized_by_fixture_policy")
