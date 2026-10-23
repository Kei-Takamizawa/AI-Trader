"""The trading ledger: cash by currency, settlement, positions, fees, FX and dividends.

The ledger changes only by applying journal entries, so it can always be rebuilt from them.
Recording the same entry twice is a no-op; a different entry under a used ID is an error.
Cash-account rules are checked before an entry is accepted; a rejected entry leaves the ledger
unchanged.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

from aitrader.domain import (
    Currency,
    Fill,
    FxConversion,
    FxDirection,
    Money,
    Side,
    TradeCost,
)
from aitrader.portfolio.journal import (
    CASH_ACCOUNTS,
    Account,
    EntryKind,
    JournalEntry,
    PositionChange,
    Posting,
)

COST_PRECISION = Decimal("1E-10")
"""Cost removed by a partial sale is rounded down to this, which keeps every sum exact."""

USD = Currency.USD
JPY = Currency.JPY


class LedgerError(Exception):
    """An entry was refused. The ledger is unchanged."""


class DuplicateEntryError(LedgerError):
    pass


class CashAccountError(LedgerError):
    """Negative cash or a short position: not possible in a cash account."""


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: Decimal
    cost_usd: Decimal

    @property
    def average_cost(self) -> Decimal:
        return self.cost_usd / self.quantity


@dataclass(frozen=True)
class PendingSettlement:
    sell_entry_id: str
    settles_on: date
    amount_usd: Decimal


@dataclass(frozen=True)
class Valuation:
    fx_jpy_per_usd: Decimal
    cash_usd_settled: Decimal
    cash_usd_unsettled: Decimal
    cash_jpy: Decimal
    market_value_usd: Decimal
    cost_basis_usd: Decimal

    @property
    def unrealized_pnl_usd(self) -> Decimal:
        return self.market_value_usd - self.cost_basis_usd

    @property
    def equity_usd(self) -> Decimal:
        """USD cash and positions (JPY cash not included)."""
        return self.cash_usd_settled + self.cash_usd_unsettled + self.market_value_usd

    @property
    def equity_jpy(self) -> Decimal:
        """Everything, in JPY at ``fx_jpy_per_usd``."""
        return self.cash_jpy + self.equity_usd * self.fx_jpy_per_usd


@dataclass(frozen=True)
class TradingPnl:
    """Cumulative trading result, by source. Operating costs are not part of it."""

    fx_jpy_per_usd: Decimal
    realized_usd: Decimal
    unrealized_usd: Decimal
    dividends_usd: Decimal
    withholding_usd: Decimal
    fees_by_type_usd: Mapping[str, Decimal]
    fx_cost_jpy: Decimal
    bank_fees_jpy: Decimal
    fx_translation_jpy: Decimal
    """Change in the JPY value of converted USD since conversion (reference rates)."""
    capital_jpy: Decimal
    capital_usd: Decimal
    """Net deposits by currency."""

    @property
    def fees_usd(self) -> Decimal:
        return sum(self.fees_by_type_usd.values(), Decimal(0))

    @property
    def trading_pnl_usd(self) -> Decimal:
        return (
            self.realized_usd
            + self.unrealized_usd
            + self.dividends_usd
            - self.withholding_usd
            - self.fees_usd
        )

    @property
    def trading_pnl_jpy(self) -> Decimal:
        return (
            self.trading_pnl_usd * self.fx_jpy_per_usd
            - self.fx_cost_jpy
            - self.bank_fees_jpy
            + self.fx_translation_jpy
        )

    @property
    def capital_jpy_equivalent(self) -> Decimal:
        return self.capital_jpy + self.capital_usd * self.fx_jpy_per_usd


_Key = tuple[Account, str, Currency]


@dataclass
class Ledger:
    _entries: list[JournalEntry] = field(default_factory=list)
    _by_id: dict[str, JournalEntry] = field(default_factory=dict)
    _balances: dict[_Key, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    _positions: dict[str, Position] = field(default_factory=dict)
    _pending: dict[str, PendingSettlement] = field(default_factory=dict)

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_entries(cls, entries: Iterable[JournalEntry]) -> Ledger:
        ledger = cls()
        for entry in entries:
            ledger.apply(entry)
        return ledger

    def as_of(self, ts: datetime) -> Ledger:
        """The ledger as it was at ``ts`` (entries up to and including ``ts``)."""
        return Ledger.from_entries(e for e in self._entries if e.ts <= ts)

    # ------------------------------------------------------------------ queries

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def entry(self, entry_id: str) -> JournalEntry | None:
        return self._by_id.get(entry_id)

    def balance(self, account: Account, currency: Currency, detail: str | None = None) -> Decimal:
        return sum(
            (
                value
                for (acc, det, cur), value in self._balances.items()
                if acc is account and cur is currency and (detail is None or det == detail)
            ),
            Decimal(0),
        )

    def balances(self) -> dict[_Key, Decimal]:
        return {key: value for key, value in self._balances.items() if value != 0}

    def trial_balance(self) -> dict[Currency, Decimal]:
        """Sum of all balances per currency. Always zero in a consistent ledger."""
        totals: dict[Currency, Decimal] = {currency: Decimal(0) for currency in Currency}
        for (_, _, currency), value in self._balances.items():
            totals[currency] += value
        return totals

    @property
    def cash_usd_settled(self) -> Decimal:
        return self.balance(Account.CASH_USD_SETTLED, USD)

    @property
    def cash_usd_unsettled(self) -> Decimal:
        return self.balance(Account.CASH_USD_UNSETTLED, USD)

    @property
    def cash_jpy(self) -> Decimal:
        return self.balance(Account.CASH_JPY, JPY)

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    @property
    def pending_settlements(self) -> tuple[PendingSettlement, ...]:
        return tuple(sorted(self._pending.values(), key=lambda p: (p.settles_on, p.sell_entry_id)))

    # ------------------------------------------------------------------ applying entries

    def apply(self, entry: JournalEntry) -> JournalEntry:
        existing = self._by_id.get(entry.entry_id)
        if existing is not None:
            if existing == entry:
                return existing
            raise DuplicateEntryError(f"entry ID {entry.entry_id!r} is already used")
        if self._entries and entry.ts < self._entries[-1].ts:
            raise LedgerError("entries must be applied in time order")
        self._check_settlement_links(entry)

        deltas: dict[_Key, Decimal] = defaultdict(Decimal)
        for posting in entry.postings:
            deltas[(posting.account, posting.detail, posting.amount.currency)] += (
                posting.amount.amount
            )
        for account in CASH_ACCOUNTS:
            currency = JPY if account is Account.CASH_JPY else USD
            before = self.balance(account, currency)
            change = sum(
                (v for (a, _, c), v in deltas.items() if a is account and c is currency),
                Decimal(0),
            )
            if before + change < 0:
                raise CashAccountError(
                    f"{account.value} would become {before + change} (cash accounts never go "
                    "negative)"
                )
        new_position = self._position_after(entry.position_change)

        # commit
        for key, value in deltas.items():
            self._balances[key] += value
        if entry.position_change is not None:
            symbol = entry.position_change.symbol
            if new_position is None:
                self._positions.pop(symbol, None)
            else:
                self._positions[symbol] = new_position
        if entry.kind is EntryKind.SELL and entry.settles_on is not None:
            amount = entry.total(Account.CASH_USD_UNSETTLED, USD)
            if amount > 0:
                self._pending[entry.entry_id] = PendingSettlement(
                    entry.entry_id, entry.settles_on, amount
                )
        if entry.kind is EntryKind.SETTLEMENT:
            del self._pending[entry.ref]
        self._entries.append(entry)
        self._by_id[entry.entry_id] = entry
        return entry

    def _check_settlement_links(self, entry: JournalEntry) -> None:
        unsettled = entry.total(Account.CASH_USD_UNSETTLED, USD)
        if entry.kind is EntryKind.SETTLEMENT:
            pending = self._pending.get(entry.ref)
            if pending is None:
                raise LedgerError(f"no pending settlement for {entry.ref!r}")
            if unsettled != -pending.amount_usd:
                raise LedgerError("a settlement must move exactly the pending amount")
        elif entry.kind is EntryKind.SELL:
            if unsettled < 0:
                raise LedgerError("a sale cannot reduce unsettled cash")
        elif unsettled != 0:
            raise LedgerError("only sales and settlements change unsettled cash")

    def _position_after(self, change: PositionChange | None) -> Position | None:
        if change is None:
            return None
        current = self._positions.get(change.symbol)
        quantity = (current.quantity if current else Decimal(0)) + change.quantity
        cost = (current.cost_usd if current else Decimal(0)) + change.cost_usd
        if quantity < 0:
            raise CashAccountError(f"selling more {change.symbol} than held (no short sales)")
        if cost < 0 or (quantity == 0 and cost != 0) or (quantity > 0 and cost <= 0):
            raise LedgerError(f"inconsistent cost basis for {change.symbol}: {cost}")
        if quantity == 0:
            return None
        return Position(change.symbol, quantity, cost)

    # ------------------------------------------------------------------ recording

    def deposit(self, entry_id: str, ts: datetime, amount: Money, ref: str = "") -> JournalEntry:
        if amount.amount <= 0:
            raise ValueError("a deposit must be positive")
        cash = Account.CASH_JPY if amount.currency is JPY else Account.CASH_USD_SETTLED
        return self.apply(
            JournalEntry(
                entry_id=entry_id,
                ts=ts,
                kind=EntryKind.DEPOSIT,
                ref=ref,
                postings=(
                    Posting(account=cash, amount=amount),
                    Posting(account=Account.OWNER_CAPITAL, amount=-amount),
                ),
            )
        )

    def withdraw(
        self,
        entry_id: str,
        ts: datetime,
        amount: Money,
        bank_fee: Money | None = None,
        ref: str = "",
    ) -> JournalEntry:
        """Take JPY out of the account. The bank fee is paid on top of ``amount``."""
        if amount.currency is not JPY or amount.amount <= 0:
            raise ValueError("withdrawals are positive JPY amounts")
        fee = bank_fee or Money.zero(JPY)
        if fee.currency is not JPY or fee.is_negative:
            raise ValueError("the bank fee must be a non-negative JPY amount")
        postings = [
            Posting(account=Account.CASH_JPY, amount=-(amount + fee)),
            Posting(account=Account.OWNER_CAPITAL, amount=amount),
        ]
        if not fee.is_zero:
            postings.append(Posting(account=Account.BANK_FEES, amount=fee))
        return self.apply(
            JournalEntry(
                entry_id=entry_id,
                ts=ts,
                kind=EntryKind.WITHDRAWAL,
                ref=ref,
                postings=tuple(postings),
            )
        )

    def convert_fx(self, entry_id: str, conversion: FxConversion, ref: str = "") -> JournalEntry:
        """Book a conversion from statement amounts; the cost against the reference rate is
        posted to FX_COST (negative when the applied rate was better than the reference)."""
        jpy = conversion.jpy_amount.amount
        usd = conversion.usd_amount.amount
        at_reference = usd * conversion.reference_rate
        if conversion.direction is FxDirection.JPY_TO_USD:
            spread = jpy - at_reference
            postings = [
                Posting(account=Account.CASH_JPY, amount=Money.of(-jpy, JPY)),
                Posting(account=Account.FX_CLEARING, amount=Money.of(at_reference, JPY)),
                Posting(account=Account.FX_CLEARING, amount=Money.of(-usd, USD)),
                Posting(account=Account.CASH_USD_SETTLED, amount=Money.of(usd, USD)),
            ]
        else:
            spread = at_reference - jpy
            postings = [
                Posting(account=Account.CASH_USD_SETTLED, amount=Money.of(-usd, USD)),
                Posting(account=Account.FX_CLEARING, amount=Money.of(usd, USD)),
                Posting(account=Account.FX_CLEARING, amount=Money.of(-at_reference, JPY)),
                Posting(account=Account.CASH_JPY, amount=Money.of(jpy, JPY)),
            ]
        if spread != 0:
            postings.append(Posting(account=Account.FX_COST, amount=Money.of(spread, JPY)))
        return self.apply(
            JournalEntry(
                entry_id=entry_id,
                ts=conversion.ts,
                kind=EntryKind.FX_CONVERSION,
                ref=ref or conversion.source,
                postings=tuple(postings),
            )
        )

    def record_fill(
        self, fill: Fill, costs: Sequence[TradeCost] = (), *, settles_on: date | None = None
    ) -> JournalEntry:
        """Book one execution and its trading costs (one fee line per cost item).

        Buys are paid from settled USD. Sale proceeds, net of the sale's fees, wait in
        unsettled USD until ``settles_on``; fees larger than the proceeds come from settled USD.
        """
        entry_id = f"fill:{fill.fill_id}"
        fee_postings = []
        for cost in costs:
            if cost.amount.currency is not USD:
                raise ValueError("trading costs of a fill must be in USD")
            if cost.client_order_id not in (None, fill.client_order_id):
                raise ValueError("a cost belongs to a different order")
            if not cost.amount.is_zero:
                fee_postings.append(
                    Posting(
                        account=Account.TRADING_FEES, amount=cost.amount, detail=cost.type.value
                    )
                )
        fees = sum((p.amount.amount for p in fee_postings), Decimal(0))
        existing = self._by_id.get(entry_id)
        if existing is not None:
            return self._same_fill(existing, fill, fee_postings, settles_on)

        notional = fill.quantity * fill.price
        symbol = fill.symbol
        if fill.side is Side.BUY:
            if settles_on is not None:
                raise ValueError("buys are paid at once; settles_on applies to sales only")
            postings = [
                Posting(account=Account.SECURITIES, amount=Money.of(notional, USD), detail=symbol),
                Posting(account=Account.CASH_USD_SETTLED, amount=Money.of(-(notional + fees), USD)),
                *fee_postings,
            ]
            change = PositionChange(symbol=symbol, quantity=fill.quantity, cost_usd=notional)
            kind = EntryKind.BUY
        else:
            if settles_on is None:
                raise ValueError("a sale needs its settlement date")
            held = self._positions.get(symbol)
            if held is None or fill.quantity > held.quantity:
                raise CashAccountError(f"selling more {symbol} than held (no short sales)")
            if fill.quantity == held.quantity:
                cost_removed = held.cost_usd
            else:
                cost_removed = (held.cost_usd * fill.quantity / held.quantity).quantize(
                    COST_PRECISION, rounding=ROUND_DOWN
                )
            realized = notional - cost_removed
            net = notional - fees
            postings = [
                Posting(
                    account=Account.SECURITIES, amount=Money.of(-cost_removed, USD), detail=symbol
                ),
                *fee_postings,
            ]
            if net > 0:
                postings.append(
                    Posting(account=Account.CASH_USD_UNSETTLED, amount=Money.of(net, USD))
                )
            elif net < 0:
                postings.append(
                    Posting(account=Account.CASH_USD_SETTLED, amount=Money.of(net, USD))
                )
            if realized != 0:
                postings.append(
                    Posting(
                        account=Account.REALIZED_PNL, amount=Money.of(-realized, USD), detail=symbol
                    )
                )
            change = PositionChange(symbol=symbol, quantity=-fill.quantity, cost_usd=-cost_removed)
            kind = EntryKind.SELL
        return self.apply(
            JournalEntry(
                entry_id=entry_id,
                ts=fill.ts,
                kind=kind,
                ref=fill.client_order_id,
                postings=tuple(postings),
                position_change=change,
                settles_on=settles_on,
                fill=fill,
            )
        )

    @staticmethod
    def _same_fill(
        existing: JournalEntry,
        fill: Fill,
        fee_postings: list[Posting],
        settles_on: date | None,
    ) -> JournalEntry:
        recorded_fees = sorted(
            (p for p in existing.postings if p.account is Account.TRADING_FEES),
            key=lambda p: (p.detail, p.amount.amount),
        )
        new_fees = sorted(fee_postings, key=lambda p: (p.detail, p.amount.amount))
        if existing.fill != fill or recorded_fees != new_fees or existing.settles_on != settles_on:
            raise DuplicateEntryError(f"fill {fill.fill_id!r} is already recorded differently")
        return existing

    def settle(self, as_of: date, ts: datetime) -> list[JournalEntry]:
        """Move every sale due on or before ``as_of`` from unsettled to settled USD."""
        settled = []
        for pending in self.pending_settlements:
            if pending.settles_on > as_of:
                continue
            amount = Money.of(pending.amount_usd, USD)
            settled.append(
                self.apply(
                    JournalEntry(
                        entry_id=f"settle:{pending.sell_entry_id}",
                        ts=ts,
                        kind=EntryKind.SETTLEMENT,
                        ref=pending.sell_entry_id,
                        postings=(
                            Posting(account=Account.CASH_USD_SETTLED, amount=amount),
                            Posting(account=Account.CASH_USD_UNSETTLED, amount=-amount),
                        ),
                    )
                )
            )
        return settled

    def record_dividend(
        self,
        entry_id: str,
        ts: datetime,
        symbol: str,
        gross: Money,
        us_withholding: Money | None = None,
        jp_withholding: Money | None = None,
        ref: str = "",
    ) -> JournalEntry:
        """Book a cash dividend: gross income, taxes withheld, and the net amount received."""
        taxes = {
            "US": us_withholding or Money.zero(USD),
            "JP": jp_withholding or Money.zero(USD),
        }
        if gross.currency is not USD or gross.amount <= 0:
            raise ValueError("a dividend is a positive USD amount")
        if any(t.currency is not USD or t.is_negative for t in taxes.values()):
            raise ValueError("withholding taxes are non-negative USD amounts")
        net = gross - taxes["US"] - taxes["JP"]
        if net.is_negative:
            raise ValueError("withholding cannot exceed the dividend")
        postings = [Posting(account=Account.DIVIDEND_INCOME, amount=-gross, detail=symbol)]
        if not net.is_zero:
            postings.append(Posting(account=Account.CASH_USD_SETTLED, amount=net))
        postings.extend(
            Posting(account=Account.WITHHOLDING_TAX, amount=tax, detail=country)
            for country, tax in taxes.items()
            if not tax.is_zero
        )
        return self.apply(
            JournalEntry(
                entry_id=entry_id,
                ts=ts,
                kind=EntryKind.DIVIDEND,
                ref=ref,
                postings=tuple(postings),
            )
        )

    def record_cost(self, entry_id: str, cost: TradeCost) -> JournalEntry:
        """Book a trading cost that is not part of a fill (for example an ADR fee)."""
        if cost.amount.currency is not USD or cost.amount.is_zero:
            raise ValueError("a trading cost is a positive USD amount")
        return self.apply(
            JournalEntry(
                entry_id=entry_id,
                ts=cost.ts,
                kind=EntryKind.COST,
                ref=cost.client_order_id or cost.source,
                postings=(
                    Posting(
                        account=Account.TRADING_FEES, amount=cost.amount, detail=cost.type.value
                    ),
                    Posting(account=Account.CASH_USD_SETTLED, amount=-cost.amount),
                ),
            )
        )

    # ------------------------------------------------------------------ valuation and P&L

    def valuation(self, prices: Mapping[str, Decimal], fx_jpy_per_usd: Decimal) -> Valuation:
        """Mark positions to ``prices`` (USD per share). Every held symbol needs a price."""
        if fx_jpy_per_usd <= 0:
            raise ValueError("the FX rate must be positive")
        missing = sorted(set(self._positions) - set(prices))
        if missing:
            raise LedgerError(f"no price for {', '.join(missing)}")
        market_value = sum(
            (pos.quantity * prices[pos.symbol] for pos in self._positions.values()), Decimal(0)
        )
        cost_basis = sum((pos.cost_usd for pos in self._positions.values()), Decimal(0))
        return Valuation(
            fx_jpy_per_usd=fx_jpy_per_usd,
            cash_usd_settled=self.cash_usd_settled,
            cash_usd_unsettled=self.cash_usd_unsettled,
            cash_jpy=self.cash_jpy,
            market_value_usd=market_value,
            cost_basis_usd=cost_basis,
        )

    def trading_pnl(self, prices: Mapping[str, Decimal], fx_jpy_per_usd: Decimal) -> TradingPnl:
        valuation = self.valuation(prices, fx_jpy_per_usd)
        fees_by_type: dict[str, Decimal] = defaultdict(Decimal)
        for (account, detail, _), value in self._balances.items():
            if account is Account.TRADING_FEES and value != 0:
                fees_by_type[detail] += value
        usd_converted = -self.balance(Account.FX_CLEARING, USD)
        return TradingPnl(
            fx_jpy_per_usd=fx_jpy_per_usd,
            realized_usd=-self.balance(Account.REALIZED_PNL, USD),
            unrealized_usd=valuation.unrealized_pnl_usd,
            dividends_usd=-self.balance(Account.DIVIDEND_INCOME, USD),
            withholding_usd=self.balance(Account.WITHHOLDING_TAX, USD),
            fees_by_type_usd=dict(fees_by_type),
            fx_cost_jpy=self.balance(Account.FX_COST, JPY),
            bank_fees_jpy=self.balance(Account.BANK_FEES, JPY),
            fx_translation_jpy=usd_converted * fx_jpy_per_usd
            - self.balance(Account.FX_CLEARING, JPY),
            capital_jpy=-self.balance(Account.OWNER_CAPITAL, JPY),
            capital_usd=-self.balance(Account.OWNER_CAPITAL, USD),
        )
