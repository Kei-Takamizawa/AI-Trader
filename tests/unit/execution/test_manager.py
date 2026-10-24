"""Order manager: write-ahead, UNKNOWN handling, fills, cancels and status adoption."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from aitrader.broker.port import (
    BrokerFill,
    BrokerOrder,
    BrokerUnavailableError,
    OrderNotFoundError,
)
from aitrader.broker.sim import SimBroker, SimFault, SimOperation
from aitrader.domain import (
    Currency,
    Fill,
    IntentPurpose,
    Money,
    OrderStatus,
    OrderType,
    Side,
    SystemState,
)
from aitrader.execution import (
    ExecutionEventKind,
    OrderManager,
    OrderNotActiveError,
    RetryNotAllowedError,
)
from aitrader.portfolio import Ledger
from tests.unit.execution.builders import NOW, SPY, D, Harness, decision, harness, intent, quote

K = ExecutionEventKind


def kinds(events: Any) -> list[tuple[ExecutionEventKind, str | None]]:
    return [(e.kind, e.rule_id) for e in events]


class Proxy:
    """Wraps the SimBroker to hide fills or alter answers for one test."""

    def __init__(self, inner: SimBroker) -> None:
        self.inner = inner
        self.hide_fills = False
        self.status_override: Callable[[str], BrokerOrder] | None = None
        self.cancel_override: Callable[[str], None] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def fills_since(self, ts: datetime) -> list[BrokerFill]:
        return [] if self.hide_fills else self.inner.fills_since(ts)

    def order_status(self, client_order_id: str) -> BrokerOrder:
        if self.status_override is not None:
            return self.status_override(client_order_id)
        return self.inner.order_status(client_order_id)

    def cancel(self, client_order_id: str) -> None:
        if self.cancel_override is not None:
            self.cancel_override(client_order_id)
        else:
            self.inner.cancel(client_order_id)


def with_proxy(h: Harness) -> Proxy:
    proxy = Proxy(h.broker)
    h.manager = OrderManager(proxy, h.gate, h.store, h.ledger, h.clock)
    return proxy


# ---------------------------------------------------------------- happy path


def test_buy_is_written_ahead_submitted_filled_and_booked() -> None:
    h = harness()
    result = h.buy()
    assert result.gate.passed
    assert result.record is not None
    assert result.record.status is OrderStatus.SUBMITTED
    assert result.record.broker_order_id == "SIM-1"
    assert kinds(result.events) == [(K.SUBMITTED, None)]
    cid = result.record.client_order_id
    assert [e.event for e in h.store.events(cid)] == ["WRITE_AHEAD", "ACK"]

    h.broker.on_quote(quote())
    events = h.manager.sync()
    assert kinds(events) == [(K.FILL_BOOKED, None)]
    record = h.store.get(cid)
    assert record is not None
    assert record.status is OrderStatus.FILLED
    assert record.filled_quantity == D("0.3")
    # the engine's books agree with the broker's
    assert h.ledger.position("SPY") == h.broker.ledger.position("SPY")
    assert h.ledger.cash_usd_settled == h.broker.ledger.cash_usd_settled
    assert h.manager.sync() == []  # nothing new


def test_exit_with_a_marketable_limit_and_market_stop_exit() -> None:
    h = harness()
    h.buy()
    h.broker.on_quote(quote())
    h.manager.sync()
    exit_intent = intent(Side.SELL, intent_id="sell-1", target_quantity=D("0.1"))
    result = h.manager.submit(
        exit_intent, decision(exit_intent, D("0.1")), SPY, h.context(), limit_price=D("99.50")
    )
    assert result.record is not None
    assert result.record.request.limit_price == D("99.50")
    stop = intent(
        Side.SELL, intent_id="stop-1", purpose=IntentPurpose.STOP_EXIT, target_quantity=D("0.2")
    )
    stop_result = h.manager.submit(
        stop, decision(stop, D("0.2")), SPY, h.context(), order_type=OrderType.MARKET
    )
    assert stop_result.record is not None
    assert stop_result.record.request.limit_price is None
    h.broker.on_quote(quote())
    h.manager.sync()
    assert h.ledger.position("SPY") is None
    assert h.broker.ledger.position("SPY") is None


def test_gate_rejection_writes_nothing() -> None:
    h = harness()
    entry = intent()
    result = h.manager.submit(entry, decision(entry), SPY, h.context(market_open=False))
    assert result.record is None
    assert not result.gate.passed
    assert kinds(result.events) == [(K.GATE_REJECTED, None)]
    assert result.events[0].detail == "HC-7"
    assert h.store.active() == []
    assert h.broker.open_orders() == []


def test_an_intent_is_submitted_once() -> None:
    h = harness()
    h.buy()
    with pytest.raises(RetryNotAllowedError):
        h.buy()


# ---------------------------------------------------------------- broker answers at submit


def test_business_rejection_drops_the_intent() -> None:
    h = harness()
    h.broker.inject(SimOperation.SUBMIT, SimFault.REJECT, code="PLACE_FAILED")
    result = h.buy()
    assert result.record is not None
    assert result.record.status is OrderStatus.REJECTED
    assert kinds(result.events) == [(K.REJECTED, "ORD-ERR-1")]
    entry = intent(intent_id="buy-1")
    with pytest.raises(RetryNotAllowedError):
        h.manager.retry_not_found(entry, decision(entry), SPY, h.context())


def test_unknown_rejection_code_is_ord_err_2() -> None:
    h = harness()
    h.broker.inject(SimOperation.SUBMIT, SimFault.REJECT, code="NEW_CODE", known=False)
    assert kinds(h.buy().events) == [(K.REJECTED, "ORD-ERR-2")]


@pytest.mark.parametrize(
    ("fault", "kind", "rule"),
    [
        (SimFault.RATE_LIMIT, K.RATE_LIMITED, "ORD-ERR-5"),
        (SimFault.AUTH_FAILURE, K.AUTH_FAILED, "AUTH-4"),
    ],
)
def test_requests_refused_before_processing(
    fault: SimFault, kind: ExecutionEventKind, rule: str
) -> None:
    h = harness()
    h.broker.inject(SimOperation.SUBMIT, fault)
    result = h.buy()
    assert result.record is not None
    assert result.record.status is OrderStatus.REJECTED
    assert kinds(result.events) == [(kind, rule)]
    assert h.broker.open_orders() == []


def test_rejection_reported_later_by_the_broker() -> None:
    h = harness()
    proxy = with_proxy(h)
    record = h.buy().record
    assert record is not None
    snapshot = h.broker.order_status(record.client_order_id)
    proxy.status_override = lambda cid: snapshot.model_copy(
        update={"status": OrderStatus.REJECTED, "reject_code": "RISK_CHECK"}
    )
    assert kinds(h.manager.sync()) == [(K.REJECTED, "ORD-ERR-1")]


# ---------------------------------------------------------------- UNKNOWN, NOT_FOUND, one retry


def test_timeout_before_accept_becomes_not_found_then_one_retry() -> None:
    h = harness()
    h.broker.inject(SimOperation.SUBMIT, SimFault.TIMEOUT_BEFORE_ACCEPT)
    result = h.buy()
    assert result.record is not None
    assert result.record.status is OrderStatus.UNKNOWN
    assert kinds(result.events) == [(K.UNKNOWN, "ORD-ERR-3")]
    assert h.manager.sync() == []  # still inside the reconciliation window
    h.advance(minutes=2)
    assert kinds(h.manager.sync()) == [(K.NOT_FOUND, None)]
    entry = intent(intent_id="buy-1")
    retry = h.manager.retry_not_found(entry, decision(entry), SPY, h.context())
    assert retry.record is not None
    assert retry.record.attempt == 2
    assert retry.record.client_order_id.endswith("-2")
    assert retry.record.status is OrderStatus.SUBMITTED
    with pytest.raises(RetryNotAllowedError):
        h.manager.retry_not_found(entry, decision(entry), SPY, h.context())


def test_a_timed_out_order_that_reached_the_broker_is_never_sent_twice() -> None:
    h = harness()
    h.broker.inject(SimOperation.SUBMIT, SimFault.TIMEOUT_AFTER_ACCEPT)
    record = h.buy().record
    assert record is not None
    assert record.status is OrderStatus.UNKNOWN
    assert kinds(h.manager.sync()) == [(K.STATUS_CHANGED, None)]
    found = h.store.get(record.client_order_id)
    assert found is not None
    assert found.status is OrderStatus.SUBMITTED
    assert found.broker_order_id == "SIM-1"
    assert len(h.broker.open_orders()) == 1
    entry = intent(intent_id="buy-1")
    with pytest.raises(RetryNotAllowedError):
        h.manager.retry_not_found(entry, decision(entry), SPY, h.context())


def test_unknown_order_found_already_filled() -> None:
    h = harness()
    h.broker.inject(SimOperation.SUBMIT, SimFault.TIMEOUT_AFTER_ACCEPT)
    record = h.buy().record
    assert record is not None
    h.broker.on_quote(quote())
    assert kinds(h.manager.sync()) == [(K.FILL_BOOKED, None)]
    final = h.store.get(record.client_order_id)
    assert final is not None
    assert final.status is OrderStatus.FILLED


# ---------------------------------------------------------------- fills


def test_duplicate_fill_events_are_booked_once() -> None:
    h = harness()
    h.buy()
    h.broker.on_quote(quote())
    h.broker.report_fills_twice()
    assert kinds(h.manager.sync()) == [(K.FILL_BOOKED, None)]
    assert h.manager.sync() == []
    position = h.ledger.position("SPY")
    assert position is not None
    assert position.quantity == D("0.3")


def _fill(
    cid: str, quantity: str = "0.3", symbol: str = "SPY", side: Side = Side.BUY
) -> BrokerFill:
    return BrokerFill(
        fill=Fill(
            fill_id=f"X-{cid}-{quantity}-{symbol}",
            client_order_id=cid,
            symbol=symbol,
            side=side,
            quantity=D(quantity),
            price=D(100),
            ts=NOW,
            source="event",
        )
    )


def test_fill_for_an_unknown_order_is_not_booked() -> None:
    h = harness()
    h.broker.report_unexpected_fill(_fill("NOBODY-1"))
    events = h.manager.sync()
    assert kinds(events) == [(K.MISMATCH, "ORD-ERR-4")]
    assert "unknown order" in events[0].detail
    assert h.ledger.positions == {}


@pytest.mark.parametrize(
    ("fill_kwargs", "message"),
    [
        ({"quantity": "0.4"}, "exceed the order quantity"),
        ({"symbol": "QQQ"}, "symbol or side"),
        ({"side": Side.SELL}, "symbol or side"),
    ],
)
def test_fill_that_does_not_match_its_order_is_not_booked(
    fill_kwargs: dict[str, Any], message: str
) -> None:
    h = harness()
    record = h.buy().record
    assert record is not None
    h.broker.report_unexpected_fill(_fill(record.client_order_id, **fill_kwargs))
    events = h.manager.sync()
    assert (K.MISMATCH, "ORD-ERR-4") in kinds(events)
    assert any(message in e.detail for e in events)
    assert h.ledger.positions == {}


def test_fill_on_a_closed_order_is_not_booked() -> None:
    h = harness()
    record = h.buy().record
    assert record is not None
    h.broker.cancel(record.client_order_id)
    h.manager.sync()
    h.broker.report_unexpected_fill(_fill(record.client_order_id, "0.1"))
    events = h.manager.sync()
    assert kinds(events) == [(K.MISMATCH, "ORD-ERR-4")]
    assert "state CANCELLED" in events[0].detail


def test_fill_the_engine_ledger_cannot_pay_is_a_mismatch() -> None:
    h = harness("300")
    h.buy()
    # an engine ledger that lost track of cash: it holds less money than the broker
    poor = Ledger()
    poor.deposit("fund-1", NOW, Money.of(10, Currency.USD))
    h.manager = OrderManager(h.broker, h.gate, h.store, poor, h.clock)
    h.broker.on_quote(quote())
    events = h.manager.sync()
    assert kinds(events) == [(K.MISMATCH, "ORD-ERR-4")]
    assert "ledger refused" in events[0].detail


def test_partial_fills_and_cancel_of_the_rest() -> None:
    h = harness()
    record = h.buy().record
    assert record is not None
    cid = record.client_order_id
    h.broker.on_quote(quote(), max_fill_quantity=D("0.1"))
    h.manager.sync()
    partial = h.store.get(cid)
    assert partial is not None
    assert partial.status is OrderStatus.PARTIALLY_FILLED
    assert kinds(h.manager.cancel(cid)) == [(K.CANCEL_REQUESTED, None)]
    assert kinds(h.manager.sync()) == [(K.STATUS_CHANGED, None)]
    final = h.store.get(cid)
    assert final is not None
    assert final.status is OrderStatus.CANCELLED
    assert final.filled_quantity == D("0.1")


# ---------------------------------------------------------------- adopting broker statuses


def test_terminal_status_waits_for_the_fills_to_be_booked() -> None:
    h = harness()
    proxy = with_proxy(h)
    record = h.buy().record
    assert record is not None
    h.broker.on_quote(quote())
    proxy.hide_fills = True
    assert h.manager.sync() == []  # broker says FILLED, no fill booked yet: keep SUBMITTED
    still = h.store.get(record.client_order_id)
    assert still is not None
    assert still.status is OrderStatus.SUBMITTED
    proxy.hide_fills = False
    assert kinds(h.manager.sync()) == [(K.FILL_BOOKED, None)]


def test_cancel_pending_waits_for_late_partial_fills() -> None:
    h = harness()
    proxy = with_proxy(h)
    record = h.buy().record
    assert record is not None
    cid = record.client_order_id
    h.broker.on_quote(quote(), max_fill_quantity=D("0.1"))
    proxy.hide_fills = True
    h.manager.sync()  # broker PARTIALLY_FILLED, nothing booked -> stays SUBMITTED
    h.manager.cancel(cid)
    h.manager.sync()  # broker CANCELLED with 0.1 filled, not booked -> stays CANCEL_PENDING
    pending = h.store.get(cid)
    assert pending is not None
    assert pending.status is OrderStatus.CANCEL_PENDING
    proxy.hide_fills = False
    h.manager.sync()
    final = h.store.get(cid)
    assert final is not None
    assert final.status is OrderStatus.CANCELLED
    assert final.filled_quantity == D("0.1")


def test_cancel_pending_while_the_broker_has_not_processed_it() -> None:
    h = harness()
    record = h.buy().record
    assert record is not None
    cid = record.client_order_id
    h.broker.inject(SimOperation.CANCEL, SimFault.TIMEOUT_BEFORE_ACCEPT)
    assert kinds(h.manager.cancel(cid)) == [
        (K.CANCEL_REQUESTED, None),
        (K.BROKER_UNAVAILABLE, "ORD-ERR-3"),
    ]
    assert h.manager.sync() == []  # broker still SUBMITTED: stay CANCEL_PENDING
    pending = h.store.get(cid)
    assert pending is not None
    assert pending.status is OrderStatus.CANCEL_PENDING


def test_broker_reports_less_than_booked() -> None:
    h = harness()
    proxy = with_proxy(h)
    record = h.buy().record
    assert record is not None
    h.broker.on_quote(quote(), max_fill_quantity=D("0.1"))
    h.manager.sync()
    snapshot = h.broker.order_status(record.client_order_id)
    proxy.status_override = lambda cid: snapshot.model_copy(update={"filled_quantity": D(0)})
    assert kinds(h.manager.sync()) == [(K.MISMATCH, "ORD-ERR-4")]


def test_impossible_broker_transition_is_a_mismatch() -> None:
    h = harness()
    proxy = with_proxy(h)
    record = h.buy().record
    assert record is not None
    h.broker.on_quote(quote(), max_fill_quantity=D("0.1"))
    h.manager.sync()
    snapshot = h.broker.order_status(record.client_order_id)
    proxy.status_override = lambda cid: snapshot.model_copy(update={"status": OrderStatus.REJECTED})
    events = h.manager.sync()
    assert kinds(events) == [(K.MISMATCH, "ORD-ERR-4")]


def test_order_that_vanished_at_the_broker_is_a_mismatch() -> None:
    h = harness()
    proxy = with_proxy(h)
    h.buy()

    def missing(cid: str) -> BrokerOrder:
        raise OrderNotFoundError(cid)

    proxy.status_override = missing
    events = h.manager.sync()
    assert kinds(events) == [(K.MISMATCH, "ORD-ERR-4")]


def test_broker_unavailable_during_sync() -> None:
    h = harness()
    h.buy()
    h.broker.inject(SimOperation.STATUS, SimFault.TIMEOUT_BEFORE_ACCEPT)
    assert kinds(h.manager.sync()) == [(K.BROKER_UNAVAILABLE, "ORD-ERR-3")]

    proxy = with_proxy(h)

    def down(ts: datetime) -> list[BrokerFill]:
        raise BrokerUnavailableError("down")

    proxy.fills_since = down  # type: ignore[method-assign]
    assert kinds(h.manager.sync()) == [(K.BROKER_UNAVAILABLE, "ORD-ERR-3")]


def test_day_orders_expire() -> None:
    h = harness()
    record = h.buy().record
    assert record is not None
    h.broker.expire_day_orders()
    h.manager.sync()
    final = h.store.get(record.client_order_id)
    assert final is not None
    assert final.status is OrderStatus.EXPIRED


# ---------------------------------------------------------------- cancels


def test_cancel_rules() -> None:
    h = harness()
    with pytest.raises(OrderNotActiveError):
        h.manager.cancel("missing")
    record = h.buy().record
    assert record is not None
    h.broker.on_quote(quote())
    h.manager.sync()
    with pytest.raises(OrderNotActiveError):
        h.manager.cancel(record.client_order_id)


def test_cancel_of_an_order_the_broker_does_not_know() -> None:
    h = harness()
    record = h.buy().record
    assert record is not None
    proxy = with_proxy(h)

    def unknown(cid: str) -> None:
        raise OrderNotFoundError(cid)

    proxy.cancel_override = unknown
    events = h.manager.cancel(record.client_order_id)
    assert kinds(events)[-1] == (K.MISMATCH, "ORD-ERR-4")


def test_kill_switch_cancels_non_protective_orders_and_keeps_stops() -> None:
    h = harness()
    h.buy("0.2", intent_id="buy-1")
    h.broker.on_quote(quote())
    h.manager.sync()
    h.buy("0.1", intent_id="buy-2")  # open entry order
    plain = intent(Side.SELL, intent_id="sell-1", target_quantity=D("0.1"))
    h.manager.submit(plain, decision(plain, D("0.1")), SPY, h.context(), limit_price=D(120))
    stop = intent(
        Side.SELL, intent_id="stop-1", purpose=IntentPurpose.STOP_EXIT, target_quantity=D("0.1")
    )
    h.manager.submit(stop, decision(stop, D("0.1")), SPY, h.context(), limit_price=D(90))
    events = h.manager.cancel_non_protective()
    assert kinds(events) == [(K.CANCEL_REQUESTED, None), (K.CANCEL_REQUESTED, None)]
    h.manager.sync()
    statuses = {r.request.purpose: r.status for r in h.store.active()}
    assert statuses == {IntentPurpose.STOP_EXIT: OrderStatus.SUBMITTED}
    # after the kill: entries are refused, a flatten order still passes the gate
    killed = h.context(system_state=SystemState.KILLED)
    entry = intent(intent_id="buy-3")
    refused = h.manager.submit(entry, decision(entry), SPY, killed)
    assert refused.record is None
    assert "HC-9" in refused.gate.failed_rules
    flatten = intent(
        Side.SELL, intent_id="flat-1", purpose=IntentPurpose.FLATTEN, target_quantity=D("0.1")
    )
    allowed = h.manager.submit(
        flatten, decision(flatten, D("0.1")), SPY, killed, order_type=OrderType.MARKET
    )
    assert allowed.record is not None
    assert allowed.record.status is OrderStatus.SUBMITTED


def test_settled_cash_is_shared_between_engine_and_broker() -> None:
    h = harness()
    h.buy()
    h.broker.on_quote(quote())
    h.manager.sync()
    assert h.ledger.cash_usd_settled == h.broker.account().cash_usd_settled
    assert Decimal(h.ledger.cash_usd_settled) < D(300)
