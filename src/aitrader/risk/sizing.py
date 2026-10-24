"""Position sizing with fees included (document 05, section 2)."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from aitrader.config import RiskLimits
from aitrader.fees import ExecutionAssumptions, FeeSchedule, round_trip_cost

HUNDRED = Decimal(100)


def round_down(quantity: Decimal, increment: Decimal) -> Decimal:
    """Largest multiple of ``increment`` that is ≤ ``quantity`` (never below zero)."""
    if quantity <= 0:
        return Decimal(0)
    units = (quantity / increment).to_integral_value(rounding=ROUND_FLOOR)
    return (units * increment).quantize(increment)


def max_quantity_within(
    total_cost: Callable[[Decimal], Decimal], budget: Decimal, upper: Decimal, increment: Decimal
) -> Decimal:
    """Largest multiple of ``increment`` in ``[0, upper]`` with ``total_cost(q) <= budget``.

    ``total_cost`` must be non-decreasing in ``q``; a binary search over increments is used.
    """
    if budget <= 0 or upper <= 0:
        return Decimal(0)
    low, high = 0, int((upper / increment).to_integral_value(rounding=ROUND_FLOOR))
    while low < high:
        mid = (low + high + 1) // 2
        if total_cost(mid * increment) <= budget:
            low = mid
        else:
            high = mid - 1
    return (low * increment).quantize(increment)


@dataclass(frozen=True)
class RiskBudget:
    sizing_equity_usd: Decimal
    risk_fraction: Decimal
    budget_usd: Decimal
    loss_per_unit_usd: Decimal


def risk_budget(
    *,
    limits: RiskLimits,
    assumptions: ExecutionAssumptions,
    equity_usd: Decimal,
    fx_jpy_per_usd: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    soft_monthly_level_reached: bool,
    soft_monthly_risk_factor: Decimal,
) -> RiskBudget:
    """Risk budget for one trade and the loss per share if the stop is hit (with gap allowance).

    Sizing equity is capped at the approved capital (R-LIVE-3), whatever the account holds.
    """
    approved_usd = limits.approved_capital_jpy / fx_jpy_per_usd
    sizing_equity = min(equity_usd, approved_usd)
    fraction = limits.risk_per_trade_pct / HUNDRED
    if soft_monthly_level_reached:
        fraction *= soft_monthly_risk_factor
    loss_per_unit = (entry_price - stop_price) + entry_price * (
        assumptions.stop_slippage_allowance_pct / HUNDRED
    )
    return RiskBudget(
        sizing_equity_usd=sizing_equity,
        risk_fraction=fraction,
        budget_usd=sizing_equity * fraction,
        loss_per_unit_usd=loss_per_unit,
    )


def risk_limited_quantity(
    *,
    budget: RiskBudget,
    schedule: FeeSchedule,
    assumptions: ExecutionAssumptions,
    entry_price: Decimal,
    stop_price: Decimal,
    increment: Decimal,
    fx_jpy_per_usd: Decimal,
) -> Decimal:
    """Largest quantity whose loss at the stop plus round-trip costs fits the risk budget."""
    if budget.loss_per_unit_usd <= 0:
        return Decimal(0)

    def total(q: Decimal) -> Decimal:
        return q * budget.loss_per_unit_usd + round_trip_cost(
            schedule,
            assumptions,
            q,
            entry_price,
            stop_price,
            fx_jpy_per_usd=fx_jpy_per_usd,
            conservative=True,
        )

    upper = budget.budget_usd / budget.loss_per_unit_usd
    return max_quantity_within(total, budget.budget_usd, upper, increment)
