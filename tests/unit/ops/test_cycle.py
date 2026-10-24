"""The shared trading cycle: pricing, ordering, stops and the execution window."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from aitrader.broker.sim import SimBroker, SimFault, SimOperation
from aitrader.clock import SimClock, TradingCalendar
from aitrader.config import RiskLimits
from aitrader.data import DailyBarSource, DataQualityGate
from aitrader.domain import (
    Bar,
    Currency,
    IntentPurpose,
    Mode,
    Money,
    OrderIntent,
    OrderStatus,
    Quote,
    Side,
    Verdict,
)
from aitrader.execution import InMemoryOrderStore, OrderErrorMonitor, OrderGate, OrderManager
from aitrader.ops import CycleSettings, TradingCycle
from aitrader.portfolio import Ledger
from aitrader.risk import HaltStateMachine, RiskEngine
from tests.unit.execution.builders import intent

D = Decimal
CAL = TradingCalendar()
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bars"
SOURCE = DailyBarSource(FIXTURES, CAL)
SESSION = date(2024, 6, 3)  # a Monday with a year of history before it


@dataclass
class Rig:
    clock: SimClock
    broker: SimBroker
    ledger: Ledger
    store: InMemoryOrderStore
    cycle: TradingCycle

    def histories(self) -> dict[str, tuple[Bar, ...]]:
        return {s: SOURCE.history(s, self.clock.now()) for s in SOURCE.symbols}

    def quotes(self, **mids: str) -> dict[str, Quote]:
        now = self.clock.now()
        return {
            s: Quote(
                symbol=s,
                ts=now,
                bid=D(m) - D("0.01"),
                ask=D(m) + D("0.01"),
                bid_size=D(1),
                ask_size=D(1),
            )
            for s, m in mids.items()
        }

    def fill(self, quotes: dict[str, Quote]) -> None:
        for q in quotes.values():
            self.broker.on_quote(q)
        self.cycle.sync()


def rig(cash: str = "323") -> Rig:
    clock = SimClock(CAL.session_open(SESSION) + timedelta(minutes=5))
    broker = SimBroker(clock, settlement_date=CAL.settlement_date)
    broker.fund(Money.of(cash, Currency.USD))
    ledger = Ledger()
    ledger.deposit("fund", clock.now(), Money.of(cash, Currency.USD))
    store = InMemoryOrderStore()
    halt = HaltStateMachine()
    halt.on_reconciliation(clock.now(), clean=True)
    manager = OrderManager(
        broker, OrderGate(RiskLimits()), store, ledger, clock, fills_from=clock.now()
    )
    cycle = TradingCycle(
        CycleSettings(mode=Mode.BACKTEST, fx_jpy_per_usd=D(155)),
        clock=clock,
        calendar=CAL,
        risk=RiskEngine(RiskLimits()),
        manager=manager,
        store=store,
        ledger=ledger,
        halt=halt,
        monitor=OrderErrorMonitor(halt),
        capabilities=broker.capabilities(),
        instruments={s: SOURCE_INSTRUMENTS[s] for s in SOURCE.symbols},
        quality=DataQualityGate(CAL),
    )
    cycle.begin_session(SESSION, D(cash))
    return Rig(clock, broker, ledger, store, cycle)


from aitrader.backtest import etf_universe  # noqa: E402 - after the fixtures it needs

SOURCE_INSTRUMENTS = etf_universe(SOURCE.symbols)


def previous_close(symbol: str = "SPY") -> Decimal:
    return SOURCE.history(symbol, CAL.session_open(SESSION))[-1].close


def entry(stop: str, **kw: Any) -> OrderIntent:
    return intent(
        Side.BUY,
        intent_id=kw.pop("intent_id", "buy-1"),
        target_quantity=None,
        target_notional=Money.of(kw.pop("notional", "80"), Currency.USD),
        limit_price=None,
        stop_price=D(stop),
        **kw,
    )


def test_entry_is_priced_with_a_capped_collar_and_filled() -> None:
    r = rig()
    close = previous_close()
    quotes = r.quotes(SPY=str(close))
    r.cycle.execute([entry(str(close * D("0.97")))], quotes, r.histories())
    (decision,) = r.cycle.decisions
    assert decision.verdict in (Verdict.APPROVE, Verdict.REDUCE)
    (record,) = r.store.active()
    # mid * 1.0005 rounded up to the cent, below the cap of 1% over the previous close
    assert record.request.limit_price == (close * D("1.0005")).quantize(
        D("0.01"), rounding="ROUND_CEILING"
    )
    r.fill(quotes)
    assert r.ledger.position("SPY") is not None
    assert r.cycle.stops == {"SPY": (close * D("0.97"))}
    holding = r.cycle.holdings()["SPY"]
    assert holding.entry_session == SESSION
    assert holding.stop_price == close * D("0.97")


def test_entry_limit_never_exceeds_one_percent_over_the_previous_close() -> None:
    r = rig()
    close = previous_close()
    gap_up = r.quotes(SPY=str(close * D("1.03")))
    r.cycle.execute([entry(str(close * D("0.97")))], gap_up, r.histories())
    (record,) = r.store.active()
    assert record.request.limit_price == (close * D("1.01")).quantize(
        D("0.01"), rounding="ROUND_FLOOR"
    )
    r.fill(gap_up)  # not marketable: the ask is above the cap
    assert r.ledger.position("SPY") is None
    r.cycle.cancel_unfilled()
    r.cycle.sync()
    assert r.store.active() == []


def test_exits_go_before_entries_and_stops_only_tighten() -> None:
    r = rig("1000")
    close = previous_close()
    quotes = r.quotes(SPY=str(close))
    r.cycle.execute([entry(str(close * D("0.97")))], quotes, r.histories())
    r.fill(quotes)
    # a second entry with a lower stop does not widen the stop
    r.cycle.execute(
        [entry(str(close * D("0.90")), intent_id="buy-2", notional="10")], quotes, r.histories()
    )
    r.fill(quotes)
    assert r.cycle.stops["SPY"] == close * D("0.97")
    position = r.ledger.position("SPY")
    assert position is not None
    sell = intent(Side.SELL, intent_id="sell-1", target_quantity=position.quantity)
    buy_qqq = entry(str(previous_close("QQQ") * D("0.97")), intent_id="buy-qqq", symbol="QQQ")
    both = r.quotes(SPY=str(close), QQQ=str(previous_close("QQQ")))
    r.cycle.execute([buy_qqq, sell], both, r.histories())
    order = [e.client_order_id for e in r.store.events() if e.event == "WRITE_AHEAD"][-2:]
    first = r.store.get(order[0])
    assert first is not None
    assert first.request.side is Side.SELL
    r.fill(both)
    assert r.ledger.position("SPY") is None
    assert "SPY" not in r.cycle.stops  # removed with the position


def test_stop_exit_is_a_market_order_and_survives_the_window_end() -> None:
    r = rig()
    close = previous_close()
    quotes = r.quotes(SPY=str(close))
    r.cycle.execute([entry(str(close * D("0.97")))], quotes, r.histories())
    r.fill(quotes)
    assert r.cycle.stop_exits({"SPY": close}) == []  # above the stop
    assert r.cycle.stop_exits({"QQQ": D(1)}) == []  # no position
    (stop_exit,) = r.cycle.stop_exits({"IWM": D(1), "SPY": close * D("0.97")})
    assert stop_exit.purpose is IntentPurpose.STOP_EXIT
    r.cycle.execute([stop_exit], {}, r.histories())  # no quote at all: still sent at market
    (record,) = r.store.active()
    assert record.request.limit_price is None
    state = r.cycle.risk_state({}, {})
    assert state.orders_today == 2
    assert state.non_protective_orders_today == {"SPY": 1}  # the stop exit is not counted
    r.cycle.cancel_unfilled()  # protective orders stay
    assert r.store.active()[0].status is OrderStatus.SUBMITTED
    r.fill(r.quotes(SPY=str(close * D("0.96"))))
    assert r.ledger.position("SPY") is None


def test_intents_that_cannot_be_executed_are_skipped_with_a_reason() -> None:
    r = rig()
    close = previous_close()
    quotes = r.quotes(SPY=str(close))
    outside = entry("1", symbol="DIA")
    unpriceable = entry(str(close * D("1.02")), intent_id="buy-high")  # stop above the limit
    r.cycle.execute([outside, unpriceable], quotes, r.histories())
    reasons = {s.intent_id: s.reason for s in r.cycle.skipped}
    assert "not in the universe" in reasons["buy-1"]
    assert "cannot be priced" in reasons["buy-high"]
    good = entry(str(close * D("0.97")))
    r.cycle.execute([good], quotes, r.histories())
    r.cycle.execute([good], quotes, r.histories())  # still open: the risk engine refuses (DUP-1)
    assert "DUP-1" in {x.rule_id for x in r.cycle.decisions[-1].rule_results if not x.passed}
    r.fill(quotes)
    r.cycle.execute([good], quotes, r.histories())  # closed: the order manager refuses a resend
    assert any(s.reason == "intent was already sent" for s in r.cycle.skipped)


def test_rejected_intents_are_recorded_but_not_sent() -> None:
    r = rig()
    r.cycle.execute([entry("1")], {}, r.histories())  # no quote: DQ-1 and others fail
    (decision,) = r.cycle.decisions
    assert decision.verdict is Verdict.REJECT
    assert r.store.active() == []


def test_reference_equities_follow_the_calendar() -> None:
    r = rig()
    r.cycle.begin_session(date(2024, 6, 4), D(330))
    state = r.cycle.risk_state({}, {})
    assert state.start_of_day_equity_usd == 330
    assert state.month_start_equity_usd == 323  # same month
    assert state.peak_equity_usd == 330
    r.cycle.begin_session(date(2024, 7, 1), D(320))
    state = r.cycle.risk_state({}, {})
    assert state.month_start_equity_usd == 320
    assert state.peak_equity_usd == 330


def test_benchmark_change_feeds_the_market_breaker() -> None:
    r = rig()
    close = previous_close()
    quotes = r.quotes(SPY=str(close * D("0.96")))
    r.cycle.execute([], quotes, r.histories())
    state = r.cycle.risk_state({}, quotes)
    assert state.market.benchmark_change_pct is not None
    assert state.market.benchmark_change_pct < -3


def test_entry_without_history_has_no_previous_close_cap() -> None:
    r = rig()
    quotes = r.quotes(SPY="100")
    r.cycle.execute([entry("90")], quotes, {})
    (decision,) = r.cycle.decisions
    assert decision.verdict is Verdict.REJECT  # no history: the data rules fail closed


def test_window_end_leaves_unknown_orders_for_the_lookup() -> None:
    r = rig()
    close = previous_close()
    r.broker.inject(SimOperation.SUBMIT, SimFault.TIMEOUT_BEFORE_ACCEPT)
    r.cycle.execute([entry(str(close * D("0.97")))], r.quotes(SPY=str(close)), r.histories())
    (record,) = r.store.active()
    assert record.status is OrderStatus.UNKNOWN
    r.cycle.cancel_unfilled()  # an UNKNOWN order is resolved by lookup, not cancelled blindly
    (still,) = r.store.active()
    assert still.status is OrderStatus.UNKNOWN
