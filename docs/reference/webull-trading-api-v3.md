# Webull Japan trading API (v3): response shapes used by the sandbox adapter

Source: Webull Securities Japan developer reference, read by the designer on 2026-10-24
(index: https://developer.webull.co.jp/apis/llms.txt; each page below has a `.md` version).
Only the fields the adapter uses are listed. The tables restate the documentation; nothing here
was observed from a real API call. Real behaviour is checked by `aitrader sandbox check` (SB-1 to
SB-3, SB-6) and any difference is fixed in a later PR.

No credentials, account numbers or balances appear in this file. Example values are invented.

## Common rules

- Host: test (UAT) `jp-openapi-alb.uat.webullbroker.com`. Production is never used (document 14).
- Header `x-version`: `v2` or `v3`, default `v3`. The adapter uses whatever the pinned SDK 3.0.4
  sends and does not set it itself. The SDK builds the signature headers.
- Every number (price, quantity, cash) is a **JSON string**. Parse with `Decimal(str)`; an empty
  string or `null` in an optional field means "absent".
- Times: `*_time_at` fields are ISO 8601 UTC (`yyyy-MM-dd'T'HH:mm:ss.SSSZ`). The epoch-millisecond
  fields `place_time` and `filled_time` are deprecated: do not use them.
- Errors: the body is `{"error_code": "<CODE>", "message": "<text>"}`. Documented statuses are
  401 (auth or permission), 417 (business rule, e.g. invalid parameter or order rejected) and 500.
  The trading FAQ also documents 403 for missing or invalid credentials. 429 is not documented.
- Documented codes: `UNAUTHORIZED` (401), `INVALID_PARAMETER` (417), `SYSTEM_ERROR` (500),
  `OPENAPI_NO_NIGHT_TRADING_TIME` (417, night orders outside the night session),
  `INVALID_TOKEN` (usually a signature mismatch, per the FAQ). No code is documented for a
  duplicate `client_order_id`, an unknown order or a fractional-rule reject.
- `client_order_id`: at most 32 characters from `A-Z a-z 0-9 - _`, unique per account.

## Endpoints

| Call | Method and path | Request | 200 body (root) |
|---|---|---|---|
| Account list | `GET /trading/accounts/list` | none | **array** of account objects |
| Balance | `GET /trading/assets/balances/get` | `account_id` | **object** |
| Positions | `GET /trading/assets/positions/list` | `account_id` | **array** of positions (no paging) |
| Instruments | `GET /trading/instruments/stocks/profiles/list` | `category=US_STOCK`, `symbols` (≤100, comma separated), `pagination_key` | `{"data": [...], "pagination_key": "..."}` |
| Preview | `POST /trading/orders/preview` | `{"account_id", "new_orders": [order]}` | `{"estimated_cost", "estimated_transaction_fee"}` |
| Place | `POST /trading/orders/place` | `{"account_id", "new_orders": [order]}` | `{"client_order_id", "order_id"}` |
| Cancel | `POST /trading/orders/cancel` | `{"account_id", "client_order_id"}` | `{"client_order_id", "order_id"}` |
| Order detail | `GET /trading/orders/get` | `account_id`, `client_order_id` | `{"client_order_id", "combo_type", "orders": [item]}` |
| Open orders | `GET /trading/orders/open-orders/list` | `account_id`, `pagination_key` | `{"data": [group], "pagination_key"}` |
| Order history | `GET /trading/orders/historical-orders/list` | `account_id`, `start_time`, `end_time`, `pagination_key` | `{"data": [group], "pagination_key"}` |

Paging: pass the previous response's `pagination_key`; when it is absent the page is the last.
Order history defaults to the last 7 days and reaches back at most 6 months; `start_time` and
`end_time` use `yyyy-MM-dd'T'HH:mm:ss.SSS'Z'`.

### Account object (account list)

`account_id`, `account_number`, `account_type` (`CASH`, `US_MARGIN`, `US_OPTION`),
`account_class`, `account_label`, `user_id`. The adapter uses only `account_id` and
`account_type`, and never writes `account_number` or `user_id` anywhere.

### Balance object

Top level: `total_asset_currency` (`JPY` or `USD`), `total_cash_balance`, `total_market_value`,
`total_unrealized_profit_loss`, margin-only fields (ignored), and `account_currency_assets`, an
array of:

| Field | Required | Meaning |
|---|---|---|
| `currency` | yes | `JPY` or `USD` |
| `cash_balance` | yes | cash in this currency |
| `market_value` | yes | holdings value in this currency |
| `buying_power` | yes | buying power in this currency |
| `unrealized_profit_loss` | yes | open P&L |

**There is no settled / unsettled split.** See the mapping rule in `.ai/CURRENT_TASK.md`
(review round 2).

### Position object

Required: `currency`, `quantity` (decimal for US fractional), `available_quantity`,
`market_value`, `symbol`, `instrument_id`, `instrument_type` (`EQUITY`, `OPTION`), `last_price`,
`cost_price`, `unrealized_profit_loss`, `base_currency`, `fx_rate`, `base_currency_market_value`.
Optional: `position_id`, `account_tax_type`, `legs` (options only).

`cost_price` is documented only as "cost basis". Whether it is per share or a total is
**not stated**: see the mapping rule in review round 2.

### Instrument object (`data[]`)

`symbol`, `instrument_id`, `category`, `status` (`OC` tradable, `CO` liquidate only,
`NT` not tradable), `fractionable` (boolean), `shortable`, `marginable`,
`overnight_trading_supported`, `lot_size`, `currency`, `sub_category` (`ETF`, `COMMON_STOCK`, …).

### Order request (`new_orders[]`, place and preview)

| Field | Value the adapter sends |
|---|---|
| `combo_type` | `NORMAL` |
| `client_order_id` | ours, already validated |
| `instrument_type` | `EQUITY` |
| `market` | `US` |
| `symbol` | e.g. `SPY` |
| `order_type` | `LIMIT` or `MARKET` |
| `entrust_type` | `QTY` (the adapter never uses `AMOUNT`) |
| `side` | `BUY` or `SELL` (never `SHORT`) |
| `time_in_force` | `DAY` |
| `support_trading_session` | `CORE` |
| `quantity` | decimal string, e.g. `"0.01000"` or `"1"` |
| `limit_price` | only for `LIMIT` |
| `account_tax_type` | from configuration, default `SPECIFIC` |

`total_cash_amount` is documented for place (with `AMOUNT`) but not for preview; the adapter does
not use it.

### Order item (detail `orders[]`, and `data[].orders[]` in open orders and history)

Required: `client_order_id`, `order_id`, `symbol`, `side`, `status`, `order_type`,
`time_in_force`, `total_quantity`, `place_time_at`, `account_tax_type`.
Optional: `filled_quantity`, `filled_price` (average price of the filled quantity, may be `"0"`
or absent when nothing filled), `filled_time_at` (time of the last execution), `limit_price`,
`stop_price`, `instrument_type`, `support_trading_session`, `commission`
(`actual_commission`, `receivable_commission`), `fees[]` (`type`, `actual_value`,
`receivable_value`).

`status` values: `PENDING`, `SUBMITTED`, `PARTIAL_FILLED`, `FILLED`, `CANCELLED`, `FAILED`.
There is no separate "rejected" or "expired" value; the documentation does not say how a DAY
order that expires is shown.

## Pages read

- https://developer.webull.co.jp/apis/docs/sdk (hosts)
- https://developer.webull.co.jp/apis/docs/reference/account-list
- https://developer.webull.co.jp/apis/docs/reference/account-balance
- https://developer.webull.co.jp/apis/docs/reference/account-position
- https://developer.webull.co.jp/apis/docs/reference/instrument-list
- https://developer.webull.co.jp/apis/docs/reference/common-order-preview
- https://developer.webull.co.jp/apis/docs/reference/common-order-place
- https://developer.webull.co.jp/apis/docs/reference/common-order-cancel
- https://developer.webull.co.jp/apis/docs/reference/order-detail
- https://developer.webull.co.jp/apis/docs/reference/order-open
- https://developer.webull.co.jp/apis/docs/reference/order-history
- https://developer.webull.co.jp/apis/docs/trade-api/faq
