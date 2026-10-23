"""Order gate: every hard-cap rule passes and fails; a passing order never breaks a hard cap."""

from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aitrader.broker.port import BrokerCapabilities
from aitrader.config import HARD_CAPS, RiskLimits
from aitrader.domain import (
    Instrument,
    InstrumentKind,
    IntentPurpose,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    SystemState,
    TimeInForce,
    Verdict,
)
from aitrader.execution import GateContext, GateRejectedError, OrderGate, OrderRecord
from tests.unit.execution.builders import FX, NOW, SPY, D, decision, intent

GATE = OrderGate(RiskLimits())
CAPS = BrokerCapabilities(
    fractional_orders=True,
    fractional_step=D("0.00001"),
    min_fractional_buy_usd=D(1),
    order_types=frozenset({OrderType.LIMIT, OrderType.MARKET}),
)
EQUITY = D(50_000) / FX


def request(side: Side = Side.BUY, **kw: Any) -> OrderRequest:
    fields: dict[str, Any] = {
        "client_order_id": "ORDER-1",
        "intent_id": "intent-1",
        "symbol": "SPY",
        "side": side,
        "purpose": IntentPurpose.ENTRY if side is Side.BUY else IntentPurpose.EXIT,
        "order_type": OrderType.LIMIT,
        "quantity": D("0.3"),
        "limit_price": D(100),
    }
    fields.update(kw)
    return OrderRequest.model_validate(fields)


def context(**kw: Any) -> GateContext:
    fields: dict[str, Any] = {
        "now": NOW,
        "system_state": SystemState.RUNNING,
        "market_open": True,
        "fx_jpy_per_usd": FX,
        "equity_usd": EQUITY,
        "settled_cash_usd": EQUITY,
        "holdings_value_usd": D(0),
        "held_quantity": {"SPY": D(1)},
        "allow_list": frozenset({"SPY"}),
        "capabilities": CAPS,
    }
    fields.update(kw)
    return GateContext.model_validate(fields)


def check(req: OrderRequest | None = None, quantity: Decimal | None = None, **ctx: Any) -> set[str]:
    req = req or request()
    side = req.side
    the_intent = intent(side, purpose=req.purpose)
    the_decision = decision(the_intent, quantity if quantity is not None else req.quantity)
    result = GATE.check(req, the_decision, SPY, context(**ctx))
    return set(result.failed_rules)


def open_record(side: Side, quantity: str, limit: str | None = "100", **kw: Any) -> OrderRecord:
    req = request(
        side,
        client_order_id=kw.pop("cid", "OPEN-1"),
        intent_id=kw.pop("intent_id", "other-intent"),
        quantity=D(quantity),
        limit_price=D(limit) if limit else None,
        order_type=OrderType.LIMIT if limit else OrderType.MARKET,
        purpose=kw.pop(
            "purpose", IntentPurpose.ENTRY if side is Side.BUY else IntentPurpose.STOP_EXIT
        ),
    )
    return OrderRecord(
        request=req,
        attempt=1,
        status=kw.pop("status", OrderStatus.SUBMITTED),
        created_at=NOW,
        updated_at=NOW,
        **kw,
    )


def test_a_normal_entry_passes_every_rule() -> None:
    result = GATE.check(request(), decision(intent()), SPY, context())
    assert result.passed
    assert [r.rule_id for r in result.results] == [
        "HC-8",
        "DUP-1",
        "HC-9",
        "HC-7",
        "HC-3",
        "HC-1",
        "HC-6",
        "HC-10",
        "HC-2",
        "HC-4",
        "HC-5",
        "HC-11",
    ]
    assert GATE.enforce(request(), decision(intent()), SPY, context()).passed


def test_enforce_raises_with_the_failed_rules() -> None:
    with pytest.raises(GateRejectedError, match="HC-7") as raised:
        GATE.enforce(request(), decision(intent()), SPY, context(market_open=False))
    assert raised.value.result.failed_rules == ("HC-7",)


# ---------------------------------------------------------------- HC-8 and DUP-1


def test_quantity_must_equal_the_approved_quantity() -> None:
    assert check(quantity=D("0.2")) == {"HC-8"}


def test_decision_must_be_for_the_same_intent() -> None:
    other = decision(intent(intent_id="other"))
    result = GATE.check(request(), other, SPY, context())
    assert set(result.failed_rules) == {"HC-8"}


def test_rejected_decision_cannot_be_executed() -> None:
    rejected = decision(intent(), verdict=Verdict.REJECT)
    assert "HC-8" in GATE.check(request(), rejected, SPY, context()).failed_rules


def test_one_active_order_per_intent() -> None:
    active = open_record(Side.BUY, "0.1", intent_id="intent-1")
    assert "DUP-1" in check(open_orders=(active,))
    closed = open_record(Side.BUY, "0.1", intent_id="intent-1", status=OrderStatus.CANCELLED)
    assert check(open_orders=(closed,)) == set()


# ---------------------------------------------------------------- HC-9 and HC-7


