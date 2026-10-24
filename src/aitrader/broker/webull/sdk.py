"""Pinned SDK request builders and a single-attempt, sandbox-only HTTP boundary.

Use the SDK initializer directly, without TradeClient or a 2FA flow. Its config and token
requests must pass the same UAT endpoint guard as trading requests.
"""

import logging
from datetime import timedelta
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from webull.core.client import ApiClient  # type: ignore[import-untyped]
from webull.core.endpoint.resolver_endpoint_request import (  # type: ignore[import-untyped]
    ResolveEndpointRequest,
)
from webull.core.exception.exceptions import (  # type: ignore[import-untyped]
    ClientException,
    ServerException,
)
from webull.core.http.initializer.client_initializer import (  # type: ignore[import-untyped]
    ClientInitializer,
)
from webull.data.request.get_instruments_request_v2 import (  # type: ignore[import-untyped]
    GetInstrumentsRequestV2,
)
from webull.trade.request.v2.get_account_balance_request import (  # type: ignore[import-untyped]
    AccountBalanceRequest,
)
from webull.trade.request.v2.get_account_list_request import (  # type: ignore[import-untyped]
    GetAccountListRequest,
)
from webull.trade.request.v2.get_account_positions_request import (  # type: ignore[import-untyped]
    AccountPositionsRequest,
)
from webull.trade.request.v3.cancel_order_request import (  # type: ignore[import-untyped]
    CancelOrderRequest,
)
from webull.trade.request.v3.get_order_detail_request import (  # type: ignore[import-untyped]
    OrderDetailRequest,
)
from webull.trade.request.v3.get_order_history_request_v2 import (  # type: ignore[import-untyped]
    OrderHistoryRequestV2,
)
from webull.trade.request.v3.get_order_open_request_v2 import (  # type: ignore[import-untyped]
    OrderOpenRequestV2,
)
from webull.trade.request.v3.place_order_request import (  # type: ignore[import-untyped]
    PlaceOrderRequest,
)
from webull.trade.request.v3.preview_order_request import (  # type: ignore[import-untyped]
    PreviewOrderRequest,
)

from aitrader.broker.port import BrokerAuthError
from aitrader.broker.webull.settings import SandboxCredentials, validate_host
from aitrader.clock import Clock
from aitrader.config.errors import ConfigError

SDK_LOGGERS = (
    "webull",
    "webull.core",
    "webull.data",
    "webull.core.client",
    "webull.core.http.response",
    "webull.core.retry.retry_condition",
    "webull.core.auth.signers.app_key_signer",
    "webull.core.auth.composer.default_signature_composer",
    "webull.core.http.initializer.client_initializer",
    "webull.core.http.initializer.token.token_storage",
    "webull.core.http.initializer.token.token_manager",
    "webull.data.internal.quotes_client",
    "webull.trade.events.signature_composer",
    "webull.trade.trade_events_client",
)


def silence_sdk() -> None:
    for name in SDK_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL + 1)
        logger.propagate = False
        logger.handlers = [logging.NullHandler()]


class Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


class Transport(Protocol):
    def send(self, request: Any) -> Response: ...


def unknown_token_facts() -> dict[str, Any]:
    return {
        "token_check_enabled": None,
        "status": None,
        "expires_present": False,
        "error_code": None,
        "http_status": None,
        "latency_ms": "0",
    }


class TokenInitializationError(BrokerAuthError):
    """Carry only safe initialization metadata when no broker could be constructed."""

    def __init__(self, facts: dict[str, Any]) -> None:
        super().__init__("sandbox token initialization refused")
        self.facts = facts
        self.code = facts["error_code"]
        self.http_status = facts["http_status"]


