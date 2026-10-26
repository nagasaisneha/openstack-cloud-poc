import sqlite3
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openstack_cloud_poc.billing import (
    BillingAuditAction,
    BillingAuditEvent,
    BillingAuditOutcome,
    BillingError,
    BillingErrorCode,
    BillingLedger,
    BillingPrincipal,
    BillingService,
    BillingStatus,
    ProjectRoleBinding,
    RateCard,
    RateRule,
    UsageBatch,
    _statement_from_wire,
)
from openstack_cloud_poc.usage import UsageProvenance, UsageSample


START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, tzinfo=timezone.utc)


def admin():
    return BillingPrincipal("admin-id", True, system_roles=frozenset({"cloud_admin"}))


def collector():
    return BillingPrincipal("collector-id", True, system_roles=frozenset({"billing_collector"}))


def project_user(project_id="project-a", *roles):
    return BillingPrincipal(
        "user-id",
        True,
        project_bindings=(ProjectRoleBinding(project_id, frozenset(roles)),),
    )


def make_sample(
    *,
    project_id="project-a",
    resource_id="server-1",
    metric="compute.vcpu_hours",
    quantity=Decimal("2"),
    unit="vCPU-hour",
    start=START,
    end=START + timedelta(hours=1),
    provenance=UsageProvenance.SIMULATED,
):
    return UsageSample(
        project_id=project_id,
        resource_id=resource_id,
        metric=metric,
        quantity=quantity,
        unit=unit,
        interval_start=start,
        interval_end=end,
        source="fixture:meter-sample" if provenance is UsageProvenance.SIMULATED else "openstack:ceilometer",
        provenance=provenance,
    )


def make_card(*rules, version="demo-v1", currency="INR", minor_units=2):
    return RateCard(version, currency, minor_units, tuple(rules))


def make_rule(*, metric="compute.vcpu_hours", unit="vCPU-hour", rate=Decimal("1.25"), start=START, end=None):
    return RateRule(metric, unit, rate, start, end)