@pytest.mark.parametrize(
    ("state", "kill", "side", "purpose", "allowed"),
    [
        (SystemState.RUNNING, False, Side.BUY, IntentPurpose.ENTRY, True),
        (SystemState.DEGRADED, False, Side.BUY, IntentPurpose.ENTRY, False),
        (SystemState.HALTED, False, Side.BUY, IntentPurpose.ENTRY, False),
        (SystemState.RUNNING, True, Side.BUY, IntentPurpose.ENTRY, False),
        (SystemState.SAFE, False, Side.SELL, IntentPurpose.STOP_EXIT, False),
        (SystemState.HALTED, False, Side.SELL, IntentPurpose.EXIT, True),
        (SystemState.DEGRADED, False, Side.SELL, IntentPurpose.EXIT, True),
        (SystemState.KILLED, False, Side.SELL, IntentPurpose.EXIT, False),
        (SystemState.KILLED, False, Side.SELL, IntentPurpose.STOP_EXIT, True),
        (SystemState.RUNNING, True, Side.SELL, IntentPurpose.FLATTEN, True),
        (SystemState.RUNNING, True, Side.SELL, IntentPurpose.EXIT, False),
    ],
)
def test_system_state_and_kill_switch(
    state: SystemState, kill: bool, side: Side, purpose: IntentPurpose, allowed: bool
) -> None:
    req = request(side, purpose=purpose)
    failed = check(req, system_state=state, kill_switch=kill)
    assert ("HC-9" not in failed) is allowed


def test_regular_hours_only() -> None:
    assert check(market_open=False) == {"HC-7"}
    assert check(request(extended_hours=True)) == {"HC-7"}


# ---------------------------------------------------------------- HC-3 and HC-1


def test_buys_only_in_allow_listed_instruments() -> None:
    assert check(allow_list=frozenset({"QQQ"})) == {"HC-3"}


def test_buy_instrument_must_match_the_request() -> None:
    qqq = Instrument(symbol="QQQ", kind=InstrumentKind.ETF, fractionable=True)
    result = GATE.check(request(), decision(intent()), qqq, context())
    assert result.failed_rules == ("HC-3",)


def test_sells_are_not_limited_by_the_allow_list() -> None:
    assert check(request(Side.SELL), allow_list=frozenset()) == set()


@pytest.mark.parametrize(
    ("quantity", "passes"), [("0.69999", True), ("0.7", True), ("0.70001", False)]
)
def test_no_short_selling(quantity: str, passes: bool) -> None:
    being_sold = open_record(Side.SELL, "0.3")
    failed = check(request(Side.SELL, quantity=D(quantity)), open_orders=(being_sold,))
    assert ("HC-1" not in failed) is passes


def test_selling_a_symbol_not_held_fails() -> None:
    assert check(request(Side.SELL), held_quantity={}) == {"HC-1"}


# ---------------------------------------------------------------- HC-6


@pytest.mark.parametrize(("orders", "passes"), [(19, True), (20, False), (21, False)])
def test_orders_per_day_hard_cap(orders: int, passes: bool) -> None:
    assert ("HC-6" not in check(orders_today=orders)) is passes


@pytest.mark.parametrize("purpose", [IntentPurpose.STOP_EXIT, IntentPurpose.FLATTEN])
def test_protective_exits_are_never_blocked_by_the_order_count(purpose: IntentPurpose) -> None:
    assert check(request(Side.SELL, purpose=purpose), orders_today=50) == set()


def test_ordinary_exits_count_against_the_cap() -> None:
    assert check(request(Side.SELL), orders_today=20) == {"HC-6"}


# ---------------------------------------------------------------- HC-10


@pytest.mark.parametrize(
    ("kw", "passes"),
    [
        ({"order_type": OrderType.MARKET, "limit_price": None}, False),  # market entry
        ({"time_in_force": TimeInForce.GTC}, False),
        ({"quantity": D("0.000001")}, False),  # below the fractional step
        ({"quantity": D("0.0099")}, False),  # USD 0.99 fractional buy
        ({"quantity": D("0.01")}, True),  # USD 1.00 fractional buy
        ({"order_type": OrderType.STOP, "limit_price": None}, False),
    ],
)
def test_fixed_order_fields(kw: dict[str, Any], passes: bool) -> None:
    req = request(**kw)
    assert ("HC-10" not in check(req)) is passes


def test_market_order_only_for_protective_exits() -> None:
    stop_exit = request(
        Side.SELL, purpose=IntentPurpose.STOP_EXIT, order_type=OrderType.MARKET, limit_price=None
    )
    assert check(stop_exit) == set()
    plain_exit = request(Side.SELL, order_type=OrderType.MARKET, limit_price=None)
    assert check(plain_exit) == {"HC-10"}


def test_whole_shares_when_the_instrument_is_not_fractionable() -> None:
    whole = Instrument(symbol="SPY", kind=InstrumentKind.ETF, fractionable=False)
    result = GATE.check(request(), decision(intent()), whole, context())
    assert result.failed_rules == ("HC-10",)
    req = request(quantity=D(1))
    one = GATE.check(req, decision(intent(), D(1)), whole, context())
    assert one.passed


