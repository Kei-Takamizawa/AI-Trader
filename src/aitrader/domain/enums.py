"""Enumerations shared across the system."""

from enum import StrEnum


class Mode(StrEnum):
    """Operating environment (document 04)."""

    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class BrokerEnvironment(StrEnum):
    """Where orders go (document 04, section 3)."""

    SIM = "sim"
    WEBULL_SANDBOX = "webull_sandbox"
    WEBULL_LIVE = "webull_live"


class RunPurpose(StrEnum):
    """Why a process runs: normal trading or broker contract tests against a test endpoint."""

    TRADING = "trading"
    BROKER_CONTRACT_TEST = "broker_contract_test"


class Side(StrEnum):
    """Order side. There is no short side: a SELL only reduces an existing long position."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    DAY = "DAY"
    GTC = "GTC"


class OrderStatus(StrEnum):
    """Order life cycle states (document 03, section 5)."""

    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    UNKNOWN = "UNKNOWN"
    NOT_FOUND = "NOT_FOUND"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {
        OrderStatus.NOT_FOUND,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
)


class SystemState(StrEnum):
    """Engine state (document 05, section 7)."""

    SAFE = "SAFE"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"
    KILLED = "KILLED"


class InstrumentKind(StrEnum):
    ETF = "ETF"
    COMMON_STOCK = "COMMON_STOCK"


class Verdict(StrEnum):
    APPROVE = "APPROVE"
    REDUCE = "REDUCE"
    REJECT = "REJECT"


class AdvisoryKind(StrEnum):
    MARKET_REGIME = "MARKET_REGIME"
    SYMBOL_EVENT = "SYMBOL_EVENT"


class AdvisorySource(StrEnum):
    SCHEDULED_TASK = "scheduled_task"
    API = "api"
    REPLAY = "replay"


class TradeCostType(StrEnum):
    COMMISSION = "COMMISSION"
    CAT_FEE = "CAT_FEE"
    FINRA_FEE = "FINRA_FEE"
    SEC_FEE = "SEC_FEE"
    ADR_FEE = "ADR_FEE"
    OTHER = "OTHER"


class FxDirection(StrEnum):
    JPY_TO_USD = "JPY_TO_USD"
    USD_TO_JPY = "USD_TO_JPY"
