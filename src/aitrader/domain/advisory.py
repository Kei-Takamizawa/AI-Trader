"""Advisory signals from AI analysis. They can only veto or shrink rule-based intents."""

from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aitrader.domain.enums import AdvisoryKind, AdvisorySource
from aitrader.domain.market import Symbol
from aitrader.domain.types import StrictDecimal, UtcDatetime


class AdvisorySignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    advisory_id: str = Field(min_length=1)
    kind: AdvisoryKind
    symbol: Symbol | None = None
    veto: bool = False
    size_multiplier: StrictDecimal = Decimal(1)
    valid_from: UtcDatetime
    valid_until: UtcDatetime
    source: AdvisorySource
    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    input_bundle_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not Decimal(0) <= self.size_multiplier <= Decimal(1):
            raise ValueError("size_multiplier must be within [0, 1]; AI may never enlarge an order")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        if self.kind is AdvisoryKind.SYMBOL_EVENT and self.symbol is None:
            raise ValueError("a SYMBOL_EVENT advisory needs a symbol")
        if self.kind is AdvisoryKind.MARKET_REGIME and self.symbol is not None:
            raise ValueError("a MARKET_REGIME advisory applies to the whole market, not one symbol")
        return self
