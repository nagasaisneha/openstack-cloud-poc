"""Manager-independent catalog records and provider contract.

Unit conventions: ``vcpus`` is a count, ``*_mib`` is mebibytes (MiB),
and ``*_gib`` is gibibytes (GiB). Values are never converted implicitly.
Fixture records must use ``simulated`` status. ``available`` and
``unavailable`` require evidence; lack of evidence should remain
``unverified`` or be represented by a missing record.
"""

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol


class CatalogCategory(Enum):
    FLAVOR = "flavor"
    IMAGE = "image"
    NETWORK = "network"
    STORAGE = "storage"


class VerificationStatus(Enum):
    AVAILABLE = "available"
    SIMULATED = "simulated"
    UNAVAILABLE = "unavailable"
    UNVERIFIED = "unverified"


CatalogValue = str | int | bool


@dataclass(frozen=True)
class CatalogRecord:
    """One catalog datum with explicit category, provenance, and status."""

    category: CatalogCategory
    identifier: str
    source: str
    status: VerificationStatus
    attributes: Mapping[str, CatalogValue] = field(default_factory=dict)
    evidence: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.category, CatalogCategory):
            raise TypeError("category must be a CatalogCategory")
        if not isinstance(self.status, VerificationStatus):
            raise TypeError("status must be a VerificationStatus")
        if not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValueError("identifier must be a non-empty stable string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source/provenance is required")
        if self.status in {VerificationStatus.AVAILABLE, VerificationStatus.UNAVAILABLE}:
            if not isinstance(self.evidence, str) or not self.evidence.strip():
                raise ValueError(f"{self.status.value} status requires evidence")
        if self.source.lower().startswith("fixture:") and self.status is not VerificationStatus.SIMULATED:
            raise ValueError("fixture-sourced records must have simulated status")
        if self.status is VerificationStatus.SIMULATED and not self.source.lower().startswith("fixture:"):
            raise ValueError("simulated records must identify fixture provenance")

        if not isinstance(self.attributes, Mapping):
            raise TypeError("attributes must be a mapping")
        normalized: dict[str, CatalogValue] = {}
        for key, value in self.attributes.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("attribute names must be non-empty strings")
            if type(value) not in {str, int, bool}:
                raise TypeError(f"attribute {key!r} must be a string, integer, or boolean")
            if key == "vcpus" or key.endswith(("_mib", "_gib", "_count")):
                if type(value) is not int or value < 0:
                    raise ValueError(f"unit-bearing attribute {key!r} must be a non-negative integer")
            normalized[key] = value
        object.__setattr__(self, "attributes", MappingProxyType(normalized))


@dataclass(frozen=True)
class Catalog:
    """Immutable, deterministically ordered catalog snapshot.

    Missing records and categories remain missing; callers must not infer
    that an unrepresented resource is unavailable.
    """

    records: tuple[CatalogRecord, ...] = ()

    def __post_init__(self) -> None:
        records = tuple(self.records)
        if any(not isinstance(record, CatalogRecord) for record in records):
            raise TypeError("records must contain CatalogRecord values")
        keys = [(record.category, record.identifier) for record in records]
        if len(keys) != len(set(keys)):
            raise ValueError("catalog contains duplicate category/identifier pairs")
        ordered = tuple(sorted(records, key=lambda record: (record.category.value, record.identifier)))
        object.__setattr__(self, "records", ordered)

    def records_for(self, category: CatalogCategory) -> tuple[CatalogRecord, ...]:
        """Return records for a category, or an empty tuple when missing."""
        return tuple(record for record in self.records if record.category is category)

    def get(self, category: CatalogCategory, identifier: str) -> CatalogRecord | None:
        """Return a matching record, or ``None`` without inventing a status."""
        return next(
            (record for record in self.records if record.category is category and record.identifier == identifier),
            None,
        )

    @property
    def missing_categories(self) -> tuple[CatalogCategory, ...]:
        """Categories with no records; this does not mean unavailable."""
        represented = {record.category for record in self.records}
        return tuple(category for category in CatalogCategory if category not in represented)


class CatalogProvider(Protocol):
    """Provider boundary independent of OpenStack and VM-manager clients."""

    def get_catalog(self) -> Catalog:
        """Return a catalog with source and verification status per record."""
