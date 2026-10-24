"""Rule thresholds from document 05 that are not (yet) configuration limits."""

from datetime import timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from aitrader.domain.types import NonNegativeDecimal, PositiveDecimal


class RiskParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # DQ-1 / DQ-6
    max_quote_age: timedelta = timedelta(seconds=15)
    max_clock_drift_seconds: PositiveDecimal = Decimal(1)
    # R-INST-1
    etf_only: bool = True
    """Owner decision Q14: liquid US ETFs only for now."""
    min_price_usd: PositiveDecimal = Decimal(5)
    min_avg_dollar_volume_usd: PositiveDecimal = Decimal(20_000_000)
    # R-EVT-1 (single stocks): 2 trading days before to 1 trading day after earnings. Counted in
    # calendar days chosen to cover a weekend plus one holiday, so the window is never too short.
    earnings_blackout_calendar_days_before: int = Field(default=5, ge=0)
    earnings_blackout_calendar_days_after: int = Field(default=4, ge=0)
    # CB-1 .. CB-5
    max_move_atr_multiple: PositiveDecimal = Decimal(3)
    max_move_pct: PositiveDecimal = Decimal(7)
    benchmark_drop_pct: PositiveDecimal = Decimal(3)
    max_spread_bps_etf: PositiveDecimal = Decimal(30)
    max_spread_bps_stock: PositiveDecimal = Decimal(50)
    max_opening_volume_multiple: PositiveDecimal = Decimal(5)
    # DUP-4
    max_non_protective_orders_per_symbol_per_day: int = Field(default=2, ge=0)
    # R-MONTH-1: risk per trade is multiplied by this once the soft monthly level is reached
    soft_monthly_risk_factor: NonNegativeDecimal = Decimal("0.5")
