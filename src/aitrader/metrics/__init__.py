"""Performance, trade and cost metrics, and benchmark curves (document 12 §6)."""

from aitrader.metrics.performance import (
    EquityPoint,
    buy_and_hold,
    daily_returns,
    deflated_sharpe,
    fmt,
    max_drawdown,
    period_returns,
    sharpe,
    sharpe_per_session,
    sortino,
    summarize,
    trade_pnls,
    trade_stats,
)

__all__ = [
    "EquityPoint",
    "buy_and_hold",
    "daily_returns",
    "deflated_sharpe",
    "fmt",
    "max_drawdown",
    "period_returns",
    "sharpe",
    "sharpe_per_session",
    "sortino",
    "summarize",
    "trade_pnls",
    "trade_stats",
]
