"""Entry (BUY) rules of the risk engine: each rule at, just below and just above its threshold."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from aitrader.config import RiskLimits
from aitrader.domain import (
    AdvisoryKind,
    Currency,
    InstrumentKind,
    Money,
    OrderIntent,
    SystemState,
    Verdict,
)
from aitrader.fees import WEBULL_JP, ExecutionAssumptions, FeeSchedule, round_trip_cost
from aitrader.risk import MarketWideState, RiskEngine, RiskParameters, round_down
from tests.unit.risk.builders import (
    EQUITY,
    NOW,
    D,
    advisory,
    entry,
    failed,
    market,
    open_order,
    position,
    rule,
    state,
)

ENGINE = RiskEngine(RiskLimits())

ENTRY_RULES = {
    "STATE-1",
    "R-INST-1",
    "R-EVT-1",
    "DQ-1",
    "DQ-2",
    "DQ-3",
    "DQ-4",
    "DQ-5",
    "DQ-6",
    "CB-1",
    "CB-2",
    "CB-3",
    "CB-4",
    "CB-5",
    "R-DAY-1",
    "R-MONTH-1",
    "R-MONTH-2",
    "R-DD-1",
    "R-ORD-1",
    "R-POS-1",
    "DUP-1",
    "DUP-3",
    "DUP-4",
    "R-LIVE-3",
    "R-TRADE-1",
    "R-POS-2",
    "R-PORT-1",
    "R-ECON-3",
    "R-PORT-2",
    "R-PORT-3",
    "R-ORD-2",
    "R-LIVE-1",
    "R-LIVE-2",
    "AI-1",
    "R-ECON-2",
    "R-ECON-1",
}


def evaluate(
    *,
    intent: OrderIntent | None = None,
    engine: RiskEngine = ENGINE,
    advisories: tuple[Any, ...] = (),
    market_overrides: dict[str, Any] | None = None,
    **state_overrides: Any,
) -> Any:
    return engine.evaluate(
        intent or entry(),
        state(**state_overrides),
        market(**(market_overrides or {})),
        advisories,
    )


# ---------------------------------------------------------------- worked example and basics


def test_worked_example_fits_the_risk_budget_including_fees() -> None:
    """Document 05 §2: JPY 50,000, stop 4% below entry, Webull fees, 0.5% gap allowance."""
    decision = evaluate()
    assert decision.verdict is Verdict.REDUCE  # the strategy asked for USD 80
    assert failed(decision) == set()
    assert {r.rule_id for r in decision.rule_results} == ENTRY_RULES
    quantity = decision.approved_quantity
    assert quantity == D("0.34643")
    budget = EQUITY * D("0.005")
    cost = round_trip_cost(WEBULL_JP, ExecutionAssumptions(), quantity, D(100), D(96))
    assert quantity * D("4.5") + cost <= budget
    assert D(34) < quantity * 100 < D(35)
    share = D(rule(decision, "R-ECON-1").values["cost_share_pct"])
    assert D(3) < share < D(4)


def test_minimum_commission_broker_fails_the_cost_rule() -> None:
    """Document 02 §4: 1% of value (at most USD 1) per side uses about a third of the budget."""
    ibsj_like = FeeSchedule(name="ibsj_fixed", commission_rate=D("0.01"), commission_max_usd=D(1))
    decision = evaluate(engine=RiskEngine(RiskLimits(), fees=ibsj_like))
    assert decision.verdict is Verdict.REJECT
    assert failed(decision) == {"R-ECON-1"}
    share = D(rule(decision, "R-ECON-1").values["cost_share_pct"])
    assert D(30) < share < D(35)


def test_small_request_is_approved_unchanged() -> None:
    decision = evaluate(intent=entry(target_notional=Money.of(20, Currency.USD)))
    assert decision.verdict is Verdict.APPROVE
    assert decision.approved_quantity == D("0.20000")


def test_target_quantity_is_used_as_requested() -> None:
    decision = evaluate(intent=entry(target_notional=None, target_quantity=D("0.1")))
    assert decision.verdict is Verdict.APPROVE
    assert decision.approved_quantity == D("0.1")


def test_entry_price_falls_back_to_the_ask() -> None:
    decision = evaluate(intent=entry(limit_price=None))
    assert decision.verdict is Verdict.REDUCE
    assert D(rule(decision, "R-INST-1").values["price"]) == D("100.0100")


def test_all_rules_are_recorded_even_after_a_failure() -> None:
    decision = evaluate(system_state=SystemState.HALTED)
    assert decision.verdict is Verdict.REJECT
    assert decision.approved_quantity == 0
    assert failed(decision) == {"STATE-1"}
    assert {r.rule_id for r in decision.rule_results} == ENTRY_RULES


def test_limits_hash_identifies_the_rule_set() -> None:
    assert ENGINE.limits_hash == RiskEngine(RiskLimits()).limits_hash
    assert evaluate().limits_hash == ENGINE.limits_hash
    other = RiskEngine(RiskLimits(risk_per_trade_pct=D("0.4")))
    assert other.limits_hash != ENGINE.limits_hash
    assert RiskEngine(RiskLimits(), RiskParameters(etf_only=False)).limits_hash != (
        ENGINE.limits_hash
    )


# ---------------------------------------------------------------- STATE-1


@pytest.mark.parametrize(
    "system_state",
    [SystemState.SAFE, SystemState.DEGRADED, SystemState.HALTED, SystemState.KILLED],
)
def test_new_entries_need_running_state(system_state: SystemState) -> None:
    assert failed(evaluate(system_state=system_state)) == {"STATE-1"}


def test_kill_switch_blocks_entries() -> None:
    assert failed(evaluate(kill_switch=True)) == {"STATE-1"}


# ---------------------------------------------------------------- R-INST-1 and R-EVT-1


def test_missing_market_state_rejects() -> None:
    decision = ENGINE.evaluate(entry(), state(), None)
    assert decision.verdict is Verdict.REJECT
    assert "R-INST-1" in failed(decision)


def test_market_state_for_another_symbol_rejects() -> None:
    decision = ENGINE.evaluate(entry(), state(), market(symbol="QQQ"))
    assert "R-INST-1" in failed(decision)


@pytest.mark.parametrize(
    "overrides",
    [
        {"allow_listed": False},
        {"tradable": False},
        {
            "instrument": {
                "symbol": "SPY",
                "kind": InstrumentKind.COMMON_STOCK,
                "fractionable": True,
            }
        },
    ],
)
def test_instrument_must_be_allowed(overrides: dict[str, Any]) -> None:
    assert failed(evaluate(market_overrides=overrides)) == {"R-INST-1"}


def test_single_stocks_pass_when_etf_only_is_off() -> None:
    engine = RiskEngine(RiskLimits(), RiskParameters(etf_only=False))
    stock = {
        "instrument": {"symbol": "SPY", "kind": InstrumentKind.COMMON_STOCK, "fractionable": True}
    }
    assert failed(evaluate(engine=engine, market_overrides=stock)) == set()


@pytest.mark.parametrize(("price", "passes"), [(D("4.99"), False), (D(5), True), (D("5.01"), True)])
def test_minimum_price(price: Decimal, passes: bool) -> None:
    decision = ENGINE.evaluate(entry(price=price), state(), market(price=price))
    assert ("R-INST-1" not in failed(decision)) is passes


@pytest.mark.parametrize(
    ("dollar_volume", "passes"),
    [(D(19_999_999), False), (D(20_000_000), True), (D(20_000_001), True)],
)
def test_minimum_average_dollar_volume(dollar_volume: Decimal, passes: bool) -> None:
    decision = evaluate(market_overrides={"avg_daily_dollar_volume_20d": dollar_volume})
    assert ("R-INST-1" not in failed(decision)) is passes


STOCK_ENGINE = RiskEngine(RiskLimits(), RiskParameters(etf_only=False))
STOCK = {"symbol": "SPY", "kind": InstrumentKind.COMMON_STOCK, "fractionable": True}


@pytest.mark.parametrize(("days_ahead", "blocked"), [(-1, False), (0, True), (5, True), (6, False)])
def test_earnings_blackout_before(days_ahead: int, blocked: bool) -> None:
    earnings = NOW.date() + timedelta(days=days_ahead)
    decision = evaluate(
        engine=STOCK_ENGINE,
        market_overrides={"instrument": STOCK, "next_earnings_date": earnings},
    )
    assert ("R-EVT-1" in failed(decision)) is blocked


@pytest.mark.parametrize(("days_ago", "blocked"), [(-1, False), (0, True), (4, True), (5, False)])
def test_earnings_blackout_after(days_ago: int, blocked: bool) -> None:
    earnings = NOW.date() - timedelta(days=days_ago)
    decision = evaluate(
        engine=STOCK_ENGINE,
        market_overrides={"instrument": STOCK, "last_earnings_date": earnings},
    )
    assert ("R-EVT-1" in failed(decision)) is blocked


def test_earnings_blackout_does_not_apply_to_etfs() -> None:
    decision = evaluate(market_overrides={"next_earnings_date": NOW.date()})
    assert "R-EVT-1" not in failed(decision)


# ---------------------------------------------------------------- data quality


def test_missing_quote_fails_data_and_spread_rules() -> None:
    decision = evaluate(market_overrides={"quote": None})
    assert {"DQ-1", "CB-1", "CB-4"} <= failed(decision)


@pytest.mark.parametrize(
    ("age_seconds", "passes"),
    [(D(14), True), (D(15), True), (D("15.001"), False), (D(-15), True), (D(-16), False)],
)
def test_quote_freshness(age_seconds: Decimal, passes: bool) -> None:
    ts = NOW - timedelta(seconds=float(age_seconds))
    m = market()
    assert m.quote is not None
    stale = m.quote.model_copy(update={"ts": ts})
    decision = evaluate(market_overrides={"quote": stale})
    assert ("DQ-1" not in failed(decision)) is passes


@pytest.mark.parametrize("rule_id", ["DQ-2", "DQ-3", "DQ-4", "DQ-5"])
def test_data_quality_gate_findings_block_entries(rule_id: str) -> None:
    decision = evaluate(market_overrides={"data_quality_failures": frozenset({rule_id})})
    assert failed(decision) == {rule_id}


def test_unknown_data_quality_rule_ids_are_refused() -> None:
    with pytest.raises(ValidationError, match="unknown data quality rule"):
        market(data_quality_failures=frozenset({"DQ-9"}))


@pytest.mark.parametrize(
    ("drift", "passes"), [(D("0.999"), True), (D(1), True), (D("1.001"), False)]
)
def test_clock_drift(drift: Decimal, passes: bool) -> None:
    decision = evaluate(
        market=MarketWideState(benchmark_change_pct=D(0), clock_drift_seconds=drift)
    )
    assert ("DQ-6" not in failed(decision)) is passes


# ---------------------------------------------------------------- circuit breakers


@pytest.mark.parametrize(
    ("mid", "passes"),
    [(D("106.99"), True), (D(107), True), (D("107.01"), False), (D(93), True), (D("92.99"), False)],
)
def test_move_from_previous_close_in_percent(mid: Decimal, passes: bool) -> None:
    decision = ENGINE.evaluate(
        entry(price=mid), state(), market(price=mid, previous_close=D(100), atr14=D(100))
    )
    assert ("CB-1" not in failed(decision)) is passes


@pytest.mark.parametrize(("atr", "passes"), [(D("1.001"), True), (D(1), True), (D("0.999"), False)])
def test_move_from_previous_close_in_atr(atr: Decimal, passes: bool) -> None:
    # mid 100, previous close 97: a move of 3.00
    decision = evaluate(market_overrides={"previous_close": D(97), "atr14": atr})
    assert ("CB-1" not in failed(decision)) is passes


@pytest.mark.parametrize("missing", ["previous_close", "atr14"])
def test_move_check_fails_closed_without_reference_data(missing: str) -> None:
    decision = evaluate(market_overrides={missing: None})
    assert "CB-1" in failed(decision)


@pytest.mark.parametrize(
    ("change", "passes"),
    [(D("-2.99"), True), (D(-3), False), (D("-3.01"), False), (D(2), True), (None, False)],
)
def test_benchmark_drop(change: Decimal | None, passes: bool) -> None:
    decision = evaluate(market=MarketWideState(benchmark_change_pct=change))
    assert ("CB-2" not in failed(decision)) is passes


def test_symbol_halt_blocks_entries() -> None:
    assert failed(evaluate(market_overrides={"trading_halted": True})) == {"CB-3"}


def test_market_wide_halt_blocks_entries() -> None:
    decision = evaluate(market=MarketWideState(benchmark_change_pct=D(0), market_wide_halt=True))
    assert failed(decision) == {"CB-3"}


def _quote_with_spread_bps(bps: Decimal) -> dict[str, Any]:
    half = D(100) * bps / D(20_000)
    return {
        "symbol": "SPY",
        "ts": NOW,
        "bid": D(100) - half,
        "ask": D(100) + half,
        "bid_size": D(1),
        "ask_size": D(1),
    }


@pytest.mark.parametrize(("bps", "passes"), [(D("29.9"), True), (D(30), True), (D("30.1"), False)])
def test_spread_limit_for_etfs(bps: Decimal, passes: bool) -> None:
    decision = evaluate(market_overrides={"quote": _quote_with_spread_bps(bps)})
    assert ("CB-4" not in failed(decision)) is passes


@pytest.mark.parametrize(("bps", "passes"), [(D("49.9"), True), (D(50), True), (D("50.1"), False)])
def test_spread_limit_for_single_stocks(bps: Decimal, passes: bool) -> None:
    decision = evaluate(
        engine=STOCK_ENGINE,
        market_overrides={"instrument": STOCK, "quote": _quote_with_spread_bps(bps)},
    )
    assert ("CB-4" not in failed(decision)) is passes


@pytest.mark.parametrize(
    ("first_30m", "passes"), [(D("4.99"), True), (D(5), True), (D("5.01"), False)]
)
def test_opening_volume_shock(first_30m: Decimal, passes: bool) -> None:
    overrides = {"first_30m_volume": first_30m, "avg_first_30m_volume_20d": D(1)}
    decision = evaluate(market_overrides=overrides)
    assert ("CB-5" not in failed(decision)) is passes


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_30m_volume": None, "avg_first_30m_volume_20d": D(1)},
        {"first_30m_volume": D(100), "avg_first_30m_volume_20d": D(0)},
        {"first_30m_volume": D(100), "avg_first_30m_volume_20d": None},
    ],
)
def test_opening_volume_rule_passes_when_not_available(overrides: dict[str, Any]) -> None:
    assert "CB-5" not in failed(evaluate(market_overrides=overrides))


# ---------------------------------------------------------------- loss limits


def _equity(equity: Decimal) -> dict[str, Any]:
    """Equity against reference values of USD 1,000 (sizing stays capped at JPY 50,000)."""
    return {
        "equity_usd": equity,
        "start_of_day_equity_usd": D(1000),
        "month_start_equity_usd": D(1000),
        "peak_equity_usd": D(1000),
    }


@pytest.mark.parametrize(
    ("equity", "passes"), [(D("985.1"), True), (D(985), False), (D("984.9"), False)]
)
def test_daily_loss_limit(equity: Decimal, passes: bool) -> None:
    decision = evaluate(**_equity(equity))  # 1.5% below the start of the day
    assert ("R-DAY-1" not in failed(decision)) is passes


@pytest.mark.parametrize(
    ("equity", "halved"), [(D("970.1"), False), (D(970), True), (D("969.9"), True)]
)
def test_soft_monthly_level_halves_the_risk_per_trade(equity: Decimal, halved: bool) -> None:
    decision = evaluate(**_equity(equity))  # 3% below the month start
    assert "R-MONTH-1" not in failed(decision)
    assert rule(decision, "R-MONTH-1").values["halved"] == str(halved)
    budget = D(rule(decision, "R-TRADE-1").values["budget_usd"])
    assert budget == EQUITY * (D("0.0025") if halved else D("0.005"))


@pytest.mark.parametrize(
    ("equity", "passes"), [(D("950.1"), True), (D(950), False), (D("949.9"), False)]
)
def test_monthly_loss_limit(equity: Decimal, passes: bool) -> None:
    decision = evaluate(**_equity(equity))  # 5% below the month start
    assert ("R-MONTH-2" not in failed(decision)) is passes


@pytest.mark.parametrize(
    ("equity", "passes"), [(D("900.1"), True), (D(900), False), (D("899.9"), False)]
)
def test_drawdown_limit(equity: Decimal, passes: bool) -> None:
    decision = evaluate(**_equity(equity))  # 10% below the peak
    assert ("R-DD-1" not in failed(decision)) is passes


# ---------------------------------------------------------------- counts and duplicates


@pytest.mark.parametrize(("orders_today", "passes"), [(6, True), (7, True), (8, False), (9, False)])
def test_orders_per_day(orders_today: int, passes: bool) -> None:
    decision = evaluate(orders_today=orders_today)
    assert ("R-ORD-1" not in failed(decision)) is passes


def _positions(count: int) -> tuple[Any, ...]:
    symbols = ["QQQ", "IWM", "DIA", "VTI", "EFA"]
    return tuple(
        position(symbol=s, market_value_usd=D(1), quantity=D("0.01"), available_quantity=D("0.01"))
        for s in symbols[:count]
    )


@pytest.mark.parametrize(("count", "passes"), [(2, True), (3, True), (4, False), (5, False)])
def test_maximum_open_positions(count: int, passes: bool) -> None:
    decision = evaluate(positions=_positions(count))
    assert ("R-POS-1" not in failed(decision)) is passes


def test_open_entry_orders_count_as_positions() -> None:
    orders = (open_order(symbol="EFA", reserved_notional_usd=D(1)),)
    decision = evaluate(positions=_positions(3), open_orders=orders)
    assert "R-POS-1" in failed(decision)


def test_adding_to_a_held_symbol_does_not_need_a_new_slot() -> None:
    held = (*_positions(3), position(market_value_usd=D(10)))
    decision = evaluate(positions=held)
    assert "R-POS-1" not in failed(decision)


def test_one_active_order_per_intent() -> None:
    orders = (open_order(intent_id="intent-buy-1", reserved_notional_usd=D(1)),)
    assert "DUP-1" in failed(evaluate(open_orders=orders))


def test_no_second_open_entry_for_the_same_symbol() -> None:
    orders = (open_order(symbol="SPY", reserved_notional_usd=D(1)),)
    assert "DUP-3" in failed(evaluate(open_orders=orders))


def test_an_open_exit_does_not_block_an_entry() -> None:
    orders = (open_order(symbol="SPY", side="SELL", purpose="EXIT", reserved_notional_usd=D(0)),)
    assert failed(evaluate(open_orders=orders)) == set()


@pytest.mark.parametrize(("count", "passes"), [(0, True), (1, True), (2, False), (3, False)])
def test_non_protective_orders_per_symbol_per_day(count: int, passes: bool) -> None:
    decision = evaluate(non_protective_orders_today={"SPY": count, "QQQ": 5})
    assert ("DUP-4" not in failed(decision)) is passes


# ---------------------------------------------------------------- R-TRADE-1 and sizing caps


def test_entry_without_a_stop_is_rejected() -> None:
    no_stop = entry().model_copy(update={"stop_price": None})
    decision = ENGINE.evaluate(no_stop, state(), market())
    assert "R-TRADE-1" in failed(decision)
    assert decision.verdict is Verdict.REJECT


def test_entry_without_any_price_is_rejected() -> None:
    decision = ENGINE.evaluate(entry(limit_price=None), state(), market(quote=None))
    assert "R-TRADE-1" in failed(decision)


def test_stop_at_or_above_the_ask_is_rejected() -> None:
    decision = ENGINE.evaluate(entry(limit_price=None, stop_price=D("100.01")), state(), market())
    assert "R-TRADE-1" in failed(decision)


def test_whole_share_instrument_too_expensive_for_the_budget() -> None:
    whole = {"symbol": "SPY", "kind": InstrumentKind.ETF, "fractionable": False}
    decision = evaluate(market_overrides={"instrument": whole})
    assert {"R-TRADE-1", "R-ECON-2"} <= failed(decision)
    assert D(rule(decision, "R-TRADE-1").values["max_quantity"]) == 0


def test_whole_share_instrument_within_the_budget() -> None:
    price = D(10)
    whole = {"symbol": "SPY", "kind": InstrumentKind.ETF, "fractionable": False}
    intent = entry(price=price, target_notional=Money.of(80, Currency.USD))
    decision = ENGINE.evaluate(intent, state(), market(price=price, instrument=whole))
    assert decision.verdict is Verdict.REDUCE
    assert decision.approved_quantity == 3  # 3 * 0.45 + costs <= 1.61 < 4 * 0.45
    assert failed(decision) == set()


def test_sizing_equity_is_capped_at_the_approved_capital() -> None:
    rich = EQUITY * 10
    decision = evaluate(
        equity_usd=rich,
        start_of_day_equity_usd=rich,
        month_start_equity_usd=rich,
        peak_equity_usd=rich,
        settled_cash_usd=rich,
    )
    assert D(rule(decision, "R-LIVE-3").values["sizing_equity_usd"]) == EQUITY
    assert decision.approved_quantity == evaluate().approved_quantity


def _cap(decision: Any, rule_id: str) -> Decimal:
    return D(rule(decision, rule_id).values["max_quantity"])


def test_position_cap_counts_the_existing_position() -> None:
    # 25% of equity is USD 80.65; holding USD 60 leaves USD 20.65 -> 0.20645 shares.
    decision = evaluate(positions=(position(market_value_usd=D(60)),))
    assert _cap(decision, "R-POS-2") == D("0.20645")
    assert decision.verdict is Verdict.REDUCE
    assert decision.approved_quantity == D("0.20645")


@pytest.mark.parametrize(
    ("held_usd", "passes"), [(EQUITY / 4 - D("0.01"), True), (EQUITY / 4, False)]
)
def test_position_cap_at_its_limit(held_usd: Decimal, passes: bool) -> None:
    decision = evaluate(positions=(position(market_value_usd=held_usd),))
    assert ("R-POS-2" not in failed(decision)) is passes


def test_single_stock_position_cap_is_tighter() -> None:
    decision = evaluate(engine=STOCK_ENGINE, market_overrides={"instrument": STOCK})
    assert _cap(decision, "R-POS-2") == D("0.32258")  # 10% of USD 322.58 at USD 100
    assert decision.approved_quantity == D("0.32258")


def test_gross_exposure_cap_counts_positions_and_open_entries() -> None:
    held = (position(symbol="QQQ", market_value_usd=EQUITY - D(20)),)
    orders = (open_order(symbol="IWM", reserved_notional_usd=D(10)),)
    decision = evaluate(positions=held, open_orders=orders, settled_cash_usd=EQUITY)
    assert _cap(decision, "R-PORT-1") == D("0.10000")
    assert decision.approved_quantity == D("0.10000")


def test_no_leverage_when_fully_invested() -> None:
    held = (position(symbol="QQQ", market_value_usd=EQUITY),)
    decision = evaluate(positions=held)
    assert "R-PORT-1" in failed(decision)
    assert decision.verdict is Verdict.REJECT


BUFFER = EQUITY * D("0.05")  # USD 16.13
UNIT_CASH = D(100) * D("1.0005") + D("0.000003")  # price plus slippage and CAT fee


@pytest.mark.parametrize(
    ("cash", "passes"),
    [(BUFFER - D("0.01"), False), (BUFFER, False), (BUFFER + UNIT_CASH * D("0.00001"), True)],
)
def test_cash_buffer(cash: Decimal, passes: bool) -> None:
    decision = evaluate(settled_cash_usd=cash)
    assert ("R-PORT-2" not in failed(decision)) is passes


def test_usd_cash_only() -> None:
    decision = evaluate(settled_cash_usd=D(0))
    assert {"R-ECON-3", "R-PORT-2"} <= failed(decision)


def test_open_entry_orders_reserve_cash() -> None:
    orders = (open_order(symbol="QQQ", reserved_notional_usd=D(20)),)
    decision = evaluate(settled_cash_usd=D(50), open_orders=orders)
    expected = round_down((D(50) - D(20) - BUFFER) / UNIT_CASH, D("0.00001"))
    assert _cap(decision, "R-PORT-2") == expected
    assert decision.approved_quantity == expected


def test_portfolio_volatility_cap() -> None:
    # Budget 1.2% of equity; QQQ uses 300 * 0.0125 = 3.75 of it; SPY moves 0.011 * 100 per share.
    held = (position(symbol="QQQ", market_value_usd=D(300), daily_volatility=D("0.0125")),)
    decision = evaluate(positions=held)
    expected = round_down((EQUITY * D("0.012") - D("3.75")) / D("1.1"), D("0.00001"))
    assert _cap(decision, "R-PORT-3") == expected
    assert decision.approved_quantity == expected


def test_portfolio_volatility_counts_open_entries() -> None:
    orders = (open_order(symbol="QQQ", reserved_notional_usd=D(300), daily_volatility=D("0.013")),)
    decision = evaluate(open_orders=orders, settled_cash_usd=EQUITY * 2)
    assert "R-PORT-3" in failed(decision)


def test_unknown_volatility_fails_closed() -> None:
    decision = evaluate(market_overrides={"daily_volatility": D(0)})
    assert "R-PORT-3" in failed(decision)


@pytest.mark.parametrize(
    ("adv", "expected"), [(D(100), D("0.1")), (D(10), D("0.01")), (D(0), D(0))]
)
def test_order_size_versus_average_daily_volume(adv: Decimal, expected: Decimal) -> None:
    decision = evaluate(market_overrides={"avg_daily_volume_20d": adv})
    assert _cap(decision, "R-ORD-2") == expected
    assert min(expected, D("0.34643")) == decision.approved_quantity


@pytest.mark.parametrize(
    ("limit_jpy", "expected"), [(D(3_100), D("0.20000")), (D(1_550), D("0.10000"))]
)
def test_maximum_order_notional(limit_jpy: Decimal, expected: Decimal) -> None:
    engine = RiskEngine(RiskLimits(live_max_order_notional_jpy=limit_jpy))
    decision = evaluate(engine=engine)
    assert _cap(decision, "R-LIVE-1") == expected
    assert decision.approved_quantity == expected


def test_maximum_total_holdings() -> None:
    engine = RiskEngine(
        RiskLimits(live_max_order_notional_jpy=D(10_000), live_max_total_holdings_jpy=D(10_000))
    )
    held = (position(symbol="QQQ", market_value_usd=D(60)),)  # 10,000 / 155 = 64.52
    decision = evaluate(engine=engine, positions=held)
    assert _cap(decision, "R-LIVE-2") == D("0.04516")
    assert decision.approved_quantity == D("0.04516")


# ---------------------------------------------------------------- AI overlay


def test_ai_multiplier_shrinks_the_quantity() -> None:
    decision = evaluate(advisories=(advisory(size_multiplier=D("0.5")),))
    assert decision.approved_quantity == D("0.17321")  # 0.34643 * 0.5 rounded down
    assert rule(decision, "AI-1").values["advisories"] == "adv-1"


def test_ai_veto_rejects() -> None:
    decision = evaluate(advisories=(advisory(veto=True, size_multiplier=D(1)),))
    assert failed(decision) == {"AI-1", "R-ECON-2"}
    assert decision.verdict is Verdict.REJECT


def test_smallest_multiplier_of_active_advisories_wins() -> None:
    advisories = (
        advisory(advisory_id="a", size_multiplier=D("0.8")),
        advisory(
            advisory_id="b",
            kind=AdvisoryKind.SYMBOL_EVENT,
            symbol="SPY",
            size_multiplier=D("0.25"),
        ),
    )
    decision = evaluate(advisories=advisories)
    assert rule(decision, "AI-1").values["multiplier"] == "0.25"


@pytest.mark.parametrize(
    "overrides",
    [
        {"valid_from": NOW + timedelta(seconds=1), "valid_until": NOW + timedelta(hours=1)},
        {"valid_from": NOW - timedelta(hours=1), "valid_until": NOW},
        {"kind": AdvisoryKind.SYMBOL_EVENT, "symbol": "QQQ"},
    ],
)
def test_inactive_or_unrelated_advisories_are_ignored(overrides: dict[str, Any]) -> None:
    decision = evaluate(advisories=(advisory(veto=True, **overrides),))
    assert failed(decision) == set()
    assert decision.approved_quantity == D("0.34643")


def test_shrinking_below_the_minimum_order_rejects() -> None:
    decision = evaluate(advisories=(advisory(size_multiplier=D("0.02")),))  # about USD 0.69
    assert failed(decision) == {"R-ECON-2"}


# ---------------------------------------------------------------- R-ECON-1 and R-ECON-2


def test_cost_share_at_its_limit() -> None:
    ibsj_like = FeeSchedule(name="ibsj_fixed", commission_rate=D("0.01"), commission_max_usd=D(1))
    probe = evaluate(engine=RiskEngine(RiskLimits(), fees=ibsj_like))
    share = D(rule(probe, "R-ECON-1").values["cost_share_pct"])
    at_limit = RiskEngine(RiskLimits(max_cost_share_of_risk_budget_pct=share), fees=ibsj_like)
    below = RiskEngine(
        RiskLimits(max_cost_share_of_risk_budget_pct=share - D("0.0001")), fees=ibsj_like
    )
    assert "R-ECON-1" not in failed(evaluate(engine=at_limit))
    assert "R-ECON-1" in failed(evaluate(engine=below))


@pytest.mark.parametrize(
    ("notional", "passes"), [(D("0.99"), False), (D(1), True), (D("1.01"), True)]
)
def test_minimum_fractional_buy(notional: Decimal, passes: bool) -> None:
    decision = evaluate(intent=entry(target_notional=Money.of(notional, Currency.USD)))
    assert ("R-ECON-2" not in failed(decision)) is passes


def test_whole_share_minimum_is_one_increment() -> None:
    whole = {"symbol": "SPY", "kind": InstrumentKind.ETF, "fractionable": False}
    decision = evaluate(
        intent=entry(target_notional=None, target_quantity=D("0.5")),
        market_overrides={"instrument": whole},
    )
    assert "R-ECON-2" in failed(decision)
    assert decision.approved_quantity == 0


def test_economics_rule_is_skipped_for_zero_quantity() -> None:
    decision = evaluate(settled_cash_usd=D(0))
    assert rule(decision, "R-ECON-1").passed
    assert "zero quantity" in rule(decision, "R-ECON-1").detail


def test_fx_cost_counts_when_usd_is_not_held() -> None:
    engine = RiskEngine(RiskLimits(), assumptions=ExecutionAssumptions(usd_held=False))
    decision = evaluate(engine=engine)
    assert decision.approved_quantity < D("0.34643")
    assert failed(decision) == set()
