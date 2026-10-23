"""Fee schedule and round-trip cost model (TASK-002, document 14 §5)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aitrader.domain import Side, TradeCostType
from aitrader.fees import (
    WEBULL_JP,
    ExecutionAssumptions,
    FeeSchedule,
    order_fees,
    round_trip_cost,
    round_up_to_cent,
    total_order_fees,
)
from aitrader.fees.calc import _finra_fee, _finra_fee_upper_bound

D = Decimal
NO_SLIPPAGE = ExecutionAssumptions(slippage_bps_per_side=D(0))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D(0), D(0)),
        (D("-1"), D(0)),
        (D("0.001"), D("0.01")),
        (D("0.01"), D("0.01")),
        (D("0.0100001"), D("0.02")),
        (D("1.234"), D("1.24")),
    ],
)
def test_round_up_to_cent(value: Decimal, expected: Decimal) -> None:
    assert round_up_to_cent(value) == expected


def test_webull_buy_pays_only_the_cat_fee() -> None:
    assert order_fees(WEBULL_JP, Side.BUY, D(10), D(100)) == [
        (TradeCostType.CAT_FEE, D("0.000030")),
    ]


def test_webull_sell_pays_cat_finra_and_sec_fees() -> None:
    # FINRA: 10 * 0.000195 = 0.00195 -> 0.01 (minimum). SEC: 1000 * 0.0000206 = 0.0206 -> 0.03.
    assert order_fees(WEBULL_JP, Side.SELL, D(10), D(100)) == [
        (TradeCostType.CAT_FEE, D("0.000030")),
        (TradeCostType.FINRA_FEE, D("0.01")),
        (TradeCostType.SEC_FEE, D("0.03")),
    ]


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        (D("0.5"), D("0.01")),  # fractional part only
        (D(10), D("0.01")),  # whole part only
        (D("10.5"), D("0.02")),  # whole and fractional parts are charged separately
        (D(1000), D("0.20")),  # 0.195 rounded up to the cent
        (D(100_000), D("9.79")),  # maximum per trade
        (D("100000.5"), D("9.80")),  # maximum for the whole part plus the fractional minimum
    ],
)
def test_webull_finra_fee(quantity: Decimal, expected: Decimal) -> None:
    assert _finra_fee(WEBULL_JP, quantity) == expected


def test_finra_fee_without_split_is_charged_once() -> None:
    schedule = WEBULL_JP.model_copy(update={"finra_fee_split_fractional": False})
    assert _finra_fee(schedule, D("10.5")) == D("0.01")
    assert _finra_fee_upper_bound(schedule, D("10.5")) == D("0.01")


def test_zero_finra_rate_means_no_finra_fee() -> None:
    schedule = FeeSchedule(name="none")
    assert _finra_fee(schedule, D(10)) == 0
    assert order_fees(schedule, Side.SELL, D(10), D(100)) == []


def test_finra_fee_without_maximum() -> None:
    schedule = WEBULL_JP.model_copy(update={"finra_fee_max_usd": None})
    assert _finra_fee(schedule, D(100_000)) == D("19.50")


@pytest.mark.parametrize(
    ("notional_price", "expected"),
    [
        (D(37), D(1)),  # 0.04884 -> minimum 1
        (D(1000), D("1.32")),
        (D(10_000), D(5)),  # 13.2 -> maximum 5
    ],
)
def test_percentage_commission_with_minimum_and_maximum(
    notional_price: Decimal, expected: Decimal
) -> None:
    schedule = FeeSchedule(
        name="pct",
        commission_rate=D("0.00132"),
        commission_min_usd=D(1),
        commission_max_usd=D(5),
    )
    assert order_fees(schedule, Side.BUY, D(1), notional_price) == [
        (TradeCostType.COMMISSION, expected)
    ]


def test_commission_without_maximum() -> None:
    schedule = FeeSchedule(name="pct", commission_rate=D("0.001"))
    assert total_order_fees(schedule, Side.BUY, D(1), D(100_000)) == D(100)


def test_total_order_fees_sums_the_items() -> None:
    assert total_order_fees(WEBULL_JP, Side.SELL, D(10), D(100)) == D("0.040030")
    assert total_order_fees(WEBULL_JP, Side.SELL, D(0), D(100)) == 0


@pytest.mark.parametrize(("quantity", "price"), [(D(-1), D(100)), (D(1), D(0))])
def test_order_fees_reject_invalid_input(quantity: Decimal, price: Decimal) -> None:
    with pytest.raises(ValueError, match="must"):
        order_fees(WEBULL_JP, Side.BUY, quantity, price)


def test_round_trip_cost_webull_with_usd_held() -> None:
    # buy: CAT 0.00003; sell at 96: CAT 0.00003 + FINRA 0.01 + SEC 0.0198 -> 0.02;
    # slippage: (1000 + 960) * 5 / 10000 = 0.98.
    cost = round_trip_cost(WEBULL_JP, ExecutionAssumptions(), D(10), D(100), D(96))
    assert cost == D("0.00003") + D("0.00003") + D("0.01") + D("0.02") + D("0.98")


def test_round_trip_cost_includes_fx_when_usd_is_not_held() -> None:
    assumptions = ExecutionAssumptions(slippage_bps_per_side=D(0), usd_held=False)
    cost = round_trip_cost(WEBULL_JP, assumptions, D(1), D(100), D(100), fx_jpy_per_usd=D(150))
    fees = D("0.000003") * 2 + D("0.01") + D("0.01")
    # FX: 200 USD converted at JPY 0.15 per USD = JPY 30 = USD 0.2
    assert cost == fees + D("0.2")


@pytest.mark.parametrize("fx", [None, D(0)])
def test_round_trip_cost_needs_fx_rate_when_usd_is_not_held(fx: Decimal | None) -> None:
    assumptions = ExecutionAssumptions(usd_held=False)
    with pytest.raises(ValueError, match="FX rate"):
        round_trip_cost(WEBULL_JP, assumptions, D(1), D(100), D(100), fx_jpy_per_usd=fx)


def test_round_trip_cost_of_nothing_is_zero() -> None:
    assert round_trip_cost(WEBULL_JP, ExecutionAssumptions(), D(0), D(100), D(96)) == 0


@pytest.mark.parametrize(
    ("quantity", "entry", "exit_"),
    [(D(-1), D(100), D(96)), (D(1), D(0), D(96)), (D(1), D(100), D(0))],
)
def test_round_trip_cost_rejects_invalid_input(
    quantity: Decimal, entry: Decimal, exit_: Decimal
) -> None:
    with pytest.raises(ValueError, match="must"):
        round_trip_cost(WEBULL_JP, ExecutionAssumptions(), quantity, entry, exit_)


quantities = st.decimals(
    min_value=D("0.00001"), max_value=D(200_000), places=5, allow_nan=False, allow_infinity=False
)
prices = st.decimals(
    min_value=D("0.5"), max_value=D(2_000), places=2, allow_nan=False, allow_infinity=False
)


@given(quantity=quantities, entry=prices, exit_=prices)
def test_conservative_cost_is_never_below_the_exact_cost(
    quantity: Decimal, entry: Decimal, exit_: Decimal
) -> None:
    exact = round_trip_cost(WEBULL_JP, NO_SLIPPAGE, quantity, entry, exit_)
    bound = round_trip_cost(WEBULL_JP, NO_SLIPPAGE, quantity, entry, exit_, conservative=True)
    assert bound >= exact


@given(a=quantities, b=quantities, entry=prices, exit_=prices)
def test_conservative_cost_never_decreases_with_quantity(
    a: Decimal, b: Decimal, entry: Decimal, exit_: Decimal
) -> None:
    low, high = sorted((a, b))
    cost_low = round_trip_cost(WEBULL_JP, NO_SLIPPAGE, low, entry, exit_, conservative=True)
    cost_high = round_trip_cost(WEBULL_JP, NO_SLIPPAGE, high, entry, exit_, conservative=True)
    assert cost_low <= cost_high


def test_exact_finra_fee_is_not_monotonic_which_is_why_sizing_uses_the_bound() -> None:
    # 1.00001 shares pay two minimums (whole and fractional part); 2 shares pay one.
    assert _finra_fee(WEBULL_JP, D("1.00001")) > _finra_fee(WEBULL_JP, D(2))
    assert _finra_fee_upper_bound(WEBULL_JP, D("1.00001")) <= _finra_fee_upper_bound(
        WEBULL_JP, D(2)
    )


def test_webull_schedule_matches_the_official_pricing_page() -> None:
    assert WEBULL_JP.commission_rate == 0
    assert WEBULL_JP.cat_fee_per_share_usd == D("0.000003")
    assert WEBULL_JP.finra_fee_per_share_sold_usd == D("0.000195")
    assert (WEBULL_JP.finra_fee_min_usd, WEBULL_JP.finra_fee_max_usd) == (D("0.01"), D("9.79"))
    assert WEBULL_JP.sec_fee_rate == D("0.0000206")
    assert WEBULL_JP.fx_spread_jpy_per_usd == D("0.15")
    assert WEBULL_JP.min_fractional_buy_usd == 1
    assert WEBULL_JP.fractional_quantity_step == D("0.00001")
