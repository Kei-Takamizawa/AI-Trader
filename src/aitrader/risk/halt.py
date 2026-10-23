"""The system-state (halt) machine of document 05 §7.

The history of transitions is the state: it is stored and replayed after a restart, so a halt
before a crash stays a halt. A process always starts in SAFE unless it was HALTED or KILLED.
"""

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from aitrader.domain import SystemState
from aitrader.domain.types import UtcDatetime

S = SystemState


class HaltReason(StrEnum):
    PROCESS_START = "PROCESS_START"
    RECONCILED = "RECONCILED"
    CONDITION_CLEARED = "CONDITION_CLEARED"
    DAILY_LOSS = "DAILY_LOSS"
    MONTHLY_LOSS = "MONTHLY_LOSS"
    DRAWDOWN = "DRAWDOWN"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    ORDER_ERRORS = "ORDER_ERRORS"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    AUTHENTICATION = "AUTHENTICATION"
    DATA = "DATA"
    KILL_SWITCH = "KILL_SWITCH"
    OWNER_RESET = "OWNER_RESET"


class Actor(StrEnum):
    SYSTEM = "SYSTEM"
    OWNER = "OWNER"


ALLOWED_TRANSITIONS: dict[SystemState, frozenset[SystemState]] = {
    S.SAFE: frozenset({S.RUNNING, S.HALTED, S.KILLED}),
    S.RUNNING: frozenset({S.SAFE, S.DEGRADED, S.HALTED, S.KILLED}),
    S.DEGRADED: frozenset({S.SAFE, S.RUNNING, S.HALTED, S.KILLED}),
    S.HALTED: frozenset({S.RUNNING, S.KILLED}),
    S.KILLED: frozenset({S.SAFE}),
}
"""RUNNING and DEGRADED fall back to SAFE only at a process start."""

AUTO_CLEARING_HALTS = frozenset({HaltReason.DAILY_LOSS})
"""Halts the system may clear itself (the daily limit at the next session). Every other halt
needs the owner (monthly limit Q5, drawdown Q12, mismatch, order errors, authentication)."""


class IllegalTransitionError(Exception):
    pass


class HaltTransition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: UtcDatetime
    from_state: SystemState
    to_state: SystemState
    reason: HaltReason
    actor: Actor
    detail: str = ""


class HaltStateMachine:
    def __init__(self, history: Iterable[HaltTransition] = ()) -> None:
        self._history: list[HaltTransition] = []
        for transition in history:
            self._check(transition)
            self._history.append(transition)

    @property
    def state(self) -> SystemState:
        return self._history[-1].to_state if self._history else S.SAFE

    @property
    def history(self) -> tuple[HaltTransition, ...]:
        return tuple(self._history)

    @property
    def halt_reason(self) -> HaltReason | None:
        """Why the system is HALTED (the reason of the transition into HALTED)."""
        if self.state is not S.HALTED:
            return None
        return self._history[-1].reason

    @property
    def allows_entries(self) -> bool:
        return self.state is S.RUNNING

    # ------------------------------------------------------------------ transitions

    def transition(
        self,
        to_state: SystemState,
        reason: HaltReason,
        actor: Actor,
        ts: datetime,
        detail: str = "",
    ) -> HaltTransition:
        transition = HaltTransition(
            ts=ts,
            from_state=self.state,
            to_state=to_state,
            reason=reason,
            actor=actor,
            detail=detail,
        )
        self._check(transition)
        self._history.append(transition)
        return transition

    def _check(self, transition: HaltTransition) -> None:
        current = self.state
        if transition.from_state is not current:
            raise IllegalTransitionError(
                f"transition starts at {transition.from_state.value}, state is {current.value}"
            )
        if self._history and transition.ts < self._history[-1].ts:
            raise IllegalTransitionError("transitions must be in time order")
        target = transition.to_state
        if target not in ALLOWED_TRANSITIONS[current]:
            raise IllegalTransitionError(f"{current.value} -> {target.value} is not allowed")
        if (
            target is S.SAFE
            and current is not S.KILLED
            and transition.reason is not HaltReason.PROCESS_START
        ):
            raise IllegalTransitionError("only a process start falls back to SAFE")
        if current is S.KILLED and (
            transition.actor is not Actor.OWNER or not transition.detail.strip()
        ):
            raise IllegalTransitionError("only the owner can unkill, and must give a reason")
        if current is S.HALTED and target is S.RUNNING:
            reason = self.halt_reason
            if reason not in AUTO_CLEARING_HALTS and transition.actor is not Actor.OWNER:
                raise IllegalTransitionError(f"a {reason} halt is cleared only by the owner")
        if (
            current is S.SAFE
            and target is S.RUNNING
            and transition.reason is not HaltReason.RECONCILED
        ):
            raise IllegalTransitionError("SAFE -> RUNNING needs a clean reconciliation")

    # ------------------------------------------------------------------ helpers

    def on_process_start(self, ts: datetime) -> HaltTransition | None:
        """A new process starts in SAFE; HALTED and KILLED survive the restart."""
        if self.state in (S.RUNNING, S.DEGRADED):
            return self.transition(S.SAFE, HaltReason.PROCESS_START, Actor.SYSTEM, ts)
        return None

    def on_reconciliation(
        self, ts: datetime, *, clean: bool, detail: str = ""
    ) -> HaltTransition | None:
        """Leave SAFE after reconciliation: RUNNING when clean, HALTED on a mismatch."""
        if not clean:
            if self.state in (S.HALTED, S.KILLED):
                return None
            return self.transition(
                S.HALTED, HaltReason.RECONCILIATION_MISMATCH, Actor.SYSTEM, ts, detail
            )
        if self.state is S.SAFE:
            return self.transition(S.RUNNING, HaltReason.RECONCILED, Actor.SYSTEM, ts, detail)
        return None

    def degrade(self, reason: HaltReason, ts: datetime, detail: str = "") -> HaltTransition | None:
        """RUNNING -> DEGRADED; ignored in any other state (an existing halt is stronger)."""
        if self.state is not S.RUNNING:
            return None
        return self.transition(S.DEGRADED, reason, Actor.SYSTEM, ts, detail)

    def halt(self, reason: HaltReason, ts: datetime, detail: str = "") -> HaltTransition | None:
        """-> HALTED from RUNNING, DEGRADED or SAFE; ignored when already HALTED or KILLED."""
        if self.state in (S.HALTED, S.KILLED):
            return None
        return self.transition(S.HALTED, reason, Actor.SYSTEM, ts, detail)

    def kill(self, ts: datetime, actor: Actor, detail: str = "") -> HaltTransition | None:
        if self.state is S.KILLED:
            return None
        return self.transition(S.KILLED, HaltReason.KILL_SWITCH, actor, ts, detail)

    def unkill(self, ts: datetime, detail: str) -> HaltTransition:
        """KILLED -> SAFE by the owner, with a reason. Reconciliation then decides RUNNING."""
        return self.transition(S.SAFE, HaltReason.OWNER_RESET, Actor.OWNER, ts, detail)

    def resume(self, ts: datetime, actor: Actor, detail: str = "") -> HaltTransition:
        """DEGRADED or HALTED -> RUNNING once the condition has cleared (rules above)."""
        reason = HaltReason.OWNER_RESET if actor is Actor.OWNER else HaltReason.CONDITION_CLEARED
        return self.transition(S.RUNNING, reason, actor, ts, detail)
