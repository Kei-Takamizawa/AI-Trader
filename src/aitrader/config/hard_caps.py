"""Hard caps compiled into the code (document 05, sections 1 and 13).

Configuration may set stricter values, never looser ones. Raising a value here requires a code
change, review and the owner's approval (protected path, see CLAUDE.md).
"""

from decimal import Decimal
from types import MappingProxyType
from typing import Final

HARD_CAPS: Final = MappingProxyType(
    {
        # field name in RiskLimits -> largest allowed value
        "risk_per_trade_pct": Decimal("1.0"),
        "daily_loss_limit_pct": Decimal("3.0"),
        "monthly_soft_loss_pct": Decimal("5.0"),
        "monthly_loss_limit_pct": Decimal("5.0"),
        "max_drawdown_pct": Decimal("15.0"),
        "max_open_positions": Decimal(8),
        "max_position_pct_etf": Decimal("30.0"),
        "max_position_pct_stock": Decimal("30.0"),
        "max_gross_exposure_pct": Decimal("100.0"),
        "max_portfolio_daily_vol_pct": Decimal("2.0"),
        "max_orders_per_day": Decimal(20),
        "max_order_adv_pct": Decimal("1.0"),
        "max_cost_share_of_risk_budget_pct": Decimal("100.0"),
        "approved_capital_jpy": Decimal(100_000),
        "live_max_order_notional_jpy": Decimal(50_000),
        "live_max_total_holdings_jpy": Decimal(100_000),
    }
)
"""Upper bounds for every numeric field of :class:`aitrader.config.settings.RiskLimits`."""

ALLOW_SHORT_SELLING: Final = False
ALLOW_MARGIN: Final = False
ALLOWED_TRADING_SESSIONS: Final = frozenset({"CORE"})
"""Regular trading hours only (HC-7)."""
