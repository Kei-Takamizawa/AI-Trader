"""The strategy port. Strategies are pure: closed bars and holdings in, order intents out."""

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from aitrader.domain import AdvisorySignal, Bar, OrderIntent
from aitrader.domain.market import Symbol
from aitrader.domain.types import PositiveDecimal, UtcDatetime


class Holding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    quantity: PositiveDecimal
    entry_session: date
    stop_price: PositiveDecimal | None = None


class StrategyContext(BaseModel):
    """Everything a strategy may use. ``history`` holds closed sessions only, oldest first."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    session: date
    """The last closed session; intents are executed in the next one."""
    as_of: UtcDatetime
    history: Mapping[str, tuple[Bar, ...]]
    holdings: Mapping[str, Holding] = Field(default_factory=dict)
    equity_usd: PositiveDecimal
    advisories: tuple[AdvisorySignal, ...] = ()


class Strategy(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    def params(self) -> Mapping[str, str]:
        """Parameter values as strings, for the run manifest."""
        ...

    def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]: ...


def params_hash(strategy: Strategy) -> str:
    text = json.dumps(dict(strategy.params()), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
