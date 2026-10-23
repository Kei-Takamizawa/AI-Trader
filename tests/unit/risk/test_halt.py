"""System-state machine (document 05 §7)."""

from datetime import UTC, datetime, timedelta
from itertools import product

import pytest

from aitrader.domain import SystemState
from aitrader.risk import (
    ALLOWED_TRANSITIONS,
    Actor,
    HaltReason,
    HaltStateMachine,
    HaltTransition,
    IllegalTransitionError,
)

S = SystemState
T0 = datetime(2026, 10, 22, 13, 0, tzinfo=UTC)


def state_of(machine: HaltStateMachine) -> SystemState:
    """Read the state through a function so the type checker does not narrow it."""
    return machine.state


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def running() -> HaltStateMachine:
    machine = HaltStateMachine()
    machine.on_reconciliation(T0, clean=True)
    return machine


def machine_in(state: SystemState) -> HaltStateMachine:
    machine = HaltStateMachine()
    if state is S.SAFE:
        return machine
    machine.on_reconciliation(T0, clean=True)
    if state is S.DEGRADED:
        machine.degrade(HaltReason.BROKER_UNAVAILABLE, at(1))
    elif state is S.HALTED:
        machine.halt(HaltReason.DAILY_LOSS, at(1))
    elif state is S.KILLED:
        machine.kill(at(1), Actor.OWNER, "test")
    assert machine.state is state
    return machine


def test_a_new_system_is_safe_and_allows_no_entries() -> None:
    machine = HaltStateMachine()
    assert state_of(machine) is S.SAFE
    assert not machine.allows_entries
    assert machine.halt_reason is None


def test_clean_reconciliation_starts_running() -> None:
    machine = running()
    assert state_of(machine) is S.RUNNING
    assert machine.allows_entries
    assert machine.on_reconciliation(at(1), clean=True) is None  # already running


def test_mismatch_halts() -> None:
    machine = HaltStateMachine()
    machine.on_reconciliation(T0, clean=False, detail="cash differs")
    assert state_of(machine) is S.HALTED
    assert machine.halt_reason is HaltReason.RECONCILIATION_MISMATCH
    assert machine.on_reconciliation(at(1), clean=False) is None  # already halted


@pytest.mark.parametrize(("current", "target"), list(product(S, S)))
def test_transition_table(current: SystemState, target: SystemState) -> None:
    machine = machine_in(current)
    reason = {
        S.SAFE: HaltReason.PROCESS_START if current is not S.KILLED else HaltReason.OWNER_RESET,
        S.RUNNING: HaltReason.RECONCILED if current is S.SAFE else HaltReason.OWNER_RESET,
    }.get(target, HaltReason.ORDER_ERRORS)
    if target is S.KILLED:
        reason = HaltReason.KILL_SWITCH
    if target in ALLOWED_TRANSITIONS[current]:
        machine.transition(target, reason, Actor.OWNER, at(5), "reason given")
        assert machine.state is target
    else:
        with pytest.raises(IllegalTransitionError):
            machine.transition(target, reason, Actor.OWNER, at(5), "reason given")


def test_daily_loss_halt_clears_itself() -> None:
    machine = running()
    machine.halt(HaltReason.DAILY_LOSS, at(1))
    machine.resume(at(60 * 18), Actor.SYSTEM, "next session")
    assert state_of(machine) is S.RUNNING
    assert machine.history[-1].reason is HaltReason.CONDITION_CLEARED


@pytest.mark.parametrize(
    "reason",
    [
        HaltReason.MONTHLY_LOSS,
        HaltReason.DRAWDOWN,
        HaltReason.RECONCILIATION_MISMATCH,
        HaltReason.ORDER_ERRORS,
        HaltReason.AUTHENTICATION,
    ],
)
def test_other_halts_need_the_owner(reason: HaltReason) -> None:
    machine = running()
    machine.halt(reason, at(1))
    with pytest.raises(IllegalTransitionError, match="only by the owner"):
        machine.resume(at(2), Actor.SYSTEM)
    machine.resume(at(3), Actor.OWNER, "reviewed")
    assert state_of(machine) is S.RUNNING
    assert machine.history[-1].reason is HaltReason.OWNER_RESET


