"""Layered configuration loading: ``base.yaml`` → ``<mode>.yaml`` → secrets from the environment.

Limits never come from environment variables. Credential-like keys are rejected in files.
LIVE cannot be loaded in this build.
"""

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from aitrader.config.errors import ConfigError, LiveDisabledError
from aitrader.config.hard_caps import HARD_CAPS
from aitrader.config.settings import RiskLimits, Secrets, Settings
from aitrader.domain import Mode

_CREDENTIAL_KEY_PARTS = ("webull", "app_key", "app_secret", "secret", "token", "password")


class _DecimalSafeLoader(yaml.SafeLoader):
    """YAML loader that reads floating-point scalars as Decimal (never as float)."""


def _construct_decimal(loader: yaml.SafeLoader, node: yaml.Node) -> Decimal:
    raw = loader.construct_scalar(node)  # type: ignore[arg-type]
    try:
        return Decimal(str(raw))
    except ArithmeticError as exc:
        raise ConfigError(f"not a finite number: {raw!r}") from exc


_DecimalSafeLoader.add_constructor("tag:yaml.org,2002:float", _construct_decimal)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.load(fh, Loader=_DecimalSafeLoader)  # noqa: S506 - SafeLoader subclass
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be a mapping")
    return data


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _reject_credential_keys(data: Mapping[str, Any], path: str = "") -> None:
    for key, value in data.items():
        full = f"{path}.{key}" if path else str(key)
        lowered = str(key).lower()
        if any(part in lowered for part in _CREDENTIAL_KEY_PARTS):
            raise ConfigError(
                f"credential-like key '{full}' is not allowed in configuration files; "
                "secrets come only from AITRADER_* environment variables, and Webull "
                "production credentials only exist on the LIVE host"
            )
        if isinstance(value, Mapping):
            _reject_credential_keys(value, full)


def assert_no_live_credentials(environ: Mapping[str, str], mode: Mode) -> None:
    """Refuse to run outside LIVE if LIVE or Webull production variables are present.

    Webull Sandbox variables (``WEBULL_SANDBOX_*``) are allowed; they hold public test keys.
    Only variable names are reported, never values.
    """
    if mode is Mode.LIVE:
        return
    offending = sorted(
        name
        for name in environ
        if name.startswith("LIVE_")
        or name == "WEBULL_OPENAPI_TOKEN_DIR"
        or (name.startswith("WEBULL_") and not name.startswith("WEBULL_SANDBOX_"))
    )
    if offending:
        raise ConfigError(
            "LIVE or Webull production variables must not exist outside the LIVE host: "
            + ", ".join(offending)
        )


def enforce_hard_caps(limits: RiskLimits) -> None:
    """Raise :class:`ConfigError` if any limit is looser than its compiled-in hard cap."""
    for name, cap in HARD_CAPS.items():
        value = Decimal(getattr(limits, name))
        if value > cap:
            raise ConfigError(f"risk.{name} = {value} exceeds the hard cap {cap}")


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"{location}: {err['msg']}")
    return "invalid configuration: " + "; ".join(parts)


def load_settings(
    mode: Mode, config_dir: Path, environ: Mapping[str, str] | None = None
) -> Settings:
    """Load and validate the settings for ``mode`` from ``config_dir``."""
    if mode is Mode.LIVE:
        raise LiveDisabledError("LIVE is disabled in this build")
    env = os.environ if environ is None else environ
    assert_no_live_credentials(env, mode)

    base = _read_yaml(config_dir / "base.yaml")
    override = _read_yaml(config_dir / f"{mode.value}.yaml")
    merged = _deep_merge(base, override)
    _reject_credential_keys(merged)

    file_mode = merged.get("mode")
    if file_mode is not None and file_mode != mode.value:
        raise ConfigError(f"{mode.value}.yaml declares mode '{file_mode}', expected '{mode.value}'")
    merged["mode"] = mode.value

    secrets = Secrets.model_validate(
        {
            field: env.get(f"AITRADER_{field.upper()}")
            for field in Secrets.model_fields
            if env.get(f"AITRADER_{field.upper()}") is not None
        }
    )
    try:
        settings = Settings.model_validate({**merged, "secrets": secrets})
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc
    enforce_hard_caps(settings.risk)
    return settings


def _canonical(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"cannot canonicalise {type(value).__name__}")


def config_hash(settings: Settings) -> str:
    """SHA-256 of the effective non-secret settings in canonical JSON.

    Stable across runs, key order and equivalent decimal spellings (``0.5`` vs ``0.50``).
    """
    data = settings.model_dump(mode="python", exclude={"secrets"})
    text = json.dumps(
        data, default=_canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
