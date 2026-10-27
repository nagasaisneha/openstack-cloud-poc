"""Derive billing principals from a refreshed Keystone SDK access context.

This adapter accepts only a server-side Keystone-authenticated session, such as
the session held by a Horizon request. It never reads identity or role claims
from request parameters, headers supplied by a caller, or browser state.
"""

from threading import RLock
from typing import Iterable

from .billing import (
    BillingError,
    BillingErrorCode,
    BillingPrincipal,
    ProjectRoleBinding,
)


PROJECT_BILLING_ROLES = frozenset({"billing_viewer", "project_admin", "billing_collector"})
SYSTEM_BILLING_ROLES = frozenset({"cloud_admin", "billing_collector"})


class KeystoneBillingPrincipalAdapter:
    """Convert a token-refreshed Keystone scope into least-privilege billing roles.

    The wrapped session must be created by trusted server-side authentication
    code. Construct one adapter per authenticated Horizon session; pass the
    expected user and selected project from Horizon's server-side request
    context to ``principal_for`` on each operation.
    """

    def __init__(self, session: object) -> None:
        self._session = session
        self._lock = RLock()

    def principal_for(
        self,
        expected_user_id: str,
        expected_project_id: str | None = None,
    ) -> BillingPrincipal:
        if not _valid_identifier(expected_user_id):
            raise BillingError(BillingErrorCode.AUTHENTICATION_REQUIRED)
        if expected_project_id is not None and not _valid_identifier(expected_project_id):
            raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)

        try:
            auth = getattr(self._session, "auth")
            invalidate = getattr(auth, "invalidate")
            get_access = getattr(auth, "get_access")
            if not callable(invalidate) or not callable(get_access):
                raise AttributeError
            with self._lock:
                invalidate()
                access = get_access(self._session)
        except Exception:
            raise BillingError(BillingErrorCode.AUTHENTICATION_REQUIRED) from None

        user_id = getattr(access, "user_id", None)
        if not _valid_identifier(user_id):
            raise BillingError(BillingErrorCode.AUTHENTICATION_REQUIRED)
        if user_id != expected_user_id:
            raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)

        project_scoped = getattr(access, "project_scoped", False)
        system_scoped = getattr(access, "system_scoped", False)
        # AccessInfoV3's missing-property wrapper returns None when a token
        # has no "system" stanza. This means no system scope/privileges; it
        # must not invalidate a correctly verified project-scoped token.
        if system_scoped is None:
            system_scoped = False
        project_id = getattr(access, "project_id", None)
        roles = _role_names(getattr(access, "role_names", None))
        if roles is None or not isinstance(project_scoped, bool) or not isinstance(system_scoped, bool):
            raise BillingError(BillingErrorCode.AUTHENTICATION_REQUIRED)

        if project_scoped and not system_scoped:
            if not _valid_identifier(project_id):
                raise BillingError(BillingErrorCode.AUTHENTICATION_REQUIRED)
            if expected_project_id is not None and project_id != expected_project_id:
                raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)
            scoped_roles = frozenset(roles.intersection(PROJECT_BILLING_ROLES))
            return BillingPrincipal(
                subject_id=user_id,
                authenticated=True,
                project_bindings=(ProjectRoleBinding(project_id, scoped_roles),),
            )

        if system_scoped and not project_scoped:
            if project_id is not None or expected_project_id is not None:
                raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)
            system_roles = frozenset(roles.intersection(SYSTEM_BILLING_ROLES))
            return BillingPrincipal(
                subject_id=user_id,
                authenticated=True,
                system_roles=system_roles,
            )

        # Unscoped, domain-scoped, or contradictory access contexts cannot
        # authorize project billing or system-wide billing operations.
        raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)


def _role_names(value: object) -> set[str] | None:
    if isinstance(value, str) or not isinstance(value, Iterable):
        return None
    result = set()
    for role in value:
        if not isinstance(role, str) or not role or "\n" in role or "\r" in role:
            return None
        result.add(role)
    return result


def _valid_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 255
        and "\n" not in value
        and "\r" not in value
    )
