"""Adapt Nova's project usage API to the project billing source contract.

Nova ``os-simple-tenant-usage`` reports allocation-hours. It does not certify
meter completeness, cover all resource lifecycle transitions, or report guest
CPU/network consumption. Every batch from this adapter is therefore observed
but incomplete, which keeps generated statements provisional.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .billing import BillingError, BillingErrorCode, UsageBatch
from .keystone_billing_auth import KeystoneBillingPrincipalAdapter
from .nova_usage import SOURCE, samples_from_nova_usage
from .usage import UsageProvenance


NOVA_USAGE_PAGE_LIMIT = 500
NOVA_USAGE_MAX_PAGES = 100


class NovaBillingUsageSource:
    """Read one project's Nova allocation-hours through an injected SDK client.

    Authentication is supplied by trusted server-side construction. Production
    deployment must provide a separate, least-privilege billing collector
    identity; this class never accepts credentials or role claims from a
    request.
    """

    def __init__(self, connection: object, collector_user_id: str, *, clock=None) -> None:
        compute = getattr(connection, "compute", None)
        if not callable(getattr(compute, "get", None)):
            raise TypeError("connection must expose a compute.get method")
        session = getattr(connection, "session", None)
        if session is None:
            raise TypeError("connection must expose its authenticated Keystone session")
        self._connection = connection
        self._identity = KeystoneBillingPrincipalAdapter(session)
        self._collector_user_id = collector_user_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def collect(
        self,
        project_id: str,
        interval_start: datetime,
        interval_end: datetime,
    ) -> UsageBatch:
        if (
            not _valid_project_id(project_id)
            or not _is_utc(interval_start)
            or not _is_utc(interval_end)
            or interval_end <= interval_start
            or interval_start.microsecond != 0
            or interval_end.microsecond != 0
        ):
            raise BillingError(BillingErrorCode.INVALID_REQUEST)

        principal = self._identity.principal_for(self._collector_user_id, project_id)
        if not any(
            binding.project_id == project_id and "billing_collector" in binding.roles
            for binding in principal.project_bindings
        ):
            raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)

        try:
            path = "/os-simple-tenant-usage/" + quote(project_id, safe="")
            params = {
                "start": interval_start.strftime("%Y-%m-%dT%H:%M:%S"),
                "end": interval_end.strftime("%Y-%m-%dT%H:%M:%S"),
                "detailed": "1",
                "limit": NOVA_USAGE_PAGE_LIMIT,
            }
            rows: list[object] = []
            seen_ids: set[str] = set()
            marker = None
            pagination_stalled = False
            for _ in range(NOVA_USAGE_MAX_PAGES):
                page_params = dict(params)
                if marker is not None:
                    page_params["marker"] = marker
                response = self._connection.compute.get(
                    path, params=page_params, raise_exc=True
                )
                page_rows = _server_usages(response.json(), project_id)
                if not page_rows:
                    break

                page_ids: list[str] = []
                for row in page_rows:
                    if not isinstance(row, Mapping) or not isinstance(row.get("instance_id"), str):
                        raise ValueError("Nova server usage entry is malformed")
                    instance_id = row["instance_id"]
                    if instance_id in seen_ids or instance_id in page_ids:
                        pagination_stalled = True
                        break
                    page_ids.append(instance_id)
                if pagination_stalled:
                    break

                rows.extend(page_rows)
                seen_ids.update(page_ids)
                if len(page_rows) < NOVA_USAGE_PAGE_LIMIT:
                    break
                marker = page_ids[-1]
            else:
                pagination_stalled = True

            samples = samples_from_nova_usage(
                {"tenant_usage": {"tenant_id": project_id, "server_usages": rows}},
                project_id,
                interval_start,
                interval_end,
            )
        except Exception:
            # Provider messages may contain request URLs or authentication data.
            raise BillingError(BillingErrorCode.USAGE_SOURCE_UNAVAILABLE) from None

        incomplete_reasons = [
            "nova_active_interval_unverified",
            "nova_pagination_unverified",
            "nova_source_timestamp_unavailable",
        ]
        if pagination_stalled:
            incomplete_reasons.append("nova_pagination_stalled")
        return UsageBatch(
            project_id=project_id,
            interval_start=interval_start,
            interval_end=interval_end,
            source=SOURCE,
            provenance=UsageProvenance.OBSERVED,
            complete=False,
            samples=samples,
            collected_at=self._clock(),
            incomplete_reasons=tuple(incomplete_reasons),
        )


def _valid_project_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 255
        and "\n" not in value
        and "\r" not in value
    )


def _is_utc(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def _server_usages(payload: object, project_id: str) -> list[object]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("tenant_usage"), Mapping):
        raise ValueError("Nova usage response has no tenant_usage object")
    tenant_usage = payload["tenant_usage"]
    if tenant_usage.get("tenant_id") != project_id:
        raise ValueError("Nova usage response project does not match request")
    rows = tenant_usage.get("server_usages")
    if not isinstance(rows, list):
        raise ValueError("Nova usage response has no server usage list")
    return rows
