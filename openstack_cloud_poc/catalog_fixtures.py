"""Deterministic illustrative catalog fixtures; no cloud capacity is observed."""

from .catalog import Catalog, CatalogCategory, CatalogRecord, VerificationStatus


_FIXTURE_SOURCE = "fixture:T08 illustrative sample; not observed cloud inventory"

_RECORDS = (
    CatalogRecord(
        category=CatalogCategory.FLAVOR,
        identifier="fixture:flavor.small",
        source=_FIXTURE_SOURCE,
        status=VerificationStatus.SIMULATED,
        attributes={"vcpus": 2, "memory_mib": 2048, "disk_gib": 20},
    ),
    CatalogRecord(
        category=CatalogCategory.IMAGE,
        identifier="fixture:image.linux-example",
        source=_FIXTURE_SOURCE,
        status=VerificationStatus.SIMULATED,
        attributes={"architecture": "x86_64", "disk_format": "qcow2", "virtual_size_gib": 3},
    ),
    CatalogRecord(
        category=CatalogCategory.NETWORK,
        identifier="fixture:network.private-demo",
        source=_FIXTURE_SOURCE,
        status=VerificationStatus.SIMULATED,
        attributes={"scope": "private-demo", "subnet_count": 1},
    ),
    CatalogRecord(
        category=CatalogCategory.STORAGE,
        identifier="fixture:storage.block-demo",
        source=_FIXTURE_SOURCE,
        status=VerificationStatus.SIMULATED,
        attributes={"storage_kind": "illustrative-block", "size_gib": 10},
    ),
)


class FixtureCatalogProvider:
    """Return the same simulated catalog on every call; never contacts a cloud."""

    def get_catalog(self) -> Catalog:
        return Catalog(_RECORDS)
