"""SQLite storage: durability across restarts, rollbacks, locks and crash recovery."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aitrader.domain import (
    Currency,
    Fill,
    Money,
    OrderStatus,
    Side,
    SystemState,
    TradeCost,
    TradeCostType,
)
from aitrader.execution import DuplicateOrderError, OrderManager
from aitrader.portfolio import CashAccountError, DuplicateEntryError, Ledger
from aitrader.risk import Actor, HaltReason, IllegalTransitionError
from aitrader.storage import (
    SCHEMA,
    Database,
    DecisionLog,
    DurableHaltStateMachine,
    DurableLedger,
    InstanceLockedError,
    RunLog,
    SingleInstanceLock,
    SqliteOrderStore,
    StorageBusyError,
    StorageError,
    acquire_lease,
    journal_entries,
    release_lease,
)
from tests.unit.execution.builders import NOW, SPY, D, decision, harness, intent, quote

pytestmark = [
    pytest.mark.filterwarnings("error::ResourceWarning"),
    pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning"),
]

T0 = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)


def db_at(tmp_path: Path, name: str = "engine.db", **kw: Any) -> Database:
    return Database(tmp_path / name, **kw)


# ---------------------------------------------------------------- database


def test_new_database_has_the_current_schema(tmp_path: Path) -> None:
    with db_at(tmp_path) as db:
        assert db.schema_version == len(SCHEMA)
        tables = {
            r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {
            "orders",
            "order_events",
            "journal",
            "halts",
            "risk_decisions",
            "runs",
            "leases",
        } <= tables
        assert db.query("PRAGMA journal_mode")[0][0] == "wal"
    with db_at(tmp_path) as again:  # reopening migrates nothing twice
        assert again.schema_version == len(SCHEMA)


def test_a_newer_schema_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with db_at(tmp_path) as db, db.transaction() as conn:
        conn.execute("UPDATE schema_version SET version = 99")
    connection = sqlite3.connect(tmp_path / "engine.db", isolation_level=None)
    monkeypatch.setattr("aitrader.storage.database.sqlite3.connect", lambda *a, **kw: connection)
    with pytest.raises(StorageError, match="newer than this program"):
        db_at(tmp_path)
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def _insert_then_fail(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute("INSERT INTO leases VALUES ('x', 'me', '2026-01-01')")
        raise RuntimeError("boom")


def test_transactions_roll_back_on_errors(tmp_path: Path) -> None:
    with db_at(tmp_path) as db:
        with pytest.raises(RuntimeError):
            _insert_then_fail(db)
        assert db.query("SELECT * FROM leases") == []


def test_database_locked_is_reported_as_busy(tmp_path: Path) -> None:
    holder = db_at(tmp_path)
    other = db_at(tmp_path, busy_timeout_ms=0)
    with holder.transaction(), pytest.raises(StorageBusyError), other.transaction() as conn:
        conn.execute("SELECT 1")
    holder.close()
    other.close()


def test_backup_is_a_consistent_copy(tmp_path: Path) -> None:
    with db_at(tmp_path) as db:
        RunLog(db).start("run-1", "paper", "abc", "hash", T0)
        db.backup(tmp_path / "copy.db")
    with Database(tmp_path / "copy.db") as copy:
        assert [r["run_id"] for r in RunLog(copy).runs()] == ["run-1"]


# ---------------------------------------------------------------- durable ledger


def fill(fill_id: str, side: Side, quantity: str, price: str) -> Fill:
    return Fill(
        fill_id=fill_id,
        client_order_id=f"o-{fill_id}",
        symbol="SPY",
        side=side,
        quantity=D(quantity),
        price=D(price),
        ts=T0,
        source="sim",
    )


def fee() -> TradeCost:
    return TradeCost(
        ts=T0,
        type=TradeCostType.FINRA_FEE,
        amount=Money.of("0.01", Currency.USD),
        estimated=True,
        source="model",
    )


def test_durable_ledger_survives_a_restart(tmp_path: Path) -> None:
    with db_at(tmp_path) as db:
        ledger = DurableLedger(db)
        ledger.deposit("dep", T0, Money.of(300, Currency.USD))
        ledger.record_fill(fill("b1", Side.BUY, "1.23456", "100.01"))
        ledger.record_fill(
            fill("s1", Side.SELL, "0.5", "101"), [fee()], settles_on=date(2026, 10, 23)
        )
        before = Ledger.from_entries(ledger.entries)
    with db_at(tmp_path) as db:
        rebuilt = DurableLedger(db)
        assert Ledger.from_entries(rebuilt.entries) == before
        assert rebuilt.positions == before.positions
        assert rebuilt.pending_settlements == before.pending_settlements
        assert list(journal_entries(db)) == list(before.entries)


def test_refused_entries_are_not_stored(tmp_path: Path) -> None:
    with db_at(tmp_path) as db:
        ledger = DurableLedger(db)
        ledger.deposit("dep", T0, Money.of(10, Currency.USD))
        with pytest.raises(CashAccountError):
            ledger.record_fill(fill("b1", Side.BUY, "1", "100"))
        assert len(db.query("SELECT * FROM journal")) == 1
        # the same entry again is a no-op, a different one under its ID is refused
        ledger.deposit("dep", T0, Money.of(10, Currency.USD))
        with pytest.raises(DuplicateEntryError):
            ledger.deposit("dep", T0, Money.of(20, Currency.USD))
        assert len(db.query("SELECT * FROM journal")) == 1


def test_a_failed_write_is_not_applied(tmp_path: Path) -> None:
    holder = db_at(tmp_path)
    blocked = db_at(tmp_path, busy_timeout_ms=0)
    ledger = DurableLedger(blocked)
    with holder.transaction(), pytest.raises(StorageBusyError):
        ledger.deposit("dep", T0, Money.of(10, Currency.USD))
    assert ledger.entries == ()
    ledger.deposit("dep", T0, Money.of(10, Currency.USD))  # works once the lock is free
    assert ledger.cash_usd_settled == 10
    holder.close()
    blocked.close()


def test_a_failed_commit_stops_the_ledger_until_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_at(tmp_path)
    ledger = DurableLedger(db)
    connection = db._conn

    @contextmanager
    def commit_fails() -> Iterator[sqlite3.Connection]:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("ROLLBACK")
        raise StorageError("commit failed: disk I/O error")

    monkeypatch.setattr(db, "transaction", commit_fails)
    with pytest.raises(StorageError, match="commit failed"):
        ledger.deposit("dep", T0, Money.of(10, Currency.USD))
    with pytest.raises(StorageError, match="out of sync"):
        ledger.deposit("dep-2", T0, Money.of(10, Currency.USD))
    monkeypatch.undo()
    assert DurableLedger(db).entries == ()  # the restart rebuilds from what was stored
    db.close()


# ---------------------------------------------------------------- durable halt state


def test_halts_survive_a_restart(tmp_path: Path) -> None:
    with db_at(tmp_path) as db:
        halt = DurableHaltStateMachine(db)
        halt.on_reconciliation(T0, clean=True)
        halt.halt(HaltReason.MONTHLY_LOSS, T0 + timedelta(hours=1), "5% monthly loss")
    with db_at(tmp_path) as db:
        restarted = DurableHaltStateMachine(db)
        assert restarted.state is SystemState.HALTED
        assert restarted.on_process_start(T0 + timedelta(days=1)) is None
        assert restarted.halt_reason is HaltReason.MONTHLY_LOSS
        with pytest.raises(IllegalTransitionError):
            restarted.resume(T0 + timedelta(days=1), Actor.SYSTEM)
        assert len(db.query("SELECT * FROM halts")) == 2  # the refused resume was not stored


def test_a_transition_that_cannot_be_stored_is_not_taken(tmp_path: Path) -> None:
    holder = db_at(tmp_path)
    blocked = db_at(tmp_path, busy_timeout_ms=0)
    halt = DurableHaltStateMachine(blocked)
    with holder.transaction(), pytest.raises(StorageBusyError):
        halt.on_reconciliation(T0, clean=True)
    assert halt.state is SystemState.SAFE
    assert halt.history == ()
    holder.close()
    blocked.close()


# ---------------------------------------------------------------- decisions and runs


def test_decision_log(tmp_path: Path) -> None:
    with db_at(tmp_path) as db:
        log = DecisionLog(db)
        entry = intent()
        log.record(T0, entry, decision(entry))
        ((stored_intent, stored_decision),) = log.all()
        assert stored_intent == entry
        assert stored_decision == decision(entry)
        with pytest.raises(ValueError, match="another intent"):
            log.record(T0, intent(intent_id="other"), decision(entry))


def test_run_log(tmp_path: Path) -> None:
    with db_at(tmp_path) as db:
        runs = RunLog(db)
        runs.start("run-1", "paper", "abc123", "cfg", T0)
        runs.end("run-1", T0 + timedelta(hours=8))
        with pytest.raises(StorageError, match="already ended"):
            runs.end("run-1", T0 + timedelta(hours=9))
        with pytest.raises(StorageError, match="unknown"):
            runs.end("run-2", T0)
        (run,) = runs.runs()
        assert run["git_commit"] == "abc123"
        assert run["ended_at"] == (T0 + timedelta(hours=8)).isoformat()
        with pytest.raises(sqlite3.IntegrityError):
            runs.start("run-1", "paper", "abc123", "cfg", T0)


# ---------------------------------------------------------------- single instance (DUP-5)


def test_file_lock_can_be_reacquired_after_writing_pid(tmp_path: Path) -> None:
    lock = SingleInstanceLock(tmp_path / "engine.db")
    for _ in range(2):
        lock.acquire()
        lock.release()
        assert lock.path.read_text(encoding="utf-8") == str(os.getpid())


def test_a_second_engine_cannot_take_the_file_lock(tmp_path: Path) -> None:
    path = tmp_path / "engine.db"
    with (
        SingleInstanceLock(path) as first,
        pytest.raises(InstanceLockedError, match="held by another process"),
    ):
        SingleInstanceLock(path).acquire()
    # Windows byte-range locks also block reads through another handle while held.
    assert first.path.read_text(encoding="utf-8") == str(os.getpid())
    second = SingleInstanceLock(path)
    second.acquire()  # free again once the first released it
    second.release()
    second.release()  # releasing twice is harmless


def test_lease_per_mode_and_account(tmp_path: Path) -> None:
    ttl = timedelta(minutes=2)
    with db_at(tmp_path) as db:
        acquire_lease(db, "paper/SIM-1", "host-a", T0, ttl)
        acquire_lease(db, "paper/SIM-1", "host-a", T0 + timedelta(minutes=1), ttl)  # renew
        with pytest.raises(InstanceLockedError, match="held by host-a"):
            acquire_lease(db, "paper/SIM-1", "host-b", T0 + timedelta(minutes=2), ttl)
        acquire_lease(db, "paper/SIM-1", "host-b", T0 + timedelta(minutes=4), ttl)  # expired
        release_lease(db, "paper/SIM-1", "host-a")  # not the holder: nothing changes
        assert db.query("SELECT holder FROM leases")[0][0] == "host-b"
        release_lease(db, "paper/SIM-1", "host-b")
        assert db.query("SELECT * FROM leases") == []


# ---------------------------------------------------------------- crash recovery with SQLite


class ProcessCrash(BaseException):
    pass


class CrashAfterAccept:
    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def submit(self, request: Any) -> Any:
        self.inner.submit(request)
        raise ProcessCrash


def test_crash_after_the_broker_accepted_recovers_from_the_database(tmp_path: Path) -> None:
    h = harness()
    path = tmp_path / "engine.db"
    db = Database(path)
    store = SqliteOrderStore(db)
    ledger = DurableLedger(db)
    ledger.deposit("fund-1", NOW, Money.of(300, Currency.USD))
    crashing = OrderManager(CrashAfterAccept(h.broker), h.gate, store, ledger, h.clock)
    entry = intent(intent_id="buy-1")
    with pytest.raises(ProcessCrash):
        crashing.submit(entry, decision(entry), SPY, h.context())
    db.close()  # the process is gone

    db = Database(path)  # a new process
    store = SqliteOrderStore(db)
    ledger = DurableLedger(db)
    (pending,) = store.active()
    assert pending.status is OrderStatus.PENDING_SUBMIT
    manager = OrderManager(h.broker, h.gate, store, ledger, h.clock, fills_from=NOW)
    h.broker.on_quote(quote())
    manager.sync()
    final = store.get(pending.client_order_id)
    assert final is not None
    assert final.status is OrderStatus.FILLED
    assert ledger.position("SPY") == h.broker.ledger.position("SPY")
    with pytest.raises(DuplicateOrderError):
        store.insert(final, "AGAIN")
    events = [e.event for e in store.events(pending.client_order_id)]
    assert events == ["WRITE_AHEAD", "RECOVERED", "FILL"]
    db.close()
