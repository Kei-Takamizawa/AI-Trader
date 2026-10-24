"""Snapshots the risk engine evaluates. Amounts are USD unless the name says otherwise."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aitrader.domain import Instrument, IntentPurpose, Mode, Quote, Side, SystemState
from aitrader.domain.market import Symbol
from aitrader.domain.types import NonNegativeDecimal, PositiveDecimal, StrictDecimal, UtcDatetime

DATA_GATE_RULES = frozenset({"DQ-2", "DQ-3", "DQ-4", "DQ-5"})
"""Findings the data quality gate (document 05 §4) reports per symbol."""


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    quantity: NonNegativeDecimal
    available_quantity: NonNegativeDecimal
    market_value_usd: NonNegativeDecimal
    daily_volatility: NonNegativeDecimal = Decimal(0)
    """60-day standard deviation of daily returns, as a fraction (0.011 = 1.1%)."""


class OpenOrderSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: str
    client_order_id: str
    symbol: Symbol
    side: Side
    purpose: IntentPurpose
    remaining_quantity: NonNegativeDecimal
    reserved_notional_usd: NonNegativeDecimal
    """Cash the order can still consume (BUY) or zero (SELL)."""
    daily_volatility: NonNegativeDecimal = Decimal(0)


class MarketWideState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark_change_pct: StrictDecimal | None = None
    """Benchmark ETF change from the previous close, in percent (−3.1 = down 3.1%)."""
    market_wide_halt: bool = False
    clock_drift_seconds: NonNegativeDecimal = Decimal(0)


class SymbolMarketState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument: Instrument
    allow_listed: bool
    tradable: bool = True
    """Broker status allows opening positions (Webull status ``OC``)."""
    quote: Quote | None = None
    previous_close: PositiveDecimal | None = None
    atr14: PositiveDecimal | None = None
    avg_daily_volume_20d: NonNegativeDecimal = Decimal(0)
    avg_daily_dollar_volume_20d: NonNegativeDecimal = Decimal(0)
    daily_volatility: NonNegativeDecimal = Decimal(0)
    first_30m_volume: NonNegativeDecimal | None = None
    avg_first_30m_volume_20d: NonNegativeDecimal | None = None
    trading_halted: bool = False
    next_earnings_date: date | None = None
    last_earnings_date: date | None = None
    data_quality_failures: frozenset[str] = frozenset()
    """Rule IDs (DQ-2 .. DQ-5) reported by the data quality gate for this symbol."""

    @field_validator("data_quality_failures")
    @classmethod
    def _known_rules(cls, value: frozenset[str]) -> frozenset[str]:
        unknown = value - DATA_GATE_RULES
        if unknown:
            raise ValueError(f"unknown data quality rule IDs: {sorted(unknown)}")
        return value


class RiskState(BaseModel):
    """Everything the risk engine needs to know about the account at one moment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    now: UtcDatetime
    mode: Mode
    system_state: SystemState
    kill_switch: bool = False
    fx_jpy_per_usd: PositiveDecimal
    equity_usd: PositiveDecimal
    start_of_day_equity_usd: PositiveDecimal
    month_start_equity_usd: PositiveDecimal
    peak_equity_usd: PositiveDecimal
    settled_cash_usd: NonNegativeDecimal
    positions: tuple[PositionSnapshot, ...] = ()
    open_orders: tuple[OpenOrderSnapshot, ...] = ()
    orders_today: int = Field(default=0, ge=0)
    non_protective_orders_today: dict[str, int] = Field(default_factory=dict)
    market: MarketWideState = MarketWideState()

    def position(self, symbol: str) -> PositionSnapshot | None:
        for pos in self.positions:
            if pos.symbol == symbol and pos.quantity > 0:
                return pos
        return None
