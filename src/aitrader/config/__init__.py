"""Settings schema, layered loading, hard caps and the configuration hash."""

from aitrader.config.errors import ConfigError, LiveDisabledError
from aitrader.config.hard_caps import HARD_CAPS
from aitrader.config.loader import (
    assert_no_live_credentials,
    config_hash,
    enforce_hard_caps,
    load_settings,
)
from aitrader.config.settings import (
    BacktestSettings,
    DataSourceSettings,
    PaperSettings,
    RiskLimits,
    Secrets,
    Settings,
)

__all__ = [
    "HARD_CAPS",
    "BacktestSettings",
    "ConfigError",
    "DataSourceSettings",
    "LiveDisabledError",
    "PaperSettings",
    "RiskLimits",
    "Secrets",
    "Settings",
    "assert_no_live_credentials",
    "config_hash",
    "enforce_hard_caps",
    "load_settings",
]