class SDKTransport:
    """Keep endpoint resolution and request pinning ahead of every SDK HTTP call."""

    def __init__(self, credentials: SandboxCredentials, endpoint: str, clock: Clock) -> None:
        host = validate_host(endpoint)
        silence_sdk()
        self._client = ApiClient(
            credentials.app_key.get_secret_value(),
            credentials.app_secret.get_secret_value(),
            "jp",
            auto_retry=False,
            token_check_duration_seconds=10,
            token_check_interval_seconds=5,
        )
        self._client.add_endpoint("jp", host)
        self._check_host(host)
        self._token_dir = TemporaryDirectory(prefix="aitrader-sandbox-")
        self._client.set_token_dir(self._token_dir.name)
        self.host = host
        self.token_facts = unknown_token_facts()
        started = clock.now()
        get_response = self._client.get_response

        def initializing_response(request: Any) -> Response:
            self._check_host(host)
            request.set_endpoint(host)
            response: Response = get_response(request)
            self.token_facts["http_status"] = response.status_code
            body = response.json()
            if isinstance(body, dict):
                token = body.get("token")
                if isinstance(token, str) and token:
                    credentials.add_token(token)
                if request.get_action_name() == "/openapi/config":
                    enabled = body.get("token_check_enabled")
                    if isinstance(enabled, bool):
                        self.token_facts["token_check_enabled"] = enabled
                else:
                    status = body.get("status")
                    if isinstance(status, str):
                        self.token_facts["status"] = credentials.redact(status)
                    self.token_facts["expires_present"] = body.get("expires") not in (None, "")
                code = body.get("error_code")
                if isinstance(code, str):
                    self.token_facts["error_code"] = credentials.redact(code)
            if response.status_code in {401, 403}:
                raise TokenInitializationError(self.token_facts)
            return response

        self._client.get_response = initializing_response
        try:
            self._check_host(host)
            ClientInitializer.initializer(self._client)
        except (ClientException, ServerException) as exc:
            self.token_facts["error_code"] = credentials.redact(exc.get_error_code())
            if isinstance(exc, ServerException):
                self.token_facts["http_status"] = exc.get_http_status()
            self.close()
            raise TokenInitializationError(self.token_facts) from None
        except OSError, ValueError:
            self.close()
            raise TokenInitializationError(self.token_facts) from None
        except ConfigError, TokenInitializationError:
            self.close()
            raise
        finally:
            token = self._client.get_token()
            if isinstance(token, str) and token:
                credentials.add_token(token)
            self._client.get_response = get_response
            self.token_facts["latency_ms"] = str(
                (clock.now() - started) // timedelta(milliseconds=1)
            )

    def _check_host(self, host: str) -> None:
        resolved = self._client._endpoint_resolver.resolve(ResolveEndpointRequest("jp"))
        if resolved != host:
            raise ConfigError("SDK resolved trading host differs from the allowed UAT host")

    def send(self, request: Any) -> Response:
        self._check_host(self.host)
        request.set_endpoint(self.host)
        response: Response = self._client.get_response(request)
        return response

    def close(self) -> None:
        self._token_dir.cleanup()


def build_request(operation: str, account_id: str, params: dict[str, Any]) -> Any:
    """Use SDK setters, including its v3 pagination and order-specific headers."""
    builders = {
        "accounts": GetAccountListRequest,
        "balance": AccountBalanceRequest,
        "positions": AccountPositionsRequest,
        "instruments": GetInstrumentsRequestV2,
        "preview": PreviewOrderRequest,
        "place": PlaceOrderRequest,
        "cancel": CancelOrderRequest,
        "detail": OrderDetailRequest,
        "open": OrderOpenRequestV2,
        "history": OrderHistoryRequestV2,
    }
    request = builders[operation]()
    if operation not in {"accounts", "instruments"}:
        request.set_account_id(account_id)
    for key, value in params.items():
        getattr(request, f"set_{key}")(value)
    if operation == "place":
        request.add_custom_headers_from_order(params["new_orders"])
    return request


__all__ = ["ApiClient", "ClientException", "ClientInitializer", "ServerException"]
