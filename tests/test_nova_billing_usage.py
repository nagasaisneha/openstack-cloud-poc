import csv
from datetime import datetime, timezone
from decimal import Decimal
import io
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from openstack_cloud_poc.billing import (
    BillingError,
    BillingErrorCode,
    BillingLedger,
    BillingPrincipal,
    BillingService,
    BillingStatus,
    ProjectRoleBinding,
    RateCard,
    RateRule,
)
from openstack_cloud_poc.nova_billing_usage import NovaBillingUsageSource
from openstack_cloud_poc.nova_billing_usage import NOVA_USAGE_PAGE_LIMIT
from openstack_cloud_poc.usage import UsageProvenance


START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 1, tzinfo=timezone.utc)


def nova_payload(project_id="project-a"):
    return {
        "tenant_usage": {
            "tenant_id": project_id,
            "server_usages": [{
                "instance_id": "server-1",
                "hours": "3",
                "vcpus": 2,
                "memory_mb": 1024,
                "local_gb": 1,
            }],
        },
    }


class NovaBillingUsageSourceTests(unittest.TestCase):
    def setUp(self):
        self.response = SimpleNamespace(json=Mock(return_value=nova_payload()))
        self.session = SimpleNamespace(
            auth=SimpleNamespace(
                invalidate=Mock(),
                get_access=Mock(return_value=SimpleNamespace(
                    user_id="collector-user",
                    project_id="project-a",
                    project_scoped=True,
                    system_scoped=False,
                    role_names=("billing_collector",),
                )),
            )
        )
        self.connection = SimpleNamespace(
            compute=SimpleNamespace(get=Mock(return_value=self.response)),
            session=self.session,
        )
        self.source = NovaBillingUsageSource(
            self.connection, "collector-user", clock=lambda: END
        )

    def test_collects_project_attributed_observed_batch_but_never_claims_complete(self):
        batch = self.source.collect("project-a", START, END)

        self.connection.compute.get.assert_called_once_with(
            "/os-simple-tenant-usage/project-a",
            params={
                "start": "2026-09-01T00:00:00",
                "end": "2026-10-01T00:00:00",
                "detailed": "1",
                "limit": NOVA_USAGE_PAGE_LIMIT,
            },
            raise_exc=True,
        )
        self.assertEqual(batch.project_id, "project-a")
        self.assertEqual(batch.source, "openstack:nova.os-simple-tenant-usage")
        self.assertIs(batch.provenance, UsageProvenance.OBSERVED)
        self.assertFalse(batch.complete)
        self.assertEqual(batch.collected_at, END)
        self.assertEqual(batch.incomplete_reasons, (
            "nova_active_interval_unverified",
            "nova_pagination_unverified",
            "nova_source_timestamp_unavailable",
        ))
        self.assertEqual(len(batch.samples), 3)
        self.assertTrue(all(sample.project_id == "project-a" for sample in batch.samples))
        self.session.auth.invalidate.assert_called_once_with()

    def test_collects_all_marker_pages_with_project_scope_and_gap_status(self):
        rows = [
            {
                "instance_id": f"server-{index}",
                "hours": "1",
                "vcpus": 1,
                "memory_mb": 512,
                "local_gb": 1,
            }
            for index in range(NOVA_USAGE_PAGE_LIMIT + 1)
        ]
        first_page = SimpleNamespace(json=Mock(return_value={
            "tenant_usage": {
                "tenant_id": "project-a",
                "server_usages": rows[:NOVA_USAGE_PAGE_LIMIT],
            }
        }))
        second_page = SimpleNamespace(json=Mock(return_value={
            "tenant_usage": {
                "tenant_id": "project-a",
                "server_usages": rows[NOVA_USAGE_PAGE_LIMIT:],
            }
        }))
        self.connection.compute.get.side_effect = [first_page, second_page]

        batch = self.source.collect("project-a", START, END)

        self.assertEqual(len(batch.samples), 3 * (NOVA_USAGE_PAGE_LIMIT + 1))
        calls = self.connection.compute.get.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertNotIn("marker", calls[0].kwargs["params"])
        self.assertEqual(calls[1].kwargs["params"]["marker"], f"server-{NOVA_USAGE_PAGE_LIMIT - 1}")
        self.assertIn("nova_pagination_unverified", batch.incomplete_reasons)
        self.assertNotIn("nova_pagination_stalled", batch.incomplete_reasons)

    def test_repeated_page_is_kept_provisional_and_reported_as_stalled(self):
        rows = [
            {
                "instance_id": f"server-{index}",
                "hours": "1",
                "vcpus": 1,
                "memory_mb": 512,
                "local_gb": 1,
            }
            for index in range(NOVA_USAGE_PAGE_LIMIT)
        ]
        self.response.json.return_value = {
            "tenant_usage": {"tenant_id": "project-a", "server_usages": rows}
        }

        batch = self.source.collect("project-a", START, END)

        self.assertEqual(len(batch.samples), 3 * NOVA_USAGE_PAGE_LIMIT)
        self.assertFalse(batch.complete)
        self.assertIn("nova_pagination_stalled", batch.incomplete_reasons)
        self.assertEqual(self.connection.compute.get.call_count, 2)

    def test_empty_nova_result_is_incomplete_not_zero_usage(self):
        self.response.json.return_value = {
            "tenant_usage": {"tenant_id": "project-a", "server_usages": []}
        }

        batch = self.source.collect("project-a", START, END)

        self.assertEqual(batch.samples, ())
        self.assertFalse(batch.complete)

    def test_provider_project_mismatch_is_sanitized(self):
        self.response.json.return_value = nova_payload("project-b")

        with self.assertRaises(BillingError) as raised:
            self.source.collect("project-a", START, END)

        self.assertEqual(raised.exception.code, BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)
        self.assertNotIn("project-b", str(raised.exception))

    def test_collector_scope_and_explicit_role_are_checked_before_nova(self):
        cases = (
            SimpleNamespace(
                user_id="collector-user", project_id="project-b", project_scoped=True,
                system_scoped=False, role_names=("billing_collector",),
            ),
            SimpleNamespace(
                user_id="collector-user", project_id="project-a", project_scoped=True,
                system_scoped=False, role_names=("reader",),
            ),
        )
        for access in cases:
            with self.subTest(access=access):
                self.session.auth.get_access.return_value = access
                with self.assertRaises(BillingError) as raised:
                    self.source.collect("project-a", START, END)
                self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)
        self.connection.compute.get.assert_not_called()

    def test_provider_failure_is_sanitized(self):
        self.connection.compute.get.side_effect = RuntimeError("Bearer token-secret")

        with self.assertRaises(BillingError) as raised:
            self.source.collect("project-a", START, END)

        self.assertEqual(raised.exception.code, BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)
        self.assertNotIn("token-secret", str(raised.exception))

    def test_invalid_scope_or_window_fails_before_provider_access(self):
        invalid_windows = (
            ("project-a", END, START),
            ("project-a", START.replace(tzinfo=None), END),
            ("project-a", START.replace(microsecond=1), END),
            ("project-a\nother", START, END),
        )
        for project_id, start, end in invalid_windows:
            with self.subTest(project_id=project_id, start=start, end=end):
                with self.assertRaises(BillingError) as raised:
                    self.source.collect(project_id, start, end)
                self.assertEqual(raised.exception.code, BillingErrorCode.INVALID_REQUEST)
        self.connection.compute.get.assert_not_called()

    def test_nova_data_can_only_create_a_provisional_project_statement(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            ledger = BillingLedger(Path(directory) / "billing.sqlite")
            try:
                ledger.add_rate_card(
                    RateCard(
                        "test-v1",
                        "USD",
                        2,
                        (
                            RateRule("compute.instance_vcpu_hours", "vCPU-hour", Decimal("0.10"), START),
                            RateRule("compute.instance_memory_mib_hours", "MiB-hour", Decimal("0.01"), START),
                            RateRule("compute.instance_local_disk_gib_hours", "GiB-hour", Decimal("0.01"), START),
                        ),
                    ),
                    "admin",
                    START,
                )
                service = BillingService(ledger, self.source, clock=lambda: END)
                collector = BillingPrincipal(
                    "collector",
                    True,
                    system_roles=frozenset({"billing_collector"}),
                )

                statement = service.generate_statement(
                    collector, "project-a", START, END, "test-v1"
                )

                self.assertEqual(statement.project_id, "project-a")
                self.assertIs(statement.provenance, UsageProvenance.OBSERVED)
                self.assertIs(statement.status, BillingStatus.PROVISIONAL)
                self.assertEqual(statement.usage_collected_at, END)
                self.assertEqual(statement.incomplete_reasons, (
                    "nova_active_interval_unverified",
                    "nova_pagination_unverified",
                    "nova_source_timestamp_unavailable",
                ))
                self.assertIsNone(statement.total)
                self.assertEqual(statement.lines[0].quantity, Decimal("6"))
                self.assertEqual(statement.lines[0].amount, Decimal("0.60"))

                viewer = BillingPrincipal(
                    "viewer",
                    True,
                    project_bindings=(ProjectRoleBinding("project-a", frozenset({"billing_viewer"})),),
                )
                saved = service.project_statement(viewer, "project-a", statement.statement_id)
                self.assertEqual(saved.usage_collected_at, END)
                csv_rows = list(csv.DictReader(io.StringIO(
                    service.project_statement_csv(viewer, "project-a", statement.statement_id).decode("utf-8")
                )))
                self.assertEqual(csv_rows[0]["schema_version"], "2")
                self.assertEqual(csv_rows[0]["usage_collected_at_utc"], END.isoformat())
                self.assertIn("nova_pagination_unverified", csv_rows[0]["incomplete_reasons"])
            finally:
                ledger.close()

    def test_project_identifier_is_encoded_as_one_path_segment(self):
        project_id = "project/a"
        self.session.auth.get_access.return_value.project_id = project_id
        self.response.json.return_value = nova_payload(project_id)

        self.source.collect(project_id, START, END)

        self.connection.compute.get.assert_called_once_with(
            "/os-simple-tenant-usage/project%2Fa",
            params={
                "start": "2026-09-01T00:00:00",
                "end": "2026-10-01T00:00:00",
                "detailed": "1",
                "limit": NOVA_USAGE_PAGE_LIMIT,
            },
            raise_exc=True,
        )


if __name__ == "__main__":
    unittest.main()
