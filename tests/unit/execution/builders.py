"""Builders for execution tests: a funded SimBroker, a ledger with the same cash, and helpers to
create intents, decisions and gate contexts from them."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from aitrader.broker.sim import SimBroker
from aitrader.clock import SimClock
from aitrader.config import RiskLimits
from aitrader.domain import (
    Currency,
    Instrument,
    InstrumentKind,
    IntentPurpose,
    Money,
    OrderIntent,
    Quote,
    RiskDecision,
    RuleResult,
    Side,
    SystemState,
    Verdict,
)
from aitrader.execution import GateContext, InMemoryOrderStore, OrderGate, OrderManager
from aitrader.portfolio import Ledger

D = Decimal
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
FX = D(155)
SPY = Instrument(symbol="SPY", kind=InstrumentKind.ETF, fractionable=True)


def quote(bid: str = "99.99", ask: str = "100.01", symbol: str = "SPY", **kw: Any) -> Quote:
    fields: dict[str, Any] = {
        "symbol": symbol,
        "ts": kw.pop("ts", NOW),
        "bid": bid,
        "ask": ask,
        "bid_size": 100,
        "ask_size": 100,
    }
    fields.update(kw)
    return Quote.model_validate(fields)


def intent(
    side: Side = Side.BUY,
    *,
    intent_id: str = "intent-1",
    purpose: IntentPurpose | None = None,
    **overrides: Any,
) -> OrderIntent:
    fields: dict[str, Any] = {
        "intent_id": intent_id,
        "strategy_id": "S1",
        "strategy_version": "1.0.0",
        "symbol": "SPY",
        "side": side,
        "purpose": purpose,
        "target_quantity": D("0.3"),
        "reason": "test",
        "signal_ts": NOW,
    }
    if side is Side.BUY:
        fields.update(limit_price=D("100.05"), stop_price=D(96))
    fields.update(overrides)
    return OrderIntent.model_validate(fields)


DEFAULT_QUANTITY = D("0.3")


def decision(
    for_intent: OrderIntent,
    quantity: Decimal = DEFAULT_QUANTITY,
    verdict: Verdict = Verdict.APPROVE,
) -> RiskDecision:
    failed = verdict is Verdict.REJECT
    return RiskDecision(
        intent_id=for_intent.intent_id,
        verdict=verdict,
        approved_quantity=D(0) if failed else quantity,
        rule_results=(RuleResult(rule_id="R-TRADE-1", passed=not failed),),
        limits_hash="test",
    )


@dataclass
class Harness:
    clock: SimClock
    broker: SimBroker
    ledger: Ledger
    store: InMemoryOrderStore
    gate: OrderGate
    manager: OrderManager

    def context(self, **overrides: Any) -> GateContext:
        positions = self.ledger.positions
        fields: dict[str, Any] = {
            "now": self.clock.now(),
            "system_state": SystemState.RUNNING,
            "market_open": True,
            "fx_jpy_per_usd": FX,
            "equity_usd": D(50_000) / FX,
            "settled_cash_usd": self.ledger.cash_usd_settled,
            "holdings_value_usd": sum((p.cost_usd for p in positions.values()), D(0)),
            "held_quantity": {s: p.quantity for s, p in positions.items()},
            "open_orders": tuple(self.store.active()),
            "allow_list": frozenset({"SPY", "QQQ"}),
            "capabilities": self.broker.capabilities(),
        }
        fields.update(overrides)
        return GateContext.model_validate(fields)

    def advance(self, **delta: float) -> None:
        self.clock.set(self.clock.now() + timedelta(**delta))

    def buy(self, quantity: str = "0.3", intent_id: str = "buy-1") -> Any:
        entry = intent(intent_id=intent_id, target_quantity=D(quantity))
        return self.manager.submit(entry, decision(entry, D(quantity)), SPY, self.context())


def harness(cash_usd: str = "300", *, not_found_after: timedelta = timedelta(minutes=2)) -> Harness:
    clock = SimClock(NOW)
    broker = SimBroker(clock)
    broker.fund(Money.of(cash_usd, Currency.USD))
    ledger = Ledger()
    ledger.deposit("fund-1", NOW, Money.of(cash_usd, Currency.USD))
    store = InMemoryOrderStore()
    gate = OrderGate(RiskLimits())
    manager = OrderManager(broker, gate, store, ledger, clock, not_found_after=not_found_after)
    return Harness(clock, broker, ledger, store, gate, manager)
