"""Recover corporate actions and cache histories using only actions already known at close."""

from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from fractions import Fraction
from itertools import zip_longest

from pydantic import BaseModel, ConfigDict

from aitrader.data.bars import DailyBarSource, DataError
from aitrader.domain import Bar
from aitrader.domain.types import NonNegativeDecimal, PositiveDecimal

RATIO_TOLERANCE = Decimal("0.0005")
MINOR_ADJUSTMENT_LIMIT = Decimal("0.002")
SPLIT_THRESHOLD = Decimal("0.2")
_SPLIT_RATIOS = tuple(
    sorted(
        {Fraction(n, 1) for n in range(2, 101)}
        | {Fraction(1, n) for n in range(2, 101)}
        | {Fraction(p, q) for p in range(1, 11) for q in range(1, 11)}
    )
)


class CorporateActionKind(StrEnum):
    DISTRIBUTION = "distribution"
    SPLIT = "split"
    UNEXPLAINED = "unexplained"


class CorporateAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    session: date
    kind: CorporateActionKind
    factor: PositiveDecimal
    cash_per_share: NonNegativeDecimal = Decimal(0)
    split_ratio: tuple[int, int] | None = None


class CorporateActionError(DataError):
    """An action cannot safely be applied to a held or pending position."""


def detect_corporate_actions(
    symbol: str,
    raw: Sequence[tuple[date, Decimal]],
    adjusted: Sequence[tuple[date, Decimal]],
) -> tuple[CorporateAction, ...]:
    """Infer actions from matched closes; cash amounts are estimates, not declared dividends."""
    adjusted_by_session = dict(adjusted)
    previous_ratio: Decimal | None = None
    previous_close = Decimal(0)
    events = []
    for session, close in raw:
        other = adjusted_by_session.get(session)
        if other is None:
            continue
        ratio = close / other
        if previous_ratio is not None:
            factor = ratio / previous_ratio
            if abs(factor - 1) > RATIO_TOLERANCE:
                split_ratio = None
                if abs(factor - 1) > SPLIT_THRESHOLD:
                    # Vendor price rounding must not turn exact share ratios into drift.
                    detected_ratio = Fraction(factor)
                    tolerance = Fraction(RATIO_TOLERANCE)
                    match = next(
                        (
                            candidate
                            for candidate in _SPLIT_RATIOS
                            if abs(candidate / detected_ratio - 1) <= tolerance
                        ),
                        None,
                    )
                    if match is None:
                        kind = CorporateActionKind.UNEXPLAINED
                    else:
                        kind = CorporateActionKind.SPLIT
                        split_ratio = (match.numerator, match.denominator)
                        factor = Decimal(match.numerator) / Decimal(match.denominator)
                elif factor < 1:
                    kind = CorporateActionKind.DISTRIBUTION
                else:
                    kind = CorporateActionKind.UNEXPLAINED
                cash = (
                    (previous_close * (1 - factor)).quantize(
                        Decimal("0.000001"), rounding=ROUND_HALF_EVEN
                    )
                    if kind is CorporateActionKind.DISTRIBUTION
                    else Decimal(0)
                )
                events.append(
                    CorporateAction(
                        symbol=symbol,
                        session=session,
                        kind=kind,
                        factor=factor,
                        cash_per_share=cash,
                        split_ratio=split_ratio,
                    )
                )
        previous_ratio, previous_close = ratio, close
    return tuple(events)


class PointInTimeBars:
    """Cache one raw-derived series per action segment, never rescale a rounded series."""

    def __init__(self, raw: DailyBarSource, adjusted: DailyBarSource | None) -> None:
        self._raw = raw
        self.snapshot_hash = raw.snapshot_hash
        self.signal_snapshot_hash = adjusted.snapshot_hash if adjusted is not None else None
        self._actions: dict[str, tuple[CorporateAction, ...]] = {}
        self._on: dict[date, list[CorporateAction]] = {}
        self._cache: dict[tuple[str, int], tuple[Bar, ...]] = {}
        for symbol in sorted(set(raw.symbols) | set(adjusted.symbols if adjusted else ())):
            bars = raw.bars(symbol)
            if adjusted is not None:
                others = adjusted.bars(symbol)
                for bar, other in zip_longest(bars, others):
                    if bar is None or other is None or bar.ts != other.ts:
                        sessions = [b.ts.date() for b in (bar, other) if b is not None]
                        raise DataError(f"{symbol}: raw/adjusted differ at session {min(sessions)}")
                if symbol not in raw.symbols or symbol not in adjusted.symbols:
                    raise DataError(f"{symbol}: raw/adjusted symbol missing; first session: none")
                events = detect_corporate_actions(
                    symbol,
                    [(b.ts.date(), b.close) for b in bars],
                    [(b.ts.date(), b.close) for b in others],
                )
            else:
                events = ()
            self._actions[symbol] = events
            for event in events:
                self._on.setdefault(event.session, []).append(event)

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._raw.symbols

    def actions(self, symbol: str) -> tuple[CorporateAction, ...]:
        return self._actions.get(symbol, ())

    def actions_on(self, session: date) -> tuple[CorporateAction, ...]:
        return tuple(self._on.get(session, ()))

    def history(self, symbol: str, as_of: datetime) -> tuple[Bar, ...]:
        visible = self._raw.history(symbol, as_of)
        if not visible:
            return visible
        events = self.actions(symbol)
        count = bisect_right([e.session for e in events], visible[-1].ts.date())
        if count == 0:
            return visible
        key = (symbol, count)
        if key not in self._cache:
            active = events[:count]
            price_factor = Decimal(1)
            split_volume_multiplier = Fraction(1)
            index = len(active) - 1
            scaled = []
            for bar in reversed(self._raw.bars(symbol)):
                while index >= 0 and bar.ts.date() < active[index].session:
                    event = active[index]
                    price_factor *= event.factor
                    if event.split_ratio is not None:
                        p, q = event.split_ratio
                        split_volume_multiplier *= Fraction(q, p)
                    index -= 1
                if bar.ts.date() >= active[-1].session:
                    scaled.append(bar)
                else:
                    scaled.append(
                        bar.model_copy(
                            update={
                                **{
                                    name: (getattr(bar, name) * price_factor).quantize(
                                        Decimal("0.0001"), rounding=ROUND_HALF_EVEN
                                    )
                                    for name in ("open", "high", "low", "close")
                                },
                                # Fraction.__round__ applies ties-to-even exactly, including
                                # products of nonterminating ratios such as 2/3.
                                "volume": Decimal(
                                    round(Fraction(bar.volume) * split_volume_multiplier)
                                ),
                            }
                        )
                    )
            self._cache[key] = tuple(reversed(scaled))
        return self._cache[key][: len(visible)]
