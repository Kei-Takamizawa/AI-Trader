"""Chart of accounts and journal entries for the double-entry trading ledger.

Postings are signed: debits are positive and credits negative. Asset accounts therefore carry
positive balances, and income accounts (realised gains, dividends) carry negative ones. Every
entry balances in each currency on its own.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aitrader.domain import Currency, Fill, Money, Side
from aitrader.domain.market import Symbol
from aitrader.domain.types import StrictDecimal, UtcDatetime


class Account(StrEnum):
    CASH_USD_SETTLED = "CASH_USD_SETTLED"
    CASH_USD_UNSETTLED = "CASH_USD_UNSETTLED"
    """Sale proceeds waiting for settlement (T+1); never used for buys."""
    CASH_JPY = "CASH_JPY"
    SECURITIES = "SECURITIES"
    """Positions at moving-average cost, in USD; ``detail`` is the symbol."""
    FX_CLEARING = "FX_CLEARING"
    """Both legs of every currency conversion at the reference rate."""
    OWNER_CAPITAL = "OWNER_CAPITAL"
    REALIZED_PNL = "REALIZED_PNL"
    """Gains (credits) and losses (debits) on sales, before fees; ``detail`` is the symbol."""
    DIVIDEND_INCOME = "DIVIDEND_INCOME"
    WITHHOLDING_TAX = "WITHHOLDING_TAX"
    """``detail`` is the country that withheld the tax (``US`` or ``JP``)."""
    TRADING_FEES = "TRADING_FEES"
    """``detail`` is the fee type (``CAT_FEE``, ``FINRA_FEE``, ...)."""
    FX_COST = "FX_COST"
    """Conversion cost against the reference rate, in JPY."""
    BANK_FEES = "BANK_FEES"
    """Withdrawal fees, in JPY."""


ACCOUNT_CURRENCY: dict[Account, Currency] = {
    Account.CASH_USD_SETTLED: Currency.USD,
    Account.CASH_USD_UNSETTLED: Currency.USD,
    Account.CASH_JPY: Currency.JPY,
    Account.SECURITIES: Currency.USD,
    Account.REALIZED_PNL: Currency.USD,
    Account.DIVIDEND_INCOME: Currency.USD,
    Account.WITHHOLDING_TAX: Currency.USD,
    Account.TRADING_FEES: Currency.USD,
    Account.FX_COST: Currency.JPY,
    Account.BANK_FEES: Currency.JPY,
}
"""Accounts restricted to one currency. Owner capital and FX clearing take either."""

CASH_ACCOUNTS = (Account.CASH_USD_SETTLED, Account.CASH_USD_UNSETTLED, Account.CASH_JPY)


class EntryKind(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FX_CONVERSION = "FX_CONVERSION"
    BUY = "BUY"
    SELL = "SELL"
    SETTLEMENT = "SETTLEMENT"
    DIVIDEND = "DIVIDEND"
    COST = "COST"


class Posting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account: Account
    amount: Money
    detail: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.amount.is_zero:
            raise ValueError("a posting must not be zero")
        required = ACCOUNT_CURRENCY.get(self.account)
        if required is not None and self.amount.currency is not required:
            raise ValueError(f"{self.account.value} is kept in {required.value}")
        return self


class PositionChange(BaseModel):
    """How a BUY or SELL changes one position: shares and cost basis (USD), both signed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    quantity: StrictDecimal
    cost_usd: StrictDecimal


class JournalEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str = Field(min_length=1)
    ts: UtcDatetime
    kind: EntryKind
    postings: tuple[Posting, ...] = Field(min_length=2)
    ref: str = ""
    """Client order ID, statement reference or, for a settlement, the SELL entry it settles."""
    position_change: PositionChange | None = None
    settles_on: date | None = None
    """For a SELL: the date its unsettled proceeds settle."""
    fill: Fill | None = None
    """For a BUY or SELL: the execution that was booked (audit trail and idempotency)."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        totals: dict[Currency, Decimal] = defaultdict(Decimal)
        for posting in self.postings:
            totals[posting.amount.currency] += posting.amount.amount
        unbalanced = {c.value: str(v) for c, v in totals.items() if v != 0}
        if unbalanced:
            raise ValueError(f"entry does not balance: {unbalanced}")
        trade = self.kind in (EntryKind.BUY, EntryKind.SELL)
        if trade != (self.position_change is not None) or trade != (self.fill is not None):
            raise ValueError(
                "BUY and SELL entries, and only those, carry a fill and a position change"
            )
        change, fill = self.position_change, self.fill
        if change is None or fill is None:
            if any(p.account is Account.SECURITIES for p in self.postings):
                raise ValueError("only BUY and SELL entries post to SECURITIES")
        else:
            self._check_trade(change, fill)
        if (self.kind is EntryKind.SELL) != (self.settles_on is not None):
            raise ValueError("SELL entries, and only those, carry a settlement date")
        if self.kind is EntryKind.SETTLEMENT and not self.ref:
            raise ValueError("a SETTLEMENT entry must reference the SELL entry it settles")
        return self

    def _check_trade(self, change: PositionChange, fill: Fill) -> None:
        buy = self.kind is EntryKind.BUY
        if (fill.side is Side.BUY) != buy or fill.symbol != change.symbol:
            raise ValueError("the fill does not match the entry kind or symbol")
        if change.quantity != (fill.quantity if buy else -fill.quantity):
            raise ValueError("the position change must equal the filled quantity")
        if any(
            p.account is Account.SECURITIES and p.detail != change.symbol for p in self.postings
        ):
            raise ValueError("an entry changes at most one position")
        securities = sum(
            (p.amount.amount for p in self.postings if p.account is Account.SECURITIES),
            Decimal(0),
        )
        if securities != change.cost_usd:
            raise ValueError("SECURITIES postings must equal the position's cost change")

    def total(self, account: Account, currency: Currency) -> Decimal:
        return sum(
            (
                p.amount.amount
                for p in self.postings
                if p.account is account and p.amount.currency is currency
            ),
            Decimal(0),
        )
