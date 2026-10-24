"""Pinned SDK request builders and a single-attempt, sandbox-only HTTP boundary.

TradeClient is deliberately not constructed: its initializer makes additional token/config
requests. The raw ApiClient and official request classes suffice for sandbox trading without 2FA.
"""

import logging
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

from aitrader.broker.webull.settings import SandboxCredentials, validate_host
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


class SDKTransport:
    """Keep endpoint resolution and request pinning ahead of every SDK HTTP call."""

    def __init__(self, credentials: SandboxCredentials, endpoint: str) -> None:
        host = validate_host(endpoint)
        silence_sdk()
        self._client = ApiClient(
            credentials.app_key.get_secret_value(),
            credentials.app_secret.get_secret_value(),
            "jp",
            auto_retry=False,
        )
        self._client.add_endpoint("jp", host)
        self._check_host(host)
        self._token_dir = TemporaryDirectory(prefix="aitrader-sandbox-")
        self._client.set_token_dir(self._token_dir.name)
        self.host = host

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


__all__ = ["ApiClient", "ClientException", "ServerException"]
