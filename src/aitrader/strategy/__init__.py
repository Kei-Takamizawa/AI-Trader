"""Strategies: pure functions from closed bars and holdings to order intents. They import only
the domain and the indicators."""

from aitrader.strategy.base import Holding, Strategy, StrategyContext, params_hash
from aitrader.strategy.pullback import PullbackParams, PullbackStrategy

STRATEGIES = {"S2": PullbackStrategy}
"""Strategies the command line can run, by ID."""

__all__ = [
    "STRATEGIES",
    "Holding",
    "PullbackParams",
    "PullbackStrategy",
    "Strategy",
    "StrategyContext",
    "params_hash",
]
