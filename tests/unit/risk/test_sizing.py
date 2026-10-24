"""Sizing helpers (document 05 §2)."""

from decimal import Decimal

import pytest

from aitrader.config import RiskLimits
from aitrader.fees import WEBULL_JP, ExecutionAssumptions
from aitrader.risk import RiskBudget, risk_budget, risk_limited_quantity, round_down

D = Decimal


@pytest.mark.parametrize(
    ("quantity", "increment", "expected"),
    [
        (D("0.123456"), D("0.00001"), D("0.12345")),
        (D("2.9"), D(1), D(2)),
        (D(3), D(1), D(3)),
        (D(0), D(1), D(0)),
        (D(-1), D("0.00001"), D(0)),
    ],
)
def test_round_down(quantity: Decimal, increment: Decimal, expected: Decimal) -> None:
    result = round_down(quantity, increment)
    assert result == expected
    assert result.as_tuple().exponent == increment.as_tuple().exponent or result == 0


def test_risk_budget_worked_example() -> None:
    budget = risk_budget(
        limits=RiskLimits(),
        assumptions=ExecutionAssumptions(),
        equity_usd=D(1000),
        fx_jpy_per_usd=D(155),
        entry_price=D(100),
        stop_price=D(96),
        soft_monthly_level_reached=False,
        soft_monthly_risk_factor=D("0.5"),
    )
    assert budget.sizing_equity_usd == D(50_000) / D(155)  # capped at the approved capital
    assert budget.risk_fraction == D("0.005")
    assert budget.loss_per_unit_usd == D("4.5")  # 4.00 to the stop plus a 0.5% gap allowance


def test_no_quantity_without_a_loss_per_unit() -> None:
    budget = RiskBudget(
        sizing_equity_usd=D(300),
        risk_fraction=D("0.005"),
        budget_usd=D("1.5"),
        loss_per_unit_usd=D(0),
    )
    quantity = risk_limited_quantity(
        budget=budget,
        schedule=WEBULL_JP,
        assumptions=ExecutionAssumptions(),
        entry_price=D(100),
        stop_price=D(100),
        increment=D("0.00001"),
        fx_jpy_per_usd=D(155),
    )
    assert quantity == 0
