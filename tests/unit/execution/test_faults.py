"""Fault tests (document 05 §15): crashes around the broker call and runs of order errors."""

from datetime import timedelta
from typing import Any

import pytest

from aitrader.broker.sim import SimFault, SimOperation
from aitrader.domain import OrderRequest, OrderStatus, SystemState
from aitrader.execution import (
    ExecutionEvent,
    ExecutionEventKind,
    OrderErrorMonitor,
    OrderManager,
)
from aitrader.risk import Actor, HaltReason, HaltStateMachine
from tests.unit.execution.builders import NOW, SPY, D, Harness, decision, harness, intent, quote

K = ExecutionEventKind


class ProcessCrash(BaseException):
    """Not an Exception: nothing in the order manager may catch it."""


class CrashingBroker:
    def __init__(self, inner: Any, *, after_accept: bool) -> None:
        self.inner = inner
        self.after_accept = after_accept

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def submit(self, request: OrderRequest) -> Any:
        if self.after_accept:
            self.inner.submit(request)
        raise ProcessCrash


def crash_during_submit(h: Harness, *, after_accept: bool) -> str:
    crashing = OrderManager(
        CrashingBroker(h.broker, after_accept=after_accept),
        h.gate,
        h.store,
        h.ledger,
        h.clock,
    )
    entry = intent(intent_id="buy-1")
    with pytest.raises(ProcessCrash):
        crashing.submit(entry, decision(entry), SPY, h.context())
    (record,) = h.store.active()
    assert record.status is OrderStatus.PENDING_SUBMIT  # the write-ahead record survived
    return record.client_order_id


def restart(h: Harness) -> OrderManager:
    """A new process: same store, same ledger, same broker."""
    return OrderManager(h.broker, h.gate, h.store, h.ledger, h.clock, fills_from=NOW)


def test_crash_before_the_broker_received_the_order() -> None:
    h = harness()
    cid = crash_during_submit(h, after_accept=False)
    manager = restart(h)
    assert [(e.kind, e.rule_id) for e in manager.sync()] == [(K.UNKNOWN, "ORD-ERR-3")]
    h.advance(minutes=3)
    assert [e.kind for e in manager.sync()] == [K.NOT_FOUND]
    record = h.store.get(cid)
    assert record is not None
    assert record.status is OrderStatus.NOT_FOUND
    assert h.broker.open_orders() == []


def test_crash_after_the_broker_accepted_the_order() -> None:
    h = harness()
    cid = crash_during_submit(h, after_accept=True)
    manager = restart(h)
    events = manager.sync()
    assert [e.kind for e in events] == [K.UNKNOWN, K.STATUS_CHANGED]
    record = h.store.get(cid)
    assert record is not None
    assert record.status is OrderStatus.SUBMITTED
    h.broker.on_quote(quote())
    assert [e.kind for e in manager.sync()] == [K.FILL_BOOKED]
    position = h.ledger.position("SPY")
    assert position is not None
    assert position.quantity == D("0.3")
    assert len(h.broker.fills_since(NOW)) == 1  # one order, one fill: nothing sent twice


def test_crash_after_the_order_filled() -> None:
    h = harness()
    cid = crash_during_submit(h, after_accept=True)
    h.broker.on_quote(quote())
    manager = restart(h)
    kinds = [e.kind for e in manager.sync()]
    assert kinds == [K.UNKNOWN, K.FILL_BOOKED]
    record = h.store.get(cid)
    assert record is not None
    assert record.status is OrderStatus.FILLED


# ---------------------------------------------------------------- order errors drive the halt state


def event(
    rule: str | None, minutes: float = 0, kind: ExecutionEventKind = K.REJECTED
) -> ExecutionEvent:
    return ExecutionEvent(ts=NOW + timedelta(minutes=minutes), kind=kind, rule_id=rule)


def running() -> HaltStateMachine:
    machine = HaltStateMachine()
    machine.on_reconciliation(NOW - timedelta(hours=1), clean=True)
    return machine


def test_one_rejection_warns_two_in_a_session_halt() -> None:
    halt = running()
    monitor = OrderErrorMonitor(halt)
    assert monitor.observe([event("ORD-ERR-1")]) == []
    assert halt.state is SystemState.RUNNING
    (transition,) = monitor.observe([event("ORD-ERR-1", 30)])
    assert transition.to_state is SystemState.HALTED
    assert transition.reason is HaltReason.ORDER_ERRORS
    assert "ORD-ERR-2" in transition.detail


def test_rejections_are_counted_per_session() -> None:
    halt = running()
    monitor = OrderErrorMonitor(halt)
    monitor.observe([event("ORD-ERR-1")])
    monitor.observe([event("ORD-ERR-1", 60 * 24)])
    assert halt.state is SystemState.RUNNING


@pytest.mark.parametrize(
    ("rule", "state", "reason"),
    [
        ("ORD-ERR-2", SystemState.HALTED, HaltReason.ORDER_ERRORS),
        ("ORD-ERR-3", SystemState.DEGRADED, HaltReason.BROKER_UNAVAILABLE),
        ("ORD-ERR-4", SystemState.HALTED, HaltReason.RECONCILIATION_MISMATCH),
        ("AUTH-4", SystemState.HALTED, HaltReason.AUTHENTICATION),
    ],
)
def test_order_error_actions(rule: str, state: SystemState, reason: HaltReason) -> None:
    halt = running()
    (transition,) = OrderErrorMonitor(halt).observe([event(rule)])
    assert halt.state is state
    assert transition.reason is reason


def test_repeated_rate_limits_degrade() -> None:
    halt = running()
    monitor = OrderErrorMonitor(halt)
    assert monitor.observe([event("ORD-ERR-5")]) == []
    assert monitor.observe([event("ORD-ERR-5", 6)]) == []  # outside the 5-minute window
    (transition,) = monitor.observe([event("ORD-ERR-5", 10)])
    assert transition.reason is HaltReason.RATE_LIMITED
    assert halt.state is SystemState.DEGRADED


def test_events_without_a_rule_change_nothing() -> None:
    halt = running()
    assert OrderErrorMonitor(halt).observe([event(None, kind=K.SUBMITTED)]) == []
    assert halt.state is SystemState.RUNNING


def test_a_run_of_rejections_stops_new_entries_end_to_end() -> None:
    h = harness("1000")
    halt = running()
    monitor = OrderErrorMonitor(halt)
    for n in (1, 2):
        h.broker.inject(SimOperation.SUBMIT, SimFault.REJECT, code="PLACE_FAILED")
        monitor.observe(h.buy("0.1", intent_id=f"buy-{n}").events)
    assert halt.state is SystemState.HALTED
    entry = intent(intent_id="buy-3", target_quantity=D("0.1"))
    blocked = h.manager.submit(
        entry, decision(entry, D("0.1")), SPY, h.context(system_state=halt.state)
    )
    assert blocked.record is None
    assert "HC-9" in blocked.gate.failed_rules
    halt.resume(NOW + timedelta(hours=1), Actor.OWNER, "codes reviewed")
    assert halt.allows_entries


def test_authentication_failure_halts_end_to_end() -> None:
    h = harness()
    halt = running()
    h.broker.inject(SimOperation.SUBMIT, SimFault.AUTH_FAILURE)
    OrderErrorMonitor(halt).observe(h.buy().events)
    assert halt.state is SystemState.HALTED
    assert halt.halt_reason is HaltReason.AUTHENTICATION
