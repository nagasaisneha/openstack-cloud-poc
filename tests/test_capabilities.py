"""Offline tests for evidence-only capability status reporting."""

import unittest
from datetime import datetime, timezone

from openstack_cloud_poc.capabilities import (
    CapabilityStatusRecord,
    format_capabilities,
)
from openstack_cloud_poc.catalog import CatalogCategory, VerificationStatus
from openstack_cloud_poc.catalog_fixtures import FixtureCatalogProvider
from openstack_cloud_poc.identity import FixtureIdentityProvider


class CapabilityStatusTests(unittest.TestCase):
    def test_all_status_values_format_with_provenance(self):
        records = (
            CapabilityStatusRecord(
                "synthetic.available", VerificationStatus.AVAILABLE,
                "test:synthetic evidence", evidence="Synthetic test evidence only.",
            ),
            CapabilityStatusRecord(
                "fixture.flow", VerificationStatus.SIMULATED,
                "fixture:test identity", evidence="Synthetic fixture output.",
            ),
            CapabilityStatusRecord(
                "synthetic.unavailable", VerificationStatus.UNAVAILABLE,
                "test:synthetic evidence", evidence="Synthetic absence evidence only.",
            ),
            CapabilityStatusRecord(
                "remote.unknown", VerificationStatus.UNVERIFIED,
                "test:no remote observation",
            ),
        )

        rendered = format_capabilities(records)
        for status in VerificationStatus:
            self.assertIn(status.value, rendered)
        self.assertIn("source=test:synthetic evidence", rendered)
        self.assertIn("synthetic.available: available", rendered)
        self.assertIn("synthetic.unavailable: unavailable", rendered)
        self.assertIn("remote.unknown: unverified", rendered)
        self.assertIn("Synthetic test evidence only.", rendered)
        self.assertIn("Synthetic absence evidence only.", rendered)
        self.assertNotIn("verified real", rendered.lower())

    def test_observation_timestamp_and_unknown_freshness(self):
        observed_at = datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)
        known = CapabilityStatusRecord(
            "known.time", VerificationStatus.UNVERIFIED, "test:timestamp", observed_at
        )
        unknown = CapabilityStatusRecord(
            "unknown.time", VerificationStatus.UNVERIFIED, "test:no timestamp"
        )

        rendered = format_capabilities((known, unknown))
        self.assertIn("observed_at=2026-10-18T12:30:00+00:00", rendered)
        self.assertIn("observed_at=unknown", rendered)

    def test_formatting_is_deterministic_and_sorted(self):
        records = (
            CapabilityStatusRecord("zeta", VerificationStatus.UNVERIFIED, "test:z"),
            CapabilityStatusRecord("alpha", VerificationStatus.UNVERIFIED, "test:a"),
        )
        self.assertEqual(format_capabilities(records), format_capabilities(records))
        self.assertLess(format_capabilities(records).index("alpha"), format_capabilities(records).index("zeta"))

    def test_fixture_catalog_provenance_and_status_are_preserved(self):
        catalog = FixtureCatalogProvider().get_catalog()
        record = catalog.get(CatalogCategory.FLAVOR, "fixture:flavor.small")
        result = CapabilityStatusRecord.from_catalog_record("catalog.flavors", record)

        self.assertEqual(result.status, VerificationStatus.SIMULATED)
        self.assertEqual(result.source, record.source)
        self.assertIn("catalog.flavors: simulated", format_capabilities((result,)))

    def test_fixture_identity_result_remains_simulated(self):
        identity_result = FixtureIdentityProvider().authenticate("fixture:alice")
        result = CapabilityStatusRecord.from_identity_result(
            "identity.authentication", identity_result, "fixture:T06 identity provider"
        )

        rendered = format_capabilities((result,))
        self.assertEqual(result.status, VerificationStatus.SIMULATED)
        self.assertIn("identity.authentication: simulated", rendered)
        self.assertIn("does not verify Keystone", rendered)

    def test_failed_fixture_identity_does_not_expose_identity_reference(self):
        unknown_reference = "fixture:unknown-sensitive-label"
        identity_result = FixtureIdentityProvider().authenticate(unknown_reference)
        result = CapabilityStatusRecord.from_identity_result(
            "identity.authentication", identity_result, "fixture:T06 identity provider"
        )

        rendered = format_capabilities((result,))
        self.assertIn("identity.authentication: simulated", rendered)
        self.assertNotIn(unknown_reference, rendered)
        self.assertNotIn("identity_not_found", rendered)

    def test_missing_source_and_evidence_for_asserted_statuses_fail_safely(self):
        with self.assertRaises(ValueError):
            CapabilityStatusRecord("missing.source", VerificationStatus.UNVERIFIED, " ")
        for status in (VerificationStatus.AVAILABLE, VerificationStatus.UNAVAILABLE):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    CapabilityStatusRecord("asserted", status, "test:missing evidence")

    def test_error_and_missing_evidence_remain_unverified(self):
        error_case = CapabilityStatusRecord(
            "identity.remote", VerificationStatus.UNVERIFIED,
            "test:synthetic provider error",
        )

        rendered = format_capabilities((error_case,))
        self.assertIn("identity.remote: unverified", rendered)
        self.assertIn("evidence=unknown", rendered)
        self.assertNotIn("unavailable", rendered)

    def test_invalid_formatter_input_and_multiline_text_are_rejected(self):
        with self.assertRaises(TypeError):
            format_capabilities((object(),))
        with self.assertRaises(ValueError):
            CapabilityStatusRecord("unsafe\nclaim", VerificationStatus.UNVERIFIED, "test:source")
        with self.assertRaises(ValueError):
            CapabilityStatusRecord("safe", VerificationStatus.UNVERIFIED, "test:source\nclaim")


if __name__ == "__main__":
    unittest.main()
