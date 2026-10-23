from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from aitrader.domain import Currency, CurrencyMismatchError, Money
from aitrader.domain.types import (
    NonNegativeDecimal,
    PositiveDecimal,
    StrictDecimal,
    UtcDatetime,
    require_utc,
    to_decimal,
)


class _Holder(BaseModel):
    value: StrictDecimal


class _Positive(BaseModel):
    value: PositiveDecimal


class _NonNegative(BaseModel):
    value: NonNegativeDecimal


class _Stamp(BaseModel):
    ts: UtcDatetime


@pytest.mark.parametrize("raw", [Decimal("1.25"), 3, "4.50", " 7 "])
def test_strict_decimal_accepts_decimal_int_and_str(raw: object) -> None:
    assert isinstance(_Holder(value=raw).value, Decimal)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", [1.5, True, None, [1], "abc", "NaN", "Infinity", Decimal("NaN")])
def test_strict_decimal_rejects_floats_bools_and_non_finite(raw: object) -> None:
    with pytest.raises(ValidationError):
        _Holder(value=raw)  # type: ignore[arg-type]


def test_positive_and_non_negative() -> None:
    assert _Positive.model_validate({"value": "0.01"}).value == Decimal("0.01")
    with pytest.raises(ValidationError):
        _Positive(value=0)  # type: ignore[arg-type]
    assert _NonNegative(value=0).value == 0  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _NonNegative(value="-1")  # type: ignore[arg-type]


def test_decimals_serialise_as_strings_in_json() -> None:
    assert _Holder.model_validate({"value": "0.10"}).model_dump_json() == '{"value":"0.10"}'


def test_to_decimal_helper() -> None:
    assert to_decimal("2") == Decimal(2)
    with pytest.raises(ValueError, match="floats"):
        to_decimal(2.0)


def test_utc_datetime_rejects_naive_and_normalises() -> None:
    with pytest.raises(ValidationError):
        _Stamp(ts=datetime(2026, 1, 2, 3, 4))  # noqa: DTZ001
    tokyo = timezone(timedelta(hours=9))
    stamp = _Stamp(ts=datetime(2026, 1, 2, 9, 0, tzinfo=tokyo))
    assert stamp.ts == datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
    assert stamp.ts.tzinfo is UTC
    with pytest.raises(ValueError, match="naive"):
        require_utc(datetime(2026, 1, 2))  # noqa: DTZ001


def test_money_arithmetic_same_currency() -> None:
    a = Money.of("10.50", Currency.USD)
    b = Money.of(2, Currency.USD)
    assert a + b == Money.of("12.50", Currency.USD)
    assert a - b == Money.of("8.50", Currency.USD)
    assert -a == Money.of("-10.50", Currency.USD)
    assert a * 2 == Money.of("21.00", Currency.USD)
    assert 3 * b == Money.of(6, Currency.USD)
    assert b < a
    assert b <= a
    assert a > b
    assert a >= a
    assert Money.zero(Currency.JPY).is_zero
    assert (-a).is_negative
    assert str(a) == "10.50 USD"


def test_money_rejects_mixed_currencies() -> None:
    usd = Money.of(1, Currency.USD)
    jpy = Money.of(150, Currency.JPY)
    with pytest.raises(CurrencyMismatchError):
        _ = usd + jpy
    with pytest.raises(CurrencyMismatchError):
        _ = usd - jpy
    with pytest.raises(CurrencyMismatchError):
        _ = usd < jpy
    with pytest.raises(CurrencyMismatchError):
        _ = usd <= jpy
    with pytest.raises(CurrencyMismatchError):
        _ = usd > jpy
    with pytest.raises(CurrencyMismatchError):
        _ = usd >= jpy


def test_money_rejects_float_amounts_and_factors() -> None:
    with pytest.raises(ValidationError):
        Money(amount=1.1, currency=Currency.USD)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="floats"):
        _ = Money.of(1, Currency.USD) * 1.5  # type: ignore[operator]


def test_money_rejects_non_money_operand() -> None:
    with pytest.raises(TypeError):
        _ = Money.of(1, Currency.USD) + 1  # type: ignore[operator]


def test_money_is_immutable() -> None:
    money = Money.of(1, Currency.USD)
    with pytest.raises(ValidationError):
        money.amount = Decimal(2)  # type: ignore[misc]
