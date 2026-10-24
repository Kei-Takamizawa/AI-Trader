"""Cumulative broker snapshots must become exactly one ledger execution per order."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from aitrader.broker.webull import SandboxCredentials, WebullBroker
from aitrader.domain import OrderStatus, Side
from aitrader.execution import ExecutionEventKind, OrderManager
from tests.contract.webull_fake import ENV, FakeTransport
from tests.unit.execution.builders import SPY, Harness, decision, harness, intent


def setup(wait_delays: list[int] | None = None) -> tuple[Harness, FakeTransport]:
    h = harness()
    fake = FakeTransport(h.clock)
    fake.fractional_accept = True

    def wait(seconds: int) -> None:
        if wait_delays is not None:
            wait_delays.append(seconds)
        h.clock.advance(timedelta(seconds=seconds))

    broker = WebullBroker(
        h.clock,
        settlement_date=lambda day: day + timedelta(days=1),
        credentials=SandboxCredentials(ENV),
        transport=fake,
        wait=wait,
    )
    h.manager = OrderManager(broker, h.gate, h.store, h.ledger, h.clock)
    return h, fake


def test_failed_after_partial_fill_is_booked_once_and_cancelled() -> None:
    h, fake = setup()
    submitted = h.buy()
    assert submitted.record is not None
    cid = submitted.record.client_order_id
    snapshot(fake, cid, "PARTIAL_FILLED", "0.1")
    assert h.manager.sync() == []
    assert h.ledger.entry(f"fill:{cid}:final") is None

    snapshot(fake, cid, "FAILED", "0.1", error_code="OPENAPI_ORDER_CANNOT_OPERATE")
    events = h.manager.sync()
    assert not any(event.kind is ExecutionEventKind.MISMATCH for event in events)
    assert sum(event.kind is ExecutionEventKind.FILL_BOOKED for event in events) == 1
    record = h.store.get(cid)
    assert record is not None
    assert record.status is OrderStatus.CANCELLED
    assert record.filled_quantity == Decimal("0.1")
    booked = h.ledger.entry(f"fill:{cid}:final")
    assert booked is not None
    assert booked.position_change is not None
    assert booked.position_change.quantity == Decimal("0.1")
    for _ in range(3):
        assert h.manager.sync() == []
    assert len([entry for entry in h.ledger.entries if entry.entry_id.startswith("fill:")]) == 1


def test_sync_paces_detail_queries_for_three_active_orders() -> None:
    waits: list[int] = []
    h, fake = setup(waits)
    ids = []
    for index in range(3):
        submitted = h.buy(intent_id=f"active-{index}")
        assert submitted.record is not None
        ids.append(submitted.record.client_order_id)
    h.clock.advance(timedelta(seconds=2))
    fake.advance = False
    fake.calls.clear()
    assert h.manager.sync() == []
    assert [params["client_order_id"] for op, params in fake.calls if op == "detail"] == ids
    assert waits == [2]
    assert len(h.store.active()) == 3


def snapshot(fake: FakeTransport, cid: str, status: str, quantity: str, **kw: Any) -> None:
    fake.orders[cid].update(
        status=status,
        filled_quantity=quantity,
        filled_price="100",
        filled_time_at=fake.clock.now().isoformat(),
        **kw,
    )


def test_manager_books_buy_then_sell_once_with_settlement() -> None:
    h, fake = setup()
    for side in (Side.BUY, Side.SELL):
        entry = intent(side, intent_id=f"entry-{side.value}", limit_price=Decimal("100"))
        result = h.manager.submit(entry, decision(entry), SPY, h.context())
        assert result.record is not None
        cid = result.record.client_order_id
        snapshot(fake, cid, "PARTIAL_FILLED", "0.1")
        partial_events = h.manager.sync()
        assert not any(e.kind is ExecutionEventKind.MISMATCH for e in partial_events)
        assert not any(e.kind is ExecutionEventKind.FILL_BOOKED for e in partial_events)
        assert h.store.get(cid) is not None
        assert h.ledger.entry(f"fill:{cid}:final") is None
        assert any(record.client_order_id == cid for record in h.store.active())

        # UTC midnight is still the preceding trade date in New York.
        h.clock.set(h.clock.now().replace(hour=0, minute=30) + timedelta(days=1))
        snapshot(fake, cid, "FILLED", "0.3")
        events = h.manager.sync()
        assert [e.kind for e in events] == [ExecutionEventKind.FILL_BOOKED]
        record = h.store.get(cid)
        assert record is not None
        assert record.status is OrderStatus.FILLED
        assert record.filled_quantity == Decimal("0.3")
        booked = h.ledger.entry(f"fill:{cid}:final")
        assert booked is not None
        assert booked.position_change is not None
        assert abs(booked.position_change.quantity) == Decimal("0.3")
        assert len([e for e in h.ledger.entries if e.entry_id == f"fill:{cid}:final"]) == 1
        assert h.manager.sync() == []
        if side is Side.SELL:
            assert len(h.ledger.pending_settlements) == 1
            assert h.ledger.pending_settlements[0].settles_on == h.clock.now().date()
            assert h.ledger.cash_usd_unsettled == Decimal("30")
        else:
            assert h.ledger.pending_settlements == ()
    assert h.ledger.position("SPY") is None


def test_cancelled_partial_older_than_cursor_is_booked_once() -> None:
    h, fake = setup()
    first = h.buy(intent_id="older")
    second = h.buy(intent_id="newer")
    assert first.record is not None
    assert second.record is not None
    older, newer = first.record.client_order_id, second.record.client_order_id
    snapshot(fake, older, "PARTIAL_FILLED", "0.1")
    old_execution_time = fake.orders[older]["filled_time_at"]
    h.clock.advance(timedelta(days=1))
    snapshot(fake, newer, "FILLED", "0.3")
    events = h.manager.sync()
    assert not any(e.kind is ExecutionEventKind.MISMATCH for e in events), events
    assert h.ledger.entry(f"fill:{older}:final") is None
    assert h.ledger.entry(f"fill:{newer}:final") is not None

    fake.orders[older]["status"] = "CANCELLED"
    assert fake.orders[older]["filled_time_at"] == old_execution_time
    events = h.manager.sync()
    assert not any(e.kind is ExecutionEventKind.MISMATCH for e in events), events
    assert [e.client_order_id for e in events if e.kind is ExecutionEventKind.FILL_BOOKED] == [
        older
    ]
    record = h.store.get(older)
    assert record is not None
    assert record.status is OrderStatus.CANCELLED
    assert record.filled_quantity == Decimal("0.1")
    assert h.manager.sync() == []
    assert h.ledger.position("SPY") is not None
    assert h.ledger.positions["SPY"].quantity == Decimal("0.4")
    assert len([e for e in h.ledger.entries if e.entry_id.startswith("fill:")]) == 2
    history = [params for op, params in fake.calls if op == "history"]
    assert history[-1]["start_time"].startswith("2026-10-16")


@pytest.mark.parametrize("cancel_pending", [False, True])
def test_terminal_status_before_history_fill_waits_for_next_sync(cancel_pending: bool) -> None:
    h, fake = setup()
    submitted = h.buy()
    assert submitted.record is not None
    cid = submitted.record.client_order_id
    snapshot(fake, cid, "PARTIAL_FILLED", "0.1")
    assert h.manager.sync() == []
    if cancel_pending:
        fake.cancel_confirm = False
        events = h.manager.cancel(cid)
        assert not any(e.kind is ExecutionEventKind.MISMATCH for e in events)
    # History sees the working snapshot; detail in this same sync sees the terminal one.
    fake.queue("history", {"data": [{"orders": [dict(fake.orders[cid])]}]})
    final_quantity = "0.1" if cancel_pending else "0.3"
    snapshot(fake, cid, "CANCELLED" if cancel_pending else "FILLED", final_quantity)
    events = h.manager.sync()
    assert not any(e.kind is ExecutionEventKind.MISMATCH for e in events)
    assert not any(e.kind is ExecutionEventKind.FILL_BOOKED for e in events)
    record = h.store.get(cid)
    assert record is not None
    assert record.status is (
        OrderStatus.CANCEL_PENDING if cancel_pending else OrderStatus.SUBMITTED
    )
    assert record.filled_quantity == 0
    assert h.ledger.entry(f"fill:{cid}:final") is None

    events = h.manager.sync()
    assert not any(e.kind is ExecutionEventKind.MISMATCH for e in events)
    assert sum(e.kind is ExecutionEventKind.FILL_BOOKED for e in events) == 1
    record = h.store.get(cid)
    assert record is not None
    assert record.status is (OrderStatus.CANCELLED if cancel_pending else OrderStatus.FILLED)
    assert record.filled_quantity == Decimal(final_quantity)
    assert len([e for e in h.ledger.entries if e.entry_id.startswith("fill:")]) == 1
    assert h.manager.sync() == []
