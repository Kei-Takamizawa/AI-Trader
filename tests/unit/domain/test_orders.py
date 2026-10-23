from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aitrader.domain import (
    Currency,
    DomainError,
    IntentPurpose,
    Money,
    OrderIntent,
    OrderStatus,
    RiskDecision,
    RuleResult,
    Side,
    Verdict,
)

TS = datetime(2026, 10, 22, 20, 0, tzinfo=UTC)


def _intent(**overrides: object) -> OrderIntent:
    fields: dict[str, object] = {
        "intent_id": "abc",
        "strategy_id": "S1",
        "strategy_version": "1.0.0",
        "symbol": "SPY",
        "side": Side.BUY,
        "target_notional": Money.of(37, Currency.USD),
        "limit_price": "650.10",
        "stop_price": "624.00",
        "reason": "close above 200-day average and in top ranks",
        "signal_ts": TS,
    }
    fields.update(overrides)
    return OrderIntent.model_validate(fields)


def test_valid_buy_intent() -> None:
    intent = _intent()
    assert intent.advisory_ids == ()
    assert intent.target_quantity is None


def test_sell_intent_needs_no_stop() -> None:
    intent = _intent(
        side=Side.SELL,
        stop_price=None,
        target_notional=None,
        target_quantity="0.05",
        limit_price=None,
    )
    assert intent.target_quantity == Decimal("0.05")


@pytest.mark.parametrize(
    "overrides",
    [
        {"stop_price": None},  # BUY without stop
        {"stop_price": "651"},  # stop above limit
        {"target_quantity": "1"},  # both sizing bases
        {"target_notional": None},  # neither sizing basis
        {"target_notional": Money.of(5000, Currency.JPY)},  # notional not in USD
        {"target_notional": Money.of(0, Currency.USD)},
        {"target_notional": None, "target_quantity": "0"},
        {"reason": ""},
        {"side": "SHORT"},  # there is no short side
    ],
)
def test_invalid_intents(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _intent(**overrides)


def _rule(passed: bool) -> RuleResult:
    return RuleResult(rule_id="R-TRADE-1", passed=passed, values={"risk": "1.61"})


def test_risk_decision_rules() -> None:
    approve = RiskDecision(
        intent_id="abc",
        verdict=Verdict.APPROVE,
        approved_quantity=Decimal("0.05"),
        rule_results=(_rule(True),),
        limits_hash="h",
    )
    assert approve.approved_quantity > 0
    RiskDecision(
        intent_id="abc",
        verdict=Verdict.REJECT,
        approved_quantity=Decimal(0),
        rule_results=(_rule(False),),
        limits_hash="h",
    )
    RiskDecision(
        intent_id="abc",
        verdict=Verdict.REDUCE,
        approved_quantity=Decimal("0.01"),
        rule_results=(_rule(False),),
        limits_hash="h",
    )


@pytest.mark.parametrize(
    ("verdict", "qty", "passed"),
    [
        (Verdict.REJECT, "0.1", False),  # rejected but quantity left
        (Verdict.REJECT, "0", True),  # rejection without a failed rule
        (Verdict.APPROVE, "0", True),  # approved without quantity
        (Verdict.APPROVE, "1", False),  # approved despite a failed rule
        (Verdict.REDUCE, "0", False),
    ],
)
def test_inconsistent_risk_decisions(verdict: Verdict, qty: str, passed: bool) -> None:
    with pytest.raises(ValidationError):
        RiskDecision(
            intent_id="abc",
            verdict=verdict,
            approved_quantity=Decimal(qty),
            rule_results=(_rule(passed),),
            limits_hash="h",
        )


def test_terminal_order_statuses() -> None:
    assert OrderStatus.FILLED.is_terminal
    assert OrderStatus.NOT_FOUND.is_terminal
    assert not OrderStatus.UNKNOWN.is_terminal
    assert not OrderStatus.PARTIALLY_FILLED.is_terminal


def test_quantity_at_uses_the_sizing_basis() -> None:
    assert _intent().quantity_at(Decimal(100)) == Decimal("0.37")
    by_quantity = _intent(target_notional=None, target_quantity="2")
    assert by_quantity.quantity_at(Decimal(100)) == 2


def test_quantity_at_refuses_invalid_input() -> None:
    with pytest.raises(DomainError, match="price"):
        _intent().quantity_at(Decimal(0))
    unchecked = _intent().model_copy(update={"target_notional": None})
    with pytest.raises(DomainError, match="neither"):
        unchecked.quantity_at(Decimal(100))


@pytest.mark.parametrize(
    ("side", "purpose"),
    [(Side.BUY, IntentPurpose.ENTRY), (Side.SELL, IntentPurpose.EXIT)],
)
def test_purpose_defaults_from_the_side(side: Side, purpose: IntentPurpose) -> None:
    fields: dict[str, object] = {"side": side}
    if side is Side.SELL:
        fields.update(stop_price=None, limit_price=None)
    assert _intent(**fields).purpose is purpose


@pytest.mark.parametrize(
    ("side", "purpose"),
    [
        (Side.BUY, IntentPurpose.EXIT),
        (Side.BUY, IntentPurpose.FLATTEN),
        (Side.SELL, IntentPurpose.ENTRY),
    ],
)
def test_entries_are_buys_and_exits_are_sells(side: Side, purpose: IntentPurpose) -> None:
    with pytest.raises(ValidationError, match="ENTRY intents are BUYs"):
        _intent(side=side, purpose=purpose, stop_price=None, limit_price=None)


def test_stop_exit_and_flatten_are_sells() -> None:
    for purpose in (IntentPurpose.STOP_EXIT, IntentPurpose.FLATTEN):
        intent = _intent(side=Side.SELL, purpose=purpose, stop_price=None, limit_price=None)
        assert intent.purpose is purpose
