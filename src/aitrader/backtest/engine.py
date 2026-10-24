"""The backtest event loop (document 12 §1): the production cycle driven by a simulated clock.

Each session mirrors production. Quotes are built from the day's bar and fed to the SimBroker
through the same quote path PAPER uses:

1. 09:30 open: positions whose stop the open gapped through are sold (fill at the open).
2. 09:35 execution window: yesterday's intents are priced, risk-checked and sent; they can fill
   against the open quote, at most ``max_participation_pct`` of the bar's volume each.
3. 10:30 end of the window: unfilled entries and exits are cancelled (no chasing).
4. Intraday: a position whose stop is inside the day's range is sold at the stop.
5. Close: day orders expire, equity is marked at the close, and the strategy sees the
   completed bar to decide the next session's intents.

Decisions use completed bars only (``DailyBarSource.history`` up to the clock time).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from aitrader.backtest.snapshots import PositionSnapshot, SessionFill, SessionSnapshot
from aitrader.broker.sim import SimBroker
from aitrader.clock import SimClock, TradingCalendar
from aitrader.config import Settings, SimulationSettings
from aitrader.data import (
    CorporateActionError,
    CorporateActionKind,
    DailyBarSource,
    DataQualityGate,
    PointInTimeBars,
)
from aitrader.data.adjusted import MINOR_ADJUSTMENT_LIMIT
from aitrader.domain import (
    Currency,
    Fill,
    Instrument,
    InstrumentKind,
    Money,
    OrderIntent,
    Quote,
    Verdict,
)
from aitrader.execution import InMemoryOrderStore, OrderErrorMonitor, OrderGate, OrderManager
from aitrader.fees import WEBULL_JP, ExecutionAssumptions
from aitrader.metrics import EquityPoint
from aitrader.ops import CycleSettings, TradingCycle
from aitrader.portfolio import Account, Ledger
from aitrader.risk import HaltStateMachine, RiskEngine, round_down
from aitrader.strategy import Strategy, StrategyContext

BPS = Decimal(10_000)
HUNDRED = Decimal(100)
EXECUTION_DELAY = timedelta(minutes=5)
WINDOW = timedelta(minutes=60)


@dataclass(frozen=True)
class FillRecord:
    fill: Fill
    reference_mid: Decimal
    """Mid-price of the quote the fill came from; the difference is spread and slippage."""


@dataclass
class BacktestResult:
    start_equity_usd: Decimal
    points: list[EquityPoint]
    ledger: Ledger
    cycle: TradingCycle
    broker: SimBroker
    store: InMemoryOrderStore
    corporate_actions: PointInTimeBars | None = None
    fills: list[FillRecord] = field(default_factory=list)
    intents: list[OrderIntent] = field(default_factory=list)
    sessions: list[SessionSnapshot] = field(default_factory=list)

    @property
    def traded_notional_usd(self) -> Decimal:
        return sum((r.fill.quantity * r.fill.price for r in self.fills), Decimal(0))

    @property
    def spread_and_slippage_usd(self) -> Decimal:
        return sum(
            (abs(r.fill.price - r.reference_mid) * r.fill.quantity for r in self.fills), Decimal(0)
        )


def etf_universe(symbols: Sequence[str]) -> dict[str, Instrument]:
    return {
        symbol: Instrument(symbol=symbol, kind=InstrumentKind.ETF, fractionable=True)
        for symbol in symbols
    }


def run_backtest(
    settings: Settings,
    strategy: Strategy,
    source: DailyBarSource,
    *,
    adjusted: DailyBarSource | None = None,
    start: date | None = None,
    end: date | None = None,
    calendar: TradingCalendar | None = None,
    point_in_time: PointInTimeBars | None = None,
    simulation: SimulationSettings | None = None,
    capture_sessions: bool = False,
) -> BacktestResult:
    """Replay raw executions; dividend cash settles on ex-date rather than actual pay date."""
    pit = point_in_time or PointInTimeBars(source, adjusted)
    bt = simulation if simulation is not None else settings.require_backtest().simulation()
    first = start if start is not None else settings.require_backtest().start
    last = end if end is not None else settings.require_backtest().end
    calendar = calendar or TradingCalendar()
    sessions = calendar.sessions(first, last)
    if not sessions:
        raise ValueError("the backtest period contains no sessions")
    clock = SimClock(calendar.session_open(sessions[0]) - timedelta(hours=1))
    instruments = etf_universe(source.symbols)
    broker = SimBroker(clock, fees=WEBULL_JP, settlement_date=calendar.settlement_date)
    capital = Money.of(bt.initial_capital_usd, Currency.USD)
    broker.fund(capital)
    ledger = Ledger()
    ledger.deposit("initial-capital", clock.now(), capital)
    store = InMemoryOrderStore()
    halt = HaltStateMachine()
    halt.on_reconciliation(clock.now(), clean=True, detail="backtest start")
    manager = OrderManager(
        broker, OrderGate(settings.risk), store, ledger, clock, fills_from=clock.now()
    )
    cycle = TradingCycle(
        CycleSettings(mode=settings.mode, fx_jpy_per_usd=bt.fx_jpy_per_usd),
        clock=clock,
        calendar=calendar,
        risk=RiskEngine(settings.risk, fees=WEBULL_JP, assumptions=ExecutionAssumptions()),
        manager=manager,
        store=store,
        ledger=ledger,
        halt=halt,
        monitor=OrderErrorMonitor(halt),
        capabilities=broker.capabilities(),
        instruments=instruments,
        quality=DataQualityGate(calendar),
    )
    result = BacktestResult(
        start_equity_usd=bt.initial_capital_usd,
        points=[],
        ledger=ledger,
        cycle=cycle,
        broker=broker,
        store=store,
        corporate_actions=pit,
    )
    half_spread = bt.half_spread_bps / BPS
    participation = bt.max_participation_pct / HUNDRED
    step = WEBULL_JP.fractional_quantity_step
    pending: list[OrderIntent] = []
    last_close: dict[str, Decimal] = {}
    equity = bt.initial_capital_usd

    def quote(symbol: str, price: Decimal, ts: datetime) -> Quote:
        return Quote(
            symbol=symbol,
            ts=ts,
            bid=price * (1 - half_spread),
            ask=price * (1 + half_spread),
            bid_size=Decimal(0),
            ask_size=Decimal(0),
        )

    def deliver(quotes: Mapping[str, Quote], volumes: Mapping[str, Decimal]) -> None:
        for symbol, q in sorted(quotes.items()):
            cap = round_down(volumes.get(symbol, Decimal(0)) * participation, step)
            mid = (q.bid + q.ask) / 2
            for broker_fill in broker.on_quote(q, max_fill_quantity=cap):
                result.fills.append(FillRecord(broker_fill.fill, mid))
        cycle.sync()

    for session in sessions:
        decisions_before = len(cycle.decisions)
        skipped_before = len(cycle.skipped)
        opens = calendar.session_open(session)
        clock.set(opens)
        broker.settle(session)
        ledger.settle(session, clock.now())
        for action in pit.actions_on(session):
            if (
                action.kind is CorporateActionKind.UNEXPLAINED
                and abs(action.factor - 1) <= MINOR_ADJUSTMENT_LIMIT
            ):
                continue
            held = ledger.position(action.symbol)
            if action.kind is not CorporateActionKind.DISTRIBUTION:
                if held is not None or any(i.symbol == action.symbol for i in pending):
                    raise CorporateActionError(
                        f"{action.symbol} {session}: {action.kind.value} factor={action.factor} "
                        "affects a held position or pending intent"
                    )
            elif held is not None:
                gross = (held.quantity * action.cash_per_share).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
                if gross == 0:
                    continue
                us = (gross * bt.dividend_us_withholding_pct / HUNDRED).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
                jp = ((gross - us) * bt.dividend_jp_withholding_pct / HUNDRED).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
                entry_id = f"div-{action.symbol}-{session}"
                ledger.record_dividend(
                    entry_id,
                    clock.now(),
                    action.symbol,
                    Money.of(gross, Currency.USD),
                    Money.of(us, Currency.USD),
                    Money.of(jp, Currency.USD),
                )
                broker.credit_dividend(
                    entry_id,
                    action.symbol,
                    Money.of(gross, Currency.USD),
                    Money.of(us, Currency.USD),
                    Money.of(jp, Currency.USD),
                )
        cycle.begin_session(session, equity)
        today = {s: b for s in source.symbols if (b := source.bar_on(s, session)) is not None}
        volumes = {s: b.volume for s, b in today.items()}
        histories = {s: pit.history(s, clock.now()) for s in source.symbols}

        # 1. stops the open gapped through
        gapped = {s: today[s].open for s in cycle.stops if s in today}
        stop_intents = cycle.stop_exits(gapped)
        if stop_intents:
            open_quotes = {
                i.symbol: quote(i.symbol, today[i.symbol].open, clock.now()) for i in stop_intents
            }
            cycle.execute(stop_intents, open_quotes, histories)
            deliver(open_quotes, volumes)

        # 2. execution window
        clock.set(opens + EXECUTION_DELAY)
        window_quotes = {s: quote(s, b.open, clock.now()) for s, b in today.items()}
        cycle.execute(pending, window_quotes, histories)
        result.intents.extend(pending)
        deliver(window_quotes, volumes)

        # 3. end of the window
        clock.set(opens + WINDOW)
        cycle.cancel_unfilled()
        cycle.sync()

        # 4. intraday stops
        clock.set(opens + WINDOW + timedelta(minutes=1))
        triggered = {
            s: min(today[s].open, stop)
            for s, stop in cycle.stops.items()
            if s in today and today[s].low <= stop
        }
        stop_intents = cycle.stop_exits(triggered)
        if stop_intents:
            stop_quotes = {
                i.symbol: quote(i.symbol, triggered[i.symbol], clock.now()) for i in stop_intents
            }
            cycle.execute(stop_intents, stop_quotes, histories)
            deliver(stop_quotes, volumes)

        # 5. close
        clock.set(calendar.session_close(session))
        broker.expire_day_orders()
        cycle.sync()
        last_close.update({s: b.close for s, b in today.items()})
        valuation = ledger.valuation(
            {s: last_close[s] for s in ledger.positions}, bt.fx_jpy_per_usd
        )
        equity = valuation.equity_usd
        result.points.append(
            EquityPoint(
                session=session, equity_usd=equity, market_value_usd=valuation.market_value_usd
            )
        )
        context = StrategyContext(
            session=session,
            next_session=calendar.next_session(session),
            as_of=clock.now(),
            history={s: pit.history(s, clock.now()) for s in source.symbols},
            holdings=cycle.holdings(),
            equity_usd=equity,
        )
        pending = strategy.evaluate(context)
        if capture_sessions:
            result.sessions.append(
                SessionSnapshot(
                    session=session,
                    equity_usd=equity,
                    cash_usd_settled=ledger.cash_usd_settled,
                    cash_usd_unsettled=ledger.cash_usd_unsettled,
                    positions=tuple(
                        PositionSnapshot(
                            symbol=symbol,
                            quantity=pos.quantity,
                            average_cost=pos.average_cost,
                            close=last_close[symbol],
                            market_value_usd=pos.quantity * last_close[symbol],
                        )
                        for symbol, pos in sorted(ledger.positions.items())
                    ),
                    fills=tuple(
                        SessionFill(
                            fill=entry.fill,
                            fees_usd=entry.total(Account.TRADING_FEES, Currency.USD),
                        )
                        for entry in ledger.entries
                        if entry.ts.date() == session and entry.fill is not None
                    ),
                    next_intents=tuple(pending),
                    rejections=tuple(
                        decision
                        for decision in cycle.decisions[decisions_before:]
                        if decision.verdict is Verdict.REJECT
                    ),
                    skipped=tuple(item.reason for item in cycle.skipped[skipped_before:]),
                    data_quality={
                        symbol: tuple(sorted(findings))
                        for symbol, history in histories.items()
                        if (findings := DataQualityGate(calendar).findings(history, session))
                    },
                )
            )
    return result
