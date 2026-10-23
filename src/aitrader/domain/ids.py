"""Deterministic identifiers for intents and broker orders (document 05, DUP-1 and DUP-2)."""

import base64
import hashlib
from datetime import datetime

from aitrader.domain.enums import Side
from aitrader.domain.types import require_utc

_ID_VERSION = "v1"
_CLIENT_ORDER_PREFIX_LEN = 26
MAX_ATTEMPT = 99_999
"""Attempt numbers have at most five digits, so client order IDs stay within 32 characters."""


def make_intent_id(
    strategy_id: str,
    strategy_version: str,
    symbol: str,
    side: Side,
    signal_ts: datetime,
    seq: int,
) -> str:
    """Return a deterministic SHA-256 hex ID for an order intent.

    The same inputs always give the same ID; any change in an input changes it.
    """
    if seq < 0:
        raise ValueError("seq must not be negative")
    parts = (
        _ID_VERSION,
        strategy_id,
        strategy_version,
        symbol,
        Side(side).value,
        require_utc(signal_ts).isoformat(),
        str(seq),
    )
    for part in parts:
        if "\x1f" in part:
            raise ValueError("identifier parts must not contain the unit separator character")
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def make_client_order_id(intent_id: str, attempt: int) -> str:
    """Return the broker-facing order ID for an intent and attempt number.

    Format: 26-character base32 prefix of SHA-256(intent_id), '-', attempt. Always at most
    32 characters from ``[A-Z2-7-0-9]``, which satisfies Webull's ``client_order_id`` rule.
    """
    if not intent_id:
        raise ValueError("intent_id must not be empty")
    if not 1 <= attempt <= MAX_ATTEMPT:
        raise ValueError(f"attempt must be between 1 and {MAX_ATTEMPT}")
    digest = hashlib.sha256(intent_id.encode("utf-8")).digest()
    prefix = base64.b32encode(digest).decode("ascii")[:_CLIENT_ORDER_PREFIX_LEN]
    return f"{prefix}-{attempt}"
