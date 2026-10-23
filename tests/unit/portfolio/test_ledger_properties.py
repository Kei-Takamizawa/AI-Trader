"""Stateful property test: the ledger stays consistent after any sequence of operations."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from aitrader.domain import (
    Currency,
    Fill,
    FxConversion,
    FxDirection,
    Money,
    Side,
    TradeCost,
    TradeCostType,
)
from aitrader.portfolio import Account, CashAccountError, Ledger

D = Decimal
SYMBOLS = ("SPY", "QQQ", "IWM")
START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def decimals(low: str, high: str, places: int) -> st.SearchStrategy[Decimal]:
    return st.decimals(
        min_value=D(low), max_value=D(high), places=places, allow_nan=False, allow_infinity=False
    )


prices = decimals("1", "900", 2)
quantities = decimals("0.00001", "5", 5)
fees = decimals("0", "0.05", 6)
fx_rates = decimals("100", "200", 2)


class LedgerMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.ledger = Ledger()
        self.step = 0
        self.prices = {symbol: D(100) for symbol in SYMBOLS}
        self.fx = D(150)

    def _next(self) -> tuple[str, datetime]:
        self.step += 1
        return f"e{self.step}", START + timedelta(hours=self.step)

    @rule(amount=decimals("1", "100000", 0))
    def deposit_jpy(self, amount: Decimal) -> None:
        entry_id, ts = self._next()
        self.ledger.deposit(entry_id, ts, Money.of(amount, Currency.JPY))

    @rule(amount=decimals("1", "1000", 2))
    def deposit_usd(self, amount: Decimal) -> None:
        entry_id, ts = self._next()
        self.ledger.deposit(entry_id, ts, Money.of(amount, Currency.USD))

    @rule(share=decimals("0.01", "1", 2), applied=fx_rates, spread=decimals("-0.5", "0.5", 2))
    def convert_jpy_to_usd(self, share: Decimal, applied: Decimal, spread: Decimal) -> None:
        jpy = (self.ledger.cash_jpy * share).quantize(D(1))
        usd = (jpy / applied).quantize(D("0.01"))
        if jpy <= 0 or usd <= 0:
            return
        entry_id, ts = self._next()
        conversion = FxConversion(
            ts=ts,
            direction=FxDirection.JPY_TO_USD,
            jpy_amount=Money.of(jpy, Currency.JPY),
            usd_amount=Money.of(usd, Currency.USD),
            applied_rate=applied,
            reference_rate=applied - spread,
            source="statement",
        )
        self.ledger.convert_fx(entry_id, conversion)

    @rule(share=decimals("0.01", "1", 2), applied=fx_rates)
    def convert_usd_to_jpy(self, share: Decimal, applied: Decimal) -> None:
        usd = (self.ledger.cash_usd_settled * share).quantize(D("0.01"), rounding="ROUND_DOWN")
        jpy = (usd * applied).quantize(D(1), rounding="ROUND_DOWN")
        if usd <= 0 or jpy <= 0:
            return
        entry_id, ts = self._next()
        conversion = FxConversion(
            ts=ts,
            direction=FxDirection.USD_TO_JPY,
            jpy_amount=Money.of(jpy, Currency.JPY),
            usd_amount=Money.of(usd, Currency.USD),
            applied_rate=applied,
            reference_rate=applied + D("0.15"),
            source="statement",
        )
        self.ledger.convert_fx(entry_id, conversion)

    @rule(symbol=st.sampled_from(SYMBOLS), quantity=quantities, price=prices, fee=fees)
    def buy(self, symbol: str, quantity: Decimal, price: Decimal, fee: Decimal) -> None:
        entry_id, ts = self._next()
        fill = Fill(
            fill_id=entry_id,
            client_order_id=f"order-{entry_id}",
            symbol=symbol,
            side=Side.BUY,
            quantity=quantity,
            price=price,
            ts=ts,
            source="sim",
        )
        costs = [_cost(ts, TradeCostType.CAT_FEE, fee)]
        affordable = quantity * price + fee <= self.ledger.cash_usd_settled
        before = self.ledger.entries
        try:
            self.ledger.record_fill(fill, costs)
        except CashAccountError:
            assert not affordable
            assert self.ledger.entries == before  # a refused entry changes nothing
        else:
            assert affordable
            self.prices[symbol] = price

    @precondition(lambda self: bool(self.ledger.positions))
    @rule(data=st.data(), price=prices, fee=fees, fraction=decimals("0.01", "1.2", 2))
    def sell(self, data: st.DataObject, price: Decimal, fee: Decimal, fraction: Decimal) -> None:
        symbol = data.draw(st.sampled_from(sorted(self.ledger.positions)))
        held = self.ledger.positions[symbol].quantity
        quantity = min(held, (held * fraction).quantize(D("0.00001")))
        if fraction > 1:
            quantity = held + D("0.00001")  # an attempted short sale
        if quantity <= 0:
            return
        entry_id, ts = self._next()
        fill = Fill(
            fill_id=entry_id,
            client_order_id=f"order-{entry_id}",
            symbol=symbol,
            side=Side.SELL,
            quantity=quantity,
            price=price,
            ts=ts,
            source="sim",
        )
        costs = [_cost(ts, TradeCostType.FINRA_FEE, fee), _cost(ts, TradeCostType.SEC_FEE, fee)]
        try:
            self.ledger.record_fill(fill, costs, settles_on=ts.date() + timedelta(days=1))
        except CashAccountError:
            assert quantity > held or 2 * fee > quantity * price + self.ledger.cash_usd_settled
        else:
            assert quantity <= held
            self.prices[symbol] = price

    @rule(days=st.integers(min_value=0, max_value=3))
    def settle(self, days: int) -> None:
        _, ts = self._next()
        as_of: date = ts.date() + timedelta(days=days)
        self.ledger.settle(as_of, ts)

    @rule(symbol=st.sampled_from(SYMBOLS), gross=decimals("0.01", "5", 2))
    def dividend(self, symbol: str, gross: Decimal) -> None:
        entry_id, ts = self._next()
        us = (gross * D("0.1")).quantize(D("0.01"))
        self.ledger.record_dividend(
            entry_id,
            ts,
            symbol,
            gross=Money.of(gross, Currency.USD),
            us_withholding=Money.of(us, Currency.USD),
        )

    @rule(fx=fx_rates)
    def move_fx(self, fx: Decimal) -> None:
        self.fx = fx

    # ------------------------------------------------------------------ invariants

    @invariant()
    def trial_balance_is_zero_in_each_currency(self) -> None:
        assert all(total == 0 for total in self.ledger.trial_balance().values())

    @invariant()
    def cash_is_never_negative(self) -> None:
        assert self.ledger.cash_usd_settled >= 0
        assert self.ledger.cash_usd_unsettled >= 0
        assert self.ledger.cash_jpy >= 0

    @invariant()
    def positions_match_the_securities_account(self) -> None:
        for symbol in SYMBOLS:
            position = self.ledger.position(symbol)
            cost = position.cost_usd if position else D(0)
            assert self.ledger.balance(Account.SECURITIES, Currency.USD, symbol) == cost
            if position is not None:
                assert position.quantity > 0
                assert position.cost_usd > 0

    @invariant()
    def unsettled_cash_equals_pending_settlements(self) -> None:
        pending = sum((p.amount_usd for p in self.ledger.pending_settlements), D(0))
        assert self.ledger.cash_usd_unsettled == pending

    @invariant()
    def equity_equals_capital_plus_trading_pnl(self) -> None:
        valuation = self.ledger.valuation(self.prices, self.fx)
        pnl = self.ledger.trading_pnl(self.prices, self.fx)
        assert valuation.equity_jpy == pnl.capital_jpy_equivalent + pnl.trading_pnl_jpy

    @invariant()
    def replaying_the_entries_gives_the_same_ledger(self) -> None:
        if self.step % 5 == 0:
            assert Ledger.from_entries(self.ledger.entries) == self.ledger


def _cost(ts: datetime, kind: TradeCostType, amount: Decimal) -> TradeCost:
    return TradeCost(
        ts=ts, type=kind, amount=Money.of(amount, Currency.USD), estimated=True, source="model"
    )


LedgerMachine.TestCase.settings = settings(max_examples=150, stateful_step_count=40, deadline=None)
TestLedgerProperties = LedgerMachine.TestCase
