"""S1 weekly rotation keeps equivalent holdings stable to avoid needless turnover."""

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal

from aitrader.domain import Currency, IntentPurpose, Money, OrderIntent, Side, make_intent_id
from aitrader.indicators import atr, sma, total_return
from aitrader.strategy.base import StrategyContext

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")


@dataclass(frozen=True)
class MomentumParams:
    symbols: tuple[str, ...] = (
        "SPY",
        "IVV",
        "VOO",
        "VTI",
        "QQQ",
        "IWM",
        "XLB",
        "XLC",
        "XLE",
        "XLF",
        "XLI",
        "XLK",
        "XLP",
        "XLRE",
        "XLU",
        "XLV",
        "XLY",
        "SHY",
        "IEF",
        "TLT",
        "GLD",
        "IAU",
    )
    equivalents: tuple[tuple[str, ...], ...] = (("SPY", "IVV", "VOO"), ("GLD", "IAU"))
    lookback: int = 126
    skip: int = 0
    trend_period: int = 200
    top_n: int = 3
    atr_period: int = 20
    atr_stop_multiple: Decimal = Decimal(3)
    target_weight: Decimal = Decimal("0.25")
    cash_proxy: str | None = None


class MomentumStrategy:
    strategy_id = "S1"
    version = "1.0.0"

    def __init__(self, params: MomentumParams | None = None) -> None:
        self._params = params or MomentumParams()

    def params(self) -> Mapping[str, str]:
        # Omit the inactive option to preserve the 1.0.0 parameter identity.
        return {
            key: str(value)
            for key, value in asdict(self._params).items()
            if key != "cash_proxy" or value is not None
        }

    def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]:
        if (
            ctx.next_session is not None
            and ctx.next_session.isocalendar()[:2] == ctx.session.isocalendar()[:2]
        ):
            return []
        p = self._params
        eligible: dict[str, tuple[Decimal, Decimal]] = {}
        reasons: dict[str, str] = {}
        for symbol in p.symbols:
            if symbol == p.cash_proxy:
                continue
            bars = ctx.history.get(symbol, ())
            closes = [b.close for b in bars]
            trend = sma(closes, p.trend_period)
            volatility = atr(bars, p.atr_period)
            momentum = total_return(closes, p.lookback, p.skip)
            if trend is None or volatility is None or momentum is None:
                reasons[symbol] = "no longer eligible: insufficient history"
            elif closes[-1] <= trend:
                reasons[symbol] = "close at or below the trend average"
            else:
                eligible[symbol] = (momentum, volatility)
        ranking = sorted(eligible, key=lambda s: (-eligible[s][0], s))
        excluded: set[str] = set()
        for group in p.equivalents:
            members = [s for s in ranking if s in group]
            held = [s for s in members if s in ctx.holdings]
            if members:
                representative = (held or members)[0]
                excluded.update(s for s in members if s != representative)
        selected = [s for s in ranking if s not in excluded][: p.top_n]
        empty = p.top_n - len(selected)
        if p.cash_proxy is not None and empty > 0:
            bars = ctx.history.get(p.cash_proxy, ())
            volatility = atr(bars, p.atr_period)
            if not bars or bars[-1].ts.date() != ctx.session:
                reasons[p.cash_proxy] = (
                    "cash proxy unavailable: no bars on the session; stay in cash"
                )
            elif volatility is None:
                reasons[p.cash_proxy] = (
                    "cash proxy unavailable: insufficient ATR history; stay in cash"
                )
            else:
                selected.append(p.cash_proxy)
                eligible[p.cash_proxy] = (Decimal(0), volatility)
            if p.cash_proxy in reasons:
                logger.info("%s: %s", p.cash_proxy, reasons[p.cash_proxy])
        intents: list[OrderIntent] = []

        def intent(symbol: str, side: Side, **fields: object) -> OrderIntent:
            return OrderIntent.model_validate(
                {
                    "intent_id": make_intent_id(
                        self.strategy_id, self.version, symbol, side, ctx.as_of, len(intents)
                    ),
                    "strategy_id": self.strategy_id,
                    "strategy_version": self.version,
                    "symbol": symbol,
                    "side": side,
                    "signal_ts": ctx.as_of,
                    **fields,
                }
            )

        for symbol, holding in sorted(ctx.holdings.items()):
            if symbol not in selected:
                intents.append(
                    intent(
                        symbol,
                        Side.SELL,
                        purpose=IntentPurpose.EXIT,
                        target_quantity=holding.quantity,
                        reason=(
                            "dropped out of the top ranks"
                            if symbol in eligible
                            else reasons.get(
                                symbol, "no longer eligible: outside strategy universe"
                            )
                        ),
                    )
                )
        for symbol in selected:
            if symbol in ctx.holdings:
                continue
            stop = (
                ctx.history[symbol][-1].close - p.atr_stop_multiple * eligible[symbol][1]
            ).quantize(CENT)
            if stop <= 0:
                continue
            intents.append(
                intent(
                    symbol,
                    Side.BUY,
                    purpose=IntentPurpose.ENTRY,
                    target_notional=Money.of(
                        (
                            ctx.equity_usd
                            * p.target_weight
                            * (empty if symbol == p.cash_proxy else 1)
                        ).quantize(CENT),
                        Currency.USD,
                    ),
                    stop_price=stop,
                    reason=(
                        "cash proxy for empty momentum slots"
                        if symbol == p.cash_proxy
                        else "weekly momentum rank above the trend average"
                    ),
                )
            )
        return intents
