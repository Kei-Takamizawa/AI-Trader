"""Deterministic pre-trade risk rules and position sizing (document 05). Never imports AI."""

from aitrader.risk.engine import RiskEngine
from aitrader.risk.halt import (
    ALLOWED_TRANSITIONS,
    AUTO_CLEARING_HALTS,
    Actor,
    HaltReason,
    HaltStateMachine,
    HaltTransition,
    IllegalTransitionError,
)
from aitrader.risk.parameters import RiskParameters
from aitrader.risk.sizing import (
    RiskBudget,
    max_quantity_within,
    risk_budget,
    risk_limited_quantity,
    round_down,
)
from aitrader.risk.state import (
    DATA_GATE_RULES,
    MarketWideState,
    OpenOrderSnapshot,
    PositionSnapshot,
    RiskState,
    SymbolMarketState,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "AUTO_CLEARING_HALTS",
    "DATA_GATE_RULES",
    "Actor",
    "HaltReason",
    "HaltStateMachine",
    "HaltTransition",
    "IllegalTransitionError",
    "MarketWideState",
    "OpenOrderSnapshot",
    "PositionSnapshot",
    "RiskBudget",
    "RiskEngine",
    "RiskParameters",
    "RiskState",
    "SymbolMarketState",
    "max_quantity_within",
    "risk_budget",
    "risk_limited_quantity",
    "round_down",
]