class Source:
    def __init__(
        self, samples=(), *, complete=True, provenance=UsageProvenance.SIMULATED,
        project_id="project-a", collected_at=END,
    ):
        self.samples = tuple(samples)
        self.complete = complete
        self.provenance = provenance
        self.project_id = project_id
        self.collected_at = collected_at
        self.calls = []

    def collect(self, project_id, interval_start, interval_end):
        self.calls.append((project_id, interval_start, interval_end))
        return UsageBatch(
            project_id,
            interval_start,
            interval_end,
            "fixture:usage-source" if self.provenance is UsageProvenance.SIMULATED else "openstack:ceilometer",
            self.provenance,
            self.complete,
            self.samples,
            collected_at=self.collected_at,
        )


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.chmod(self.temp.name, 0o700)
        self.path = Path(self.temp.name) / "billing.sqlite"
        self.ledger = BillingLedger(self.path)
        self.source = Source((make_sample(),))
        self.service = BillingService(
            self.ledger,
            self.source,
            clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        self.service.configure_rate_card(admin(), make_card(make_rule()))

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def generate(self, source=None, project_id="project-a"):
        if source is not None:
            self.service.source = source
        return self.service.generate_statement(collector(), project_id, START, END, "demo-v1")

    def test_decimal_rating_rounds_each_line_half_up_and_sums_rounded_lines(self):
        sample_a = make_sample(resource_id="server-a", quantity=Decimal("1.005"))
        sample_b = make_sample(resource_id="server-b", quantity=Decimal("1.005"))
        source = Source((sample_a, sample_b))
        card = make_card(make_rule(rate=Decimal("1")), version="rounding-v2")
        self.service.configure_rate_card(admin(), card)
        self.service.source = source

        statement = self.service.generate_statement(collector(), "project-a", START, END, "rounding-v2")

        self.assertEqual([line.amount for line in statement.lines], [Decimal("1.01"), Decimal("1.01")])
        self.assertEqual(statement.total, Decimal("2.02"))
        self.assertEqual(statement.currency, "INR")

    def test_statement_is_project_billed_and_visible_to_billing_role(self):
        statement = self.generate()

        visible = self.service.project_statement(
            project_user("project-a", "billing_viewer"), "project-a", statement.statement_id
        )

        self.assertEqual(visible.project_id, "project-a")
        self.assertEqual(visible.total, Decimal("2.50"))
        with self.assertRaises(BillingError) as raised:
            self.service.project_statements(project_user("project-a", "member"), "project-a")
        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)

    def test_authorized_project_statement_csv_is_itemized_and_labels_simulated_data(self):
        statement = self.generate()

        content = self.service.project_statement_csv(
            project_user("project-a", "billing_viewer"), "project-a", statement.statement_id
        )
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))

        self.assertEqual([row["record_type"] for row in rows], ["line", "statement"])
        self.assertEqual(rows[0]["project_id"], "project-a")
        self.assertEqual(rows[0]["resource_id"], "server-1")
        self.assertEqual(rows[0]["metric"], "compute.vcpu_hours")
        self.assertEqual(rows[0]["quantity"], "2")
        self.assertEqual(rows[0]["rate_per_unit"], "1.25")
        self.assertEqual(rows[0]["line_amount"], "2.50")
        self.assertEqual(rows[0]["schema_version"], "2")
        self.assertEqual(rows[0]["usage_collected_at_utc"], END.isoformat())
        self.assertEqual(rows[0]["provenance"], "simulated")
        self.assertEqual(rows[0]["statement_total"], "")
        self.assertEqual(rows[1]["statement_total"], "2.50")
        self.assertEqual(
            content,
            self.service.project_statement_csv(
                project_user("project-a", "billing_viewer"), "project-a", statement.statement_id
            ),
        )

    def test_statement_csv_denies_before_ledger_access(self):
        class DenyLedger:
            def record_audit_event(self, event):
                self.event = event

            def get_project_statement(self, project_id, statement_id):
                raise AssertionError("ledger was accessed before authorization")

        service = BillingService(DenyLedger(), Source())
        with self.assertRaises(BillingError) as raised:
            service.project_statement_csv(
                project_user("project-a", "member"), "project-a", "statement-id"
            )
        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)
        self.assertEqual(service.ledger.event.action, BillingAuditAction.STATEMENT_EXPORT)
        self.assertEqual(service.ledger.event.outcome, BillingAuditOutcome.DENIED)
        self.assertIsNone(service.ledger.event.target_id)

    def test_statement_csv_does_not_disclose_a_foreign_statement_id(self):
        statement = self.generate()

        with self.assertRaises(BillingError) as raised:
            self.service.project_statement_csv(
                project_user("project-b", "billing_viewer"), "project-b", statement.statement_id
            )

        self.assertEqual(raised.exception.code, BillingErrorCode.STATEMENT_NOT_FOUND)

    def test_statement_csv_escapes_formula_like_resource_text(self):
        sample = make_sample(resource_id='=HYPERLINK("https://invalid.example/","open")')
        statement = self.generate(Source((sample,)))

        content = self.service.project_statement_csv(
            project_user("project-a", "billing_viewer"), "project-a", statement.statement_id
        )
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))

        self.assertTrue(rows[0]["resource_id"].startswith("'=HYPERLINK("))

    def test_statement_export_audit_records_actor_project_target_and_correlation(self):
        statement = self.generate()
        correlation_id = "d6b72e2a-4896-4d61-8e5b-c3b8248047b1"

        self.service.project_statement_csv(
            project_user("project-a", "billing_viewer"),
            "project-a",
            statement.statement_id,
            correlation_id=correlation_id,
        )
        events = self.service.audit_events(admin())
        export = next(event for event in events if event.action is BillingAuditAction.STATEMENT_EXPORT)

        self.assertEqual(export.actor_id, "user-id")
        self.assertEqual(export.project_id, "project-a")
        self.assertEqual(export.target_id, statement.statement_id)
        self.assertEqual(export.outcome, BillingAuditOutcome.SUCCESS)
        self.assertEqual(export.correlation_id, correlation_id)
        self.assertEqual(export.occurred_at.utcoffset(), timedelta(0))

    def test_audit_log_is_admin_only_and_denied_attempt_is_retained(self):
        with self.assertRaises(BillingError) as raised:
            self.service.audit_events(project_user("project-a", "billing_viewer"))
        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)

        events = self.ledger.list_audit_events()
        denied = next(
            event for event in events
            if event.action is BillingAuditAction.AUDIT_READ
            and event.outcome is BillingAuditOutcome.DENIED
        )
        self.assertEqual(denied.actor_id, "user-id")
        self.assertIsNone(denied.project_id)

    def test_audit_events_cannot_be_updated_or_deleted(self):
        self.generate()
        event = self.ledger.list_audit_events()[0]

        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger._db.execute(
                "UPDATE billing_audit_events SET outcome='denied' WHERE event_id=?",
                (event.event_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger._db.execute(
                "DELETE FROM billing_audit_events WHERE event_id=?",
                (event.event_id,),
            )

    def test_audit_events_survive_ledger_reopen(self):
        self.generate()
        event_ids = {event.event_id for event in self.ledger.list_audit_events()}
        self.ledger.close()

        self.ledger = BillingLedger(self.path)

        self.assertTrue(event_ids.issubset({event.event_id for event in self.ledger.list_audit_events()}))

    def test_rate_card_write_rolls_back_if_its_audit_event_cannot_be_stored(self):
        event = BillingAuditEvent(
            event_id="96c405a1-0561-4af2-910c-876445f3ac09",
            occurred_at=START,
            actor_id="admin-id",
            project_id=None,
            action=BillingAuditAction.RATE_CARD_CONFIGURE,
            target_id="atomic-v2",
            outcome=BillingAuditOutcome.SUCCESS,
            correlation_id="3c836409-42d5-4260-badb-1c2c326bd075",
        )
        with patch.object(self.ledger, "_insert_audit_event", side_effect=sqlite3.OperationalError):
            with self.assertRaises(BillingError) as raised:
                self.ledger.add_rate_card(
                    make_card(make_rule(), version="atomic-v2"), "admin-id", START, audit_event=event
                )
        self.assertEqual(raised.exception.code, BillingErrorCode.LEDGER_UNAVAILABLE)
        with self.assertRaises(BillingError) as missing:
            self.ledger.get_rate_card("atomic-v2")
        self.assertEqual(missing.exception.code, BillingErrorCode.INVALID_REQUEST)

    def test_statement_write_rolls_back_if_its_audit_event_cannot_be_stored(self):
        first = self.generate()
        self.source.samples = (make_sample(quantity=Decimal("3")),)

        with patch.object(self.ledger, "_insert_audit_event", side_effect=sqlite3.OperationalError):
            with self.assertRaises(BillingError) as raised:
                self.service.generate_statement(collector(), "project-a", START, END, "demo-v1")

        self.assertEqual(raised.exception.code, BillingErrorCode.LEDGER_UNAVAILABLE)
        self.assertEqual(self.ledger.list_project_statements("project-a"), (first,))

    def test_two_users_in_one_project_share_the_same_statement(self):
        statement = self.generate()
        binding = (ProjectRoleBinding("project-a", frozenset({"billing_viewer"})),)
        first_user = BillingPrincipal("user-one", True, project_bindings=binding)
        second_user = BillingPrincipal("user-two", True, project_bindings=binding)

        first_view = self.service.project_statement(first_user, "project-a", statement.statement_id)
        second_view = self.service.project_statement(second_user, "project-a", statement.statement_id)

        self.assertEqual(first_view.statement_id, second_view.statement_id)
        self.assertEqual(first_view.project_id, second_view.project_id)
        self.assertEqual(first_view.total, second_view.total)

    def test_cross_project_statement_lookup_does_not_disclose_statement(self):
        statement = self.generate()

        with self.assertRaises(BillingError) as raised:
            self.service.project_statement(
                project_user("project-b", "billing_viewer"), "project-b", statement.statement_id
            )

        self.assertEqual(raised.exception.code, BillingErrorCode.STATEMENT_NOT_FOUND)

    def test_unauthorized_reads_do_not_access_the_ledger(self):
        class DenyLedger:
            def record_audit_event(self, event):
                self.event = event

            def list_project_statements(self, project_id):
                raise AssertionError("ledger was accessed before authorization")

        service = BillingService(DenyLedger(), Source())
        with self.assertRaises(BillingError) as raised:
            service.project_statements(project_user("project-a", "member"), "project-a")
        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)
        self.assertEqual(service.ledger.event.action, BillingAuditAction.STATEMENT_LIST)
        self.assertEqual(service.ledger.event.outcome, BillingAuditOutcome.DENIED)
        self.assertIsNone(service.ledger.event.target_id)

    def test_unauthorized_statement_generation_does_not_collect_usage(self):
        source = Source((make_sample(),))
        service = BillingService(self.ledger, source)

        with self.assertRaises(BillingError) as raised:
            service.generate_statement(project_user("project-a", "billing_viewer"), "project-a", START, END, "demo-v1")

        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)
        self.assertEqual(source.calls, [])
        denied = next(
            event for event in self.ledger.list_audit_events()
            if event.action is BillingAuditAction.STATEMENT_GENERATE
            and event.outcome is BillingAuditOutcome.DENIED
        )
        self.assertEqual(denied.actor_id, "user-id")

    def test_only_admin_can_configure_a_rate_card(self):
        with self.assertRaises(BillingError) as raised:
            self.service.configure_rate_card(project_user("project-a", "project_admin"), make_card(make_rule(), version="v2"))
        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)
        denied = next(
            event for event in self.ledger.list_audit_events()
            if event.action is BillingAuditAction.RATE_CARD_CONFIGURE
            and event.outcome is BillingAuditOutcome.DENIED
        )
        self.assertEqual(denied.actor_id, "user-id")

        admin_service = self.service
        admin_service.configure_rate_card(admin(), make_card(make_rule(rate=Decimal("2")), version="v2"))
        self.assertEqual(self.ledger.get_rate_card("v2").rules[0].rate_per_unit, Decimal("2"))

    def test_admin_can_inspect_all_projects_but_tenant_cannot(self):
        statement = self.generate()
        self.assertEqual(self.service.all_statements(admin()), (statement,))
        with self.assertRaises(BillingError) as raised:
            self.service.all_statements(project_user("project-a", "billing_viewer"))
        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)

    def test_incomplete_coverage_is_provisional_and_has_no_total(self):
        source = Source((make_sample(),), complete=False)

        statement = self.generate(source)

        self.assertEqual(statement.status, BillingStatus.PROVISIONAL)
        self.assertIsNone(statement.total)
        self.assertEqual(statement.provenance, UsageProvenance.SIMULATED)
        self.assertEqual(statement.lines[0].amount, Decimal("2.50"))

    def test_missing_rate_is_provisional_and_never_zero(self):
        source = Source((make_sample(metric="compute.unknown"),))

        statement = self.generate(source)

        self.assertEqual(statement.status, BillingStatus.PROVISIONAL)
        self.assertIsNone(statement.total)
        self.assertEqual(statement.missing_rates, ("compute.unknown:vCPU-hour",))
        self.assertIsNone(statement.lines[0].amount)

    def test_interval_crossing_a_rate_change_is_not_charged_at_either_rate(self):
        changed = START + timedelta(minutes=30)
        card = make_card(
            make_rule(rate=Decimal("1"), start=START, end=changed),
            make_rule(rate=Decimal("2"), start=changed),
            version="boundary-v2",
        )
        self.service.configure_rate_card(admin(), card)
        sample = make_sample(start=START, end=START + timedelta(hours=1))
        source = Source((sample,))
        self.service.source = source

        statement = self.service.generate_statement(collector(), "project-a", START, END, "boundary-v2")

        self.assertEqual(statement.status, BillingStatus.PROVISIONAL)
        self.assertIsNone(statement.total)

    def test_effective_dated_rules_may_be_adjacent_but_not_overlap(self):
        boundary = START + timedelta(days=2)
        card = make_card(
            make_rule(start=START, end=boundary),
            make_rule(rate=Decimal("2"), start=boundary),
        )
        self.assertEqual(len(card.rules), 2)
        with self.assertRaises(ValueError):
            make_card(
                make_rule(start=START, end=boundary + timedelta(seconds=1)),
                make_rule(rate=Decimal("2"), start=boundary),
            )

    def test_complete_zero_sample_window_is_not_confused_with_missing_coverage(self):
        statement = self.generate(Source((), complete=True))
        self.assertEqual(statement.status, BillingStatus.COMPLETE)
        self.assertEqual(statement.total, Decimal("0.00"))

    def test_statement_provenance_is_simulated_for_fixture_input(self):
        statement = self.generate()
        self.assertEqual(statement.provenance, UsageProvenance.SIMULATED)

    def test_provider_samples_can_be_rated_but_are_not_created_by_this_module(self):
        sample = make_sample(provenance=UsageProvenance.OBSERVED)
        statement = self.generate(Source((sample,), provenance=UsageProvenance.OBSERVED))
        self.assertEqual(statement.status, BillingStatus.COMPLETE)
        self.assertEqual(statement.provenance, UsageProvenance.OBSERVED)
        self.assertEqual(statement.total, Decimal("2.50"))

    def test_statement_history_survives_resource_deletion_and_revisions_append(self):
        first = self.generate()
        # Replaying identical source data is idempotent; a changed meter value
        # is an appended revision. The ledger has no live-resource foreign key.
        second = self.generate()
        self.source.samples = (make_sample(quantity=Decimal("3")),)
        corrected = self.generate()

        self.assertEqual(first.lines[0].resource_id, "server-1")
        self.assertEqual(self.service.project_statement(
            project_user("project-a", "billing_viewer"), "project-a", first.statement_id
        ), first)
        self.assertEqual(second.statement_id, first.statement_id)
        self.assertEqual(second.revision, first.revision)
        self.assertEqual(corrected.revision, first.revision + 1)
        self.assertEqual(corrected.total, Decimal("3.75"))
        self.assertEqual(len(self.service.project_statements(
            project_user("project-a", "billing_viewer"), "project-a"
        )), 2)

    def test_collection_time_refresh_does_not_create_an_identical_statement_revision(self):
        first = self.generate()
        self.source.collected_at = datetime(2026, 11, 2, tzinfo=timezone.utc)

        second = self.service.generate_statement(collector(), "project-a", START, END, "demo-v1")

        self.assertEqual(second.statement_id, first.statement_id)
        self.assertEqual(second.revision, 1)
        self.assertEqual(second.usage_collected_at, END)
        self.assertEqual(len(self.ledger.list_project_statements("project-a")), 1)

    def test_legacy_statement_wire_without_collection_fields_remains_readable(self):
        statement = self.generate()
        payload = statement.to_wire()
        payload.pop("usage_collected_at")
        payload.pop("incomplete_reasons")

        restored = _statement_from_wire(payload)

        self.assertEqual(restored.statement_id, statement.statement_id)
        self.assertIsNone(restored.usage_collected_at)
        self.assertEqual(restored.incomplete_reasons, ())

    def test_corrupt_statement_history_fails_closed_without_appending_revision(self):
        statement = self.generate()
        with patch("openstack_cloud_poc.billing._statement_from_wire", side_effect=ValueError("corrupt")):
            with self.assertRaises(BillingError) as raised:
                self.ledger.append_statement(statement)

        self.assertEqual(raised.exception.code, BillingErrorCode.LEDGER_UNAVAILABLE)
        self.assertEqual(len(self.ledger.list_project_statements("project-a")), 1)

    def test_sqlite_prevents_statement_and_rate_card_updates_or_deletes(self):
        statement = self.generate()
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger._db.execute("DELETE FROM statements WHERE statement_id=?", (statement.statement_id,))
        self.ledger._db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger._db.execute("UPDATE rate_cards SET payload='{}' WHERE version='demo-v1'")
        self.ledger._db.rollback()

    def test_ledger_files_are_private(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(str(self.path) + ".lock").stat().st_mode & 0o777, 0o600)

    def test_rate_card_versions_are_immutable(self):
        with self.assertRaises(BillingError) as raised:
            self.service.configure_rate_card(admin(), make_card(make_rule(rate=Decimal("9"))))
        self.assertEqual(raised.exception.code, BillingErrorCode.RATE_CARD_CONFLICT)
        self.assertEqual(self.ledger.get_rate_card("demo-v1").rules[0].rate_per_unit, Decimal("1.25"))

    def test_bad_batch_scope_is_rejected_without_persisting_a_statement(self):
        class WrongProjectSource:
            def collect(self, project_id, interval_start, interval_end):
                return UsageBatch(
                    "project-b", interval_start, interval_end, "fixture:bad-source",
                    UsageProvenance.SIMULATED, True, (),
                )

        self.service.source = WrongProjectSource()
        with self.assertRaises(BillingError) as raised:
            self.service.generate_statement(collector(), "project-a", START, END, "demo-v1")
        self.assertEqual(raised.exception.code, BillingErrorCode.INVALID_REQUEST)
        self.assertEqual(self.ledger.list_all_statements(), ())

    def test_rate_card_requires_decimal_and_valid_currency(self):
        with self.assertRaises(ValueError):
            make_rule(rate=1.25)
        with self.assertRaises(ValueError):
            make_card(make_rule(), currency="usd")
        with self.assertRaises(ValueError):
            make_card(make_rule(), currency="ZZZ")
        with self.assertRaises(ValueError):
            make_card(make_rule(), currency="JPY", minor_units=2)

    def test_source_label_rejects_urls_and_credential_like_values(self):
        with self.assertRaises(ValueError):
            UsageBatch("project-a", START, END, "https://user:password@example.test", UsageProvenance.OBSERVED, True, ())

    def test_unauthenticated_principal_is_denied(self):
        with self.assertRaises(BillingError) as raised:
            self.service.project_statements(BillingPrincipal("anonymous", False), "project-a")
        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHENTICATION_REQUIRED)

    def test_billing_period_must_be_a_utc_calendar_month(self):
        with self.assertRaises(BillingError) as raised:
            self.service.generate_statement(
                collector(), "project-a", START + timedelta(days=1), END, "demo-v1"
            )
        self.assertEqual(raised.exception.code, BillingErrorCode.INVALID_REQUEST)

    def test_billing_viewer_can_read_rate_card_for_its_project(self):
        card = self.service.project_rate_card(
            project_user("project-a", "billing_viewer"), "project-a", "demo-v1"
        )
        self.assertEqual(card.currency, "INR")

    def test_regular_member_cannot_read_project_rate_card(self):
        with self.assertRaises(BillingError) as raised:
            self.service.project_rate_card(
                project_user("project-a", "member"), "project-a", "demo-v1"
            )
        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)


if __name__ == "__main__":
    unittest.main()
