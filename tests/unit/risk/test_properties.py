"""Property tests: no approved order breaks a cap, and fees always fit the risk budget."""

from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from aitrader.config import RiskLimits
from aitrader.domain import Currency, InstrumentKind, Money, Verdict
from aitrader.fees import WEBULL_JP, ExecutionAssumptions, round_trip_cost
from aitrader.risk import RiskEngine, max_quantity_within, round_down
from tests.unit.risk.builders import (
    D,
    advisory,
    entry,
    exit_intent,
    market,
    position,
    state,
)

LIMITS = RiskLimits()
ENGINE = RiskEngine(LIMITS)
ASSUMPTIONS = ExecutionAssumptions()


def money(low: str, high: str, places: int = 2) -> st.SearchStrategy[Decimal]:
    return st.decimals(
        min_value=D(low), max_value=D(high), places=places, allow_nan=False, allow_infinity=False
    )


@st.composite
def entry_case(draw: st.DrawFn) -> dict[str, Any]:
    price = draw(money("5", "800"))
    stop_pct = draw(money("0.5", "15"))
    equity = draw(money("100", "2000"))
    fx = draw(money("100", "200"))
    held_value = draw(money("0", "200"))
    cash = draw(money("10", "2000"))
    fractionable = draw(st.booleans())
    multiplier = draw(money("0", "1"))
    requested = draw(money("1", "5000"))
    volatility = draw(money("0.001", "0.05", places=4))
    return {
        "price": price,
        "stop": (price * (1 - stop_pct / 100)).quantize(D("0.01")),
        "equity": equity,
        "fx": fx,
        "held_value": held_value,
        "cash": cash,
        "fractionable": fractionable,
        "multiplier": multiplier,
        "requested": requested,
        "volatility": volatility,
    }


def _run(case: dict[str, Any], *, with_ai: bool) -> tuple[Any, Any]:
    price = case["price"]
    equity = case["equity"]
    instrument = {"symbol": "SPY", "kind": InstrumentKind.ETF, "fractionable": case["fractionable"]}
    held = (
        (position(symbol="QQQ", market_value_usd=case["held_value"], daily_volatility=D("0.01")),)
        if case["held_value"] > 0
        else ()
    )
    risk_state = state(
        fx_jpy_per_usd=case["fx"],
        equity_usd=equity,
        start_of_day_equity_usd=equity,
        month_start_equity_usd=equity,
        peak_equity_usd=equity,
        settled_cash_usd=case["cash"],
        positions=held,
    )
    market_state = market(
        price=price,
        instrument=instrument,
        daily_volatility=case["volatility"],
        avg_daily_volume_20d=D(50_000_000),
    )
    intent = entry(
        price=price,
        stop_price=case["stop"],
        target_notional=Money.of(case["requested"], Currency.USD),
    )
    advisories = (advisory(size_multiplier=case["multiplier"]),) if with_ai else ()
    return ENGINE.evaluate(intent, risk_state, market_state, advisories), risk_state


@settings(max_examples=300, deadline=None)
@given(case=entry_case())
def test_approved_entries_respect_every_cap(case: dict[str, Any]) -> None:
    decision, risk_state = _run(case, with_ai=True)
    q = decision.approved_quantity
    if decision.verdict is Verdict.REJECT:
        assert q == 0
        return
    price, stop, fx = case["price"], case["stop"], case["fx"]
    sizing_equity = min(risk_state.equity_usd, LIMITS.approved_capital_jpy / fx)
    increment = WEBULL_JP.fractional_quantity_step if case["fractionable"] else D(1)
    notional = q * price
    exposure = case["held_value"]

    # quantity is a positive multiple of the increment and never above the request
    assert q > 0
    assert q % increment == 0
    assert q <= case["requested"] / price
    # risk per trade including fees, slippage and gap allowance
    budget = sizing_equity * LIMITS.risk_per_trade_pct / 100
    loss_per_unit = (price - stop) + price * ASSUMPTIONS.stop_slippage_allowance_pct / 100
    cost = round_trip_cost(WEBULL_JP, ASSUMPTIONS, q, price, stop)
    assert q * loss_per_unit + cost <= budget
    assert cost <= budget * LIMITS.max_cost_share_of_risk_budget_pct / 100
    # position, exposure (no leverage), cash, live order and live holdings caps
    assert notional <= sizing_equity * LIMITS.max_position_pct_etf / 100
    assert exposure + notional <= sizing_equity * LIMITS.max_gross_exposure_pct / 100
    assert q * price * (1 + ASSUMPTIONS.slippage_bps_per_side / 10_000) <= case["cash"]
    assert notional <= LIMITS.live_max_order_notional_jpy / fx
    assert exposure + notional <= LIMITS.live_max_total_holdings_jpy / fx
    # broker minimum
    if case["fractionable"]:
        assert notional >= WEBULL_JP.min_fractional_buy_usd


@settings(max_examples=200, deadline=None)
@given(case=entry_case())
def test_ai_never_increases_a_quantity(case: dict[str, Any]) -> None:
    with_ai, _ = _run(case, with_ai=True)
    without_ai, _ = _run(case, with_ai=False)
    assert with_ai.approved_quantity <= without_ai.approved_quantity
    expected = round_down(without_ai.approved_quantity * case["multiplier"], D("0.00001"))
    if with_ai.verdict is not Verdict.REJECT:
        assert with_ai.approved_quantity <= expected


@settings(max_examples=200, deadline=None)
@given(
    available=money("0.00001", "1000", places=5),
    extra=money("0", "1000", places=5),
    requested=money("0.00001", "2000", places=5),
)
def test_a_sell_never_exceeds_the_available_quantity(
    available: Decimal, extra: Decimal, requested: Decimal
) -> None:
    held = (
        position(
            quantity=available + extra,
            available_quantity=available,
            market_value_usd=(available + extra) * 100,
        ),
    )
    decision = ENGINE.evaluate(
        exit_intent(target_quantity=requested), state(positions=held), market()
    )
    assert decision.approved_quantity <= available
    assert decision.approved_quantity <= requested


@given(
    budget=money("0", "100"),
    upper=money("0", "1000", places=5),
    unit=money("0.01", "50"),
    fixed=money("0", "5"),
)
def test_binary_search_finds_the_largest_fitting_quantity(
    budget: Decimal, upper: Decimal, unit: Decimal, fixed: Decimal
) -> None:
    step = D("0.001")

    def cost(q: Decimal) -> Decimal:
        return q * unit + (fixed if q > 0 else D(0))

    q = max_quantity_within(cost, budget, upper, step)
    assert q % step == 0
    assert 0 <= q <= upper
    if q > 0:
        assert cost(q) <= budget
    if q + step <= upper:
        assert cost(q + step) > budget
