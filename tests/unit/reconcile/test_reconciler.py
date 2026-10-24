"""Reconciliation: every difference kind, tolerances, outcomes and the halt state."""

import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from aitrader.broker.port import BrokerOrder, BrokerUnavailableError
from aitrader.broker.sim import SimFault, SimOperation
from aitrader.domain import (
    Currency,
    IntentPurpose,
    Money,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    SystemState,
)
from aitrader.portfolio import Ledger
from aitrader.reconcile import (
    DifferenceKind,
    Outcome,
    Reconciler,
    ReconciliationReport,
    Scope,
    Tolerances,
    cash_matches,
    compare_orders,
    compare_positions,
    quantities_match,
)
from aitrader.risk import HaltReason, HaltStateMachine
from aitrader.storage import SCHEMA, Database, ReconciliationLog
from tests.unit.execution.builders import NOW, Harness, harness, quote

D = Decimal
TOL = Tolerances()


def reconciler(h: Harness, halt: HaltStateMachine | None = None, **kw: Any) -> Reconciler:
    return Reconciler(
        broker=h.broker,
        manager=h.manager,
        store=h.store,
        ledger=h.ledger,
        halt=halt or HaltStateMachine(),
        **kw,
    )


# ---------------------------------------------------------------- comparisons


def test_quantity_tolerance() -> None:
    assert quantities_match(D(3), D(3), TOL)
    assert not quantities_match(D(3), D(4), TOL)
    assert quantities_match(D("0.300000"), D("0.3000009"), TOL)
    assert not quantities_match(D("0.3"), D("0.300002"), TOL)


def test_cash_tolerance_is_the_larger_of_absolute_and_percent() -> None:
    assert cash_matches(D("100.04"), D(100), D("0.05"), D("0.1"))
    assert cash_matches(D("100.10"), D(100), D("0.05"), D("0.1"))  # 0.1% of 100 = 0.10
    assert not cash_matches(D("100.11"), D(100), D("0.05"), D("0.1"))
    assert not cash_matches(D("10.06"), D(10), D("0.05"), D("0.1"))


def test_position_differences_on_either_side() -> None:
    differences = compare_positions(
        {"SPY": D("0.3"), "QQQ": D(1)}, {"SPY": D("0.3"), "IWM": D(2)}, TOL
    )
    assert [(d.key, d.local, d.broker) for d in differences] == [
        ("IWM", "0", "2"),
        ("QQQ", "1", "0"),
    ]
    assert all(d.kind is DifferenceKind.POSITION for d in differences)


def _broker_order(cid: str, quantity: str = "0.3") -> BrokerOrder:
    return BrokerOrder(
        client_order_id=cid,
        broker_order_id=f"B-{cid}",
        symbol="SPY",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=D(quantity),
        limit_price=D(100),
        status=OrderStatus.SUBMITTED,
        updated_at=NOW,
    )


def test_order_differences() -> None:
    h = harness()
    known = h.buy("0.3", intent_id="buy-1").record
    assert known is not None
    cid = known.client_order_id
    local = h.store.active()
    assert compare_orders(local, [_broker_order(cid)]) == []
    kinds = {
        d.kind
        for d in compare_orders(local, [_broker_order(cid, "0.2"), _broker_order("MANUAL-1")])
    }
    assert kinds == {DifferenceKind.ORDER_QUANTITY, DifferenceKind.ORDER_UNKNOWN_LOCALLY}
    (missing,) = compare_orders(local, [])
    assert missing.kind is DifferenceKind.ORDER_MISSING_AT_BROKER


# ---------------------------------------------------------------- full runs with the SimBroker


def test_clean_after_normal_trading_takes_safe_to_running() -> None:
    h = harness()
    h.buy()
    h.broker.on_quote(quote())
    halt = HaltStateMachine()
    report, transition = reconciler(h, halt).run(NOW)
    assert report.outcome is Outcome.CLEAN
    assert report.differences == ()
    assert transition is not None
    assert halt.state is SystemState.RUNNING
    assert h.ledger.position("SPY") is not None  # the sync booked the fill first


