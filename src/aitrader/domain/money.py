"""Currency-aware money amounts."""

from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict

from aitrader.domain.errors import CurrencyMismatchError
from aitrader.domain.types import StrictDecimal, to_decimal


class Currency(StrEnum):
    USD = "USD"
    JPY = "JPY"


class Money(BaseModel):
    """An exact amount in one currency. Arithmetic is only allowed between equal currencies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: StrictDecimal
    currency: Currency

    @classmethod
    def of(cls, amount: Decimal | int | str, currency: Currency) -> Self:
        return cls(amount=to_decimal(amount), currency=currency)

    @classmethod
    def zero(cls, currency: Currency) -> Self:
        return cls(amount=Decimal(0), currency=currency)

    def _check(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency is not self.currency:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency.value} with {other.currency.value}"
            )

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(amount=-self.amount, currency=self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(amount=self.amount * to_decimal(factor), currency=self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def __str__(self) -> str:
        return f"{self.amount} {self.currency.value}"
