import shutil
from pathlib import Path

import pytest

from aitrader.config import (
    ConfigError,
    LiveDisabledError,
    assert_no_live_credentials,
    config_hash,
    load_settings,
)
from aitrader.domain import BrokerEnvironment, Mode

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, target)
    return target


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("mode", [Mode.BACKTEST, Mode.PAPER])
def test_repository_configs_load(mode: Mode) -> None:
    settings = load_settings(mode, REPO_CONFIG, environ={})
    assert settings.mode is mode
    assert settings.broker_environment is BrokerEnvironment.SIM


def test_layering_mode_file_overrides_base(config_dir: Path) -> None:
    _write(
        config_dir / "paper.yaml",
        "mode: paper\nbroker_environment: sim\npaper:\n  virtual_capital_jpy: 40000\n"
        "risk:\n  risk_per_trade_pct: 0.25\n",
    )
    settings = load_settings(Mode.PAPER, config_dir, environ={})
    assert str(settings.risk.risk_per_trade_pct) == "0.25"
    assert str(settings.risk.daily_loss_limit_pct) == "1.5"  # still from base.yaml
    assert settings.paper is not None
    assert str(settings.paper.virtual_capital_jpy) == "40000"


def test_live_cannot_be_loaded(config_dir: Path) -> None:
    with pytest.raises(LiveDisabledError, match="LIVE is disabled in this build"):
        load_settings(Mode.LIVE, config_dir, environ={})


def test_limit_above_hard_cap_is_rejected(config_dir: Path) -> None:
    _write(
        config_dir / "paper.yaml",
        "mode: paper\nbroker_environment: sim\npaper: {}\nrisk:\n  risk_per_trade_pct: 2.0\n",
    )
    with pytest.raises(ConfigError, match=r"risk\.risk_per_trade_pct = 2\.0 exceeds the hard cap"):
        load_settings(Mode.PAPER, config_dir, environ={})


@pytest.mark.parametrize(
    "extra",
    [
        "webull_app_key: abc\n",
        "data:\n  provider: alpaca\n  api_secret: abc\n",
        "LIVE_TOKEN: abc\n",
        "secrets:\n  data_api_key: abc\n",
    ],
)
def test_credential_like_keys_are_rejected(config_dir: Path, extra: str) -> None:
    _write(config_dir / "paper.yaml", "mode: paper\nbroker_environment: sim\npaper: {}\n" + extra)
    with pytest.raises(ConfigError, match="credential-like key"):
        load_settings(Mode.PAPER, config_dir, environ={})


def test_unknown_fields_are_rejected(config_dir: Path) -> None:
    _write(config_dir / "paper.yaml", "mode: paper\nbroker_environment: sim\npaper: {}\nfoo: 1\n")
    with pytest.raises(ConfigError, match="foo"):
        load_settings(Mode.PAPER, config_dir, environ={})


def test_paper_cannot_target_webull(config_dir: Path) -> None:
    _write(
        config_dir / "paper.yaml", "mode: paper\nbroker_environment: webull_sandbox\npaper: {}\n"
    )
    with pytest.raises(ConfigError, match="must use broker environment sim"):
        load_settings(Mode.PAPER, config_dir, environ={})


def test_mode_mismatch_is_rejected(config_dir: Path) -> None:
    _write(config_dir / "paper.yaml", "mode: backtest\nbroker_environment: sim\npaper: {}\n")
    with pytest.raises(ConfigError, match="declares mode"):
        load_settings(Mode.PAPER, config_dir, environ={})


def test_missing_section_and_file(config_dir: Path, tmp_path: Path) -> None:
    _write(config_dir / "paper.yaml", "mode: paper\nbroker_environment: sim\n")
    with pytest.raises(ConfigError, match="paper"):
        load_settings(Mode.PAPER, config_dir, environ={})
    with pytest.raises(ConfigError, match="not found"):
        load_settings(Mode.PAPER, tmp_path / "missing", environ={})


@pytest.mark.parametrize("text", ["- a\n- b\n", "risk:\n  risk_per_trade_pct: .inf\n"])
def test_malformed_yaml_content(config_dir: Path, text: str) -> None:
    _write(config_dir / "base.yaml", text)
    with pytest.raises(ConfigError):
        load_settings(Mode.PAPER, config_dir, environ={})


