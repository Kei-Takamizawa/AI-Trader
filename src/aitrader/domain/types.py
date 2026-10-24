"""Annotated value types used by the domain models.

Money and prices are always :class:`decimal.Decimal`. Floats are rejected because they cannot
represent prices exactly. Datetimes must be timezone-aware and are normalised to UTC.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, PlainSerializer


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("booleans are not numbers")
    if isinstance(value, float):
        raise ValueError("floats are not accepted; pass a Decimal, an int or a numeric string")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal number: {value!r}") from exc
    else:
        raise ValueError(f"expected Decimal, int or str, got {type(value).__name__}")
    if not result.is_finite():
        raise ValueError("NaN and infinity are not accepted")
    return result


def _positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("must be greater than zero")
    return value


def _non_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("must not be negative")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetimes are not accepted; use a timezone-aware datetime")
    return value.astimezone(UTC)


_decimal_as_str = PlainSerializer(lambda v: str(v), return_type=str, when_used="json")

StrictDecimal = Annotated[Decimal, BeforeValidator(_to_decimal), _decimal_as_str]
"""A finite Decimal that never comes from a float."""

PositiveDecimal = Annotated[
    Decimal, BeforeValidator(_to_decimal), AfterValidator(_positive), _decimal_as_str
]
"""A finite Decimal greater than zero."""

NonNegativeDecimal = Annotated[
    Decimal, BeforeValidator(_to_decimal), AfterValidator(_non_negative), _decimal_as_str
]
"""A finite Decimal greater than or equal to zero."""

UtcDatetime = Annotated[datetime, AfterValidator(_utc)]
"""A timezone-aware datetime, normalised to UTC."""


def to_decimal(value: object) -> Decimal:
    """Convert ``value`` with the same rules as :data:`StrictDecimal`."""
    return _to_decimal(value)


def require_utc(value: datetime) -> datetime:
    """Return ``value`` in UTC; raise ``ValueError`` for naive datetimes."""
    return _utc(value)
