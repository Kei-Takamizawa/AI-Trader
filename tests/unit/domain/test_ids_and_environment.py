import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aitrader.domain import (
    BrokerEnvironment,
    EnvironmentNotAllowedError,
    Mode,
    RunPurpose,
    Side,
    check_environment,
    make_client_order_id,
    make_intent_id,
)
from aitrader.domain.ids import MAX_ATTEMPT

_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_text = st.text(
    alphabet=st.characters(exclude_characters="\x1f", exclude_categories=["Cs"]),
    min_size=1,
    max_size=20,
)
_ts = st.datetimes(
    min_value=datetime(2000, 1, 1),  # noqa: DTZ001 - hypothesis bounds are naive
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001
    timezones=st.just(UTC),
)


@given(
    strategy=_text,
    version=_text,
    symbol=_text,
    side=st.sampled_from(list(Side)),
    ts=_ts,
    seq=st.integers(min_value=0, max_value=10**6),
)
def test_intent_id_is_deterministic(
    strategy: str, version: str, symbol: str, side: Side, ts: datetime, seq: int
) -> None:
    first = make_intent_id(strategy, version, symbol, side, ts, seq)
    second = make_intent_id(strategy, version, symbol, side, ts, seq)
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)


@given(
    strategy=_text,
    version=_text,
    symbol=_text,
    side=st.sampled_from(list(Side)),
    ts=_ts,
    seq=st.integers(min_value=0, max_value=10**6),
    which=st.integers(min_value=0, max_value=5),
)
def test_intent_id_changes_when_any_input_changes(
    strategy: str, version: str, symbol: str, side: Side, ts: datetime, seq: int, which: int
) -> None:
    args: list[object] = [strategy, version, symbol, side, ts, seq]
    base = make_intent_id(*args)  # type: ignore[arg-type]
    changed = list(args)
    if which == 0:
        changed[0] = strategy + "x"
    elif which == 1:
        changed[1] = version + "x"
    elif which == 2:
        changed[2] = symbol + "x"
    elif which == 3:
        changed[3] = Side.SELL if side is Side.BUY else Side.BUY
    elif which == 4:
        changed[4] = ts + timedelta(seconds=1)
    else:
        changed[5] = seq + 1
    assert make_intent_id(*changed) != base  # type: ignore[arg-type]


def test_intent_id_is_the_same_for_equal_instants_in_other_timezones() -> None:
    utc = datetime(2026, 10, 22, 20, 0, tzinfo=UTC)
    tokyo = utc.astimezone(timezone(timedelta(hours=9)))
    assert make_intent_id("S1", "1", "SPY", Side.BUY, utc, 0) == make_intent_id(
        "S1", "1", "SPY", Side.BUY, tokyo, 0
    )


def test_intent_id_input_checks() -> None:
    ts = datetime(2026, 10, 22, tzinfo=UTC)
    with pytest.raises(ValueError, match="seq"):
        make_intent_id("S1", "1", "SPY", Side.BUY, ts, -1)
    with pytest.raises(ValueError, match="separator"):
        make_intent_id("S\x1f1", "1", "SPY", Side.BUY, ts, 0)
    with pytest.raises(ValueError, match="naive"):
        make_intent_id("S1", "1", "SPY", Side.BUY, datetime(2026, 10, 22), 0)  # noqa: DTZ001


@given(intent_id=st.text(min_size=1, max_size=200), attempt=st.integers(1, MAX_ATTEMPT))
def test_client_order_id_fits_webull_rules(intent_id: str, attempt: int) -> None:
    first = make_client_order_id(intent_id, attempt)
    assert first == make_client_order_id(intent_id, attempt)
    assert len(first) <= 32
    assert _CLIENT_ID_RE.fullmatch(first)
    assert first.endswith(f"-{attempt}")


def test_client_order_id_differs_by_attempt_and_intent() -> None:
    assert make_client_order_id("a", 1) != make_client_order_id("a", 2)
    assert make_client_order_id("a", 1) != make_client_order_id("b", 1)


@pytest.mark.parametrize("attempt", [0, -1, MAX_ATTEMPT + 1])
def test_client_order_id_rejects_bad_attempts(attempt: int) -> None:
    with pytest.raises(ValueError, match="attempt"):
        make_client_order_id("a", attempt)


def test_client_order_id_rejects_empty_intent() -> None:
    with pytest.raises(ValueError, match="intent_id"):
        make_client_order_id("", 1)


@pytest.mark.parametrize(
    ("mode", "env"),
    [
        (Mode.BACKTEST, BrokerEnvironment.SIM),
        (Mode.PAPER, BrokerEnvironment.SIM),
        (Mode.LIVE, BrokerEnvironment.WEBULL_LIVE),
    ],
)
def test_allowed_trading_combinations(mode: Mode, env: BrokerEnvironment) -> None:
    check_environment(RunPurpose.TRADING, mode, env)


@pytest.mark.parametrize(
    ("mode", "env"),
    [
        (Mode.BACKTEST, BrokerEnvironment.WEBULL_LIVE),
        (Mode.BACKTEST, BrokerEnvironment.WEBULL_SANDBOX),
        (Mode.PAPER, BrokerEnvironment.WEBULL_SANDBOX),
        (Mode.PAPER, BrokerEnvironment.WEBULL_LIVE),
        (Mode.LIVE, BrokerEnvironment.SIM),
        (Mode.LIVE, BrokerEnvironment.WEBULL_SANDBOX),
        (None, BrokerEnvironment.SIM),
    ],
)
def test_forbidden_trading_combinations(mode: Mode | None, env: BrokerEnvironment) -> None:
    with pytest.raises(EnvironmentNotAllowedError):
        check_environment(RunPurpose.TRADING, mode, env)


def test_contract_tests_never_reach_production() -> None:
    check_environment(RunPurpose.BROKER_CONTRACT_TEST, None, BrokerEnvironment.WEBULL_SANDBOX)
    check_environment(RunPurpose.BROKER_CONTRACT_TEST, None, BrokerEnvironment.SIM)
    with pytest.raises(EnvironmentNotAllowedError):
        check_environment(RunPurpose.BROKER_CONTRACT_TEST, None, BrokerEnvironment.WEBULL_LIVE)
    with pytest.raises(EnvironmentNotAllowedError):
        check_environment(
            RunPurpose.BROKER_CONTRACT_TEST, Mode.PAPER, BrokerEnvironment.WEBULL_SANDBOX
        )
