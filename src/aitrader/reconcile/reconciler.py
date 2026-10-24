"""Compare the engine's books with the broker's; halt on any mismatch (document 05 §10).

Nothing is ever corrected automatically: a mismatch stops new trading until the owner has
looked at the report and reset the halt.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from aitrader.broker.port import AccountSnapshot, BrokerOrder, BrokerPort, BrokerUnavailableError
from aitrader.domain import OrderStatus
from aitrader.domain.types import NonNegativeDecimal, UtcDatetime
from aitrader.execution import ExecutionEvent, OrderManager, OrderRecord, OrderStore
from aitrader.portfolio import Ledger
from aitrader.risk import HaltStateMachine, HaltTransition

HUNDRED = Decimal(100)


class Scope(StrEnum):
    FULL = "FULL"
    """Positions, open orders and cash (start of day, end of day, after a restart)."""
    ORDERS_AND_POSITIONS = "ORDERS_AND_POSITIONS"
    """The intraday check every 15 minutes."""


class Outcome(StrEnum):
    CLEAN = "CLEAN"
    MISMATCH = "MISMATCH"
    INCOMPLETE = "INCOMPLETE"
    """Some orders are still UNKNOWN inside the lookup window; no conclusion yet."""


class DifferenceKind(StrEnum):
    POSITION = "POSITION"
    CASH = "CASH"
    ORDER_UNKNOWN_LOCALLY = "ORDER_UNKNOWN_LOCALLY"
    ORDER_MISSING_AT_BROKER = "ORDER_MISSING_AT_BROKER"
    ORDER_QUANTITY = "ORDER_QUANTITY"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"


class Difference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DifferenceKind
    key: str
    local: str
    broker: str


class Tolerances(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fractional_quantity: NonNegativeDecimal = Decimal("0.000001")
    cash_usd: NonNegativeDecimal = Decimal("0.05")
    cash_jpy: NonNegativeDecimal = Decimal(5)
    cash_pct: NonNegativeDecimal = Decimal("0.1")


class ReconciliationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: UtcDatetime
    scope: Scope
    outcome: Outcome
    differences: tuple[Difference, ...] = ()
    unresolved_orders: tuple[str, ...] = ()


def quantities_match(local: Decimal, broker: Decimal, tolerances: Tolerances) -> bool:
    """Exact for whole shares, within ``fractional_quantity`` when either side is fractional."""
    if local % 1 == 0 and broker % 1 == 0:
        return local == broker
    return abs(local - broker) <= tolerances.fractional_quantity


def cash_matches(local: Decimal, broker: Decimal, absolute: Decimal, pct: Decimal) -> bool:
    return abs(local - broker) <= max(absolute, abs(broker) * pct / HUNDRED)


def compare_positions(
    local: Mapping[str, Decimal], broker: Mapping[str, Decimal], tolerances: Tolerances
) -> list[Difference]:
    differences = []
    for symbol in sorted(set(local) | set(broker)):
        mine = local.get(symbol, Decimal(0))
        theirs = broker.get(symbol, Decimal(0))
        if not quantities_match(mine, theirs, tolerances):
            differences.append(
                Difference(
                    kind=DifferenceKind.POSITION, key=symbol, local=str(mine), broker=str(theirs)
                )
            )
    return differences


def compare_cash(
    ledger: Ledger, account: AccountSnapshot, tolerances: Tolerances
) -> list[Difference]:
    pairs = (
        ("USD settled", ledger.cash_usd_settled, account.cash_usd_settled, tolerances.cash_usd),
        (
            "USD unsettled",
            ledger.cash_usd_unsettled,
            account.cash_usd_unsettled,
            tolerances.cash_usd,
        ),
        ("JPY", ledger.cash_jpy, account.cash_jpy, tolerances.cash_jpy),
    )
    return [
        Difference(kind=DifferenceKind.CASH, key=key, local=str(mine), broker=str(theirs))
        for key, mine, theirs, absolute in pairs
        if not cash_matches(mine, theirs, absolute, tolerances.cash_pct)
    ]


def compare_orders(local: Sequence[OrderRecord], broker: Sequence[BrokerOrder]) -> list[Difference]:
    """Open orders on both sides. UNKNOWN local orders are left to the lookup, not compared."""
    differences = []
    mine = {r.client_order_id: r for r in local if r.status is not OrderStatus.UNKNOWN}
    theirs = {o.client_order_id: o for o in broker}
    known_unknowns = {r.client_order_id for r in local if r.status is OrderStatus.UNKNOWN}
    for cid in sorted(set(theirs) - set(mine) - known_unknowns):
        order = theirs[cid]
        differences.append(
            Difference(
                kind=DifferenceKind.ORDER_UNKNOWN_LOCALLY,
                key=cid,
                local="none",
                broker=f"{order.side.value} {order.quantity} {order.symbol} {order.status.value}",
            )
        )
    for cid in sorted(set(mine) - set(theirs)):
        differences.append(
            Difference(
                kind=DifferenceKind.ORDER_MISSING_AT_BROKER,
                key=cid,
                local=mine[cid].status.value,
                broker="not open",
            )
        )
    for cid in sorted(set(mine) & set(theirs)):
        if mine[cid].request.quantity != theirs[cid].quantity:
            differences.append(
                Difference(
                    kind=DifferenceKind.ORDER_QUANTITY,
                    key=cid,
                    local=str(mine[cid].request.quantity),
                    broker=str(theirs[cid].quantity),
                )
            )
    return differences


class Reconciler:
    def __init__(
        self,
        *,
        broker: BrokerPort,
        manager: OrderManager,
        store: OrderStore,
        ledger: Ledger,
        halt: HaltStateMachine,
        tolerances: Tolerances | None = None,
        record: Callable[[ReconciliationReport], None] | None = None,
    ) -> None:
        self._broker = broker
        self._manager = manager
        self._store = store
        self._ledger = ledger
        self._halt = halt
        self._tolerances = tolerances or Tolerances()
        self._record = record
        self.events: list[ExecutionEvent] = []

    def run(
        self, ts: datetime, scope: Scope = Scope.FULL
    ) -> tuple[ReconciliationReport, HaltTransition | None]:
        """Sync, compare, record, then move the halt state. Returns the report and the
        transition it caused (if any)."""
        self.events.extend(self._manager.sync())
        try:
            differences = self._compare(scope)
        except BrokerUnavailableError as exc:
            differences = [
                Difference(
                    kind=DifferenceKind.BROKER_UNAVAILABLE, key="broker", local="-", broker=str(exc)
                )
            ]
        unresolved = tuple(
            r.client_order_id
            for r in self._store.active()
            if r.status in (OrderStatus.UNKNOWN, OrderStatus.PENDING_SUBMIT)
        )
        if any(d.kind is DifferenceKind.BROKER_UNAVAILABLE for d in differences):
            outcome = Outcome.INCOMPLETE
        elif differences:
            outcome = Outcome.MISMATCH
        elif unresolved:
            outcome = Outcome.INCOMPLETE
        else:
            outcome = Outcome.CLEAN
        report = ReconciliationReport(
            ts=ts,
            scope=scope,
            outcome=outcome,
            differences=tuple(differences),
            unresolved_orders=unresolved,
        )
        if self._record is not None:
            self._record(report)
        transition = None
        if outcome is Outcome.CLEAN:
            transition = self._halt.on_reconciliation(ts, clean=True, detail=scope.value)
        elif outcome is Outcome.MISMATCH:
            summary = "; ".join(
                f"{d.kind.value} {d.key}: {d.local} vs {d.broker}" for d in differences
            )
            transition = self._halt.on_reconciliation(ts, clean=False, detail=summary)
        return report, transition

    def _compare(self, scope: Scope) -> list[Difference]:
        local_positions = {s: p.quantity for s, p in self._ledger.positions.items()}
        broker_positions = {p.symbol: p.quantity for p in self._broker.positions()}
        differences = compare_positions(local_positions, broker_positions, self._tolerances)
        differences += compare_orders(self._store.active(), self._broker.open_orders())
        if scope is Scope.FULL:
            differences += compare_cash(self._ledger, self._broker.account(), self._tolerances)
        return differences
