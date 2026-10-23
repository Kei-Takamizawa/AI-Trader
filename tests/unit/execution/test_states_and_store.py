"""Order state machine and order store."""

from datetime import timedelta
from itertools import pairwise, product

import pytest

from aitrader.domain import IntentPurpose, OrderRequest, OrderStatus, OrderType, Side
from aitrader.execution import (
    ORDER_TRANSITIONS,
    DuplicateOrderError,
    IllegalOrderTransitionError,
    InMemoryOrderStore,
    OrderRecord,
    check_transition,
)
from tests.unit.execution.builders import NOW, D

ALL = list(OrderStatus)


@pytest.mark.parametrize(("current", "target"), list(product(ALL, ALL)))
def test_every_transition_is_allowed_or_refused_as_specified(
    current: OrderStatus, target: OrderStatus
) -> None:
    allowed = target in ORDER_TRANSITIONS.get(current, frozenset()) or (
        current is target and not current.is_terminal
    )
    if allowed:
        check_transition(current, target)
    else:
        with pytest.raises(IllegalOrderTransitionError):
            check_transition(current, target)


def test_terminal_states_have_no_way_out() -> None:
    for status in ALL:
        if status.is_terminal:
            assert status not in ORDER_TRANSITIONS


def test_document_03_paths_exist() -> None:
    paths = [
        [
            OrderStatus.PENDING_SUBMIT,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        ],
        [
            OrderStatus.PENDING_SUBMIT,
            OrderStatus.UNKNOWN,
            OrderStatus.SUBMITTED,
            OrderStatus.FILLED,
        ],
        [OrderStatus.PENDING_SUBMIT, OrderStatus.UNKNOWN, OrderStatus.NOT_FOUND],
        [OrderStatus.SUBMITTED, OrderStatus.CANCEL_PENDING, OrderStatus.CANCELLED],
        [OrderStatus.SUBMITTED, OrderStatus.CANCEL_PENDING, OrderStatus.FILLED],
        [OrderStatus.SUBMITTED, OrderStatus.REJECTED],
        [OrderStatus.SUBMITTED, OrderStatus.EXPIRED],
    ]
    for path in paths:
        for current, target in pairwise(path):
            check_transition(current, target)


def request(cid: str = "AAA-1", intent_id: str = "intent-1") -> OrderRequest:
    return OrderRequest(
        client_order_id=cid,
        intent_id=intent_id,
        symbol="SPY",
        side=Side.BUY,
        purpose=IntentPurpose.ENTRY,
        order_type=OrderType.LIMIT,
        quantity=D("0.3"),
        limit_price=D(100),
    )


def record(cid: str = "AAA-1", intent_id: str = "intent-1", attempt: int = 1) -> OrderRecord:
    return OrderRecord(
        request=request(cid, intent_id),
        attempt=attempt,
        status=OrderStatus.PENDING_SUBMIT,
        created_at=NOW,
        updated_at=NOW,
    )


def test_store_keeps_records_and_an_event_log() -> None:
    store = InMemoryOrderStore()
    first = record()
    store.insert(first, "WRITE_AHEAD")
    submitted = first.moved_to(
        OrderStatus.SUBMITTED, NOW + timedelta(seconds=1), broker_order_id="B1"
    )
    store.update(submitted, "ACK")
    assert store.get("AAA-1") == submitted
    assert store.get("missing") is None
    assert [e.event for e in store.events("AAA-1")] == ["WRITE_AHEAD", "ACK"]
    assert store.events()[1].from_status is OrderStatus.PENDING_SUBMIT
    assert store.active() == [submitted]
    assert submitted.client_order_id == "AAA-1"
    assert submitted.intent_id == "intent-1"
    assert submitted.remaining == D("0.3")


def test_one_active_order_per_intent() -> None:
    store = InMemoryOrderStore()
    store.insert(record(), "WRITE_AHEAD")
    with pytest.raises(DuplicateOrderError, match="active order"):
        store.insert(record("AAA-2", attempt=2), "WRITE_AHEAD")
    with pytest.raises(DuplicateOrderError, match="already exists"):
        store.insert(record(), "WRITE_AHEAD")


def test_a_new_attempt_is_possible_once_the_first_is_closed() -> None:
    store = InMemoryOrderStore()
    first = record()
    store.insert(first, "WRITE_AHEAD")
    store.update(first.moved_to(OrderStatus.UNKNOWN, NOW), "TIMEOUT")
    store.update(store.get("AAA-1").moved_to(OrderStatus.NOT_FOUND, NOW), "NOT_FOUND")  # type: ignore[union-attr]
    store.insert(record("AAA-2", attempt=2), "WRITE_AHEAD")
    assert [r.attempt for r in store.for_intent("intent-1")] == [1, 2]


def test_store_refuses_illegal_updates() -> None:
    store = InMemoryOrderStore()
    first = record()
    store.insert(first, "WRITE_AHEAD")
    illegal = first.model_copy(update={"status": OrderStatus.FILLED})
    with pytest.raises(IllegalOrderTransitionError):
        store.update(illegal, "BAD")
    with pytest.raises(KeyError):
        store.update(record("BBB-1", "other"), "BAD")
    with pytest.raises(IllegalOrderTransitionError):
        first.moved_to(OrderStatus.CANCELLED, NOW)
