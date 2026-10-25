"""Project-scoped usage samples with explicit source and provenance.

This module does not discover meters, contact OpenStack, normalize units, or
calculate prices. ``FixtureUsageSource`` is an offline test/POC source only.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Protocol

from .adapter import FixtureManagementAdapter
from .identity import AuthenticationResult


class UsageCollectionErrorCode(str, Enum):
    AUTHORIZATION_DENIED = "authorization_denied"


class UsageCollectionError(PermissionError):
    """Safe usage collection denial; contains no submitted identifiers."""

    def __init__(self, code: UsageCollectionErrorCode) -> None:
        self.code = code
        super().__init__("Usage data is unavailable for this request.")


class UsageProvenance(str, Enum):
    OBSERVED = "observed"
    SIMULATED = "simulated"


@dataclass(frozen=True)
class UsageSample:
    """One raw meter value; units and time bounds are preserved verbatim."""

    project_id: str
    resource_id: str
    metric: str
    quantity: Decimal
    unit: str
    interval_start: datetime
    interval_end: datetime
    source: str
    provenance: UsageProvenance

    def __post_init__(self) -> None:
        for field_name in ("project_id", "resource_id", "metric", "unit", "source"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
                raise ValueError(f"{field_name} must be a non-empty single-line string")
        if not isinstance(self.quantity, Decimal):
            raise TypeError("quantity must be Decimal; implicit numeric conversion is not allowed")
        if not self.quantity.is_finite() or self.quantity < 0:
            raise ValueError("quantity must be finite and non-negative")
        if not isinstance(self.provenance, UsageProvenance):
            raise TypeError("provenance must be a UsageProvenance")
        if self.provenance is UsageProvenance.SIMULATED and not self.source.startswith("fixture:"):
            raise ValueError("simulated usage must identify fixture provenance")
        if not _is_utc(self.interval_start) or not _is_utc(self.interval_end):
            raise ValueError("usage interval timestamps must be timezone-aware UTC")
        if self.interval_end <= self.interval_start:
            raise ValueError("usage interval end must be after its start")


class UsageSource(Protocol):
    """Low-level source contract; callers must authorize project scope first."""

    def collect(
        self,
        project_id: str,
        interval_start: datetime,
        interval_end: datetime,
    ) -> tuple[UsageSample, ...]:
        """Return complete samples for the requested project and UTC window."""


class FixtureUsageSource:
    """Deterministic injected usage data; never contacts a cloud or meter API."""

    def __init__(self, samples: tuple[UsageSample, ...] = ()) -> None:
        if not isinstance(samples, tuple) or any(not isinstance(item, UsageSample) for item in samples):
            raise TypeError("samples must be a tuple of UsageSample values")
        if any(item.provenance is not UsageProvenance.SIMULATED for item in samples):
            raise ValueError("fixture usage sources accept only simulated samples")
        keys = {
            (item.project_id, item.resource_id, item.metric, item.interval_start, item.interval_end)
            for item in samples
        }
        if len(keys) != len(samples):
            raise ValueError("duplicate project/resource/metric/interval samples are not allowed")
        self._samples = tuple(sorted(samples, key=_sort_key))

    def collect(
        self,
        project_id: str,
        interval_start: datetime,
        interval_end: datetime,
    ) -> tuple[UsageSample, ...]:
        """Return only full samples contained in the exact project/window.

        Partial overlaps are omitted, not prorated. An empty result means no
        matching fixture sample, not that a real meter is unavailable.
        """
        if not isinstance(project_id, str) or not project_id.strip() or "\n" in project_id or "\r" in project_id:
            raise ValueError("project_id must be a non-empty single-line string")
        if not _is_utc(interval_start) or not _is_utc(interval_end):
            raise ValueError("query interval timestamps must be timezone-aware UTC")
        if interval_end <= interval_start:
            raise ValueError("query interval end must be after its start")
        return tuple(
            sample
            for sample in self._samples
            if sample.project_id == project_id
            and sample.interval_start >= interval_start
            and sample.interval_end <= interval_end
        )


class FixtureUsageCollector:
    """Authorize ``usage.read`` before accessing the fixture source."""

    def __init__(
        self,
        source: UsageSource,
        adapter: FixtureManagementAdapter | None = None,
    ) -> None:
        if not callable(getattr(source, "collect", None)):
            raise TypeError("source must implement collect")
        self._source = source
        self._adapter = adapter or FixtureManagementAdapter()

    def collect(
        self,
        authentication: AuthenticationResult,
        project_id: str,
        interval_start: datetime,
        interval_end: datetime,
    ) -> tuple[UsageSample, ...]:
        if not isinstance(authentication, AuthenticationResult):
            raise UsageCollectionError(UsageCollectionErrorCode.AUTHORIZATION_DENIED)
        decision = self._adapter.authorize(authentication, project_id, "usage.read")
        if not decision.allowed:
            raise UsageCollectionError(UsageCollectionErrorCode.AUTHORIZATION_DENIED)
        return self._source.collect(project_id, interval_start, interval_end)


def _is_utc(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def _sort_key(sample: UsageSample) -> tuple[datetime, str, str, str]:
    return (sample.interval_start.astimezone(timezone.utc), sample.project_id, sample.resource_id, sample.metric)
