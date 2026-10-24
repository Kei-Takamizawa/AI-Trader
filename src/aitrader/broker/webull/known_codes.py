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
