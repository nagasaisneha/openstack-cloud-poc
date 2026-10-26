"""Translate Nova's project usage API into explicitly attributed raw samples.

Nova simple-tenant usage is allocation-time accounting (vCPU/RAM/local disk
hours), not guest CPU utilization, network traffic, or billing-grade telemetry.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .usage import UsageProvenance, UsageSample


SOURCE = "openstack:nova.os-simple-tenant-usage"


def samples_from_nova_usage(
    payload: Mapping[str, Any], project_id: str, interval_start: datetime,
    interval_end: datetime,
) -> tuple[UsageSample, ...]:
    """Convert per-instance Nova allocation-hours without normalization."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("tenant_usage"), Mapping):
        raise ValueError("Nova usage response has no tenant_usage object")
    usage = payload["tenant_usage"]
    if usage.get("tenant_id") != project_id:
        raise ValueError("Nova usage response project does not match request")
    rows = usage.get("server_usages")
    if not isinstance(rows, list):
        raise ValueError("Nova usage response has no server usage list")

    result: list[UsageSample] = []
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("instance_id"), str):
            raise ValueError("Nova server usage entry is malformed")
        hours = _decimal(row.get("hours"), "hours")
        if hours < 0:
            raise ValueError("Nova server usage hours cannot be negative")
        for field, metric, unit in (
            ("vcpus", "compute.vcpu_hours", "vCPU-hour"),
            ("memory_mb", "compute.memory_mb_hours", "MB-hour"),
            ("local_gb", "compute.local_disk_gb_hours", "GB-hour"),
        ):
            quantity = hours * _decimal(row.get(field), field)
            result.append(UsageSample(
                project_id=project_id,
                resource_id=row["instance_id"],
                metric=metric,
                quantity=quantity,
                unit=unit,
                interval_start=interval_start,
                interval_end=interval_end,
                source=SOURCE,
                provenance=UsageProvenance.OBSERVED,
            ))
    return tuple(result)


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Nova usage field {field} is missing or invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Nova usage field {field} is invalid") from None
    if not number.is_finite() or number < 0:
        raise ValueError(f"Nova usage field {field} is invalid")
    return number
