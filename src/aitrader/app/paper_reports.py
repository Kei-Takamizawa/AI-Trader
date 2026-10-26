"""Atomic, local PAPER status and immutable daily reports for the operator."""

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, ConfigDict

from aitrader.backtest.snapshots import PositionSnapshot, SessionSnapshot, canonical_json
from aitrader.domain import OrderIntent
from aitrader.domain.types import StrictDecimal


class PaperStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    strategy_version: str
    purpose: Literal["reference", "observation"]
    state: Literal["NOT_STARTED", "RUNNING", "HALTED"]
    reason: str
    as_of: datetime
    start: date
    last_session: date | None
    sessions_recorded: int
    data_end: date | None
    data_stale: bool
    equity_usd: StrictDecimal | None
    equity_jpy: StrictDecimal | None
    return_since_start: StrictDecimal | None
    max_drawdown_since_start: StrictDecimal | None
    positions: tuple[PositionSnapshot, ...]
    next_intents: tuple[OrderIntent, ...]
    b0_return_since_start: StrictDecimal | None
    git_commit: str


def atomic_write(path: Path, content: str) -> None:
    """Readers see either the old complete file or the new complete file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            file.close()
            temporary.unlink()
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_status(state_dir: Path, status: PaperStatus) -> None:
    atomic_write(state_dir / "status/latest.json", canonical_json(status.model_dump()) + "\n")


def write_daily(
    state_dir: Path,
    snapshot: SessionSnapshot,
    previous_equity: Decimal,
    *,
    strategy_id: str,
    strategy_version: str,
    purpose: Literal["reference", "observation"],
) -> None:
    """Existing report files are immutable; provenance lives in the SQLite session row."""
    pnl = snapshot.equity_usd - previous_equity
    report = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "purpose": purpose,
        **snapshot.model_dump(),
        "snapshot_hash": snapshot.snapshot_hash,
        "day_pnl_usd": pnl,
        "fees_usd": snapshot.fees_usd,
    }
    lines = [
        f"# PAPER daily report — {snapshot.session}",
        "",
        f"Strategy: {strategy_id} {strategy_version}. Purpose: {purpose}.",
        "",
    ]
    if purpose == "observation":
        lines.extend(
            [
                "Observation book: this strategy did not pass its research rules "
                "and is not a LIVE candidate.",
                "",
            ]
        )
    lines.extend(
        [
            f"Virtual portfolio: USD {snapshot.equity_usd:.2f}. Day P&L: USD {pnl:+.2f}.",
            f"Trading fees: USD {snapshot.fees_usd:.2f}.",
            "",
            "## Fills",
            "",
        ]
    )
    lines.extend(
        f"- {item.fill.side.value} {item.fill.quantity} {item.fill.symbol} "
        f"at USD {item.fill.price}; fees USD {item.fees_usd}."
        for item in snapshot.fills
    )
    if not snapshot.fills:
        lines.append("No fills.")
    lines.extend(["", "## Positions", ""])
    lines.extend(
        f"- {pos.symbol}: {pos.quantity} shares, average cost USD {pos.average_cost}, "
        f"close USD {pos.close}, value USD {pos.market_value_usd:.2f}."
        for pos in snapshot.positions
    )
    if not snapshot.positions:
        lines.append("No open positions.")
    lines.extend(["", "## Next session", ""])
    lines.extend(
        f"- {intent.side.value} {intent.symbol}: {intent.reason} "
        f"(target {intent.target_quantity or intent.target_notional}; "
        f"stop {intent.stop_price})."
        for intent in snapshot.next_intents
    )
    if not snapshot.next_intents:
        lines.append("No pending intents.")
    lines.extend(["", "## Rejections and data quality", ""])
    lines.extend(
        f"- Rejected {decision.intent_id}: "
        + "; ".join(
            f"{rule.rule_id}: {rule.detail}" for rule in decision.rule_results if not rule.passed
        )
        for decision in snapshot.rejections
    )
    lines.extend(f"- Skipped: {reason}" for reason in snapshot.skipped)
    lines.extend(
        f"- {symbol}: {', '.join(rules)}" for symbol, rules in snapshot.data_quality.items()
    )
    if not (snapshot.rejections or snapshot.skipped or snapshot.data_quality):
        lines.append("No findings.")
    lines.extend(["", "This is a virtual portfolio using daily-bar modelled fills.", ""])
    base = state_dir / "reports/daily" / snapshot.session.isoformat()
    for suffix, content in ((".json", canonical_json(report) + "\n"), (".md", "\n".join(lines))):
        target = base.with_suffix(suffix)
        if not target.exists():
            atomic_write(target, content)