def test_empty_base_file_uses_defaults(config_dir: Path) -> None:
    _write(config_dir / "base.yaml", "")
    settings = load_settings(Mode.PAPER, config_dir, environ={})
    assert str(settings.risk.risk_per_trade_pct) == "0.5"


def test_hash_is_stable_and_ignores_key_order_and_spelling(config_dir: Path) -> None:
    first = config_hash(load_settings(Mode.PAPER, config_dir, environ={}))
    assert first == config_hash(load_settings(Mode.PAPER, config_dir, environ={}))
    assert len(first) == 64

    _write(
        config_dir / "paper.yaml",
        "paper:\n  virtual_capital_jpy: 50000.00\nbroker_environment: sim\nmode: paper\n"
        "data:\n  provider: alpaca\n",
    )
    assert config_hash(load_settings(Mode.PAPER, config_dir, environ={})) == first

    _write(
        config_dir / "paper.yaml",
        "mode: paper\nbroker_environment: sim\npaper:\n  virtual_capital_jpy: 49999\n"
        "data:\n  provider: alpaca\n",
    )
    assert config_hash(load_settings(Mode.PAPER, config_dir, environ={})) != first


def test_secrets_come_from_environment_and_never_leak(config_dir: Path) -> None:
    environ = {
        "AITRADER_DATA_API_KEY": "key-value-123",
        "AITRADER_DATA_API_SECRET": "secret-value-456",
        "AITRADER_DATA_REPO_TOKEN": "token-value-789",
    }
    with_secrets = load_settings(Mode.PAPER, config_dir, environ=environ)
    without = load_settings(Mode.PAPER, config_dir, environ={})

    assert with_secrets.secrets.data_api_key is not None
    assert with_secrets.secrets.data_api_key.get_secret_value() == "key-value-123"
    assert config_hash(with_secrets) == config_hash(without)

    rendered = repr(with_secrets) + str(with_secrets) + with_secrets.model_dump_json()
    for value in environ.values():
        assert value not in rendered
    assert "secret-value-456" not in repr(with_secrets.secrets)


@pytest.mark.parametrize(
    "name",
    ["LIVE_WEBULL_APP_KEY", "WEBULL_APP_KEY", "WEBULL_APP_SECRET", "WEBULL_OPENAPI_TOKEN_DIR"],
)
def test_live_or_production_variables_block_non_live_modes(config_dir: Path, name: str) -> None:
    with pytest.raises(ConfigError, match=name) as info:
        load_settings(Mode.PAPER, config_dir, environ={name: "must-not-appear"})
    assert "must-not-appear" not in str(info.value)


def test_sandbox_variables_are_allowed() -> None:
    assert_no_live_credentials({"WEBULL_SANDBOX_APP_KEY": "public-test-key"}, Mode.PAPER)
    assert_no_live_credentials({"WEBULL_APP_KEY": "x"}, Mode.LIVE)


def test_backtest_section_rules(config_dir: Path) -> None:
    _write(config_dir / "backtest.yaml", "mode: backtest\nbroker_environment: sim\n")
    with pytest.raises(ConfigError, match="backtest"):
        load_settings(Mode.BACKTEST, config_dir, environ={})
    _write(
        config_dir / "backtest.yaml",
        "mode: backtest\nbroker_environment: sim\nbacktest:\n  start: 2024-01-02\n"
        "  end: 2024-01-02\n  initial_capital_usd: 323\n",
    )
    with pytest.raises(ConfigError, match="end must be after start"):
        load_settings(Mode.BACKTEST, config_dir, environ={})


def test_canonical_encoding_of_dates_and_enums() -> None:
    from datetime import UTC, date, datetime

    from aitrader.config.loader import _canonical

    assert _canonical(date(2026, 1, 2)) == "2026-01-02"
    assert _canonical(datetime(2026, 1, 2, tzinfo=UTC)) == "2026-01-02T00:00:00+00:00"
    assert _canonical(Mode.PAPER) == "paper"
    with pytest.raises(TypeError):
        _canonical(object())