def test_broker_without_fractional_orders_needs_whole_shares() -> None:
    caps = CAPS.model_copy(update={"fractional_orders": False})
    assert check(capabilities=caps) == {"HC-10"}


# ---------------------------------------------------------------- HC-2, HC-4, HC-5, HC-11


def test_settled_cash_pays_for_every_open_buy() -> None:
    reserved = open_record(Side.BUY, "2.9")  # USD 290 reserved; the new order needs USD 30
    assert check(settled_cash_usd=D("319.99"), open_orders=(reserved,)) == {"HC-2"}
    assert check(settled_cash_usd=D(320), open_orders=(reserved,)) == set()
    assert check(settled_cash_usd=D("29.99")) == {"HC-2"}


def test_no_leverage_on_gross_exposure() -> None:
    assert "HC-2" in check(holdings_value_usd=EQUITY - D(29), settled_cash_usd=D(1000))


@pytest.mark.parametrize(("quantity", "passes"), [("1.93548", True), ("1.93549", False)])
def test_order_notional_cap(quantity: str, passes: bool) -> None:
    # configured cap JPY 30,000 = USD 193.548... at JPY 155
    req = request(quantity=D(quantity))
    assert ("HC-4" not in check(req, settled_cash_usd=D(1000))) is passes


def test_order_notional_never_above_the_hard_cap() -> None:
    loose = OrderGate(
        RiskLimits(live_max_order_notional_jpy=D(90_000), live_max_total_holdings_jpy=D(100_000))
    )
    req = request(quantity=D(4))  # USD 400 = JPY 62,000 > hard cap 50,000
    result = loose.check(
        req, decision(intent(), D(4)), SPY, context(settled_cash_usd=D(1000), equity_usd=D(1000))
    )
    assert "HC-4" in result.failed_rules
    assert HARD_CAPS["live_max_order_notional_jpy"] == D(50_000)


def test_total_holdings_and_approved_capital() -> None:
    # holdings USD 300 + order USD 30 = JPY 51,150 > approved JPY 50,000 (but < JPY 100,000)
    assert check(holdings_value_usd=D(300), settled_cash_usd=D(1000), equity_usd=D(1000)) == {
        "HC-11"
    }
    # USD 620 + 30 = JPY 100,750 > JPY 100,000 holdings cap
    failed = check(holdings_value_usd=D(620), settled_cash_usd=D(1000), equity_usd=D(1000))
    assert {"HC-5", "HC-11"} <= failed


def test_buy_without_a_limit_price_fails_closed() -> None:
    req = request(order_type=OrderType.MARKET, limit_price=None)
    assert {"HC-2", "HC-4", "HC-5", "HC-11", "HC-10"} <= check(req)


def test_sells_reduce_risk_and_skip_the_buying_rules() -> None:
    assert check(request(Side.SELL), settled_cash_usd=D(0), holdings_value_usd=D(10_000)) == set()


# ---------------------------------------------------------------- property


@settings(max_examples=300, deadline=None)
@given(
    quantity=st.decimals(min_value=D("0.00001"), max_value=D(20), places=5),
    price=st.decimals(min_value=D(1), max_value=D(600), places=2),
    cash=st.decimals(min_value=D(0), max_value=D(2000), places=2),
    holdings=st.decimals(min_value=D(0), max_value=D(1000), places=2),
    reserved_qty=st.decimals(min_value=D(0), max_value=D(3), places=5),
    fx=st.decimals(min_value=D(100), max_value=D(200), places=2),
)
def test_a_buy_that_passes_never_breaks_a_hard_cap(
    quantity: Decimal,
    price: Decimal,
    cash: Decimal,
    holdings: Decimal,
    reserved_qty: Decimal,
    fx: Decimal,
) -> None:
    loosest = OrderGate(
        RiskLimits(
            approved_capital_jpy=D(100_000),
            live_max_order_notional_jpy=D(50_000),
            live_max_total_holdings_jpy=D(100_000),
        )
    )
    open_orders = (
        (open_record(Side.BUY, str(reserved_qty), str(price)),) if reserved_qty > 0 else ()
    )
    req = request(quantity=quantity, limit_price=price)
    ctx = context(
        fx_jpy_per_usd=fx,
        settled_cash_usd=cash,
        holdings_value_usd=holdings,
        equity_usd=D(2000),
        open_orders=open_orders,
    )
    result = loosest.check(req, decision(intent(), quantity), SPY, ctx)
    if not result.passed:
        return
    notional = quantity * price
    reserved = reserved_qty * price
    assert notional + reserved <= cash  # HC-2: no leverage
    assert notional * fx <= HARD_CAPS["live_max_order_notional_jpy"]  # HC-4
    assert (holdings + reserved + notional) * fx <= HARD_CAPS["live_max_total_holdings_jpy"]  # HC-5
    assert (holdings + reserved + notional) * fx <= HARD_CAPS["approved_capital_jpy"]  # HC-11
    assert quantity % D("0.00001") == 0
    assert quantity % 1 == 0 or notional >= 1
