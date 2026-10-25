"""Offline checks for source-attributed fixture usage summaries."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest

from openstack_cloud_poc.usage import UsageProvenance, UsageSample
from openstack_cloud_poc.usage_reporting import format_fixture_usage_summary


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


class FixtureUsageReportingTests(unittest.TestCase):
    def test_summary_is_deterministic_source_attributed_and_not_priced(self):
        first = sample()
        second = sample(
            resource_id="fixture-instance-000002",
            metric="compute.memory_mib_hours",
            quantity=Decimal("2048"),
            unit="MiB-hour",
        )

        rendered = format_fixture_usage_summary((second, first), "fixture:project-alpha", START, END)
        report = json.loads(rendered)

        self.assertEqual(rendered, format_fixture_usage_summary((first, second), "fixture:project-alpha", START, END))
        self.assertEqual(report["report_type"], "usage_summary")
        self.assertEqual(report["report_status"], "simulated")
        self.assertEqual(report["data_state"], "samples_present")
        self.assertFalse(report["billing_grade"])
        self.assertIsNone(report["charge"])
        self.assertEqual([item["metric"] for item in report["items"]], [
            "compute.vcpu_hours", "compute.memory_mib_hours",
        ])
        self.assertEqual(report["items"][0]["source"], first.source)
        self.assertEqual(report["items"][0]["unit"], "vCPU-hour")
        self.assertEqual(report["items"][0]["provenance"], "simulated")
        self.assertIn("not an invoice", " ".join(report["limitations"]))
        self.assertNotIn("currency", report)
        self.assertNotIn("price", report)

    def test_empty_result_is_an_empty_simulated_report_not_zero_usage_claim(self):
        report = json.loads(format_fixture_usage_summary((), "fixture:project-alpha", START, END))

        self.assertEqual(report["report_status"], "simulated")
        self.assertEqual(report["data_state"], "no_matching_fixture_samples")
        self.assertEqual(report["items"], [])
        self.assertIsNone(report["charge"])

    def test_real_or_cross_project_samples_cannot_be_presented_as_fixture_summary(self):
        real_sample = UsageSample(
            "fixture:project-alpha",
            "server-id-123",
            "compute.vcpu_hours",
            Decimal("2"),
            "vCPU-hour",
            START,
            END,
            "ceilometer:sample",
            UsageProvenance.OBSERVED,
        )
        with self.assertRaises(ValueError):
            format_fixture_usage_summary((real_sample,), "fixture:project-alpha", START, END)
        with self.assertRaises(ValueError):
            format_fixture_usage_summary((sample(),), "fixture:project-beta", START, END)

    def test_sample_outside_report_window_and_invalid_window_are_rejected(self):
        with self.assertRaises(ValueError):
            format_fixture_usage_summary((sample(),), "fixture:project-alpha", START, END - timedelta(minutes=1))
        with self.assertRaises(ValueError):
            format_fixture_usage_summary((), "fixture:project-alpha", END, START)

    def test_non_usage_objects_are_rejected(self):
        with self.assertRaises(TypeError):
            format_fixture_usage_summary((object(),), "fixture:project-alpha", START, END)

    def test_duplicate_samples_are_rejected(self):
        with self.assertRaises(ValueError):
            format_fixture_usage_summary(
                (sample(), sample(quantity=Decimal("3"))),
                "fixture:project-alpha",
                START,
                END,
            )


if __name__ == "__main__":
    unittest.main()
