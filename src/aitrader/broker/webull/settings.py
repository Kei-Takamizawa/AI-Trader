"""Sandbox configuration has no production or file-based credential fields."""

import os
from collections.abc import Mapping

from pydantic import SecretStr

from aitrader.config.errors import ConfigError
from aitrader.config.settings import WebullSandboxSettings

SANDBOX_HOST = "jp-openapi-alb.uat.webullbroker.com"


def validate_host(host: str) -> str:
    """Accept only the documented bare trading UAT host, before building SDK objects."""
    if host.lower() != SANDBOX_HOST:
        raise ConfigError("webull_sandbox.endpoint must be the allowed bare UAT trading host")
    return host.lower()


class SandboxCredentials:
    """Only the three sandbox environment variables may supply credentials."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        env = os.environ if environ is None else environ
        names = ("APP_KEY", "APP_SECRET", "ACCOUNT_ID")
        if any(not env.get(f"WEBULL_SANDBOX_{name}") for name in names):
            raise ConfigError(
                "set WEBULL_SANDBOX_APP_KEY, WEBULL_SANDBOX_APP_SECRET "
                "and WEBULL_SANDBOX_ACCOUNT_ID"
            )
        self.app_key = SecretStr(env["WEBULL_SANDBOX_APP_KEY"])
        self.app_secret = SecretStr(env["WEBULL_SANDBOX_APP_SECRET"])
        self.account_id = SecretStr(env["WEBULL_SANDBOX_ACCOUNT_ID"])

    def redact(self, text: str) -> str:
        for secret in (self.app_key, self.app_secret, self.account_id):
            text = text.replace(secret.get_secret_value(), "[redacted]")
        return text


__all__ = ["SandboxCredentials", "WebullSandboxSettings"]
