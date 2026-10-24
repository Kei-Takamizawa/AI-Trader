"""S2 — pullback in an uptrend (document 11 §3).

Entry when the close is above the 200-day average and RSI(2) is below 10; exit when the close is
above the 5-day average or after 5 sessions; stop 2.5 × ATR(20) below the signal close. The
size asked for is a fixed share of equity; the risk engine decides the final quantity.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from aitrader.domain import (
    Currency,
    IntentPurpose,
    Money,
    OrderIntent,
    Side,
    make_intent_id,
)
from aitrader.indicators import atr, rsi, sma
from aitrader.strategy.base import StrategyContext

CENT = Decimal("0.01")


@dataclass(frozen=True)
class PullbackParams:
    symbols: tuple[str, ...] = ("SPY", "QQQ", "IWM")
    trend_period: int = 200
    rsi_period: int = 2
    rsi_entry_below: Decimal = Decimal(10)
    exit_sma_period: int = 5
    max_hold_sessions: int = 5
    atr_period: int = 20
    atr_stop_multiple: Decimal = Decimal("2.5")
    target_weight: Decimal = Decimal("0.25")


class PullbackStrategy:
    def __init__(self, params: PullbackParams | None = None) -> None:
        self._params = params or PullbackParams()

    @property
    def strategy_id(self) -> str:
        return "S2"

    @property
    def version(self) -> str:
        return "1.0.0"

    def params(self) -> Mapping[str, str]:
        return {key: str(value) for key, value in asdict(self._params).items()}

    def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]:
        p = self._params
        intents: list[OrderIntent] = []
        for symbol in sorted(p.symbols):
            bars = ctx.history.get(symbol, ())
            if not bars:
                continue
            last = bars[-1]
            closes = [b.close for b in bars]
            holding = ctx.holdings.get(symbol)
            if holding is not None:
                held = sum(1 for b in bars if b.ts.date() > holding.entry_session)
                exit_average = sma(closes, p.exit_sma_period)
                reasons = []
                if exit_average is not None and last.close > exit_average:
                    reasons.append(f"close above the {p.exit_sma_period}-day average")
                if held >= p.max_hold_sessions:
                    reasons.append(f"held {held} sessions")
                if reasons:
                    intents.append(
                        self._intent(
                            symbol,
                            Side.SELL,
                            last.ts,
                            len(intents),
                            reason="; ".join(reasons),
                            purpose=IntentPurpose.EXIT,
                            target_quantity=holding.quantity,
                        )
                    )
                continue
            trend = sma(closes, p.trend_period)
            strength = rsi(closes, p.rsi_period)
            true_range = atr(bars, p.atr_period)
            if trend is None or strength is None or true_range is None:
                continue
            if last.close <= trend or strength >= p.rsi_entry_below:
                continue
            stop = (last.close - p.atr_stop_multiple * true_range).quantize(CENT)
            if stop <= 0:
                continue
            intents.append(
                self._intent(
                    symbol,
                    Side.BUY,
                    last.ts,
                    len(intents),
                    reason=(
                        f"close {last.close} above the {p.trend_period}-day average {trend:.2f}; "
                        f"RSI({p.rsi_period}) {strength:.1f} below {p.rsi_entry_below}"
                    ),
                    purpose=IntentPurpose.ENTRY,
                    target_notional=Money.of(
                        (ctx.equity_usd * p.target_weight).quantize(CENT), Currency.USD
                    ),
                    stop_price=stop,
                )
            )
        return intents

    def _intent(
        self,
        symbol: str,
        side: Side,
        signal_ts: datetime,
        seq: int,
        **fields: object,
    ) -> OrderIntent:
        return OrderIntent.model_validate(
            {
                "intent_id": make_intent_id(
                    self.strategy_id,
                    self.version,
                    symbol,
                    side,
                    signal_ts,
                    seq,
                ),
                "strategy_id": self.strategy_id,
                "strategy_version": self.version,
                "symbol": symbol,
                "side": side,
                "signal_ts": signal_ts,
                **fields,
            }
        )
