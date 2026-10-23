"""Order life-cycle transitions (document 03 §5)."""

from aitrader.domain import OrderStatus

ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_SUBMIT: frozenset(
        {OrderStatus.SUBMITTED, OrderStatus.UNKNOWN, OrderStatus.REJECTED}
    ),
    OrderStatus.UNKNOWN: frozenset(
        {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.NOT_FOUND,
        }
    ),
    OrderStatus.SUBMITTED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.CANCELLED, OrderStatus.EXPIRED}
    ),
    OrderStatus.CANCEL_PENDING: frozenset(
        {OrderStatus.CANCELLED, OrderStatus.FILLED, OrderStatus.EXPIRED}
    ),
}
"""Allowed moves. Terminal states have none; a non-terminal state may stay where it is (for
example a partial fill while a cancel is pending)."""


class IllegalOrderTransitionError(Exception):
    pass


def check_transition(current: OrderStatus, target: OrderStatus) -> None:
    if current is target and not current.is_terminal:
        return
    if target not in ORDER_TRANSITIONS.get(current, frozenset()):
        raise IllegalOrderTransitionError(f"{current.value} -> {target.value} is not allowed")
