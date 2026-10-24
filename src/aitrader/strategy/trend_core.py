"""S3 holds fixed core slots, replacing unwanted trends with a defensive asset."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN, Decimal

from aitrader.domain import Currency, IntentPurpose, Money, OrderIntent, Side, make_intent_id
from aitrader.indicators import atr, sma
from aitrader.strategy.base import StrategyContext


@dataclass(frozen=True)
class TrendCoreParams:
    core: tuple[str, ...] = ("SPY", "QQQ", "IWM", "GLD")
    defensive: str = "SHY"
    trend_period: int = 200
    atr_period: int = 20
    atr_stop_multiple: Decimal = Decimal(3)
    target_weight: Decimal = Decimal("0.25")


class TrendCoreStrategy:
    strategy_id = "S3"
    version = "1.0.0"

    def __init__(self, params: TrendCoreParams | None = None) -> None:
        self._params = params or TrendCoreParams()

    def params(self) -> Mapping[str, str]:
        return {key: str(value) for key, value in asdict(self._params).items()}

    def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]:
        if (
            ctx.next_session is not None
            and ctx.next_session.isocalendar()[:2] == ctx.session.isocalendar()[:2]
        ):
            return []
        p = self._params
        wanted: dict[str, Decimal] = {}
        reasons: dict[str, str] = {}
        for symbol in p.core:
            bars = ctx.history.get(symbol, ())
            trend = sma([b.close for b in bars], p.trend_period)
            volatility = atr(bars, p.atr_period)
            if not bars or bars[-1].ts.date() != ctx.session:
                reasons[symbol] = "no bar on the session"
            elif trend is None:
                reasons[symbol] = "not enough trend history"
            elif volatility is None:
                reasons[symbol] = "not enough ATR history"
            elif bars[-1].close <= trend:
                reasons[symbol] = "below the trend average"
            else:
                wanted[symbol] = volatility
        unwanted = len(reasons)
        intents: list[OrderIntent] = []

        def intent(symbol: str, side: Side, reason: str, **fields: object) -> None:
            intents.append(
                OrderIntent.model_validate(
                    {
                        "intent_id": make_intent_id(
                            self.strategy_id, self.version, symbol, side, ctx.as_of, len(intents)
                        ),
                        "strategy_id": self.strategy_id,
                        "strategy_version": self.version,
                        "symbol": symbol,
                        "side": side,
                        "signal_ts": ctx.as_of,
                        "purpose": IntentPurpose.ENTRY if side is Side.BUY else IntentPurpose.EXIT,
                        "reason": reason,
                        **fields,
                    }
                )
            )

        def buy(symbol: str, volatility: Decimal, slots: int, reason: str) -> None:
            stop = (ctx.history[symbol][-1].close - p.atr_stop_multiple * volatility).quantize(
                Decimal("0.01")
            )
            if stop > 0:
                intent(
                    symbol,
                    Side.BUY,
                    reason,
                    target_notional=Money.of(
                        (ctx.equity_usd * p.target_weight * slots).quantize(Decimal("0.01")),
                        Currency.USD,
                    ),
                    stop_price=stop,
                )

        for symbol in sorted(reasons):
            if symbol in ctx.holdings:
                intent(
                    symbol,
                    Side.SELL,
                    reasons[symbol],
                    target_quantity=ctx.holdings[symbol].quantity,
                )
        defensive = ctx.holdings.get(p.defensive)
        bars = ctx.history.get(p.defensive, ())
        if defensive is not None:
            if unwanted == 0:
                intent(
                    p.defensive,
                    Side.SELL,
                    "no unwanted core slots",
                    target_quantity=defensive.quantity,
                )
            elif bars:
                close = bars[-1].close
                target = ctx.equity_usd * p.target_weight * unwanted
                if defensive.quantity * close > target + ctx.equity_usd * Decimal("0.05"):
                    excess = ((defensive.quantity * close - target) / close).quantize(
                        Decimal("0.00001"), rounding=ROUND_DOWN
                    )
                    if excess > 0:
                        intent(
                            p.defensive,
                            Side.SELL,
                            "reduce the defensive asset",
                            target_quantity=excess,
                        )
        elif unwanted > 0 and bars and bars[-1].ts.date() == ctx.session:
            volatility = atr(bars, p.atr_period)
            if volatility is not None:
                buy(
                    p.defensive,
                    volatility,
                    unwanted,
                    f"defensive asset for {unwanted} unwanted core slots",
                )
        for symbol in sorted(wanted):
            if symbol not in ctx.holdings:
                buy(symbol, wanted[symbol], 1, f"above the {p.trend_period}-session trend average")
        return intents
