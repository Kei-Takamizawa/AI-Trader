"""Allowed combinations of operating mode, broker environment and run purpose (document 04)."""

from aitrader.domain.enums import BrokerEnvironment, Mode, RunPurpose
from aitrader.domain.errors import EnvironmentNotAllowedError

TRADING_ENVIRONMENTS: dict[Mode, BrokerEnvironment] = {
    Mode.BACKTEST: BrokerEnvironment.SIM,
    Mode.PAPER: BrokerEnvironment.SIM,
    Mode.LIVE: BrokerEnvironment.WEBULL_LIVE,
}
"""The only broker environment each mode may trade against."""

CONTRACT_TEST_ENVIRONMENTS: frozenset[BrokerEnvironment] = frozenset(
    {BrokerEnvironment.SIM, BrokerEnvironment.WEBULL_SANDBOX}
)
"""Broker contract tests run against the simulator or the Webull Sandbox, never production."""


def check_environment(
    purpose: RunPurpose, mode: Mode | None, broker_environment: BrokerEnvironment
) -> None:
    """Raise :class:`EnvironmentNotAllowedError` unless the combination is allowed."""
    if purpose is RunPurpose.TRADING:
        if mode is None:
            raise EnvironmentNotAllowedError("trading needs a mode")
        expected = TRADING_ENVIRONMENTS[mode]
        if broker_environment is not expected:
            raise EnvironmentNotAllowedError(
                f"mode {mode.value} must use broker environment {expected.value}, "
                f"not {broker_environment.value}"
            )
        return
    if mode is not None:
        raise EnvironmentNotAllowedError("broker contract tests do not run in a trading mode")
    if broker_environment not in CONTRACT_TEST_ENVIRONMENTS:
        raise EnvironmentNotAllowedError(
            f"broker contract tests may not use {broker_environment.value}"
        )
