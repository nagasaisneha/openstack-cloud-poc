"""Offline tests for the manager-independent simulated catalog."""

import unittest

from openstack_cloud_poc.catalog import (
    Catalog,
    CatalogCategory,
    CatalogRecord,
    VerificationStatus,
)
from openstack_cloud_poc.catalog_fixtures import FixtureCatalogProvider


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = FixtureCatalogProvider().get_catalog()

    def test_fixture_covers_all_four_categories(self):
        self.assertEqual(
            {record.category for record in self.catalog.records},
            set(CatalogCategory),
        )

    def test_fixture_output_is_deterministic(self):
        provider = FixtureCatalogProvider()
        self.assertEqual(provider.get_catalog(), provider.get_catalog())
        self.assertEqual(
            tuple(record.identifier for record in self.catalog.records),
            tuple(sorted(record.identifier for record in self.catalog.records)),
        )

    def test_each_fixture_has_required_metadata_and_simulated_provenance(self):
        for record in self.catalog.records:
            with self.subTest(identifier=record.identifier):
                self.assertTrue(record.identifier)
                self.assertTrue(record.source.startswith("fixture:"))
                self.assertEqual(record.status, VerificationStatus.SIMULATED)

    def test_fixture_records_never_claim_real_availability(self):
        self.assertNotIn(
            VerificationStatus.AVAILABLE,
            {record.status for record in self.catalog.records},
        )
        self.assertNotIn(
            VerificationStatus.UNAVAILABLE,
            {record.status for record in self.catalog.records},
        )

    def test_unit_fields_are_explicit_and_unconverted(self):
        flavor = self.catalog.get(CatalogCategory.FLAVOR, "fixture:flavor.small")
        storage = self.catalog.get(CatalogCategory.STORAGE, "fixture:storage.block-demo")

        self.assertEqual(flavor.attributes["vcpus"], 2)
        self.assertEqual(flavor.attributes["memory_mib"], 2048)
        self.assertEqual(flavor.attributes["disk_gib"], 20)
        self.assertEqual(storage.attributes["size_gib"], 10)
        self.assertNotIn("memory", flavor.attributes)
        self.assertNotIn("size", storage.attributes)

    def test_unverified_remote_record_does_not_claim_unavailability(self):
        record = CatalogRecord(
            category=CatalogCategory.IMAGE,
            identifier="remote:image-status-unknown",
            source="T07 local-only inventory; remote APIs not queried",
            status=VerificationStatus.UNVERIFIED,
        )

        self.assertEqual(record.status, VerificationStatus.UNVERIFIED)
        self.assertIsNone(record.evidence)

    def test_available_and_unavailable_statuses_require_evidence(self):
        for status in (VerificationStatus.AVAILABLE, VerificationStatus.UNAVAILABLE):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    CatalogRecord(
                        category=CatalogCategory.NETWORK,
                        identifier="remote:network-1",
                        source="approved inventory evidence",
                        status=status,
                    )

                evidenced = CatalogRecord(
                    category=CatalogCategory.NETWORK,
                    identifier=f"test:{status.value}",
                    source="test:synthetic record for schema validation",
                    status=status,
                    evidence="synthetic test evidence; no cloud observation",
                )
                self.assertEqual(evidenced.status, status)

    def test_missing_records_remain_missing_not_unavailable(self):
        empty = Catalog()

        self.assertEqual(empty.records_for(CatalogCategory.FLAVOR), ())
        self.assertIsNone(empty.get(CatalogCategory.FLAVOR, "not-present"))
        self.assertEqual(set(empty.missing_categories), set(CatalogCategory))

    def test_partial_catalog_does_not_infer_missing_categories_unavailable(self):
        record = CatalogRecord(
            category=CatalogCategory.FLAVOR,
            identifier="fixture:flavor.partial",
            source="fixture:T08 partial test data",
            status=VerificationStatus.SIMULATED,
            attributes={"vcpus": 1},
        )
        partial = Catalog((record,))

        self.assertEqual(partial.records_for(CatalogCategory.IMAGE), ())
        self.assertIn(CatalogCategory.IMAGE, partial.missing_categories)
        self.assertEqual(record.status, VerificationStatus.SIMULATED)

    def test_unit_fields_require_non_negative_integer_values(self):
        with self.assertRaises(ValueError):
            CatalogRecord(
                category=CatalogCategory.FLAVOR,
                identifier="fixture:flavor.invalid",
                source="fixture:T08 invalid unit test",
                status=VerificationStatus.SIMULATED,
                attributes={"memory_mib": "2 GiB"},
            )


if __name__ == "__main__":
    unittest.main()
