"""Deterministic, non-billing summaries for locally supplied usage samples."""

import json
from datetime import datetime, timedelta
from typing import Iterable

from .usage import UsageProvenance, UsageSample


def format_fixture_usage_summary(
    samples: Iterable[UsageSample],
    project_id: str,
    interval_start: datetime,
    interval_end: datetime,
) -> str:
    """Render raw fixture samples without aggregation, prices, or invoice claims."""
    normalized = tuple(samples)
    if any(not isinstance(sample, UsageSample) for sample in normalized):
        raise TypeError("samples must contain UsageSample values")
    _validate_window(project_id, interval_start, interval_end)
    if any(
        sample.project_id != project_id
        or sample.interval_start < interval_start
        or sample.interval_end > interval_end
        or sample.provenance is not UsageProvenance.SIMULATED
        for sample in normalized
    ):
        raise ValueError("summary samples must be simulated and within the requested project and interval")
    keys = {
        (item.project_id, item.resource_id, item.metric, item.interval_start, item.interval_end)
        for item in normalized
    }
    if len(keys) != len(normalized):
        raise ValueError("duplicate usage samples cannot be summarized")

    ordered = sorted(
        normalized,
        key=lambda item: (
            item.interval_start,
            item.project_id,
            item.resource_id,
            item.metric,
        ),
    )
    report = {
        "report_type": "usage_summary",
        "report_status": "simulated",
        "project_id": project_id,
        "interval_start": interval_start.isoformat(),
        "interval_end": interval_end.isoformat(),
        "data_state": "samples_present" if ordered else "no_matching_fixture_samples",
        "billing_grade": False,
        "charge": None,
        "items": [
            {
                "resource_id": item.resource_id,
                "metric": item.metric,
                "quantity": str(item.quantity),
                "unit": item.unit,
                "interval_start": item.interval_start.isoformat(),
                "interval_end": item.interval_end.isoformat(),
                "source": item.source,
                "provenance": item.provenance.value,
            }
            for item in ordered
        ],
        "limitations": [
            "Fixture samples are illustrative and are not measured OpenStack usage.",
            "Values are listed without aggregation, normalization, imputation, or pricing.",
            "This summary is not an invoice or a charge.",
        ],
    }
    return json.dumps(report, sort_keys=True, separators=(",", ":"))


def _validate_window(project_id: str, start: datetime, end: datetime) -> None:
    if not isinstance(project_id, str) or not project_id.strip() or "\n" in project_id or "\r" in project_id:
        raise ValueError("project_id must be a non-empty single-line string")
    if not _is_utc(start) or not _is_utc(end) or end <= start:
        raise ValueError("summary window must be a non-empty UTC interval")


def _is_utc(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )
