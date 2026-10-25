"""Offline tests for explicit, project-scoped fixture usage samples."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import socket
import unittest
from unittest.mock import patch

from openstack_cloud_poc.adapter import FixtureManagementAdapter
from openstack_cloud_poc.identity import FixtureIdentityProvider
from openstack_cloud_poc.usage import (
    FixtureUsageCollector,
    FixtureUsageSource,
    UsageCollectionError,
    UsageCollectionErrorCode,
    UsageProvenance,
    UsageSample,
)


START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


def sample(
    project_id="fixture:project-alpha",
    resource_id="fixture-instance-000001",
    metric="compute.vcpu_hours",
    quantity=Decimal("2.0"),
    unit="vCPU-hour",
    start=START,
    end=END,
    source="fixture:T20 illustrative usage; not measured",
    provenance=UsageProvenance.SIMULATED,
):
    return UsageSample(
        project_id, resource_id, metric, quantity, unit, start, end, source, provenance
    )


class FixtureUsageSourceTests(unittest.TestCase):
    def test_collection_is_repeatable_and_preserves_source_units_and_quantity(self):
        expected = sample()
        source = FixtureUsageSource((expected,))

        first = source.collect("fixture:project-alpha", START, END)
        second = source.collect("fixture:project-alpha", START, END)

        self.assertEqual(first, (expected,))
        self.assertEqual(second, first)
        self.assertEqual(first[0].quantity, Decimal("2.0"))
        self.assertEqual(first[0].unit, "vCPU-hour")
        self.assertEqual(first[0].source, "fixture:T20 illustrative usage; not measured")
        self.assertEqual(first[0].provenance, UsageProvenance.SIMULATED)

    def test_project_scope_does_not_return_other_project_samples(self):
        source = FixtureUsageSource((
            sample(),
            sample(project_id="fixture:project-beta", resource_id="fixture-instance-000002"),
        ))

        self.assertEqual(
            source.collect("fixture:project-alpha", START, END),
            (sample(),),
        )
        self.assertEqual(source.collect("fixture:project-gamma", START, END), ())

    def test_only_fully_contained_samples_are_returned_without_proration(self):
        inside = sample()
        partial = sample(
            resource_id="fixture-instance-000002",
            start=START - timedelta(minutes=1),
        )
        source = FixtureUsageSource((inside, partial))

        self.assertEqual(source.collect("fixture:project-alpha", START, END), (inside,))

    def test_fixture_source_rejects_observed_samples(self):
        with self.assertRaises(ValueError):
            FixtureUsageSource((sample(source="ceilometer:meter", provenance=UsageProvenance.OBSERVED),))

    def test_duplicate_project_resource_metric_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            FixtureUsageSource((sample(), sample(quantity=Decimal("3.0"))))

    def test_invalid_sample_values_and_intervals_are_rejected(self):
        invalid = (
            ({"quantity": 2.0}, TypeError),
            ({"quantity": Decimal("-1")}, ValueError),
            ({"quantity": Decimal("NaN")}, ValueError),
            ({"start": datetime(2026, 10, 1)}, ValueError),
            ({"end": START}, ValueError),
            ({"source": "meter:unknown", "provenance": UsageProvenance.SIMULATED}, ValueError),
            ({"unit": "vCPU-hour\nsecret"}, ValueError),
        )
        for overrides, error in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(error):
                    sample(**overrides)

    def test_invalid_query_scope_and_window_are_rejected(self):
        source = FixtureUsageSource((sample(),))
        invalid_queries = (
            ("", START, END),
            ("fixture:project-alpha", datetime(2026, 10, 1), END),
            ("fixture:project-alpha", END, START),
        )
        for project_id, start, end in invalid_queries:
            with self.subTest(project_id=project_id, start=start, end=end):
                with self.assertRaises(ValueError):
                    source.collect(project_id, start, end)

    def test_fixture_source_makes_no_network_connection(self):
        source = FixtureUsageSource((sample(),))
        with patch.object(socket, "create_connection", side_effect=AssertionError("network call")) as connect:
            result = source.collect("fixture:project-alpha", START, END)
            connect.assert_not_called()
        self.assertEqual(len(result), 1)

    def test_collection_requires_usage_viewer_role_and_matching_project(self):
        source = FixtureUsageSource((sample(),))
        collector = FixtureUsageCollector(source, FixtureManagementAdapter())
        identities = FixtureIdentityProvider()
        usage_viewer = identities.authenticate("fixture:dana")
        catalog_viewer = identities.authenticate("fixture:alice")

        self.assertEqual(
            collector.collect(usage_viewer, "fixture:project-alpha", START, END),
            (sample(),),
        )
        for authentication, project in (
            (catalog_viewer, "fixture:project-alpha"),
            (usage_viewer, "fixture:project-beta"),
        ):
            with self.subTest(authentication=authentication.identity, project=project):
                with self.assertRaises(UsageCollectionError) as caught:
                    collector.collect(authentication, project, START, END)
                self.assertEqual(caught.exception.code, UsageCollectionErrorCode.AUTHORIZATION_DENIED)

    def test_denied_collection_does_not_call_the_source(self):
        class SpySource:
            called = False

            def collect(self, project_id, interval_start, interval_end):
                self.called = True
                return ()

        source = SpySource()
        collector = FixtureUsageCollector(source)
        unauthenticated = FixtureIdentityProvider().authenticate("fixture:unknown")

        with self.assertRaises(UsageCollectionError):
            collector.collect(unauthenticated, "fixture:project-alpha", START, END)
        self.assertFalse(source.called)

    def test_invalid_authentication_is_denied_without_source_access(self):
        class SpySource:
            called = False

            def collect(self, project_id, interval_start, interval_end):
                self.called = True
                return ()

        source = SpySource()
        collector = FixtureUsageCollector(source)

        with self.assertRaises(UsageCollectionError) as caught:
            collector.collect(None, "fixture:project-alpha", START, END)
        self.assertEqual(caught.exception.code, UsageCollectionErrorCode.AUTHORIZATION_DENIED)
        self.assertFalse(source.called)


if __name__ == "__main__":
    unittest.main()
