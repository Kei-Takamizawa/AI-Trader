"""One trading cycle, shared by BACKTEST and PAPER (document 01 §4, document 11 §4).

Strategy intents are priced at execution time (marketable limit collars), checked by the risk
engine, passed through the order gate and sent by the order manager. Exits go first. Protective
stops are kept per position, only ever tightened, and turned into stop-exit orders when
triggered. The cycle never talks to a concrete broker: market data reaches the broker outside it.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from pydantic import ValidationError

from aitrader.broker.port import BrokerCapabilities
from aitrader.clock import Clock, TradingCalendar
from aitrader.data import DataQualityGate
from aitrader.domain import (
    Bar,
    Instrument,
    IntentPurpose,
    Mode,
    OrderIntent,
    OrderStatus,
    OrderType,
    Quote,
    RiskDecision,
    Side,
    Verdict,
    make_intent_id,
)
from aitrader.execution import (
    ExecutionEvent,
    GateContext,
    OrderErrorMonitor,
    OrderManager,
    OrderStore,
    RetryNotAllowedError,
)
from aitrader.execution.gate import PROTECTIVE
from aitrader.indicators import atr, daily_volatility, mean
from aitrader.portfolio import Ledger
from aitrader.risk import (
    HaltStateMachine,
    MarketWideState,
    OpenOrderSnapshot,
    PositionSnapshot,
    RiskEngine,
    RiskState,
    SymbolMarketState,
)
from aitrader.strategy import Holding

BPS = Decimal(10_000)
HUNDRED = Decimal(100)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class CycleSettings:
    mode: Mode
    fx_jpy_per_usd: Decimal
    benchmark_symbol: str = "SPY"
    entry_collar_bps: Decimal = Decimal(5)
    max_entry_above_previous_close_pct: Decimal = Decimal(1)
    exit_collar_bps: Decimal = Decimal(5)
    atr_period: int = 14
    volume_period: int = 20
    volatility_period: int = 60


@dataclass(frozen=True)
class SkippedIntent:
    intent_id: str
    reason: str


class TradingCycle:
    def __init__(
        self,
        settings: CycleSettings,
        *,
        clock: Clock,
        calendar: TradingCalendar,
        risk: RiskEngine,
        manager: OrderManager,
        store: OrderStore,
        ledger: Ledger,
        halt: HaltStateMachine,
        monitor: OrderErrorMonitor,
        capabilities: BrokerCapabilities,
        instruments: Mapping[str, Instrument],
        quality: DataQualityGate,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._calendar = calendar
        self._risk = risk
        self._manager = manager
        self._store = store
        self._ledger = ledger
        self._halt = halt
        self._monitor = monitor
        self._capabilities = capabilities
        self._instruments = dict(instruments)
        self._quality = quality
        self.decisions: list[RiskDecision] = []
        self.events: list[ExecutionEvent] = []
        self.skipped: list[SkippedIntent] = []
        self._stops: dict[str, Decimal] = {}
        self._entry_sessions: dict[str, date] = {}
        self._entries: dict[str, OrderIntent] = {}
        """Submitted entry intents by client order ID, until their order is closed."""
        self._session: date | None = None
        self._start_of_day_equity: Decimal | None = None
        self._month_start_equity: Decimal | None = None
        self._peak_equity: Decimal | None = None
        self._benchmark_close: Decimal | None = None

    # ------------------------------------------------------------------ sessions

    def begin_session(self, session: date, equity_usd: Decimal) -> None:
        """Record the reference equities of the loss limits (start of day, month, peak)."""
        if self._session is None or (session.year, session.month) != (
            self._session.year,
            self._session.month,
        ):
            self._month_start_equity = equity_usd
        self._start_of_day_equity = equity_usd
        self._peak_equity = max(self._peak_equity or equity_usd, equity_usd)
        self._session = session

    @property
    def stops(self) -> dict[str, Decimal]:
        return dict(self._stops)

    def holdings(self) -> dict[str, Holding]:
        session = self._session or self._clock.now().date()
        return {
            symbol: Holding(
                symbol=symbol,
                quantity=position.quantity,
                entry_session=self._entry_sessions.get(symbol, session),
                stop_price=self._stops.get(symbol),
            )
            for symbol, position in self._ledger.positions.items()
        }

    # ------------------------------------------------------------------ snapshots

    def market_state(
        self, symbol: str, history: Sequence[Bar], quote: Quote | None
    ) -> SymbolMarketState:
        s = self.settings
        instrument = self._instruments[symbol]
        recent = history[-s.volume_period :]
        session = self._session or self._clock.now().date()
        return SymbolMarketState(
            instrument=instrument,
            allow_listed=True,
            quote=quote,
            previous_close=history[-1].close if history else None,
            atr14=atr(history, s.atr_period),
            avg_daily_volume_20d=mean([b.volume for b in recent]) or Decimal(0),
            avg_daily_dollar_volume_20d=mean([b.close * b.volume for b in recent]) or Decimal(0),
            daily_volatility=daily_volatility([b.close for b in history], s.volatility_period)
            or Decimal(0),
            data_quality_failures=self._quality.findings(history, session),
        )

    def _open_orders(self) -> list[OpenOrderSnapshot]:
        snapshots = []
        for record in self._store.active():
            request = record.request
            reserved = (
                record.remaining * (request.limit_price or Decimal(0))
                if request.side is Side.BUY
                else Decimal(0)
            )
            snapshots.append(
                OpenOrderSnapshot(
                    intent_id=request.intent_id,
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=request.side,
                    purpose=request.purpose,
                    remaining_quantity=record.remaining,
                    reserved_notional_usd=reserved,
                )
            )
        return snapshots

    def _orders_today(self) -> tuple[int, dict[str, int]]:
        today = self._clock.now().date()
        total = 0
        non_protective: dict[str, int] = {}
        for event in self._store.events():
            if event.event != "WRITE_AHEAD" or event.ts.date() != today:
                continue
            total += 1
            record = self._store.get(event.client_order_id)
            if record is not None and record.request.purpose not in PROTECTIVE:
                symbol = record.request.symbol
                non_protective[symbol] = non_protective.get(symbol, 0) + 1
        return total, non_protective

    def _market_values(self, prices: Mapping[str, Decimal]) -> dict[str, Decimal]:
        return {
            symbol: position.quantity * prices.get(symbol, position.average_cost)
            for symbol, position in self._ledger.positions.items()
        }

    def risk_state(self, prices: Mapping[str, Decimal], quotes: Mapping[str, Quote]) -> RiskState:
        values = self._market_values(prices)
        equity = (
            self._ledger.cash_usd_settled
            + self._ledger.cash_usd_unsettled
            + sum(values.values(), Decimal(0))
        )
        selling: dict[str, Decimal] = {}
        for order in self._open_orders():
            if order.side is Side.SELL:
                selling[order.symbol] = (
                    selling.get(order.symbol, Decimal(0)) + order.remaining_quantity
                )
        positions = tuple(
            PositionSnapshot(
                symbol=symbol,
                quantity=position.quantity,
                available_quantity=max(
                    position.quantity - selling.get(symbol, Decimal(0)), Decimal(0)
                ),
                market_value_usd=values[symbol],
            )
            for symbol, position in self._ledger.positions.items()
        )
        total, non_protective = self._orders_today()
        benchmark = quotes.get(self.settings.benchmark_symbol)
        benchmark_change = None
        reference = self._benchmark_close
        if benchmark is not None and reference is not None:
            benchmark_change = ((benchmark.bid + benchmark.ask) / 2 / reference - 1) * HUNDRED
        return RiskState(
            now=self._clock.now(),
            mode=self.settings.mode,
            system_state=self._halt.state,
            fx_jpy_per_usd=self.settings.fx_jpy_per_usd,
            equity_usd=equity,
            start_of_day_equity_usd=self._start_of_day_equity or equity,
            month_start_equity_usd=self._month_start_equity or equity,
            peak_equity_usd=self._peak_equity or equity,
            settled_cash_usd=self._ledger.cash_usd_settled,
            positions=positions,
            open_orders=tuple(self._open_orders()),
            orders_today=total,
            non_protective_orders_today=non_protective,
            market=MarketWideState(benchmark_change_pct=benchmark_change),
        )

    def gate_context(self, prices: Mapping[str, Decimal], equity_usd: Decimal) -> GateContext:
        total, _ = self._orders_today()
        return GateContext(
            now=self._clock.now(),
            system_state=self._halt.state,
            market_open=self._calendar.is_open(self._clock.now()),
            fx_jpy_per_usd=self.settings.fx_jpy_per_usd,
            equity_usd=equity_usd,
            settled_cash_usd=self._ledger.cash_usd_settled,
            holdings_value_usd=sum(self._market_values(prices).values(), Decimal(0)),
            held_quantity={s: p.quantity for s, p in self._ledger.positions.items()},
            open_orders=tuple(self._store.active()),
            orders_today=total,
            allow_list=frozenset(self._instruments),
            capabilities=self._capabilities,
        )

    # ------------------------------------------------------------------ execution

    def execute(
        self,
        intents: Iterable[OrderIntent],
        quotes: Mapping[str, Quote],
        histories: Mapping[str, Sequence[Bar]],
    ) -> None:
        """Price, check and send intents; exits before entries."""
        benchmark_history = histories.get(self.settings.benchmark_symbol, ())
        self._benchmark_close = benchmark_history[-1].close if benchmark_history else None
        for intent in sorted(intents, key=lambda i: (i.side is Side.BUY, i.symbol, i.intent_id)):
            self._execute_one(intent, quotes, histories)

    def _execute_one(
        self,
        intent: OrderIntent,
        quotes: Mapping[str, Quote],
        histories: Mapping[str, Sequence[Bar]],
    ) -> None:
        symbol = intent.symbol
        instrument = self._instruments.get(symbol)
        if instrument is None:
            self.skipped.append(SkippedIntent(intent.intent_id, f"{symbol} is not in the universe"))
            return
        quote = quotes.get(symbol)
        history = histories.get(symbol, ())
        priced = self._priced(intent, quote, history)
        if priced is None:
            return
        prices = {s: (q.bid + q.ask) / 2 for s, q in quotes.items()}
        state = self.risk_state(prices, quotes)
        decision = self._risk.evaluate(priced, state, self.market_state(symbol, history, quote))
        self.decisions.append(decision)
        if decision.verdict is Verdict.REJECT:
            return
        order_type = (
            OrderType.MARKET if intent.purpose is IntentPurpose.STOP_EXIT else OrderType.LIMIT
        )
        try:
            result = self._manager.submit(
                priced,
                decision,
                instrument,
                self.gate_context(prices, state.equity_usd),
                order_type=order_type,
                limit_price=priced.limit_price,
            )
        except RetryNotAllowedError:
            self.skipped.append(SkippedIntent(intent.intent_id, "intent was already sent"))
            return
        self._observe(result.events)
        if result.record is not None and intent.side is Side.BUY:
            self._entries[result.record.client_order_id] = priced

    def _priced(
        self, intent: OrderIntent, quote: Quote | None, history: Sequence[Bar]
    ) -> OrderIntent | None:
        """The intent with its execution limit (document 11 §4): marketable limit collars."""
        if quote is None or intent.purpose is IntentPurpose.STOP_EXIT:
            return intent
        s = self.settings
        mid = (quote.bid + quote.ask) / 2
        if intent.side is Side.BUY:
            limit = (mid * (1 + s.entry_collar_bps / BPS)).quantize(CENT, rounding=ROUND_CEILING)
            if history:
                cap = history[-1].close * (1 + s.max_entry_above_previous_close_pct / HUNDRED)
                limit = min(limit, cap.quantize(CENT, rounding=ROUND_FLOOR))
        else:
            limit = (mid * (1 - s.exit_collar_bps / BPS)).quantize(CENT, rounding=ROUND_FLOOR)
        try:
            return OrderIntent.model_validate({**intent.model_dump(), "limit_price": limit})
        except ValidationError as exc:
            self.skipped.append(SkippedIntent(intent.intent_id, f"cannot be priced: {exc}"))
            return None

    def stop_exits(self, trigger_prices: Mapping[str, Decimal]) -> list[OrderIntent]:
        """Stop-exit intents for held symbols whose price is at or below their stop."""
        intents = []
        now = self._clock.now()
        for symbol, price in sorted(trigger_prices.items()):
            stop = self._stops.get(symbol)
            position = self._ledger.position(symbol)
            if stop is None or position is None or price > stop:
                continue
            intents.append(
                OrderIntent(
                    intent_id=make_intent_id("STOP", "1", symbol, Side.SELL, now, 0),
                    strategy_id="STOP",
                    strategy_version="1",
                    symbol=symbol,
                    side=Side.SELL,
                    purpose=IntentPurpose.STOP_EXIT,
                    target_quantity=position.quantity,
                    reason=f"price {price} at or below the stop {stop}",
                    signal_ts=now,
                )
            )
        return intents

    def cancel_unfilled(self) -> None:
        """End of the execution window: no chasing (document 11 §4)."""
        for record in self._store.active():
            if record.request.purpose in PROTECTIVE:
                continue
            if record.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED):
                self._observe(self._manager.cancel(record.client_order_id))

    def sync(self) -> None:
        self._observe(self._manager.sync())
        session = self._session or self._clock.now().date()
        for cid, intent in list(self._entries.items()):
            record = self._store.get(cid)
            filled = record.filled_quantity if record is not None else Decimal(0)
            if filled > 0 and intent.stop_price is not None:
                symbol = intent.symbol
                if symbol not in self._entry_sessions:
                    self._entry_sessions[symbol] = session
                self._stops[symbol] = max(
                    self._stops.get(symbol, intent.stop_price), intent.stop_price
                )
            if record is None or not record.is_active:
                del self._entries[cid]
        for symbol in list(self._stops):
            if self._ledger.position(symbol) is None:
                del self._stops[symbol]
                self._entry_sessions.pop(symbol, None)

    def _observe(self, events: Sequence[ExecutionEvent]) -> None:
        self.events.extend(events)
        self._monitor.observe(events)
