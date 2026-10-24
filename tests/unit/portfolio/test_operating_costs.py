"""Operating costs are kept apart from trading P&L (document 08 §4)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from aitrader.domain import Currency, Money, OperatingCost, OperatingCostCategory
from aitrader.portfolio import DuplicateEntryError, Ledger, OperatingCostBook, pnl_report

D = Decimal
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def op_cost(
    cost_id: str, category: OperatingCostCategory, amount: Money, **kw: Any
) -> OperatingCost:
    fields: dict[str, Any] = {
        "cost_id": cost_id,
        "ts": T0,
        "category": category,
        "provider": "provider",
        "amount": amount,
    }
    fields.update(kw)
    return OperatingCost.model_validate(fields)


def test_totals_by_category_in_jpy() -> None:
    book = OperatingCostBook()
    book.add(op_cost("gcp-10", OperatingCostCategory.SERVER, Money.of(780, Currency.JPY)))
    book.add(op_cost("ai-run-1", OperatingCostCategory.AI, Money.of(0, Currency.JPY), ref="run 1"))
    book.add(op_cost("data-10", OperatingCostCategory.DATA, Money.of(2, Currency.USD)))
    totals = book.totals_jpy(D(150))
    assert totals == {
        OperatingCostCategory.AI: D(0),
        OperatingCostCategory.SERVER: D(780),
        OperatingCostCategory.DATA: D(300),
        OperatingCostCategory.OTHER: D(0),
    }
    assert [c.cost_id for c in book.costs] == ["ai-run-1", "data-10", "gcp-10"]


def test_totals_for_a_period() -> None:
    book = OperatingCostBook()
    book.add(
        op_cost(
            "sep",
            OperatingCostCategory.SERVER,
            Money.of(700, Currency.JPY),
            ts=T0 - timedelta(days=1),
        )
    )
    book.add(op_cost("oct", OperatingCostCategory.SERVER, Money.of(780, Currency.JPY)))
    book.add(
        op_cost(
            "nov",
            OperatingCostCategory.SERVER,
            Money.of(800, Currency.JPY),
            ts=T0 + timedelta(days=31),
        )
    )
    totals = book.totals_jpy(D(150), start=T0, end=T0 + timedelta(days=31))
    assert totals[OperatingCostCategory.SERVER] == D(780)


def test_costs_are_recorded_once() -> None:
    book = OperatingCostBook()
    item = op_cost("gcp-10", OperatingCostCategory.SERVER, Money.of(780, Currency.JPY))
    assert book.add(item) is item
    assert book.add(item) is item
    assert len(book.costs) == 1
    with pytest.raises(DuplicateEntryError):
        book.add(item.model_copy(update={"provider": "other"}))


def test_negative_operating_costs_are_refused() -> None:
    with pytest.raises(ValidationError, match="must not be negative"):
        op_cost("x", OperatingCostCategory.OTHER, Money.of(-1, Currency.JPY))


def test_totals_need_a_positive_fx_rate() -> None:
    with pytest.raises(ValueError, match="FX rate"):
        OperatingCostBook().totals_jpy(D(0))


def test_report_shows_trading_pnl_operating_costs_and_net_separately() -> None:
    ledger = Ledger()
    ledger.deposit("dep", T0, Money.of(100, Currency.USD))
    ledger.record_dividend("div", T0, "SPY", gross=Money.of(2, Currency.USD))
    book = OperatingCostBook()
    book.add(op_cost("gcp-10", OperatingCostCategory.SERVER, Money.of(780, Currency.JPY)))
    book.add(op_cost("data-10", OperatingCostCategory.DATA, Money.of(1, Currency.USD)))
    report = pnl_report(ledger, book, {}, D(150))
    assert report.trading_pnl_jpy == D(300)  # USD 2 dividend
    assert report.operating_costs_total_jpy == D(930)
    assert report.net_jpy == D(-630)
    # operating costs never touch trading cash
    assert ledger.cash_usd_settled == D(102)
