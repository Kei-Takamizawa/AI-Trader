"""Settings schema. Every field is validated; unknown fields are rejected."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aitrader.config.errors import ConfigError
from aitrader.domain import (
    BrokerEnvironment,
    EnvironmentNotAllowedError,
    Mode,
    RunPurpose,
    check_environment,
)
from aitrader.domain.types import NonNegativeDecimal, PositiveDecimal

PositiveInt = Annotated[int, Field(ge=1, strict=True)]


class RiskLimits(BaseModel):
    """Risk limits (document 05, section 1). Defaults are the initial values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_per_trade_pct: PositiveDecimal = Decimal("0.5")
    daily_loss_limit_pct: PositiveDecimal = Decimal("1.5")
    monthly_soft_loss_pct: PositiveDecimal = Decimal("3.0")
    monthly_loss_limit_pct: PositiveDecimal = Decimal("5.0")
    max_drawdown_pct: PositiveDecimal = Decimal("10.0")
    max_open_positions: PositiveInt = 4
    max_position_pct_etf: PositiveDecimal = Decimal(25)
    max_position_pct_stock: PositiveDecimal = Decimal(10)
    max_gross_exposure_pct: PositiveDecimal = Decimal(100)
    min_cash_buffer_pct: NonNegativeDecimal = Decimal(5)
    max_portfolio_daily_vol_pct: PositiveDecimal = Decimal("1.2")
    max_orders_per_day: PositiveInt = 8
    max_order_adv_pct: PositiveDecimal = Decimal("0.1")
    max_cost_share_of_risk_budget_pct: PositiveDecimal = Decimal(20)
    approved_capital_jpy: PositiveDecimal = Decimal(50_000)
    live_max_order_notional_jpy: PositiveDecimal = Decimal(30_000)
    live_max_total_holdings_jpy: PositiveDecimal = Decimal(100_000)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.monthly_soft_loss_pct >= self.monthly_loss_limit_pct:
            raise ValueError("monthly_soft_loss_pct must be below monthly_loss_limit_pct")
        if self.live_max_order_notional_jpy > self.live_max_total_holdings_jpy:
            raise ValueError(
                "live_max_order_notional_jpy must not exceed live_max_total_holdings_jpy"
            )
        if self.min_cash_buffer_pct >= 100:
            raise ValueError("min_cash_buffer_pct must be below 100")
        return self


class SimulationSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_capital_usd: PositiveDecimal = Decimal(323)
    fx_jpy_per_usd: PositiveDecimal = Decimal(155)
    """Fixed rate for the JPY-denominated caps and reports (USD is held; no conversions)."""
    half_spread_bps: PositiveDecimal = Decimal(1)
    """Half the quoted spread assumed at the open (document 12 §4: 1-2 bp for liquid ETFs)."""
    max_participation_pct: PositiveDecimal = Decimal(1)
    """Largest share of a bar's volume one order may fill (document 12 §4)."""

    dividend_us_withholding_pct: Annotated[NonNegativeDecimal, Field(le=100)] = Decimal(10)
    dividend_jp_withholding_pct: Annotated[NonNegativeDecimal, Field(le=100)] = Decimal("20.315")

    def simulation(self) -> SimulationSettings:
        """Keep flat YAML keys while sharing validated simulator inputs."""
        return SimulationSettings.model_validate(
            self.model_dump(include=set(SimulationSettings.model_fields))
        )


class BacktestSettings(SimulationSettings):
    start: date
    end: date
    initial_capital_usd: PositiveDecimal = Field(...)

    @model_validator(mode="after")
    def _check_period(self) -> Self:
        if self.end <= self.start:
            raise ValueError("backtest end must be after start")
        return self


class PaperSettings(SimulationSettings):
    start: date
    strategy: Literal["S1", "S3"] = "S1"
    purpose: Literal["reference", "observation"] = "reference"


class DataSourceSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["none", "csv", "alpaca"] = "none"


class WebullSandboxSettings(BaseModel):
    """Non-secret settings for the operator-only sandbox check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str = "jp-openapi-alb.uat.webullbroker.com"
    account_tax_type: str = "SPECIFIC"
    far_limit_price: PositiveDecimal = Decimal("1.00")


class Secrets(BaseSettings):
    """Secrets come only from ``AITRADER_*`` environment variables, never from files.

    There is deliberately no field for Webull production credentials: they exist only on the
    LIVE host, which this build cannot run.
    """

    model_config = SettingsConfigDict(env_prefix="AITRADER_", extra="ignore", frozen=True)

    data_api_key: SecretStr | None = None
    data_api_secret: SecretStr | None = None
    data_repo_token: SecretStr | None = None


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Mode
    broker_environment: BrokerEnvironment
    risk: RiskLimits = RiskLimits()
    data: DataSourceSettings = DataSourceSettings()
    webull_sandbox: WebullSandboxSettings = WebullSandboxSettings()
    backtest: BacktestSettings | None = None
    paper: PaperSettings | None = None
    secrets: Secrets = Field(default_factory=Secrets, exclude=True, repr=False)

    @model_validator(mode="after")
    def _check_mode(self) -> Self:
        try:
            check_environment(RunPurpose.TRADING, self.mode, self.broker_environment)
        except EnvironmentNotAllowedError as exc:
            raise ValueError(str(exc)) from exc
        if self.mode is Mode.BACKTEST and self.backtest is None:
            raise ValueError("backtest mode needs a 'backtest' section")
        if self.mode is Mode.PAPER and self.paper is None:
            raise ValueError("paper mode needs a 'paper' section")
        return self

    def require_backtest(self) -> BacktestSettings:
        """The backtest section; a configuration error when there is none."""
        if self.backtest is None:
            raise ConfigError("these settings have no 'backtest' section")
        return self.backtest