def test_degraded_recovers_when_the_condition_clears() -> None:
    machine = running()
    machine.degrade(HaltReason.BROKER_UNAVAILABLE, at(1))
    assert machine.degrade(HaltReason.DATA, at(2)) is None  # only from RUNNING
    machine.resume(at(3), Actor.SYSTEM, "reconciled")
    assert state_of(machine) is S.RUNNING


def test_a_halt_overrides_degraded_but_not_killed() -> None:
    machine = running()
    machine.degrade(HaltReason.BROKER_UNAVAILABLE, at(1))
    machine.halt(HaltReason.ORDER_ERRORS, at(2))
    assert state_of(machine) is S.HALTED
    assert machine.halt(HaltReason.DRAWDOWN, at(3)) is None
    machine.kill(at(4), Actor.SYSTEM, "critical incident")
    assert machine.halt(HaltReason.DRAWDOWN, at(5)) is None
    assert machine.kill(at(6), Actor.OWNER) is None
    assert state_of(machine) is S.KILLED


def test_unkill_needs_the_owner_and_a_reason() -> None:
    machine = running()
    machine.kill(at(1), Actor.SYSTEM, "watchdog")
    with pytest.raises(IllegalTransitionError, match="give a reason"):
        machine.unkill(at(2), "  ")
    with pytest.raises(IllegalTransitionError, match="owner"):
        machine.transition(S.SAFE, HaltReason.OWNER_RESET, Actor.SYSTEM, at(2), "auto")
    machine.unkill(at(3), "checked positions in the app")
    assert state_of(machine) is S.SAFE
    machine.on_reconciliation(at(4), clean=True)
    assert state_of(machine) is S.RUNNING


def test_process_start_falls_back_to_safe_but_keeps_halts() -> None:
    machine = running()
    assert machine.on_process_start(at(1)) is not None
    assert state_of(machine) is S.SAFE
    assert machine.on_process_start(at(2)) is None

    halted = running()
    halted.halt(HaltReason.MONTHLY_LOSS, at(1))
    restarted = HaltStateMachine(halted.history)
    assert restarted.on_process_start(at(2)) is None
    assert restarted.state is S.HALTED
    assert restarted.halt_reason is HaltReason.MONTHLY_LOSS


def test_safe_needs_a_process_start_or_unkill() -> None:
    machine = running()
    with pytest.raises(IllegalTransitionError, match="process start"):
        machine.transition(S.SAFE, HaltReason.OWNER_RESET, Actor.OWNER, at(1), "x")


def test_running_needs_a_clean_reconciliation() -> None:
    with pytest.raises(IllegalTransitionError, match="clean reconciliation"):
        HaltStateMachine().transition(S.RUNNING, HaltReason.OWNER_RESET, Actor.OWNER, T0)


def test_history_must_be_consistent() -> None:
    first = HaltTransition(
        ts=T0,
        from_state=S.SAFE,
        to_state=S.RUNNING,
        reason=HaltReason.RECONCILED,
        actor=Actor.SYSTEM,
    )
    wrong_start = first.model_copy(update={"from_state": S.HALTED, "ts": at(1)})
    with pytest.raises(IllegalTransitionError, match="starts at"):
        HaltStateMachine([first, wrong_start])
    earlier = HaltTransition(
        ts=T0 - timedelta(seconds=1),
        from_state=S.RUNNING,
        to_state=S.HALTED,
        reason=HaltReason.DRAWDOWN,
        actor=Actor.SYSTEM,
    )
    with pytest.raises(IllegalTransitionError, match="time order"):
        HaltStateMachine([first, earlier])
