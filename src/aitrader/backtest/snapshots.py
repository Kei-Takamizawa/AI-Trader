"""Immutable close-of-session evidence, captured before the replay advances the ledger."""

import hashlib
import json
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from aitrader.config.loader import _canonical
from aitrader.domain import Fill, OrderIntent, RiskDecision
from aitrader.domain.types import StrictDecimal


def canonical_json(value: object) -> str:
    """Use stable decimal spelling and key order for replay comparisons."""
    return json.dumps(value, default=_canonical, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    quantity: StrictDecimal
    average_cost: StrictDecimal
    close: StrictDecimal
    market_value_usd: StrictDecimal


class SessionFill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fill: Fill
    fees_usd: StrictDecimal


class SessionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session: date
    equity_usd: StrictDecimal
    cash_usd_settled: StrictDecimal
    cash_usd_unsettled: StrictDecimal
    positions: tuple[PositionSnapshot, ...]
    fills: tuple[SessionFill, ...]
    next_intents: tuple[OrderIntent, ...]
    rejections: tuple[RiskDecision, ...]
    skipped: tuple[str, ...]
    data_quality: dict[str, tuple[str, ...]]

    @property
    def snapshot_json(self) -> str:
        return canonical_json(self.model_dump(mode="python"))

    @property
    def snapshot_hash(self) -> str:
        return digest(self.model_dump(mode="python"))

    @property
    def fees_usd(self) -> Decimal:
        return sum((f.fees_usd for f in self.fills), Decimal(0))
