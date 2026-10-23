"""Trading costs and currency conversions, recorded one item at a time (decision D-13)."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aitrader.domain.enums import FxDirection, TradeCostType
from aitrader.domain.money import Currency, Money
from aitrader.domain.types import PositiveDecimal, UtcDatetime


class TradeCost(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: UtcDatetime
    type: TradeCostType
    amount: Money
    client_order_id: str | None = None
    estimated: bool = Field(
        description="True when computed by a cost model, False when reported by the broker."
    )
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.amount.is_negative:
            raise ValueError("a cost must not be negative")
        return self


class FxConversion(BaseModel):
    """One JPY⇄USD conversion. Rates are JPY per USD."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: UtcDatetime
    direction: FxDirection
    jpy_amount: Money
    usd_amount: Money
    applied_rate: PositiveDecimal
    reference_rate: PositiveDecimal
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.jpy_amount.currency is not Currency.JPY:
            raise ValueError("jpy_amount must be in JPY")
        if self.usd_amount.currency is not Currency.USD:
            raise ValueError("usd_amount must be in USD")
        if self.jpy_amount.amount <= 0 or self.usd_amount.amount <= 0:
            raise ValueError("conversion amounts must be greater than zero")
        return self

    @property
    def spread_cost(self) -> Money:
        """Cost against the reference rate, in JPY (never negative)."""
        diff = abs(self.applied_rate - self.reference_rate)
        return Money(amount=diff * self.usd_amount.amount, currency=Currency.JPY)
