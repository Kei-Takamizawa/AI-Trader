"""Executions reported by a broker or produced by the simulator."""

from pydantic import BaseModel, ConfigDict, Field

from aitrader.domain.enums import Side
from aitrader.domain.market import Symbol
from aitrader.domain.types import PositiveDecimal, UtcDatetime


class Fill(BaseModel):
    """One execution of an order. Prices are USD per share."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fill_id: str = Field(min_length=1)
    client_order_id: str = Field(min_length=1)
    symbol: Symbol
    side: Side
    quantity: PositiveDecimal
    price: PositiveDecimal
    ts: UtcDatetime
    source: str = Field(min_length=1)
    """Where the fill came from: ``event``, ``order-detail`` or ``sim``."""
