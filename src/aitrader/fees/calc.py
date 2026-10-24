"""Fee and round-trip cost calculations. All amounts are USD Decimals."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from aitrader.domain.enums import Side, TradeCostType
from aitrader.fees.schedule import ExecutionAssumptions, FeeSchedule

CENT = Decimal("0.01")
BPS = Decimal(10_000)


def round_up_to_cent(value: Decimal) -> Decimal:
    if value <= 0:
        return Decimal(0)
    return value.quantize(CENT, rounding=ROUND_CEILING)


def _finra_part(schedule: FeeSchedule, shares: Decimal) -> Decimal:
    if shares <= 0 or schedule.finra_fee_per_share_sold_usd == 0:
        return Decimal(0)
    fee = max(
        round_up_to_cent(shares * schedule.finra_fee_per_share_sold_usd),
        schedule.finra_fee_min_usd,
    )
    if schedule.finra_fee_max_usd is not None:
        fee = min(fee, schedule.finra_fee_max_usd)
    return fee


def _finra_fee(schedule: FeeSchedule, quantity: Decimal) -> Decimal:
    if not schedule.finra_fee_split_fractional:
        return _finra_part(schedule, quantity)
    whole = quantity.to_integral_value(rounding=ROUND_FLOOR)
    return _finra_part(schedule, whole) + _finra_part(schedule, quantity - whole)


def _finra_fee_upper_bound(schedule: FeeSchedule, quantity: Decimal) -> Decimal:
    """Never below :func:`_finra_fee` and non-decreasing in ``quantity`` (used for sizing)."""
    fee = _finra_part(schedule, quantity)
    if schedule.finra_fee_split_fractional and quantity >= 1:
        fee += schedule.finra_fee_min_usd
    return fee


def _commission(schedule: FeeSchedule, notional: Decimal) -> Decimal:
    if schedule.commission_rate == 0 or notional <= 0:
        return Decimal(0)
    fee = max(notional * schedule.commission_rate, schedule.commission_min_usd)
    if schedule.commission_max_usd is not None:
        fee = min(fee, schedule.commission_max_usd)
    return fee


def order_fees(
    schedule: FeeSchedule, side: Side, quantity: Decimal, price: Decimal
) -> list[tuple[TradeCostType, Decimal]]:
    """Fees for one fully filled order, one item per fee type (zero items are omitted)."""
    if quantity < 0 or price <= 0:
        raise ValueError("quantity must not be negative and price must be positive")
    notional = quantity * price
    items: list[tuple[TradeCostType, Decimal]] = [
        (TradeCostType.COMMISSION, _commission(schedule, notional)),
        (TradeCostType.CAT_FEE, quantity * schedule.cat_fee_per_share_usd),
    ]
    if side is Side.SELL:
        items.append((TradeCostType.FINRA_FEE, _finra_fee(schedule, quantity)))
        items.append((TradeCostType.SEC_FEE, round_up_to_cent(notional * schedule.sec_fee_rate)))
    return [(kind, amount) for kind, amount in items if amount > 0]


def total_order_fees(
    schedule: FeeSchedule, side: Side, quantity: Decimal, price: Decimal
) -> Decimal:
    return sum((amount for _, amount in order_fees(schedule, side, quantity, price)), Decimal(0))


def round_trip_cost(
    schedule: FeeSchedule,
    assumptions: ExecutionAssumptions,
    quantity: Decimal,
    entry_price: Decimal,
    exit_price: Decimal,
    *,
    fx_jpy_per_usd: Decimal | None = None,
    conservative: bool = False,
) -> Decimal:
    """Expected cost of buying ``quantity`` at ``entry_price`` and selling at ``exit_price``.

    Includes broker and regulatory fees, spread and slippage on both sides and, when USD is not
    held, the FX spread on both conversions. ``conservative=True`` returns an upper bound that
    never decreases when ``quantity`` grows, which the sizing search relies on.
    """
    if quantity < 0 or entry_price <= 0 or exit_price <= 0:
        raise ValueError("quantity must not be negative and prices must be positive")
    if quantity == 0:
        return Decimal(0)
    buy = total_order_fees(schedule, Side.BUY, quantity, entry_price)
    sell_notional = quantity * exit_price
    sell = (
        _commission(schedule, sell_notional)
        + quantity * schedule.cat_fee_per_share_usd
        + (
            _finra_fee_upper_bound(schedule, quantity)
            if conservative
            else _finra_fee(schedule, quantity)
        )
        + round_up_to_cent(sell_notional * schedule.sec_fee_rate)
    )
    gross_notional = quantity * entry_price + sell_notional
    slippage = gross_notional * assumptions.slippage_bps_per_side / BPS
    fx = Decimal(0)
    if not assumptions.usd_held:
        if fx_jpy_per_usd is None or fx_jpy_per_usd <= 0:
            raise ValueError("an FX rate is needed when USD is not held")
        fx = gross_notional * schedule.fx_spread_jpy_per_usd / fx_jpy_per_usd
    return buy + sell + slippage + fx
