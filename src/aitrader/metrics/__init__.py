"""Performance, trade and cost metrics, and benchmark curves (document 12 §6)."""

from aitrader.metrics.performance import (
    EquityPoint,
    buy_and_hold,
    daily_returns,
    fmt,
    max_drawdown,
    period_returns,
    sharpe,
    sortino,
    summarize,
    trade_pnls,
    trade_stats,
)

__all__ = [
    "EquityPoint",
    "buy_and_hold",
    "daily_returns",
    "fmt",
    "max_drawdown",
    "period_returns",
    "sharpe",
    "sortino",
    "summarize",
    "trade_pnls",
    "trade_stats",
]
