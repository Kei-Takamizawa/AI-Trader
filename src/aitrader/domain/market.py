"""Instruments and market data value objects."""

import re
from decimal import Decimal
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, model_validator

from aitrader.domain.enums import InstrumentKind
from aitrader.domain.money import Currency
from aitrader.domain.types import NonNegativeDecimal, PositiveDecimal, UtcDatetime

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def validate_symbol(value: str) -> str:
    """US ticker symbols: upper case letters, digits, '.' and '-', at most 10 characters."""
    if not _SYMBOL_RE.fullmatch(value):
        raise ValueError(f"invalid symbol: {value!r}")
    return value


Symbol = Annotated[str, AfterValidator(validate_symbol)]
"""A validated US ticker symbol."""


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    kind: InstrumentKind
    currency: Currency = Currency.USD
    fractionable: bool = False
    quantity_increment: PositiveDecimal = Decimal(1)


class Bar(BaseModel):
    """One OHLCV bar. ``ts`` is the bar's start time in UTC."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    ts: UtcDatetime
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal
    source: str = "unknown"

    @model_validator(mode="after")
    def _check_ohlc(self) -> Self:
        if self.high < self.low:
            raise ValueError("high must not be below low")
        if self.high < max(self.open, self.close):
            raise ValueError("high must be at least max(open, close)")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be at most min(open, close)")
        return self


class Quote(BaseModel):
    """Top-of-book quote."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    ts: UtcDatetime
    bid: PositiveDecimal
    ask: PositiveDecimal
    bid_size: NonNegativeDecimal
    ask_size: NonNegativeDecimal

    @model_validator(mode="after")
    def _check_spread(self) -> Self:
        if self.ask < self.bid:
            raise ValueError("crossed quote: ask is below bid")
        return self
