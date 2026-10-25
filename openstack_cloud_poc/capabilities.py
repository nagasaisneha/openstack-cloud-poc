"""Evidence-only capability status records and deterministic formatting.

This module reports supplied observations only. It performs no discovery and
does not infer freshness, service availability, or remote state.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .catalog import CatalogRecord, VerificationStatus
from .identity import AuthenticationResult, AuthorizationResult


@dataclass(frozen=True)
class CapabilityStatusRecord:
    """One capability claim with explicit provenance and optional timestamp."""

    capability: str
    status: VerificationStatus
    source: str
    observed_at: datetime | None = None
    evidence: str | None = None

    def __post_init__(self) -> None:
        _validate_text("capability", self.capability)
        if not isinstance(self.status, VerificationStatus):
            raise TypeError("status must be a VerificationStatus")
        _validate_text("source", self.source)
        if self.observed_at is not None and not isinstance(self.observed_at, datetime):
            raise TypeError("observed_at must be a datetime or None")
        if self.evidence is not None:
            _validate_text("evidence", self.evidence)
        if self.status in {VerificationStatus.AVAILABLE, VerificationStatus.UNAVAILABLE}:
            if self.evidence is None:
                raise ValueError(f"{self.status.value} status requires evidence")

    @classmethod
    def from_catalog_record(
        cls,
        capability: str,
        record: CatalogRecord,
        observed_at: datetime | None = None,
    ) -> "CapabilityStatusRecord":
        """Preserve catalog provenance/status without implying a new observation."""
        return cls(
            capability=capability,
            status=record.status,
            source=record.source,
            observed_at=observed_at,
            evidence=record.evidence,
        )

    @classmethod
    def from_identity_result(
        cls,
        capability: str,
        result: AuthenticationResult | AuthorizationResult,
        source: str,
        observed_at: datetime | None = None,
    ) -> "CapabilityStatusRecord":
        """Represent fixture identity/authorization output as simulated evidence."""
        if result.status != "simulated":
            raise ValueError("identity result status is not recognized by this fixture reporter")
        return cls(
            capability=capability,
            status=VerificationStatus.SIMULATED,
            source=source,
            observed_at=observed_at,
            evidence="Fixture identity result; this does not verify Keystone.",
        )


def format_capabilities(records: Iterable[CapabilityStatusRecord]) -> str:
    """Format supplied status records in stable capability-name order."""
    normalized = tuple(records)
    if any(not isinstance(record, CapabilityStatusRecord) for record in normalized):
        raise TypeError("records must contain CapabilityStatusRecord values")
    if len({record.capability for record in normalized}) != len(normalized):
        raise ValueError("capability names must be unique")

    lines = []
    for record in sorted(normalized, key=lambda item: item.capability):
        observed = record.observed_at.isoformat() if record.observed_at is not None else "unknown"
        evidence = record.evidence if record.evidence is not None else "unknown"
        lines.append(
            f"{record.capability}: {record.status.value} | source={record.source} "
            f"| observed_at={observed} | evidence={evidence}"
        )
    return "\n".join(lines)


def _validate_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if any(character in value for character in "\r\n"):
        raise ValueError(f"{field_name} must be a single line")
