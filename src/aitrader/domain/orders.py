"""Order intents produced by strategies and the risk engine's decisions about them."""

from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aitrader.domain.enums import IntentPurpose, OrderType, Side, TimeInForce, Verdict
from aitrader.domain.errors import DomainError
from aitrader.domain.ids import CLIENT_ORDER_ID_PATTERN
from aitrader.domain.market import Symbol
from aitrader.domain.money import Currency, Money
from aitrader.domain.types import NonNegativeDecimal, PositiveDecimal, UtcDatetime


class OrderIntent(BaseModel):
    """What a strategy wants to do. The risk engine decides the final quantity.

    Exactly one sizing basis is given: ``target_notional`` (amount of money) or
    ``target_quantity`` (number of shares). Entries (BUY) must carry a stop price.
    ``purpose`` defaults to ENTRY for a BUY and EXIT for a SELL.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    symbol: Symbol
    side: Side
    purpose: IntentPurpose
    target_notional: Money | None = None
    target_quantity: PositiveDecimal | None = None
    limit_price: PositiveDecimal | None = None
    stop_price: PositiveDecimal | None = None
    reason: str = Field(min_length=1)
    signal_ts: UtcDatetime
    advisory_ids: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _default_purpose(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("purpose") is None:
            side = data.get("side")
            data = {
                **data,
                "purpose": IntentPurpose.ENTRY if side in (Side.BUY, "BUY") else IntentPurpose.EXIT,
            }
        return data

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.purpose is IntentPurpose.ENTRY) != (self.side is Side.BUY):
            raise ValueError("ENTRY intents are BUYs and every other purpose is a SELL")
        if (self.target_notional is None) == (self.target_quantity is None):
            raise ValueError("give exactly one of target_notional and target_quantity")
        if self.target_notional is not None:
            if self.target_notional.currency is not Currency.USD:
                raise ValueError("target_notional must be in USD")
            if self.target_notional.amount <= 0:
                raise ValueError("target_notional must be greater than zero")
        if self.side is Side.BUY:
            if self.stop_price is None:
                raise ValueError("entries (BUY) must carry a stop price")
            if self.limit_price is not None and self.stop_price >= self.limit_price:
                raise ValueError("stop price of a BUY must be below its limit price")
        return self

    def quantity_at(self, price: Decimal) -> Decimal:
        """Requested quantity: ``target_quantity``, or ``target_notional`` divided by ``price``."""
        if self.target_quantity is not None:
            return self.target_quantity
        if self.target_notional is None:
            raise DomainError("intent has neither target_quantity nor target_notional")
        if price <= 0:
            raise DomainError("price must be greater than zero")
        return self.target_notional.amount / price


class RuleResult(BaseModel):
    """The outcome of one risk rule, with the numbers it used (for the audit trail)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(min_length=1)
    passed: bool
    detail: str = ""
    values: dict[str, str] = Field(default_factory=dict)


class RiskDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: str = Field(min_length=1)
    verdict: Verdict
    approved_quantity: NonNegativeDecimal
    rule_results: tuple[RuleResult, ...]
    limits_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        failed = [r for r in self.rule_results if not r.passed]
        if self.verdict is Verdict.REJECT:
            if self.approved_quantity != 0:
                raise ValueError("a rejected intent must have approved_quantity 0")
            if not failed:
                raise ValueError("a rejection must name at least one failed rule")
        else:
            if self.approved_quantity <= 0:
                raise ValueError("an approved or reduced intent needs a positive quantity")
            if self.verdict is Verdict.APPROVE and failed:
                raise ValueError("APPROVE is not allowed when a rule failed; use REDUCE or REJECT")
        return self


class OrderRequest(BaseModel):
    """The order sent to a broker for one attempt of one intent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_order_id: str = Field(pattern=CLIENT_ORDER_ID_PATTERN)
    intent_id: str = Field(min_length=1)
    symbol: Symbol
    side: Side
    purpose: IntentPurpose
    order_type: OrderType
    quantity: PositiveDecimal
    limit_price: PositiveDecimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    extended_hours: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.purpose is IntentPurpose.ENTRY) != (self.side is Side.BUY):
            raise ValueError("ENTRY orders are BUYs and every other purpose is a SELL")
        if (self.order_type is OrderType.LIMIT) != (self.limit_price is not None):
            raise ValueError("LIMIT orders, and only those, carry a limit price")
        return self

    @property
    def notional_at_limit(self) -> Decimal | None:
        return self.quantity * self.limit_price if self.limit_price is not None else None
