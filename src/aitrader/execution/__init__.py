"""Order manager, order state machine, order store and the order gate. Never imports AI."""

from aitrader.execution.gate import GateContext, GateRejectedError, GateResult, OrderGate
from aitrader.execution.manager import (
    MAX_ATTEMPTS,
    ExecutionEvent,
    ExecutionEventKind,
    OrderManager,
    OrderNotActiveError,
    RetryNotAllowedError,
    SubmitResult,
)
from aitrader.execution.monitor import OrderErrorMonitor
from aitrader.execution.states import (
    ORDER_TRANSITIONS,
    IllegalOrderTransitionError,
    check_transition,
)
from aitrader.execution.store import (
    DuplicateOrderError,
    InMemoryOrderStore,
    OrderEvent,
    OrderRecord,
    OrderStore,
)

__all__ = [
    "MAX_ATTEMPTS",
    "ORDER_TRANSITIONS",
    "DuplicateOrderError",
    "ExecutionEvent",
    "ExecutionEventKind",
    "GateContext",
    "GateRejectedError",
    "GateResult",
    "IllegalOrderTransitionError",
    "InMemoryOrderStore",
    "OrderErrorMonitor",
    "OrderEvent",
    "OrderGate",
    "OrderManager",
    "OrderNotActiveError",
    "OrderRecord",
    "OrderStore",
    "RetryNotAllowedError",
    "SubmitResult",
    "check_transition",
]
