"""Backtesting: the production cycle driven by a simulated clock and the SimBroker."""

from aitrader.backtest.engine import BacktestResult, FillRecord, etf_universe, run_backtest
from aitrader.backtest.manifest import build_manifest, equity_csv, metrics_for, write_results

__all__ = [
    "BacktestResult",
    "FillRecord",
    "build_manifest",
    "equity_csv",
    "etf_universe",
    "metrics_for",
    "run_backtest",
    "write_results",
]
