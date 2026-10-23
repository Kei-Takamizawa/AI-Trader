"""Broker fee schedules and execution-cost assumptions."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from aitrader.domain.types import NonNegativeDecimal, PositiveDecimal


class FeeSchedule(BaseModel):
    """What a broker charges for US stock orders. Amounts are USD unless stated otherwise."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    commission_rate: NonNegativeDecimal = Decimal(0)
    """Commission as a fraction of the order notional."""
    commission_min_usd: NonNegativeDecimal = Decimal(0)
    """Minimum commission per order (only when a commission is charged)."""
    commission_max_usd: NonNegativeDecimal | None = None
    cat_fee_per_share_usd: NonNegativeDecimal = Decimal(0)
    """Consolidated Audit Trail fee, charged on buys and sells."""
    finra_fee_per_share_sold_usd: NonNegativeDecimal = Decimal(0)
    finra_fee_min_usd: NonNegativeDecimal = Decimal(0)
    finra_fee_max_usd: NonNegativeDecimal | None = None
    finra_fee_split_fractional: bool = False
    """True when the whole-share and fractional parts of a sale are charged separately."""
    sec_fee_rate: NonNegativeDecimal = Decimal(0)
    """SEC fee per USD of sale proceeds, rounded up to the cent."""
    fx_spread_jpy_per_usd: NonNegativeDecimal = Decimal(0)
    """Charged on each JPY⇄USD conversion, not per trade when USD is held."""
    min_fractional_buy_usd: NonNegativeDecimal = Decimal(0)
    fractional_quantity_step: PositiveDecimal = Decimal("0.00001")


WEBULL_JP = FeeSchedule(
    name="webull_jp",
    commission_rate=Decimal(0),
    cat_fee_per_share_usd=Decimal("0.000003"),
    finra_fee_per_share_sold_usd=Decimal("0.000195"),
    finra_fee_min_usd=Decimal("0.01"),
    finra_fee_max_usd=Decimal("9.79"),
    finra_fee_split_fractional=True,
    sec_fee_rate=Decimal("0.0000206"),
    fx_spread_jpy_per_usd=Decimal("0.15"),
    min_fractional_buy_usd=Decimal(1),
    fractional_quantity_step=Decimal("0.00001"),
)
"""Webull Securities Japan, cash account, US stocks (official pricing page, 2026-10-23)."""


class ExecutionAssumptions(BaseModel):
    """Model assumptions for costs that are not broker fees (document 05 §2, document 12 §4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slippage_bps_per_side: NonNegativeDecimal = Decimal(5)
    """Spread plus slippage, in basis points of the notional, on each side."""
    stop_slippage_allowance_pct: NonNegativeDecimal = Decimal("0.5")
    """Extra loss per share assumed beyond the stop price (gaps), in percent of the entry."""
    usd_held: bool = True
    """When USD is held, trades pay no FX spread; conversions are recorded separately."""
