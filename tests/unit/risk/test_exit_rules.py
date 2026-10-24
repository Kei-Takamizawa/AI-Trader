"""Exit (SELL) rules: allowed in most states, never more than the available quantity."""

from decimal import Decimal
from typing import Any

import pytest

from aitrader.config import RiskLimits
from aitrader.domain import Currency, IntentPurpose, Money, SystemState, Verdict
from aitrader.risk import MarketWideState, RiskEngine
from tests.unit.risk.builders import (
    D,
    exit_intent,
    failed,
    market,
    open_order,
    position,
    rule,
    state,
)

ENGINE = RiskEngine(RiskLimits())
HELD = (position(),)  # 0.3 shares, USD 30


def evaluate(intent: Any = None, market_state: Any = "default", **state_overrides: Any) -> Any:
    state_overrides.setdefault("positions", HELD)
    return ENGINE.evaluate(
        intent or exit_intent(),
        state(**state_overrides),
        market() if market_state == "default" else market_state,
    )


def test_full_exit_is_approved() -> None:
    decision = evaluate()
    assert decision.verdict is Verdict.APPROVE
    assert decision.approved_quantity == D("0.3")
    assert {r.rule_id for r in decision.rule_results} == {
        "STATE-1",
        "HC-1",
        "DUP-1",
        "DUP-3",
        "EXIT-QTY",
    }


@pytest.mark.parametrize(
    ("requested", "verdict", "approved"),
    [
        (D("0.29999"), Verdict.APPROVE, D("0.29999")),
        (D("0.3"), Verdict.APPROVE, D("0.3")),
        (D("0.30001"), Verdict.REDUCE, D("0.3")),
        (D(5), Verdict.REDUCE, D("0.3")),
    ],
)
def test_never_sells_more_than_available(
    requested: Decimal, verdict: Verdict, approved: Decimal
) -> None:
    decision = evaluate(exit_intent(target_quantity=requested))
    assert decision.verdict is verdict
    assert decision.approved_quantity == approved


def test_partial_exit_is_rounded_down_to_the_increment() -> None:
    decision = evaluate(exit_intent(target_quantity=D("0.1234567")))
    assert decision.verdict is Verdict.APPROVE  # rounding alone is not a reduction (as for entries)
    assert decision.approved_quantity == D("0.12345")


def test_partial_exit_without_market_state_uses_the_fractional_step() -> None:
    decision = evaluate(exit_intent(target_quantity=D("0.1234567")), market_state=None)
    assert decision.approved_quantity == D("0.12345")


def test_quantity_reserved_by_open_orders_is_not_available() -> None:
    held = (position(available_quantity=D("0.1")),)
    decision = evaluate(positions=held)
    assert decision.verdict is Verdict.REDUCE
    assert decision.approved_quantity == D("0.1")


def test_no_position_means_no_sell() -> None:
    decision = evaluate(positions=())
    assert failed(decision) == {"HC-1", "EXIT-QTY"}
    assert decision.verdict is Verdict.REJECT


@pytest.mark.parametrize(
    ("system_state", "kill_switch", "purpose", "allowed"),
    [
        (SystemState.RUNNING, False, IntentPurpose.EXIT, True),
        (SystemState.DEGRADED, False, IntentPurpose.EXIT, True),
        (SystemState.HALTED, False, IntentPurpose.EXIT, True),
        (SystemState.SAFE, False, IntentPurpose.STOP_EXIT, False),
        (SystemState.KILLED, False, IntentPurpose.EXIT, False),
        (SystemState.KILLED, False, IntentPurpose.STOP_EXIT, True),
        (SystemState.KILLED, False, IntentPurpose.FLATTEN, True),
        (SystemState.RUNNING, True, IntentPurpose.EXIT, False),
        (SystemState.RUNNING, True, IntentPurpose.FLATTEN, True),
    ],
)
def test_exits_by_system_state(
    system_state: SystemState, kill_switch: bool, purpose: IntentPurpose, allowed: bool
) -> None:
    decision = evaluate(
        exit_intent(purpose=purpose), system_state=system_state, kill_switch=kill_switch
    )
    assert ("STATE-1" not in failed(decision)) is allowed


def test_one_active_order_per_intent() -> None:
    orders = (open_order(intent_id="intent-sell-1", symbol="SPY", side="SELL", purpose="EXIT"),)
    assert failed(evaluate(open_orders=orders)) == {"DUP-1", "DUP-3"}


def test_no_second_open_exit_for_the_same_symbol() -> None:
    orders = (open_order(symbol="SPY", side="SELL", purpose="STOP_EXIT"),)
    assert failed(evaluate(open_orders=orders)) == {"DUP-3"}


def test_open_entry_order_does_not_block_an_exit() -> None:
    orders = (open_order(symbol="SPY"),)
    assert failed(evaluate(open_orders=orders)) == set()


def test_exits_ignore_data_circuit_breaker_loss_and_count_rules() -> None:
    bad_market = market(
        quote=None,
        trading_halted=True,
        data_quality_failures=frozenset({"DQ-2"}),
        allow_listed=False,
    )
    decision = evaluate(
        market_state=bad_market,
        market=MarketWideState(benchmark_change_pct=D(-9), clock_drift_seconds=D(5)),
        start_of_day_equity_usd=D(1000),
        orders_today=50,
    )
    assert decision.verdict is Verdict.APPROVE


@pytest.mark.parametrize(
    ("overrides", "market_state", "expected"),
    [
        ({"limit_price": D(100)}, "default", D("0.15")),  # USD 15 at the limit price
        ({}, "default", D("0.15001")),  # at the bid of 99.99
        ({}, None, D("0.15")),  # at the position's value per share (USD 100)
    ],
)
def test_exit_by_notional(overrides: dict[str, Any], market_state: Any, expected: Decimal) -> None:
    intent = exit_intent(
        target_quantity=None, target_notional=Money.of(15, Currency.USD), **overrides
    )
    decision = evaluate(intent, market_state=market_state)
    assert decision.approved_quantity == expected


def test_exit_by_notional_without_any_price_is_rejected() -> None:
    intent = exit_intent(target_quantity=None, target_notional=Money.of(15, Currency.USD))
    decision = evaluate(intent, market_state=None, positions=())
    assert "EXIT-QTY" in failed(decision)
    assert "no price" in rule(decision, "EXIT-QTY").detail
