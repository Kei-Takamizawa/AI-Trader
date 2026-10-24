"""Performance, trade and cost metrics (document 12 §6), in exact decimals.

Values are formatted as fixed-point strings, so the same inputs always give byte-identical
reports. The Sharpe and Sortino ratios use a 0% risk-free rate until a T-bill series is
available; trade P&L is the realised gain of a sale minus that sale's fees.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from itertools import pairwise

from aitrader.domain import Bar, Currency, Side
from aitrader.fees import ExecutionAssumptions, FeeSchedule, total_order_fees
from aitrader.indicators import mean, stdev
from aitrader.portfolio import Account, EntryKind, Ledger

TRADING_DAYS = 252
HUNDRED = Decimal(100)
BPS = Decimal(10_000)


@dataclass(frozen=True)
class EquityPoint:
    session: date
    equity_usd: Decimal
    market_value_usd: Decimal


def fmt(value: Decimal | None, places: int) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_EVEN), "f")


def daily_returns(start_equity: Decimal, points: Sequence[EquityPoint]) -> list[Decimal]:
    values = [start_equity, *(p.equity_usd for p in points)]
    return [current / previous - 1 for previous, current in pairwise(values)]


def max_drawdown(start_equity: Decimal, points: Sequence[EquityPoint]) -> tuple[Decimal, int]:
    """Largest peak-to-trough decline (fraction) and the longest time below a peak (sessions)."""
    peak = start_equity
    worst = Decimal(0)
    below = longest = 0
    for point in points:
        if point.equity_usd >= peak:
            peak = point.equity_usd
            below = 0
            continue
        below += 1
        longest = max(longest, below)
        worst = max(worst, 1 - point.equity_usd / peak)
    return worst, longest


def period_returns(
    start_equity: Decimal, points: Sequence[EquityPoint], key: Callable[[date], str]
) -> dict[str, Decimal]:
    """Return of each period (month, year), from the last equity of the previous period."""
    results: dict[str, Decimal] = {}
    base = start_equity
    for index, point in enumerate(points):
        label = key(point.session)
        last_of_period = index == len(points) - 1 or key(points[index + 1].session) != label
        if last_of_period:
            results[label] = point.equity_usd / base - 1
            base = point.equity_usd
    return results


def sharpe(returns: Sequence[Decimal]) -> Decimal | None:
    deviation = stdev(returns)
    average = mean(returns)
    if deviation is None or average is None or deviation == 0:
        return None
    return average / deviation * Decimal(TRADING_DAYS).sqrt()


def sortino(returns: Sequence[Decimal]) -> Decimal | None:
    average = mean(returns)
    if average is None:
        return None
    downside = mean([min(r, Decimal(0)) ** 2 for r in returns])
    if downside is None or downside == 0:
        return None
    return average / downside.sqrt() * Decimal(TRADING_DAYS).sqrt()


def trade_pnls(ledger: Ledger) -> list[Decimal]:
    """Realised gain minus the sale's fees, for every sale."""
    return [
        -entry.total(Account.REALIZED_PNL, Currency.USD)
        - entry.total(Account.TRADING_FEES, Currency.USD)
        for entry in ledger.entries
        if entry.kind is EntryKind.SELL
    ]