def test_an_order_placed_outside_the_engine_halts() -> None:
    h = harness()
    halt = HaltStateMachine()
    manual = OrderRequest(
        client_order_id="APP-ORDER-1",
        intent_id="manual",
        symbol="SPY",
        side=Side.BUY,
        purpose=IntentPurpose.ENTRY,
        order_type=OrderType.LIMIT,
        quantity=D("0.1"),
        limit_price=D(90),
    )
    h.broker.submit(manual)  # e.g. typed into the broker's app
    report, _ = reconciler(h, halt).run(NOW)
    assert report.outcome is Outcome.MISMATCH
    assert [d.kind for d in report.differences] == [DifferenceKind.ORDER_UNKNOWN_LOCALLY]
    assert halt.state is SystemState.HALTED
    assert halt.halt_reason is HaltReason.RECONCILIATION_MISMATCH
    assert "APP-ORDER-1" in halt.history[-1].detail


def test_cash_difference_only_in_the_full_scope() -> None:
    h = harness()
    h.broker.fund(Money.of(5, Currency.USD))  # a deposit the engine did not book
    quick, _ = reconciler(h).run(NOW, Scope.ORDERS_AND_POSITIONS)
    assert quick.outcome is Outcome.CLEAN
    full, _ = reconciler(h).run(NOW, Scope.FULL)
    assert full.outcome is Outcome.MISMATCH
    (difference,) = full.differences
    assert (difference.kind, difference.key) == (DifferenceKind.CASH, "USD settled")


def test_position_the_engine_does_not_know_halts() -> None:
    h = harness()
    h.buy()
    h.broker.on_quote(quote())
    h.manager.sync()
    # an engine ledger that lost the fill: the broker holds a position the engine does not know
    fresh = Ledger()
    fresh.deposit("fund-1", NOW, Money.of(300, Currency.USD))
    rec = Reconciler(
        broker=h.broker, manager=h.manager, store=h.store, ledger=fresh, halt=HaltStateMachine()
    )
    report, _ = rec.run(NOW, Scope.ORDERS_AND_POSITIONS)
    assert [d.kind for d in report.differences] == [DifferenceKind.POSITION]


def test_unresolved_unknown_orders_make_the_result_incomplete() -> None:
    h = harness()
    halt = HaltStateMachine()
    h.broker.inject(SimOperation.SUBMIT, SimFault.TIMEOUT_BEFORE_ACCEPT)
    h.buy()
    report, transition = reconciler(h, halt).run(NOW)
    assert report.outcome is Outcome.INCOMPLETE
    assert len(report.unresolved_orders) == 1
    assert transition is None
    assert halt.state is SystemState.SAFE  # no conclusion, no change
    h.advance(minutes=3)
    later, _ = reconciler(h, halt).run(NOW + timedelta(minutes=3))
    assert later.outcome is Outcome.CLEAN  # NOT_FOUND after the window
    assert halt.allows_entries


def test_broker_unavailable_makes_the_result_incomplete() -> None:
    h = harness()
    halt = HaltStateMachine()

    class Down:
        def __getattr__(self, name: str) -> Any:
            return getattr(h.broker, name)

        def positions(self) -> list[Any]:
            raise BrokerUnavailableError("timeout")

    rec = Reconciler(broker=Down(), manager=h.manager, store=h.store, ledger=h.ledger, halt=halt)
    report, transition = rec.run(NOW)
    assert report.outcome is Outcome.INCOMPLETE
    assert report.differences[0].kind is DifferenceKind.BROKER_UNAVAILABLE
    assert transition is None


def test_reports_are_recorded_and_stored(tmp_path: Path) -> None:
    h = harness()
    with Database(tmp_path / "engine.db") as db:
        log = ReconciliationLog(db)
        assert log.latest() is None
        reconciler(h, record=log.record).run(NOW)
        h.broker.fund(Money.of(5, Currency.USD))
        reconciler(h, record=log.record).run(NOW + timedelta(minutes=15))
        outcomes = [r.outcome for r in log.all()]
        assert outcomes == [Outcome.CLEAN, Outcome.MISMATCH]
        latest = log.latest()
        assert isinstance(latest, ReconciliationReport)
        assert latest.differences[0].kind is DifferenceKind.CASH


def test_schema_1_databases_are_migrated(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (1)")
    for statement in SCHEMA[0]:
        conn.execute(statement)
    conn.commit()
    conn.close()
    with Database(path) as db:
        assert db.schema_version == len(SCHEMA)
        assert ReconciliationLog(db).all() == []
