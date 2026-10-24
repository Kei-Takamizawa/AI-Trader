"""Only codes documented in the designer's pinned trading API reference are known."""

KNOWN_CODES = {
    # Missing permission or authentication; /reference/account-list.
    "UNAUTHORIZED": (
        "Authentication or permission refused",
        "https://developer.webull.co.jp/apis/docs/reference/account-list",
    ),
    # Invalid order input; /reference/common-order-place.
    "INVALID_PARAMETER": (
        "Invalid request parameter",
        "https://developer.webull.co.jp/apis/docs/reference/common-order-place",
    ),
    # Server failure; /reference/common-order-place.
    "SYSTEM_ERROR": (
        "Server error",
        "https://developer.webull.co.jp/apis/docs/reference/common-order-place",
    ),
    # Night order outside the session; /reference/common-order-place.
    "OPENAPI_NO_NIGHT_TRADING_TIME": (
        "Outside night trading hours",
        "https://developer.webull.co.jp/apis/docs/reference/common-order-place",
    ),
    # Usually a signature mismatch; /trade-api/faq.
    "INVALID_TOKEN": (
        "Invalid token or signature",
        "https://developer.webull.co.jp/apis/docs/trade-api/faq",
    ),
}

# Observed by the operator in document 14, section 8.1 (2026-10-24).
KNOWN_CODES.update(
    {
        "OPENAPI_REPEAT_REQUEST": (
            "Duplicate client order ID",
            "docs/design/14-webull-openapi-research.md#81-first-sandbox-results-2026-10-24",
        ),
        "OPENAPI_ORDER_CANNOT_OPERATE": (
            "Operation not allowed in the order state",
            "docs/design/14-webull-openapi-research.md#81-first-sandbox-results-2026-10-24",
        ),
        "OPENAPI_FRACTION_V2_BUY_AMOUNT_TOO_SMALL": (
            "Fractional buy below the minimum amount",
            "docs/design/14-webull-openapi-research.md#81-first-sandbox-results-2026-10-24",
        ),
    }
)