def trade_stats(pnls: Sequence[Decimal]) -> dict[str, str | None]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins, Decimal(0))
    gross_loss = -sum(losses, Decimal(0))
    average_win = mean(wins)
    average_loss = mean([-p for p in losses])
    return {
        "count": str(len(pnls)),
        "wins": str(len(wins)),
        "losses": str(len(losses)),
        "win_rate_pct": fmt(Decimal(len(wins)) / len(pnls) * HUNDRED, 2) if pnls else None,
        "profit_factor": fmt(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "payoff_ratio": (
            fmt(average_win / average_loss, 4) if average_win is not None and average_loss else None
        ),
    }


def buy_and_hold(
    bars: Sequence[Bar],
    capital_usd: Decimal,
    fees: FeeSchedule,
    assumptions: ExecutionAssumptions,
) -> list[EquityPoint]:
    """Benchmark B0: buy at the first open (plus slippage and fees), hold to the end."""
    if not bars:
        return []
    price = bars[0].open * (1 + assumptions.slippage_bps_per_side / BPS)
    step = fees.fractional_quantity_step
    quantity = (capital_usd / price / step).to_integral_value(rounding=ROUND_DOWN) * step
    while (
        quantity > 0
        and quantity * price + total_order_fees(fees, Side.BUY, quantity, price) > capital_usd
    ):
        quantity -= step
    cash = capital_usd - quantity * price - total_order_fees(fees, Side.BUY, quantity, price)
    return [
        EquityPoint(
            session=bar.ts.date(),
            equity_usd=cash + quantity * bar.close,
            market_value_usd=quantity * bar.close,
        )
        for bar in bars
    ]


def summarize(
    *,
    start_equity: Decimal,
    points: Sequence[EquityPoint],
    ledger: Ledger,
    fx_jpy_per_usd: Decimal,
    traded_notional_usd: Decimal,
    spread_and_slippage_usd: Decimal,
    prices: Mapping[str, Decimal],
    benchmark_symbol: str,
    benchmark: Sequence[EquityPoint],
) -> dict[str, object]:
    returns = daily_returns(start_equity, points)
    end_equity = points[-1].equity_usd if points else start_equity
    sessions = len(points)
    total = end_equity / start_equity - 1
    cagr = (
        (end_equity / start_equity) ** (Decimal(TRADING_DAYS) / sessions) - 1 if sessions else None
    )
    drawdown, drawdown_sessions = max_drawdown(start_equity, points)
    monthly = period_returns(start_equity, points, lambda d: d.strftime("%Y-%m"))
    yearly = period_returns(start_equity, points, lambda d: d.strftime("%Y"))
    exposure = mean([p.market_value_usd / p.equity_usd for p in points]) if points else None
    average_equity = mean([p.equity_usd for p in points]) or start_equity
    pnl = ledger.trading_pnl(prices, fx_jpy_per_usd)
    fees = {kind: fmt(value, 8) for kind, value in sorted(pnl.fees_by_type_usd.items())}
    costs_total = pnl.fees_usd + spread_and_slippage_usd
    gross = pnl.trading_pnl_usd + costs_total
    b0_drawdown, _ = max_drawdown(start_equity, benchmark)
    return {
        "period": {
            "start": points[0].session.isoformat() if points else None,
            "end": points[-1].session.isoformat() if points else None,
            "sessions": str(sessions),
        },
        "capital": {
            "start_usd": fmt(start_equity, 2),
            "end_usd": fmt(end_equity, 2),
            "fx_jpy_per_usd": fmt(fx_jpy_per_usd, 2),
        },
        "returns": {
            "total_pct": fmt(total * HUNDRED, 4),
            "cagr_pct": fmt(cagr * HUNDRED, 4) if cagr is not None else None,
            "monthly_pct": {k: fmt(v * HUNDRED, 4) for k, v in monthly.items()},
            "yearly_pct": {k: fmt(v * HUNDRED, 4) for k, v in yearly.items()},
            "share_of_months_at_least_1pct": _share(monthly.values(), Decimal("0.01")),
            "share_of_months_at_least_2pct": _share(monthly.values(), Decimal("0.02")),
        },
        "risk": {
            "max_drawdown_pct": fmt(drawdown * HUNDRED, 4),
            "max_drawdown_sessions": str(drawdown_sessions),
            "sharpe_rf0": fmt(sharpe(returns), 4),
            "sortino_rf0": fmt(sortino(returns), 4),
        },
        "trades": trade_stats(trade_pnls(ledger)),
        "activity": {
            "exposure_pct": fmt(exposure * HUNDRED, 4) if exposure is not None else None,
            "turnover": fmt(traded_notional_usd / average_equity, 4),
        },
        "costs": {
            "fees_usd": fees,
            "spread_and_slippage_usd": fmt(spread_and_slippage_usd, 8),
            "fx_usd": fmt(Decimal(0), 8),
            "total_usd": fmt(costs_total, 8),
            "share_of_gross_pnl_pct": fmt(costs_total / gross * HUNDRED, 4) if gross > 0 else None,
        },
        "pnl": {
            "trading_pnl_usd": fmt(pnl.trading_pnl_usd, 6),
            "trading_pnl_jpy": fmt(pnl.trading_pnl_jpy, 2),
        },
        "benchmarks": {
            "B0_buy_and_hold": {
                "symbol": benchmark_symbol,
                "total_pct": fmt((benchmark[-1].equity_usd / start_equity - 1) * HUNDRED, 4)
                if benchmark
                else None,
                "max_drawdown_pct": fmt(b0_drawdown * HUNDRED, 4),
                "invested_usd": fmt(benchmark[0].market_value_usd, 2) if benchmark else None,
            },
            "B1_cash": {"total_pct": fmt(Decimal(0), 4)},
        },
    }


def _share(values: Iterable[Decimal], threshold: Decimal) -> str | None:
    items = list(values)
    if not items:
        return None
    return fmt(Decimal(sum(1 for v in items if v >= threshold)) / len(items), 4)
