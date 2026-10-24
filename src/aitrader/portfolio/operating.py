"""Operating costs (AI, server, data, other), kept apart from the trading ledger.

They are paid outside the brokerage account, so they never touch trading cash. Reports show
three lines: trading P&L, operating costs and net (document 08 §4).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from aitrader.domain import Currency, OperatingCost, OperatingCostCategory
from aitrader.portfolio.ledger import DuplicateEntryError, Ledger


class OperatingCostBook:
    def __init__(self) -> None:
        self._costs: dict[str, OperatingCost] = {}

    def add(self, cost: OperatingCost) -> OperatingCost:
        """Record a cost. The same cost again is a no-op; a different one under its ID is an
        error."""
        existing = self._costs.get(cost.cost_id)
        if existing is not None:
            if existing == cost:
                return existing
            raise DuplicateEntryError(f"operating cost {cost.cost_id!r} is already recorded")
        self._costs[cost.cost_id] = cost
        return cost

    @property
    def costs(self) -> tuple[OperatingCost, ...]:
        return tuple(sorted(self._costs.values(), key=lambda c: (c.ts, c.cost_id)))

    def totals_jpy(
        self,
        fx_jpy_per_usd: Decimal,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[OperatingCostCategory, Decimal]:
        """Totals per category in JPY for ``start <= ts < end``. USD costs use the given rate."""
        if fx_jpy_per_usd <= 0:
            raise ValueError("the FX rate must be positive")
        totals = {category: Decimal(0) for category in OperatingCostCategory}
        for cost in self._costs.values():
            if (start is not None and cost.ts < start) or (end is not None and cost.ts >= end):
                continue
            amount = cost.amount.amount
            if cost.amount.currency is Currency.USD:
                amount *= fx_jpy_per_usd
            totals[cost.category] += amount
        return totals


@dataclass(frozen=True)
class PnlReport:
    fx_jpy_per_usd: Decimal
    trading_pnl_jpy: Decimal
    operating_costs_jpy: Mapping[OperatingCostCategory, Decimal]

    @property
    def operating_costs_total_jpy(self) -> Decimal:
        return sum(self.operating_costs_jpy.values(), Decimal(0))

    @property
    def net_jpy(self) -> Decimal:
        return self.trading_pnl_jpy - self.operating_costs_total_jpy


def pnl_report(
    ledger: Ledger,
    book: OperatingCostBook,
    prices: Mapping[str, Decimal],
    fx_jpy_per_usd: Decimal,
) -> PnlReport:
    """Cumulative trading P&L, operating costs and net result, in JPY."""
    return PnlReport(
        fx_jpy_per_usd=fx_jpy_per_usd,
        trading_pnl_jpy=ledger.trading_pnl(prices, fx_jpy_per_usd).trading_pnl_jpy,
        operating_costs_jpy=book.totals_jpy(fx_jpy_per_usd),
    )
