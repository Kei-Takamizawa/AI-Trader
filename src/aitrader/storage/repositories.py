"""Repositories on the SQLite database: orders, journal, halt history, decisions and runs."""

import sqlite3
from collections.abc import Iterable
from datetime import datetime

from aitrader.domain import OrderIntent, OrderStatus, RiskDecision, SystemState
from aitrader.execution import (
    DuplicateOrderError,
    OrderEvent,
    OrderRecord,
    check_transition,
)
from aitrader.portfolio import JournalEntry, Ledger
from aitrader.reconcile import ReconciliationReport
from aitrader.risk import Actor, HaltReason, HaltStateMachine, HaltTransition
from aitrader.storage.database import Database, StorageError


def _ts(value: datetime) -> str:
    return value.isoformat()


class SqliteOrderStore:
    """The ``OrderStore`` port on SQLite; the database is the only copy of the records."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, record: OrderRecord, event: str) -> None:
        with self._db.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM orders WHERE client_order_id = ?", (record.client_order_id,)
            ).fetchone()
            if exists:
                raise DuplicateOrderError(f"order {record.client_order_id} already exists")
            try:
                conn.execute(
                    "INSERT INTO orders (client_order_id, intent_id, attempt, status, active,"
                    " created_at, updated_at, record_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.client_order_id,
                        record.intent_id,
                        record.attempt,
                        record.status.value,
                        int(record.is_active),
                        _ts(record.created_at),
                        _ts(record.updated_at),
                        record.model_dump_json(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateOrderError(
                    f"intent {record.intent_id} already has an active order"
                ) from exc
            self._log(conn, record, event, None, record.last_error)

    def update(self, record: OrderRecord, event: str, detail: str = "") -> None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT status FROM orders WHERE client_order_id = ?", (record.client_order_id,)
            ).fetchone()
            if row is None:
                raise KeyError(record.client_order_id)
            current = OrderStatus(row["status"])
            check_transition(current, record.status)
            conn.execute(
                "UPDATE orders SET status = ?, active = ?, updated_at = ?, record_json = ?"
                " WHERE client_order_id = ?",
                (
                    record.status.value,
                    int(record.is_active),
                    _ts(record.updated_at),
                    record.model_dump_json(),
                    record.client_order_id,
                ),
            )
            self._log(conn, record, event, current, detail)

    def get(self, client_order_id: str) -> OrderRecord | None:
        rows = self._db.query(
            "SELECT record_json FROM orders WHERE client_order_id = ?", (client_order_id,)
        )
        return OrderRecord.model_validate_json(rows[0]["record_json"]) if rows else None

    def for_intent(self, intent_id: str) -> list[OrderRecord]:
        rows = self._db.query(
            "SELECT record_json FROM orders WHERE intent_id = ? ORDER BY attempt", (intent_id,)
        )
        return [OrderRecord.model_validate_json(r["record_json"]) for r in rows]

    def active(self) -> list[OrderRecord]:
        rows = self._db.query(
            "SELECT record_json FROM orders WHERE active = 1 ORDER BY created_at, client_order_id"
        )
        return [OrderRecord.model_validate_json(r["record_json"]) for r in rows]

    def events(self, client_order_id: str | None = None) -> list[OrderEvent]:
        if client_order_id is None:
            rows = self._db.query("SELECT * FROM order_events ORDER BY seq")
        else:
            rows = self._db.query(
                "SELECT * FROM order_events WHERE client_order_id = ? ORDER BY seq",
                (client_order_id,),
            )
        return [
            OrderEvent(
                ts=datetime.fromisoformat(r["ts"]),
                client_order_id=r["client_order_id"],
                event=r["event"],
                from_status=OrderStatus(r["from_status"]) if r["from_status"] else None,
                to_status=OrderStatus(r["to_status"]),
                detail=r["detail"],
            )
            for r in rows
        ]

    @staticmethod
    def _log(
        conn: sqlite3.Connection,
        record: OrderRecord,
        event: str,
        from_status: OrderStatus | None,
        detail: str,
    ) -> None:
        conn.execute(
            "INSERT INTO order_events (client_order_id, ts, event, from_status, to_status, detail)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.client_order_id,
                _ts(record.updated_at),
                event,
                from_status.value if from_status else None,
                record.status.value,
                detail,
            ),
        )


class DurableLedger(Ledger):
    """A ledger whose journal lives in the database.

    Each entry is written in the same transaction in which it is applied, so a refused entry is
    never stored and a failed write is never applied. If the commit itself fails after the
    entry was applied in memory, the ledger refuses all further entries until the process
    restarts and rebuilds it from the database (fail closed).
    """

    def __init__(self, db: Database) -> None:
        super().__init__()
        self._db = db
        self._out_of_sync = False
        rows = db.query("SELECT entry_json FROM journal ORDER BY seq")
        for row in rows:
            super().apply(JournalEntry.model_validate_json(row["entry_json"]))

    def apply(self, entry: JournalEntry) -> JournalEntry:
        if self._out_of_sync:
            raise StorageError("the ledger is out of sync with the database; restart")
        if self.entry(entry.entry_id) is not None:
            return super().apply(entry)  # the same entry again, or a duplicate-ID error
        committed = False
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO journal (entry_id, ts, kind, entry_json) VALUES (?, ?, ?, ?)",
                    (entry.entry_id, _ts(entry.ts), entry.kind.value, entry.model_dump_json()),
                )
                applied = super().apply(entry)
                committed = True
        except StorageError:
            if committed:  # applied in memory, but the commit failed
                self._out_of_sync = True
            raise
        return applied


class DurableHaltStateMachine(HaltStateMachine):
    """Halt states that survive restarts (document 05 §7)."""

    def __init__(self, db: Database) -> None:
        self._db = db
        rows = db.query("SELECT transition_json FROM halts ORDER BY seq")
        super().__init__(HaltTransition.model_validate_json(r["transition_json"]) for r in rows)

    def transition(
        self,
        to_state: SystemState,
        reason: HaltReason,
        actor: Actor,
        ts: datetime,
        detail: str = "",
    ) -> HaltTransition:
        transition = super().transition(to_state, reason, actor, ts, detail)
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO halts (ts, from_state, to_state, reason, actor, transition_json)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        _ts(transition.ts),
                        transition.from_state.value,
                        transition.to_state.value,
                        transition.reason.value,
                        transition.actor.value,
                        transition.model_dump_json(),
                    ),
                )
        except BaseException:
            self._history.pop()  # not stored, so not taken
            raise
        return transition


class DecisionLog:
    """Every risk decision with the intent it judged (audit trail)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, ts: datetime, intent: OrderIntent, decision: RiskDecision) -> None:
        if decision.intent_id != intent.intent_id:
            raise ValueError("the decision belongs to another intent")
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO risk_decisions (ts, intent_id, verdict, approved_quantity,"
                " limits_hash, intent_json, decision_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _ts(ts),
                    intent.intent_id,
                    decision.verdict.value,
                    str(decision.approved_quantity),
                    decision.limits_hash,
                    intent.model_dump_json(),
                    decision.model_dump_json(),
                ),
            )

    def all(self) -> list[tuple[OrderIntent, RiskDecision]]:
        rows = self._db.query("SELECT intent_json, decision_json FROM risk_decisions ORDER BY seq")
        return [
            (
                OrderIntent.model_validate_json(r["intent_json"]),
                RiskDecision.model_validate_json(r["decision_json"]),
            )
            for r in rows
        ]


class RunLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    def start(
        self, run_id: str, mode: str, git_commit: str, config_hash: str, ts: datetime
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, mode, git_commit, config_hash, started_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, mode, git_commit, config_hash, _ts(ts)),
            )

    def end(self, run_id: str, ts: datetime) -> None:
        with self._db.transaction() as conn:
            updated = conn.execute(
                "UPDATE runs SET ended_at = ? WHERE run_id = ? AND ended_at IS NULL",
                (_ts(ts), run_id),
            ).rowcount
            if updated != 1:
                raise StorageError(f"run {run_id} is unknown or already ended")

    def runs(self) -> list[dict[str, str | None]]:
        rows = self._db.query("SELECT * FROM runs ORDER BY started_at, run_id")
        return [dict(r) for r in rows]


def journal_entries(db: Database) -> Iterable[JournalEntry]:
    """The stored journal, oldest first (for reports and reconciliation)."""
    rows = db.query("SELECT entry_json FROM journal ORDER BY seq")
    return [JournalEntry.model_validate_json(r["entry_json"]) for r in rows]


class ReconciliationLog:
    """Stored reconciliation reports (document 03 §6, ``reconciliations``)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, report: ReconciliationReport) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO reconciliations (ts, scope, outcome, report_json) VALUES (?, ?, ?, ?)",
                (
                    _ts(report.ts),
                    report.scope.value,
                    report.outcome.value,
                    report.model_dump_json(),
                ),
            )

    def all(self) -> list[ReconciliationReport]:
        rows = self._db.query("SELECT report_json FROM reconciliations ORDER BY seq")
        return [ReconciliationReport.model_validate_json(r["report_json"]) for r in rows]

    def latest(self) -> ReconciliationReport | None:
        rows = self._db.query("SELECT report_json FROM reconciliations ORDER BY seq DESC LIMIT 1")
        return ReconciliationReport.model_validate_json(rows[0]["report_json"]) if rows else None
