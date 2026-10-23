from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aitrader.domain import (
    AdvisoryKind,
    AdvisorySignal,
    AdvisorySource,
    Currency,
    FxConversion,
    FxDirection,
    Money,
    TradeCost,
    TradeCostType,
)

T0 = datetime(2026, 10, 23, 11, 0, tzinfo=UTC)


def _advisory(**overrides: object) -> AdvisorySignal:
    fields: dict[str, object] = {
        "advisory_id": "adv-1",
        "kind": AdvisoryKind.MARKET_REGIME,
        "size_multiplier": "0.5",
        "valid_from": T0,
        "valid_until": T0 + timedelta(hours=10),
        "source": AdvisorySource.SCHEDULED_TASK,
        "model_id": "claude-opus-5-5",
        "prompt_version": "regime-v1",
        "input_bundle_hash": "deadbeef",
    }
    fields.update(overrides)
    return AdvisorySignal.model_validate(fields)


@pytest.mark.parametrize("multiplier", ["0", "0.5", "1"])
def test_multiplier_within_zero_and_one(multiplier: str) -> None:
    assert _advisory(size_multiplier=multiplier).size_multiplier == Decimal(multiplier)


@pytest.mark.parametrize("multiplier", ["1.01", "2", "-0.1"])
def test_multiplier_outside_range_is_rejected(multiplier: str) -> None:
    with pytest.raises(ValidationError, match="enlarge"):
        _advisory(size_multiplier=multiplier)


def test_advisory_window_and_symbol_rules() -> None:
    with pytest.raises(ValidationError):
        _advisory(valid_until=T0)
    with pytest.raises(ValidationError):
        _advisory(kind=AdvisoryKind.SYMBOL_EVENT)  # missing symbol
    with pytest.raises(ValidationError):
        _advisory(symbol="SPY")  # market regime with a symbol
    event = _advisory(kind=AdvisoryKind.SYMBOL_EVENT, symbol="XLK", veto=True)
    assert event.veto


def test_trade_cost_must_not_be_negative() -> None:
    cost = TradeCost(
        ts=T0,
        type=TradeCostType.FINRA_FEE,
        amount=Money.of("0.01", Currency.USD),
        client_order_id="ABC-1",
        estimated=False,
        source="webull_order_detail",
    )
    assert cost.amount.amount == Decimal("0.01")
    with pytest.raises(ValidationError):
        TradeCost(
            ts=T0,
            type=TradeCostType.SEC_FEE,
            amount=Money.of("-0.01", Currency.USD),
            estimated=True,
            source="cost_model",
        )


def test_fx_conversion_spread_cost() -> None:
    fx = FxConversion(
        ts=T0,
        direction=FxDirection.JPY_TO_USD,
        jpy_amount=Money.of(50_000, Currency.JPY),
        usd_amount=Money.of("322.27", Currency.USD),
        applied_rate=Decimal("155.15"),
        reference_rate=Decimal("155.00"),
        source="statement",
    )
    assert fx.spread_cost == Money.of("48.3405", Currency.JPY)


@pytest.mark.parametrize(
    "overrides",
    [
        {"jpy_amount": Money.of(1, Currency.USD)},
        {"usd_amount": Money.of(1, Currency.JPY)},
        {"usd_amount": Money.of(0, Currency.USD)},
    ],
)
def test_fx_conversion_currency_checks(overrides: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "ts": T0,
        "direction": FxDirection.USD_TO_JPY,
        "jpy_amount": Money.of(15_485, Currency.JPY),
        "usd_amount": Money.of(100, Currency.USD),
        "applied_rate": "154.85",
        "reference_rate": "155.00",
        "source": "statement",
    }
    fields.update(overrides)
    with pytest.raises(ValidationError):
        FxConversion.model_validate(fields)
