"""Project-level rating and immutable statement snapshots.

This module is a provider-independent billing domain core. It does not collect
meters or authenticate browser users. Production callers must construct
``BillingPrincipal`` only from a Keystone-verified session and provide a
trusted usage source. Unit tests use explicitly simulated samples.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from .usage import UsageProvenance, UsageSample


SUPPORTED_CURRENCY_MINOR_UNITS = {
    "EUR": 2,
    "GBP": 2,
    "INR": 2,
    "JPY": 0,
    "USD": 2,
}


class BillingErrorCode(str, Enum):
    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHORIZATION_DENIED = "authorization_denied"
    INVALID_REQUEST = "invalid_request"
    RATE_CARD_CONFLICT = "rate_card_conflict"
    STATEMENT_NOT_FOUND = "statement_not_found"
    LEDGER_UNAVAILABLE = "ledger_unavailable"


class BillingError(RuntimeError):
    """Safe billing error; never includes identifiers, credentials, or SQL."""

    _MESSAGES = {
        BillingErrorCode.AUTHENTICATION_REQUIRED: "Authentication is required.",
        BillingErrorCode.AUTHORIZATION_DENIED: "This billing operation is not authorized.",
        BillingErrorCode.INVALID_REQUEST: "The billing request is invalid.",
        BillingErrorCode.RATE_CARD_CONFLICT: "The rate card version already exists.",
        BillingErrorCode.STATEMENT_NOT_FOUND: "The billing statement was not found.",
        BillingErrorCode.LEDGER_UNAVAILABLE: "Billing records are unavailable.",
    }

    def __init__(self, code: BillingErrorCode) -> None:
        self.code = code
        super().__init__(self._MESSAGES[code])


@dataclass(frozen=True)
class ProjectRoleBinding:
    project_id: str
    roles: frozenset[str]

    def __post_init__(self) -> None:
        _safe_text(self.project_id)
        if not isinstance(self.roles, frozenset) or any(not _safe_text(role) for role in self.roles):
            raise ValueError("roles must be a frozenset of safe role names")


@dataclass(frozen=True)
class BillingPrincipal:
    """Trusted identity context supplied by an authentication adapter.

    Never construct this from request JSON, query parameters, or UI state.
    A Horizon adapter must derive it from its authenticated Keystone session.
    """

    subject_id: str
    authenticated: bool
    project_bindings: tuple[ProjectRoleBinding, ...] = ()
    system_roles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _safe_text(self.subject_id)
        if not isinstance(self.authenticated, bool):
            raise TypeError("authenticated must be bool")
        if not isinstance(self.project_bindings, tuple) or any(
            not isinstance(binding, ProjectRoleBinding) for binding in self.project_bindings
        ):
            raise TypeError("project_bindings must be a tuple of ProjectRoleBinding values")
        if len({binding.project_id for binding in self.project_bindings}) != len(self.project_bindings):
            raise ValueError("project bindings must have unique project IDs")
        if not isinstance(self.system_roles, frozenset) or any(
            not _safe_text(role) for role in self.system_roles
        ):
            raise ValueError("system_roles must be a frozenset of safe role names")


class BillingStatus(str, Enum):
    COMPLETE = "complete"
    PROVISIONAL = "provisional"


@dataclass(frozen=True)
class RateRule:
    metric: str
    unit: str
    rate_per_unit: Decimal
    effective_from: datetime
    effective_until: datetime | None = None

    def __post_init__(self) -> None:
        _safe_text(self.metric)
        _safe_text(self.unit)
        _valid_money(self.rate_per_unit)
        if not _is_utc(self.effective_from):
            raise ValueError("effective_from must be UTC")
        if self.effective_until is not None:
            if not _is_utc(self.effective_until) or self.effective_until <= self.effective_from:
                raise ValueError("effective_until must be after effective_from in UTC")


@dataclass(frozen=True)
class RateCard:
    version: str
    currency: str
    minor_units: int
    rules: tuple[RateRule, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", self.version):
            raise ValueError("version is invalid")
        if self.currency not in SUPPORTED_CURRENCY_MINOR_UNITS:
            raise ValueError("currency is not in the supported ISO currency set")
        if self.minor_units != SUPPORTED_CURRENCY_MINOR_UNITS[self.currency]:
            raise ValueError("minor_units do not match the configured currency")
        if not isinstance(self.rules, tuple) or not self.rules or any(
            not isinstance(rule, RateRule) for rule in self.rules
        ):
            raise ValueError("rules must be a non-empty tuple of RateRule values")
        by_meter: dict[tuple[str, str], list[RateRule]] = {}
        for rule in self.rules:
            by_meter.setdefault((rule.metric, rule.unit), []).append(rule)
        for rules in by_meter.values():
            ordered = sorted(rules, key=lambda rule: rule.effective_from)
            for previous, current in zip(ordered, ordered[1:]):
                if previous.effective_until is None or current.effective_from < previous.effective_until:
                    raise ValueError("effective rate periods cannot overlap")

    def to_wire(self) -> dict[str, object]:
        return {
            "version": self.version,
            "currency": self.currency,
            "minor_units": self.minor_units,
            "rules": [
                {
                    "metric": rule.metric,
                    "unit": rule.unit,
                    "rate_per_unit": str(rule.rate_per_unit),
                    "effective_from": rule.effective_from.isoformat(),
                    "effective_until": rule.effective_until.isoformat() if rule.effective_until else None,
                }
                for rule in self.rules
            ],
        }

    @classmethod
    def from_wire(cls, value: object) -> "RateCard":
        if not isinstance(value, dict) or set(value) != {"version", "currency", "minor_units", "rules"}:
            raise ValueError("rate card shape is invalid")
        if not isinstance(value["rules"], list):
            raise ValueError("rate card rules are invalid")
        rules = []
        for row in value["rules"]:
            if not isinstance(row, dict) or set(row) != {
                "metric", "unit", "rate_per_unit", "effective_from", "effective_until"
            }:
                raise ValueError("rate rule shape is invalid")
            rules.append(RateRule(
                metric=row["metric"],
                unit=row["unit"],
                rate_per_unit=Decimal(row["rate_per_unit"]),
                effective_from=datetime.fromisoformat(row["effective_from"]),
                effective_until=datetime.fromisoformat(row["effective_until"])
                if row["effective_until"] is not None else None,
            ))
        return cls(value["version"], value["currency"], value["minor_units"], tuple(rules))


@dataclass(frozen=True)
class UsageBatch:
    project_id: str
    interval_start: datetime
    interval_end: datetime
    source: str
    provenance: UsageProvenance
    complete: bool
    samples: tuple[UsageSample, ...]

    def __post_init__(self) -> None:
        _safe_text(self.project_id)
        _safe_source(self.source)
        if not _is_utc(self.interval_start) or not _is_utc(self.interval_end):
            raise ValueError("usage batch interval must be UTC")
        if self.interval_end <= self.interval_start:
            raise ValueError("usage batch interval must be non-empty")
        if not isinstance(self.provenance, UsageProvenance) or not isinstance(self.complete, bool):
            raise TypeError("batch provenance and completeness are invalid")
        if not isinstance(self.samples, tuple) or any(not isinstance(sample, UsageSample) for sample in self.samples):
            raise TypeError("samples must be a tuple of UsageSample values")
        if any(
            sample.project_id != self.project_id
            or sample.interval_start < self.interval_start
            or sample.interval_end > self.interval_end
            or sample.provenance is not self.provenance
            for sample in self.samples
        ):
            raise ValueError("usage samples must match the batch project, window, and provenance")
        keys = {
            (sample.project_id, sample.resource_id, sample.metric, sample.interval_start, sample.interval_end)
            for sample in self.samples
        }
        if len(keys) != len(self.samples):
            raise ValueError("duplicate usage samples are not allowed")


class BillingUsageSource(Protocol):
    def collect(self, project_id: str, interval_start: datetime, interval_end: datetime) -> UsageBatch:
        """Return trusted project usage plus explicit coverage/provenance."""


@dataclass(frozen=True)
class BillingLine:
    resource_id: str
    metric: str
    quantity: Decimal
    unit: str
    interval_start: datetime
    interval_end: datetime
    rate_per_unit: Decimal | None
    amount: Decimal | None

    def to_wire(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "metric": self.metric,
            "quantity": str(self.quantity),
            "unit": self.unit,
            "interval_start": self.interval_start.isoformat(),
            "interval_end": self.interval_end.isoformat(),
            "rate_per_unit": str(self.rate_per_unit) if self.rate_per_unit is not None else None,
            "amount": str(self.amount) if self.amount is not None else None,
        }


@dataclass(frozen=True)
class BillingStatement:
    statement_id: str
    project_id: str
    period_start: datetime
    period_end: datetime
    rate_card_version: str
    currency: str
    minor_units: int
    status: BillingStatus
    provenance: UsageProvenance
    source: str
    total: Decimal | None
    lines: tuple[BillingLine, ...]
    missing_rates: tuple[str, ...]
    created_at: datetime
    revision: int = 0

    def to_wire(self) -> dict[str, object]:
        return {
            "statement_id": self.statement_id,
            "project_id": self.project_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "rate_card_version": self.rate_card_version,
            "currency": self.currency,
            "minor_units": self.minor_units,
            "status": self.status.value,
            "provenance": self.provenance.value,
            "source": self.source,
            "total": str(self.total) if self.total is not None else None,
            "lines": [line.to_wire() for line in self.lines],
            "missing_rates": list(self.missing_rates),
            "created_at": self.created_at.isoformat(),
            "revision": self.revision,
        }


class BillingAuditAction(str, Enum):
    RATE_CARD_CONFIGURE = "billing.rate_card.configure"
    RATE_CARD_READ = "billing.rate_card.read"
    STATEMENT_GENERATE = "billing.statement.generate"
    STATEMENT_LIST = "billing.statement.list"
    STATEMENT_READ = "billing.statement.read"
    STATEMENT_EXPORT = "billing.statement.export"
    ADMIN_STATEMENT_LIST = "billing.statement.admin_list"
    AUDIT_READ = "billing.audit.read"


class BillingAuditOutcome(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(frozen=True)
class BillingAuditEvent:
    """Minimal immutable audit metadata; never store request bodies or secrets."""

    event_id: str
    occurred_at: datetime
    actor_id: str
    project_id: str | None
    action: BillingAuditAction
    target_id: str | None
    outcome: BillingAuditOutcome
    correlation_id: str

    def __post_init__(self) -> None:
        try:
            UUID(self.event_id)
            UUID(self.correlation_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("audit event identifiers must be UUIDs") from None
        if not _is_utc(self.occurred_at):
            raise ValueError("audit event timestamp must be UTC")
        for field_name, value in (
            ("actor_id", self.actor_id),
            ("project_id", self.project_id),
            ("target_id", self.target_id),
        ):
            if value is None and field_name != "actor_id":
                continue
            if not _safe_text(value) or len(value) > 255:
                raise ValueError(f"{field_name} is invalid")
        if not isinstance(self.action, BillingAuditAction):
            raise TypeError("action must be a BillingAuditAction")
        if not isinstance(self.outcome, BillingAuditOutcome):
            raise TypeError("outcome must be a BillingAuditOutcome")


class BillingLedger:
    """Protected SQLite storage with append-only rate cards/statements.

    The database is application-owned state; it must not point at or access an
    OpenStack service database. Backups and retention are handled by operations.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        parent = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise BillingError(BillingErrorCode.INVALID_REQUEST)
        self._lock = RLock()
        self._lock_fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise BillingError(BillingErrorCode.INVALID_REQUEST)
            finally:
                os.close(fd)
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS rate_cards (
                    version TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS statements (
                    statement_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE(project_id, period_start, period_end, revision),
                    UNIQUE(project_id, period_start, period_end, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS statements_project_period
                    ON statements(project_id, period_start, period_end);
                CREATE TRIGGER IF NOT EXISTS rate_cards_no_update
                    BEFORE UPDATE ON rate_cards BEGIN SELECT RAISE(ABORT, 'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS rate_cards_no_delete
                    BEFORE DELETE ON rate_cards BEGIN SELECT RAISE(ABORT, 'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS statements_no_update
                    BEFORE UPDATE ON statements BEGIN SELECT RAISE(ABORT, 'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS statements_no_delete
                    BEFORE DELETE ON statements BEGIN SELECT RAISE(ABORT, 'immutable'); END;
                CREATE TABLE IF NOT EXISTS billing_audit_events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    project_id TEXT,
                    action TEXT NOT NULL,
                    target_id TEXT,
                    outcome TEXT NOT NULL,
                    correlation_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS billing_audit_time
                    ON billing_audit_events(occurred_at DESC, event_id DESC);
                CREATE INDEX IF NOT EXISTS billing_audit_project_time
                    ON billing_audit_events(project_id, occurred_at DESC, event_id DESC);
                CREATE TRIGGER IF NOT EXISTS billing_audit_no_update
                    BEFORE UPDATE ON billing_audit_events BEGIN SELECT RAISE(ABORT, 'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS billing_audit_no_delete
                    BEFORE DELETE ON billing_audit_events BEGIN SELECT RAISE(ABORT, 'immutable'); END;
                """
            )
            self._db.commit()
        except Exception:
            os.close(self._lock_fd)
            raise

    def close(self) -> None:
        with self._lock:
            self._db.close()
            os.close(self._lock_fd)

    def add_rate_card(
        self,
        card: RateCard,
        actor_id: str,
        created_at: datetime,
        audit_event: BillingAuditEvent | None = None,
    ) -> None:
        _safe_text(actor_id)
        if not _is_utc(created_at):
            raise BillingError(BillingErrorCode.INVALID_REQUEST)
        try:
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    "INSERT INTO rate_cards(version, payload, actor_id, created_at) VALUES (?, ?, ?, ?)",
                    (card.version, _encode(card.to_wire()), actor_id, created_at.isoformat()),
                )
                if audit_event is not None:
                    self._insert_audit_event(audit_event)
                self._db.commit()
        except sqlite3.IntegrityError:
            self._db.rollback()
            raise BillingError(BillingErrorCode.RATE_CARD_CONFLICT) from None
        except sqlite3.Error:
            self._db.rollback()
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE) from None

    def get_rate_card(self, version: str) -> RateCard:
        try:
            with self._lock:
                row = self._db.execute("SELECT payload FROM rate_cards WHERE version=?", (version,)).fetchone()
            if row is None:
                raise BillingError(BillingErrorCode.INVALID_REQUEST)
            return RateCard.from_wire(json.loads(row["payload"]))
        except BillingError:
            raise
        except (sqlite3.Error, TypeError, ValueError, KeyError):
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE) from None

    def append_statement(
        self,
        statement: BillingStatement,
        audit_event: BillingAuditEvent | None = None,
    ) -> BillingStatement:
        try:
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                fingerprint = _statement_fingerprint(statement)
                previous = self._db.execute(
                    "SELECT payload FROM statements WHERE project_id=? AND period_start=? "
                    "AND period_end=? AND fingerprint=?",
                    (statement.project_id, statement.period_start.isoformat(),
                     statement.period_end.isoformat(), fingerprint),
                ).fetchone()
                if previous is not None:
                    if audit_event is not None:
                        self._insert_audit_event(audit_event)
                    self._db.commit()
                    return _statement_from_wire(json.loads(previous["payload"]))
                row = self._db.execute(
                    "SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision FROM statements "
                    "WHERE project_id=? AND period_start=? AND period_end=?",
                    (statement.project_id, statement.period_start.isoformat(), statement.period_end.isoformat()),
                ).fetchone()
                saved = replace(statement, revision=row["next_revision"])
                self._db.execute(
                    "INSERT INTO statements(statement_id, project_id, period_start, period_end, revision, fingerprint, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (saved.statement_id, saved.project_id, saved.period_start.isoformat(),
                     saved.period_end.isoformat(), saved.revision, fingerprint, _encode(saved.to_wire())),
                )
                if audit_event is not None:
                    self._insert_audit_event(audit_event)
                self._db.commit()
                return saved
        except sqlite3.Error:
            self._db.rollback()
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE) from None

    def get_project_statement(self, project_id: str, statement_id: str) -> BillingStatement:
        try:
            with self._lock:
                row = self._db.execute(
                    "SELECT payload FROM statements WHERE project_id=? AND statement_id=?",
                    (project_id, statement_id),
                ).fetchone()
            if row is None:
                raise BillingError(BillingErrorCode.STATEMENT_NOT_FOUND)
            return _statement_from_wire(json.loads(row["payload"]))
        except BillingError:
            raise
        except (sqlite3.Error, TypeError, ValueError, KeyError):
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE) from None

    def list_project_statements(self, project_id: str) -> tuple[BillingStatement, ...]:
        return self._list_statements("project_id=?", (project_id,))

    def list_all_statements(self) -> tuple[BillingStatement, ...]:
        return self._list_statements("1=1", ())

    def record_audit_event(self, event: BillingAuditEvent) -> None:
        if not isinstance(event, BillingAuditEvent):
            raise BillingError(BillingErrorCode.INVALID_REQUEST)
        try:
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                self._insert_audit_event(event)
                self._db.commit()
        except sqlite3.Error:
            self._db.rollback()
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE) from None

    def list_audit_events(self, limit: int = 200) -> tuple[BillingAuditEvent, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise BillingError(BillingErrorCode.INVALID_REQUEST)
        try:
            with self._lock:
                rows = self._db.execute(
                    "SELECT * FROM billing_audit_events ORDER BY occurred_at DESC, event_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return tuple(_audit_event_from_row(row) for row in rows)
        except (sqlite3.Error, TypeError, ValueError, KeyError):
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE) from None

    def _insert_audit_event(self, event: BillingAuditEvent) -> None:
        self._db.execute(
            "INSERT INTO billing_audit_events(event_id, occurred_at, actor_id, project_id, action, "
            "target_id, outcome, correlation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.occurred_at.isoformat(),
                event.actor_id,
                event.project_id,
                event.action.value,
                event.target_id,
                event.outcome.value,
                event.correlation_id,
            ),
        )

    def _list_statements(self, where: str, values: tuple[object, ...]) -> tuple[BillingStatement, ...]:
        try:
            with self._lock:
                rows = self._db.execute(
                    "SELECT payload FROM statements WHERE " + where + " ORDER BY period_start, project_id, revision",
                    values,
                ).fetchall()
            return tuple(_statement_from_wire(json.loads(row["payload"])) for row in rows)
        except (sqlite3.Error, TypeError, ValueError, KeyError):
            raise BillingError(BillingErrorCode.LEDGER_UNAVAILABLE) from None


class BillingService:
    """Authorize bill access and record billing actions in the protected ledger."""

    def __init__(self, ledger: BillingLedger, source: BillingUsageSource, clock=None) -> None:
        if not callable(getattr(source, "collect", None)):
            raise TypeError("source must implement collect")
        self.ledger = ledger
        self.source = source
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _correlation(self, correlation_id: str | None) -> str:
        if correlation_id is None:
            return str(uuid4())
        try:
            return str(UUID(correlation_id))
        except (ValueError, TypeError, AttributeError):
            raise BillingError(BillingErrorCode.INVALID_REQUEST) from None

    def _make_audit_event(
        self,
        principal: BillingPrincipal | None,
        action: BillingAuditAction,
        outcome: BillingAuditOutcome,
        correlation_id: str,
        *,
        project_id: str | None = None,
        target_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> BillingAuditEvent:
        actor_candidate = principal.subject_id if isinstance(principal, BillingPrincipal) else "anonymous"
        actor_id = actor_candidate if len(actor_candidate) <= 255 else "unknown"
        safe_project = project_id if isinstance(project_id, str) and project_id.strip() and len(project_id) <= 255 and "\n" not in project_id and "\r" not in project_id else None
        safe_target = target_id if outcome is BillingAuditOutcome.SUCCESS and isinstance(target_id, str) and target_id.strip() and len(target_id) <= 255 and "\n" not in target_id and "\r" not in target_id else None
        timestamp_value = occurred_at if occurred_at is not None else self.clock()
        if not _is_utc(timestamp_value):
            raise BillingError(BillingErrorCode.INVALID_REQUEST)
        return BillingAuditEvent(
            event_id=str(uuid4()),
            occurred_at=timestamp_value,
            actor_id=actor_id,
            project_id=safe_project,
            action=action,
            target_id=safe_target,
            outcome=outcome,
            correlation_id=correlation_id,
        )

    def _record_audit_event(
        self,
        principal: BillingPrincipal | None,
        action: BillingAuditAction,
        outcome: BillingAuditOutcome,
        correlation_id: str,
        *,
        project_id: str | None = None,
        target_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        event = self._make_audit_event(
            principal,
            action,
            outcome,
            correlation_id,
            project_id=project_id,
            target_id=target_id,
            occurred_at=occurred_at,
        )
        self.ledger.record_audit_event(event)

    @staticmethod
    def _audit_outcome(error: BillingError) -> BillingAuditOutcome:
        if error.code in {BillingErrorCode.AUTHENTICATION_REQUIRED, BillingErrorCode.AUTHORIZATION_DENIED}:
            return BillingAuditOutcome.DENIED
        if error.code is BillingErrorCode.STATEMENT_NOT_FOUND:
            return BillingAuditOutcome.NOT_FOUND
        return BillingAuditOutcome.ERROR

    def _record_failed_action(
        self,
        principal: BillingPrincipal | None,
        action: BillingAuditAction,
        error: BillingError,
        correlation_id: str,
        *,
        project_id: str | None = None,
    ) -> None:
        self._record_audit_event(
            principal,
            action,
            self._audit_outcome(error),
            correlation_id,
            project_id=project_id,
        )

    def configure_rate_card(
        self,
        principal: BillingPrincipal,
        card: RateCard,
        *,
        correlation_id: str | None = None,
    ) -> None:
        correlation = self._correlation(correlation_id)
        try:
            _require_system_role(principal, "cloud_admin")
        except BillingError as error:
            self._record_failed_action(principal, BillingAuditAction.RATE_CARD_CONFIGURE, error, correlation)
            raise
        try:
            created_at = self.clock()
            if not _is_utc(created_at):
                raise BillingError(BillingErrorCode.INVALID_REQUEST)
            event = self._make_audit_event(
                principal,
                BillingAuditAction.RATE_CARD_CONFIGURE,
                BillingAuditOutcome.SUCCESS,
                correlation,
                target_id=card.version,
                occurred_at=created_at,
            )
            self.ledger.add_rate_card(card, principal.subject_id, created_at, audit_event=event)
        except BillingError as error:
            self._record_failed_action(principal, BillingAuditAction.RATE_CARD_CONFIGURE, error, correlation)
            raise
        except Exception:
            error = BillingError(BillingErrorCode.INVALID_REQUEST)
            self._record_failed_action(principal, BillingAuditAction.RATE_CARD_CONFIGURE, error, correlation)
            raise error from None

    def project_rate_card(
        self,
        principal: BillingPrincipal,
        project_id: str,
        version: str,
        *,
        correlation_id: str | None = None,
    ) -> RateCard:
        correlation = self._correlation(correlation_id)
        try:
            _require_project_billing_role(principal, project_id)
            card = self.ledger.get_rate_card(version)
        except BillingError as error:
            self._record_failed_action(principal, BillingAuditAction.RATE_CARD_READ, error, correlation, project_id=project_id)
            raise
        self._record_audit_event(
            principal, BillingAuditAction.RATE_CARD_READ, BillingAuditOutcome.SUCCESS,
            correlation, project_id=project_id, target_id=card.version,
        )
        return card

    def generate_statement(
        self,
        principal: BillingPrincipal,
        project_id: str,
        period_start: datetime,
        period_end: datetime,
        rate_card_version: str,
        *,
        correlation_id: str | None = None,
    ) -> BillingStatement:
        correlation = self._correlation(correlation_id)
        try:
            _require_system_role(principal, "billing_collector", "cloud_admin")
            if (
                not isinstance(project_id, str)
                or not project_id.strip()
                or "\n" in project_id
                or "\r" in project_id
                or not _is_utc_calendar_month(period_start, period_end)
            ):
                raise BillingError(BillingErrorCode.INVALID_REQUEST)
            card = self.ledger.get_rate_card(rate_card_version)
            batch = self.source.collect(project_id, period_start, period_end)
            if (
                not isinstance(batch, UsageBatch)
                or batch.project_id != project_id
                or batch.interval_start != period_start
                or batch.interval_end != period_end
            ):
                raise BillingError(BillingErrorCode.INVALID_REQUEST)
            lines = []
            missing = []
            for sample in batch.samples:
                rule = _matching_rate(card, sample)
                if rule is None:
                    missing.append(sample.metric + ":" + sample.unit)
                    lines.append(BillingLine(
                        sample.resource_id, sample.metric, sample.quantity, sample.unit,
                        sample.interval_start, sample.interval_end, None, None,
                    ))
                    continue
                try:
                    amount = (sample.quantity * rule.rate_per_unit).quantize(
                        Decimal(1).scaleb(-card.minor_units), rounding=ROUND_HALF_UP
                    )
                except InvalidOperation:
                    raise BillingError(BillingErrorCode.INVALID_REQUEST) from None
                lines.append(BillingLine(
                    sample.resource_id, sample.metric, sample.quantity, sample.unit,
                    sample.interval_start, sample.interval_end, rule.rate_per_unit, amount,
                ))
            complete = batch.complete and not missing
            total = None
            if complete:
                total = sum((line.amount for line in lines if line.amount is not None), Decimal(0))
                total = total.quantize(Decimal(1).scaleb(-card.minor_units), rounding=ROUND_HALF_UP)
            statement = BillingStatement(
                statement_id=str(uuid4()),
                project_id=project_id,
                period_start=period_start,
                period_end=period_end,
                rate_card_version=card.version,
                currency=card.currency,
                minor_units=card.minor_units,
                status=BillingStatus.COMPLETE if complete else BillingStatus.PROVISIONAL,
                provenance=batch.provenance,
                source=batch.source,
                total=total,
                lines=tuple(lines),
                missing_rates=tuple(sorted(set(missing))),
                created_at=self.clock(),
            )
            if not _is_utc(statement.created_at):
                raise BillingError(BillingErrorCode.INVALID_REQUEST)
            event = self._make_audit_event(
                principal,
                BillingAuditAction.STATEMENT_GENERATE,
                BillingAuditOutcome.SUCCESS,
                correlation,
                project_id=project_id,
                target_id=statement.statement_id,
                occurred_at=statement.created_at,
            )
            return self.ledger.append_statement(statement, audit_event=event)
        except BillingError as error:
            self._record_failed_action(principal, BillingAuditAction.STATEMENT_GENERATE, error, correlation, project_id=project_id)
            raise
        except Exception:
            error = BillingError(BillingErrorCode.LEDGER_UNAVAILABLE)
            self._record_failed_action(principal, BillingAuditAction.STATEMENT_GENERATE, error, correlation, project_id=project_id)
            raise error from None

    def project_statements(
        self,
        principal: BillingPrincipal,
        project_id: str,
        *,
        correlation_id: str | None = None,
    ) -> tuple[BillingStatement, ...]:
        correlation = self._correlation(correlation_id)
        try:
            _require_project_billing_role(principal, project_id)
            statements = self.ledger.list_project_statements(project_id)
        except BillingError as error:
            self._record_failed_action(principal, BillingAuditAction.STATEMENT_LIST, error, correlation, project_id=project_id)
            raise
        self._record_audit_event(
            principal, BillingAuditAction.STATEMENT_LIST, BillingAuditOutcome.SUCCESS,
            correlation, project_id=project_id,
        )
        return statements

    def project_statement(
        self,
        principal: BillingPrincipal,
        project_id: str,
        statement_id: str,
        *,
        correlation_id: str | None = None,
    ) -> BillingStatement:
        correlation = self._correlation(correlation_id)
        statement = self._get_authorized_statement(
            principal, project_id, statement_id, BillingAuditAction.STATEMENT_READ, correlation
        )
        self._record_audit_event(
            principal, BillingAuditAction.STATEMENT_READ, BillingAuditOutcome.SUCCESS,
            correlation, project_id=project_id, target_id=statement.statement_id,
        )
        return statement

    def _get_authorized_statement(
        self,
        principal: BillingPrincipal,
        project_id: str,
        statement_id: str,
        action: BillingAuditAction,
        correlation_id: str,
    ) -> BillingStatement:
        try:
            _require_project_billing_role(principal, project_id)
            return self.ledger.get_project_statement(project_id, statement_id)
        except BillingError as error:
            self._record_failed_action(principal, action, error, correlation_id, project_id=project_id)
            raise

    def project_statement_csv(
        self,
        principal: BillingPrincipal,
        project_id: str,
        statement_id: str,
        *,
        correlation_id: str | None = None,
    ) -> bytes:
        """Return a role-checked, spreadsheet-safe CSV snapshot for one project.

        This is a server-side export primitive, not an HTTP endpoint. The
        project role check runs before the ledger lookup; callers must derive
        ``principal`` from a trusted Keystone/Horizon session adapter.
        """
        correlation = self._correlation(correlation_id)
        statement = self._get_authorized_statement(
                principal, project_id, statement_id, BillingAuditAction.STATEMENT_EXPORT, correlation
        )
        try:
            output = io.StringIO(newline="")
            writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow((
                "record_type", "schema_version", "statement_id", "project_id",
                "period_start_utc", "period_end_utc", "status", "provenance",
                "source", "rate_card_version", "currency", "currency_minor_units",
                "resource_id", "metric", "quantity", "unit", "rate_per_unit",
                "line_amount", "statement_total", "missing_rates",
            ))
            common = (
                statement.statement_id,
                statement.project_id,
                statement.period_start.isoformat(),
                statement.period_end.isoformat(),
                statement.status.value,
                statement.provenance.value,
                statement.source,
                statement.rate_card_version,
                statement.currency,
                statement.minor_units,
            )
            for line in sorted(
                statement.lines,
                key=lambda item: (item.interval_start, item.resource_id, item.metric, item.interval_end, item.unit),
            ):
                writer.writerow(_spreadsheet_safe_cells((
                    "line", 1, *common, line.resource_id, line.metric, line.quantity,
                    line.unit, line.rate_per_unit, line.amount, "", "",
                )))
            writer.writerow(_spreadsheet_safe_cells((
                "statement", 1, *common, "", "", "", "", "", "",
                statement.total if statement.total is not None else "",
                ";".join(statement.missing_rates),
            )))
            content = output.getvalue().encode("utf-8")
        except Exception:
            error = BillingError(BillingErrorCode.INVALID_REQUEST)
            self._record_failed_action(
                principal, BillingAuditAction.STATEMENT_EXPORT, error,
                correlation, project_id=project_id,
            )
            raise error from None
        self._record_audit_event(
            principal, BillingAuditAction.STATEMENT_EXPORT, BillingAuditOutcome.SUCCESS,
            correlation, project_id=project_id, target_id=statement.statement_id,
        )
        return content

    def all_statements(
        self,
        principal: BillingPrincipal,
        *,
        correlation_id: str | None = None,
    ) -> tuple[BillingStatement, ...]:
        correlation = self._correlation(correlation_id)
        try:
            _require_system_role(principal, "cloud_admin")
            statements = self.ledger.list_all_statements()
        except BillingError as error:
            self._record_failed_action(principal, BillingAuditAction.ADMIN_STATEMENT_LIST, error, correlation)
            raise
        self._record_audit_event(
            principal, BillingAuditAction.ADMIN_STATEMENT_LIST, BillingAuditOutcome.SUCCESS,
            correlation,
        )
        return statements

    def audit_events(
        self,
        principal: BillingPrincipal,
        *,
        limit: int = 200,
        correlation_id: str | None = None,
    ) -> tuple[BillingAuditEvent, ...]:
        correlation = self._correlation(correlation_id)
        try:
            _require_system_role(principal, "cloud_admin")
            events = self.ledger.list_audit_events(limit)
        except BillingError as error:
            self._record_failed_action(principal, BillingAuditAction.AUDIT_READ, error, correlation)
            raise
        self._record_audit_event(
            principal, BillingAuditAction.AUDIT_READ, BillingAuditOutcome.SUCCESS, correlation
        )
        return events


def _matching_rate(card: RateCard, sample: UsageSample) -> RateRule | None:
    matches = [
        rule for rule in card.rules
        if rule.metric == sample.metric
        and rule.unit == sample.unit
        and rule.effective_from <= sample.interval_start
        and (rule.effective_until is None or sample.interval_end <= rule.effective_until)
    ]
    return matches[0] if len(matches) == 1 else None


def _require_project_billing_role(principal: BillingPrincipal, project_id: str) -> None:
    _require_authenticated(principal)
    if "cloud_admin" in principal.system_roles:
        return
    for binding in principal.project_bindings:
        if binding.project_id == project_id and binding.roles.intersection({"billing_viewer", "project_admin"}):
            return
    raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)


def _require_system_role(principal: BillingPrincipal, *allowed_roles: str) -> None:
    _require_authenticated(principal)
    if principal.system_roles.isdisjoint(allowed_roles):
        raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)


def _require_authenticated(principal: BillingPrincipal) -> None:
    if not isinstance(principal, BillingPrincipal) or not principal.authenticated:
        raise BillingError(BillingErrorCode.AUTHENTICATION_REQUIRED)


def _spreadsheet_safe_cells(values: tuple[object, ...]) -> tuple[object, ...]:
    """Prefix formula-like text cells so spreadsheet apps do not evaluate them."""
    safe = []
    for value in values:
        if not isinstance(value, str):
            safe.append(value)
            continue
        first_content = value.lstrip(" \t\r\n\ufeff")[:1]
        if first_content in {"=", "+", "-", "@"} or value.startswith(("\t", "\r", "\n", "\ufeff")):
            value = "'" + value
        safe.append(value)
    return tuple(safe)


def _statement_from_wire(value: object) -> BillingStatement:
    if not isinstance(value, dict):
        raise ValueError("statement must be an object")
    lines = tuple(
        BillingLine(
            resource_id=row["resource_id"],
            metric=row["metric"],
            quantity=Decimal(row["quantity"]),
            unit=row["unit"],
            interval_start=datetime.fromisoformat(row["interval_start"]),
            interval_end=datetime.fromisoformat(row["interval_end"]),
            rate_per_unit=Decimal(row["rate_per_unit"]) if row["rate_per_unit"] is not None else None,
            amount=Decimal(row["amount"]) if row["amount"] is not None else None,
        )
        for row in value["lines"]
    )
    return BillingStatement(
        statement_id=value["statement_id"],
        project_id=value["project_id"],
        period_start=datetime.fromisoformat(value["period_start"]),
        period_end=datetime.fromisoformat(value["period_end"]),
        rate_card_version=value["rate_card_version"],
        currency=value["currency"],
        minor_units=value["minor_units"],
        status=BillingStatus(value["status"]),
        provenance=UsageProvenance(value["provenance"]),
        source=value["source"],
        total=Decimal(value["total"]) if value["total"] is not None else None,
        lines=lines,
        missing_rates=tuple(value["missing_rates"]),
        created_at=datetime.fromisoformat(value["created_at"]),
        revision=value["revision"],
    )


def _audit_event_from_row(row: sqlite3.Row) -> BillingAuditEvent:
    return BillingAuditEvent(
        event_id=row["event_id"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        actor_id=row["actor_id"],
        project_id=row["project_id"],
        action=BillingAuditAction(row["action"]),
        target_id=row["target_id"],
        outcome=BillingAuditOutcome(row["outcome"]),
        correlation_id=row["correlation_id"],
    )


def _encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _statement_fingerprint(statement: BillingStatement) -> str:
    payload = statement.to_wire()
    payload.pop("statement_id")
    payload.pop("created_at")
    payload.pop("revision")
    return hashlib.sha256(_encode(payload).encode("utf-8")).hexdigest()


def _valid_money(value: object) -> bool:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("money and rates must be finite non-negative Decimal values")
    return True


def _safe_text(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ValueError("value must be a non-empty single-line string")
    return True


def _safe_source(value: object) -> bool:
    _safe_text(value)
    if len(value) > 128 or any(character in value for character in ("@", "?", "=", "\\")) or "://" in value:
        raise ValueError("source must be a safe source label, not a URL or credential")
    return True


def _is_utc(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _is_utc_calendar_month(start: object, end: object) -> bool:
    if not _is_utc(start) or not _is_utc(end):
        return False
    if start.day != 1 or any((start.hour, start.minute, start.second, start.microsecond)):
        return False
    next_month_year = start.year + (1 if start.month == 12 else 0)
    next_month = 1 if start.month == 12 else start.month + 1
    expected_end = datetime(next_month_year, next_month, 1, tzinfo=timezone.utc)
    return end == expected_end
