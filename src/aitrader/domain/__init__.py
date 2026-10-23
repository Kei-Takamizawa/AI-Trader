"""Immutable domain types shared by every package (no I/O)."""

from aitrader.domain.advisory import AdvisorySignal
from aitrader.domain.costs import FxConversion, TradeCost
from aitrader.domain.enums import (
    AdvisoryKind,
    AdvisorySource,
    BrokerEnvironment,
    FxDirection,
    InstrumentKind,
    Mode,
    OrderStatus,
    OrderType,
    RunPurpose,
    Side,
    SystemState,
    TimeInForce,
    TradeCostType,
    Verdict,
)
from aitrader.domain.environment import check_environment
from aitrader.domain.errors import (
    CurrencyMismatchError,
    DomainError,
    EnvironmentNotAllowedError,
)
from aitrader.domain.ids import make_client_order_id, make_intent_id
from aitrader.domain.market import Bar, Instrument, Quote
from aitrader.domain.money import Currency, Money
from aitrader.domain.orders import OrderIntent, RiskDecision, RuleResult

__all__ = [
    "AdvisoryKind",
    "AdvisorySignal",
    "AdvisorySource",
    "Bar",
    "BrokerEnvironment",
    "Currency",
    "CurrencyMismatchError",
    "DomainError",
    "EnvironmentNotAllowedError",
    "FxConversion",
    "FxDirection",
    "Instrument",
    "InstrumentKind",
    "Mode",
    "Money",
    "OrderIntent",
    "OrderStatus",
    "OrderType",
    "Quote",
    "RiskDecision",
    "RuleResult",
    "RunPurpose",
    "Side",
    "SystemState",
    "TimeInForce",
    "TradeCost",
    "TradeCostType",
    "Verdict",
    "check_environment",
    "make_client_order_id",
    "make_intent_id",
]
