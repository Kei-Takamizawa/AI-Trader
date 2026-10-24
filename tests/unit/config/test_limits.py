from decimal import Decimal

import pytest
from pydantic import ValidationError

from aitrader.config import HARD_CAPS, ConfigError, RiskLimits, enforce_hard_caps
from aitrader.config.hard_caps import (
    ALLOW_MARGIN,
    ALLOW_SHORT_SELLING,
    ALLOWED_TRADING_SESSIONS,
)

_INT_FIELDS = {"max_open_positions", "max_orders_per_day"}


def test_defaults_are_the_documented_initial_values() -> None:
    limits = RiskLimits()
    assert limits.risk_per_trade_pct == Decimal("0.5")
    assert limits.daily_loss_limit_pct == Decimal("1.5")
    assert limits.monthly_loss_limit_pct == Decimal(5)
    assert limits.max_drawdown_pct == Decimal(10)
    assert limits.approved_capital_jpy == Decimal(50_000)
    assert limits.live_max_total_holdings_jpy == Decimal(100_000)
    enforce_hard_caps(limits)


def test_every_upper_limit_has_a_hard_cap() -> None:
    upper_limits = set(RiskLimits.model_fields) - {"min_cash_buffer_pct"}
    assert set(HARD_CAPS) == upper_limits


def test_defaults_are_within_hard_caps() -> None:
    limits = RiskLimits()
    for name, cap in HARD_CAPS.items():
        assert Decimal(getattr(limits, name)) <= cap, name


@pytest.mark.parametrize("name", sorted(HARD_CAPS))
def test_value_at_cap_is_accepted_and_above_cap_is_rejected(name: str) -> None:
    cap = HARD_CAPS[name]
    at_cap: object = int(cap) if name in _INT_FIELDS else cap
    above: object = int(cap) + 1 if name in _INT_FIELDS else cap + Decimal("0.01")
    base = RiskLimits().model_dump()
    # Keep the consistency rules satisfied while probing one field at a time.
    if name == "monthly_soft_loss_pct":
        base["monthly_loss_limit_pct"] = HARD_CAPS["monthly_loss_limit_pct"]
        at_cap = cap - Decimal("0.01")
        above = None
    if name == "live_max_order_notional_jpy":
        base["live_max_total_holdings_jpy"] = HARD_CAPS["live_max_total_holdings_jpy"]

    enforce_hard_caps(RiskLimits.model_validate({**base, name: at_cap}))

    if above is None:
        return
    try:
        limits = RiskLimits.model_validate({**base, name: above})
    except ValidationError:
        return  # a consistency rule already rejects it
    with pytest.raises(ConfigError) as info:
        enforce_hard_caps(limits)
    message = str(info.value)
    assert name in message
    assert str(cap) in message


def test_consistency_rules() -> None:
    with pytest.raises(ValidationError, match="monthly_soft_loss_pct"):
        RiskLimits(monthly_soft_loss_pct=Decimal(5), monthly_loss_limit_pct=Decimal(5))
    with pytest.raises(ValidationError, match="live_max_order_notional_jpy"):
        RiskLimits(
            live_max_order_notional_jpy=Decimal(60_000),
            live_max_total_holdings_jpy=Decimal(50_000),
        )
    with pytest.raises(ValidationError, match="min_cash_buffer_pct"):
        RiskLimits(min_cash_buffer_pct=Decimal(100))


def test_integer_limits_are_strict() -> None:
    with pytest.raises(ValidationError):
        RiskLimits(max_open_positions="4")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RiskLimits(max_open_positions=0)


def test_structural_hard_caps() -> None:
    assert ALLOW_SHORT_SELLING is False
    assert ALLOW_MARGIN is False
    assert frozenset({"CORE"}) == ALLOWED_TRADING_SESSIONS


def test_hard_caps_cannot_be_modified() -> None:
    with pytest.raises(TypeError):
        HARD_CAPS["risk_per_trade_pct"] = Decimal(5)  # type: ignore[index]
