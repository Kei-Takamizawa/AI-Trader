# 14. Webull Securities Japan OpenAPI: Research Findings

Research date: **2026-10-23**. Scope: everything this project needs from Webull Securities Japan ("ウィブル証券") to trade US stocks and ETFs automatically. Sources are the official Webull Japan website, the official developer documentation (including its Markdown/`llms.txt` versions), the official Python SDK source and the FSA registry. Tags: **[V]** verified in an official source today, **[V-old]** stated only in Webull's deprecated documentation, **[S]** secondary source, **[U]** not documented / must be confirmed.

> Webull is the **first-choice live broker** by owner decision (2026-10-23). This document records what the API can and cannot do, so the design can rely on facts and so the open points can be tested in the sandbox and with Webull support **after design approval**. No account was opened and no API was called for this research.

## 1. Summary

| Area | Finding |
|---|---|
| Legal status | ウィブル証券株式会社, Kanto Local Finance Bureau (FIBO) **No. 48**, JSDA member [V: FSA registry and Webull website footer]. The developer docs' "About" page shows "No. 3424", which does not match the FSA registry — a documentation error [V]. |
| API scope | US stocks: cash trading (24-hour and **fractional** supported), margin, options; Japanese stocks: cash only [V]. |
| Fractional orders via API | **Supported**: `entrust_type: AMOUNT` with `total_cash_amount`, or decimal `quantity`, for US stocks [V]. |
| Fees | US stock commission **free** (cash and margin) since 2026-07-27; FX **JPY 0.15 per USD** per conversion; US regulatory fees (CAT, FINRA, SEC) passed through [V]. |
| Tax account | Tokutei (特定) and general (一般) accounts supported; every order must state `account_tax_type` (`SPECIFIC` or `GENERAL`) [V]. |
| Official SDK | `webull-openapi-python-sdk` 3.0.4 (2026-10-17), Apache-2.0, Python 3.8–3.14 [V]. |
| Test environment | A **sandbox with six shared, publicly listed test accounts**; their orders and positions can change at any time [V]. Good for connection and order-flow tests, **not** for running a portfolio. |
| Unattended operation | Possible, with constraints: optional 2FA token that a human must verify once and that lapses after 15 days without API calls; App Key validity that the deprecated docs gave as 45 days by default; market data needs a separate OpenAPI subscription; frequent API and SDK changes. Details in section 6. |

## 2. Supported features relevant to this project

| Feature | Detail | Tag |
|---|---|---|
| Protocols | HTTPS REST (trading, accounts, historical/snapshot data), MQTT (real-time market data), gRPC (order events) | [V] |
| Authentication | HMAC signature on every request (App Key + App Secret); optional 2FA access token | [V] |
| Order types, US stocks | `MARKET`, `LIMIT`, `STOP_LOSS`, `STOP_LOSS_LIMIT` | [V] |
| Time in force | `DAY`, `GTC` (GTC lasts until filled or cancelled, "typically 90 days" maximum) | [V] |
| Sessions | `CORE` (regular hours), `ALL` (with extended hours), `NIGHT`, `ALL_DAY` (overnight, about 900 symbols) | [V] |
| Fractional shares | `entrust_type: AMOUNT` + `total_cash_amount`, or decimal `quantity`. Buy minimum **USD 1**, quantity step **0.00001**; sell: no minimum amount, quantity ≥ 0.00001. Only some symbols (incl. ETFs); instrument flag `fractionable` | [V] |
| Order preview | `POST /trading/orders/preview` returns `estimated_cost` and `estimated_transaction_fee` (exchange, clearing and commission) | [V] |
| Idempotency key | `client_order_id`: required, unique per account, max 32 characters, letters/digits/`-`/`_` | [V] |
| Order queries | Open orders, order detail by `client_order_id`, order history (default 7 days, **maximum look-back 6 months**) | [V] |
| Fee breakdown per order | Order detail returns `commission` (actual / receivable) and `fees[]` (type, actual, receivable) | [V] |
| Order events | gRPC stream: `FILLED` (partial), `FINAL_FILLED`, `PLACE_FAILED`, `MODIFY_SUCCESS/FAILED`, `CANCEL_SUCCESS/FAILED`; control events `Ping`, `AuthError`, `NumOfConnExceed`, `SubscribeExpired` | [V] |
| Accounts | Account list with `account_type` (`CASH`, `US_MARGIN`, `US_OPTION`); balance per currency (JPY, USD) with cash, market value, buying power; positions with quantity, available quantity, cost, unrealised P&L, reference FX rate | [V] |
| Instruments | Tradable status (`OC` tradable, `CO` liquidate only, `NT` not tradable), `fractionable`, `overnight_trading_supported`, `sub_category` (e.g. `ETF`), lot size, currency | [V] |
| Rate limits (trading) | Place / replace / cancel 600 per 60 s; preview 150 per 10 s; balance, positions and order queries 2 per 2 s; account list and instruments 10 per 30 s | [V] |
| Market data | Snapshot, quotes (depth), ticks, historical bars (S5 … Y; ≤ 100 symbols and ≤ 1,200 bars per request, M1 ≤ 1,650), MQTT streaming of quote/snapshot/tick | [V] |
| Fundamentals | Earnings calendar (last 6 quarters with `expected_publish_date`), 3 most recent filings, dividend calendar, company profile, analyst data, LLM-generated news summaries (English) | [V] |
| AI tooling | Cloud MCP server (OAuth), local MCP via `uvx`, Agent Skills, CLI; options for read-only toolsets, symbol allow-lists and order-size limits | [V] |

## 3. Not supported or not documented (treat as unavailable)

| Feature | Status | Consequence for the design |
|---|---|---|
| Stop orders for **fractional** positions | Fractional orders are documented as market or limit only [V] | Protective stops for fractional positions are **system-side** (document 05, STOP-2) |
| Resting fractional limit orders | A fractional buy limit must be **above the best ask** and a sell limit **below the best bid** (equal not allowed) [V] — i.e. marketable limits only | Entries/exits use marketable limits with a price cap; no passive limit orders for fractional sizes |
| Fractional trading outside regular hours | Regular session only (09:30–16:00 ET) [V] | Matches our execution window |
| Combo orders (OCO, OTO, bracket) via the JP API | Only `combo_type: NORMAL` is documented for stocks [V]; the app offers IFD+OCO and OCO [V] | Exit logic stays in our engine |
| Trailing stop via API | Not in the order-type enum [V] | Trailing logic (if any) is system-side |
| Market/limit-on-open or -close | Not offered [V] | Execution window design unchanged |
| FX conversion, deposits, withdrawals via API | Not documented [U] | FX conversion is a manual owner action; recorded in the ledger from statements |
| Settlement currency choice (JPY vs USD settlement) via API | Not documented; the app offers both [V app, U API] | Risk engine must use **USD buying power only** and never rely on implicit conversion; behaviour to confirm |
| Read-only (data-only) API key | Not documented; App Key/Secret appear to carry the account's trading permission [U] | A production key is never placed on non-LIVE hosts (section 7) |
| Per-execution fill list | Only order-level `filled_quantity`, average `filled_price`, last `filled_time` [V] | Ledger stores order-level fills; partial fills from events |
| Account cash-transaction history (dividends, FX, deposits) | Not documented [U] | Reconcile cash from balances; record dividends and FX from statements |
| Split/dividend adjustment of historical bars | Not documented [U] | Use an adjusted research data set for backtests; verify Webull bars before use |
| Private paper-trading account | Not offered; the sandbox uses shared public accounts [V] | PAPER runs on our own virtual portfolio |
| Short selling | API offers `SHORT` [V] | Forbidden by our hard caps |

## 4. Data usage conditions

| Condition | Detail | Tag |
|---|---|---|
| Subscription required | US stock/ETF market data (historical **and** real-time) needs an active **OpenAPI** market data subscription; without it calls return 403 | [V] |
| Free tier | Nasdaq Basic (Level 1) is free for OpenAPI | [V] |
| Paid tiers | Nasdaq TotalView (50-level depth), OPRA (options), Overnight Consolidated Level 2, Order Flow — prices not in the docs | [V names, U prices] |
| Separate from the app | Subscriptions bought in the app or desktop do not apply to OpenAPI | [V] |
| One device at a time | Level 1/Level 2 data may be used by only one device at a time per subscription | [V] |
| Delayed permission | Bar requests mention that "delayed permission will automatically offset the time" | [V text, U details] |
| Limits | Data API 300 requests/min; MQTT max 5 concurrent connections per App Key, max 3 pushes/s per connection; reconnect after about 1 minute when the limit is hit; subscriptions are **not** restored after a reconnect | [V] |
| Formats | JSON over HTTP (prices as strings, timestamps in ms); MQTT payloads in Protocol Buffers | [V] |
| News summaries | Generated by an LLM for a watchlist, English only | [V] |
| Redistribution / professional-user rules | Not found in the docs | [U] — design rule: never redistribute raw quotes; reports and the Muse bundle contain only derived figures |

## 5. Costs (US stocks, cash account)

| Item | Amount | Tag |
|---|---|---|
| Commission | Free (since 2026-07-27, announced as permanent) | [V] |
| FX spread | JPY 0.15 per USD each way, added to QUICK's reference rate; with USD held in the account only conversions cost | [V] |
| CAT fee | USD 0.000003 per share (exchange trades), USD 0.00000003 (OTC), on buys and sells | [V] |
| FINRA fee (sales only) | USD 0.000195 per share, min USD 0.01, max USD 9.79 per trade; charged **separately for the whole-share part and the fractional part** | [V] |
| SEC fee (sales only) | USD 0.0000206 per USD 1 sold, rounded up to the smallest currency unit | [V] |
| ADR fee | USD 0.01–0.03 per share periodically (avoid ADRs) | [V] |
| API usage | Free; same fees as the app | [V] |
| Account opening and maintenance | Free | [V] |
| Deposits | Quick deposit free; normal bank transfer at the customer's cost | [V] |
| JPY withdrawals | ¥110 (MUFG), ¥330 (other banks, under ¥30,000), ¥440 (¥30,000 or more) | [V] |
| Market data | Nasdaq Basic free; advanced subscriptions paid | [V / U prices] |

Worked example for a ¥50,000 account (USD/JPY 155 [A]):

- Converting ¥50,000 to USD costs about **¥48** (0.15 × ≈ USD 322); converting back costs about the same. With USD held, there is no FX cost per trade.
- Selling a USD 37 fractional position costs about **USD 0.02–0.03** (FINRA minimum, possibly twice, plus SEC minimum); the buy costs a negligible CAT fee.
- Round-trip cost ≈ 0.08% of a USD 37 position plus spread/slippage — about 4% of the 0.5% risk budget, consistent with document 02 §4.
- If trades were settled in JPY instead (conversion per trade), FX alone would add ≈ 0.19% per round trip (≈ USD 0.07, about 4% of the risk budget). Holding USD is therefore the default.

## 6. Constraints for unattended operation

| # | Constraint | Tag | Design response |
|---|---|---|---|
| U1 | **2FA token (optional):** creation sends an SMS; a human must confirm in the Webull app within 5 minutes. Status `NORMAL` / `PENDING` / `INVALID` / `EXPIRED`. Becomes `INVALID` after **15 consecutive days without API calls**; the CLI docs say it is valid for 15 days and refreshes on use; `expires_at` is returned by Check Token. Sandbox tokens need no 2FA. | [V] | Token creation is an **owner-run command**, never part of the trading loop. Engine checks status and `expires_at` at start-up and hourly; alerts ahead of expiry; **any non-`NORMAL` status stops new entries**. A daily read-only call keeps the token alive. |
| U2 | **App Key validity:** the deprecated docs state a default validity of **45 days** (adjustable at the next reset; max 3 changes/generations/resets per day). Current docs are silent. Key generation needs SMS **and** the trading password. | [V-old], [V] | Owner records the key expiry date in the LIVE config; alerts at 14/7/3/1 days; expired or unknown expiry → no new entries. Only the owner can generate keys. |
| U3 | **IP allow-list** ("self-managed IP whitelist") is advertised; how to configure it is not in the developer docs. | [V marketing, U how-to] | LIVE VM gets a static IP registered in the allow-list (checklist item). |
| U4 | **Market data:** separate OpenAPI subscription; one device at a time; 5 MQTT connections per key; no automatic re-subscription. | [V] | One market-data client per host; re-subscribe on every reconnect; freshness checks (DQ-1) stop new entries on delays. |
| U5 | **Sandbox** is shared and public; credentials are published in the docs; state changes at any time; host names differ between pages (`…uat…` vs `…pre…`). | [V] | Sandbox only for adapter tests; tests assert on our own order IDs only; endpoints come from config with an allow-list; credentials are read from environment variables, not copied into the repository. |
| U6 | **Order history only 6 months** via API. | [V] | Our database is the permanent record; tax exports come from our ledger plus broker statements. |
| U7 | **Fractional rules:** regular hours only; marketable limits only; no documented stop orders. | [V] | System-side stops; entries/exits in the execution window with price caps. |
| U8 | **FX and funding** are not available through the API. | [U] | Owner converts JPY to USD manually (once, at funding); ledger records it. |
| U9 | **Change velocity:** changelog shows frequent changes (required header `x-version` moved from v2 to v3 on 2026-09-20; endpoint paths revised on 2026-08-22). SDK went from 2.0.13 (2026-07-06) to 3.0.4 (2026-10-17), including a major version on 2026-09-08. | [V] | Pin the exact SDK version; upgrade only via a PR that passes the sandbox contract tests; watch the changelog monthly. |
| U10 | **SDK token file:** the SDK stores the token and expiry in plain text, by default in `./conf/token.txt`; the directory can be set with `token_dir` or `WEBULL_OPENAPI_TOKEN_DIR`. | [V source] | Token directory outside the repository, permissions 0700, on the LIVE host only; `conf/` is git-ignored. |
| U11 | **SDK retries:** the default retry configuration lists only `GET` as retryable; read timeout 10 s, connect timeout 5 s. | [V source] | The adapter never relies on SDK retries for order submission; a submit timeout becomes `UNKNOWN` and is resolved by `client_order_id` lookup (document 05, DUP-2). |
| U12 | **SDK dependency pins:** on Python 3.12–3.13 the SDK requires `cryptography < 43` and `grpcio < 1.70`; on Python 3.14 it allows `cryptography` 43–54 and `grpcio ≥ 1.75.1`; on all versions `protobuf < 6` and `paho-mqtt < 2`. | [V PyPI] | Prefer Python 3.14 if the rest of the stack supports it (decided in TASK-001); run a dependency vulnerability audit in CI; the SDK is imported only by the Webull adapter package. |
| U13 | **Agentic tools** (MCP, CLI, Skills) can place orders when connected with trading permission. | [V] | Never connected to Claude, Codex or Muse with a production account (section 7). |
| U14 | **Disclaimer:** Webull disclaims responsibility for losses from automated or AI-driven decisions. | [V] | Owner accepts this in the LIVE approval record. |
| U15 | **Documentation errors** exist (registration number, inconsistent host names, an example labelled HMAC-SHA1 vs HMAC-SHA256). | [V] | Every behaviour we depend on is verified in the sandbox (section 8) and, where needed, with Webull support. |
| U16 | **Balance and position semantics:** the balance gives per-currency `cash_balance` and `buying_power` but no settled/unsettled split; position `cost_price` is not stated as per share or total (reference: `docs/reference/webull-trading-api-v3.md`). | [U] | Sandbox adapter assumes settled USD = min(cash, buying power) and per-share `cost_price` (TASK-008); verify with the owner's account before LIVE (WB-9). |

## 7. How this maps to the three environments

| Environment | Webull use | Credentials present |
|---|---|---|
| BACKTEST | None (research data set) | None |
| PAPER | **No Webull trading.** Our own virtual portfolio (`SimBroker` + ledger) with the Webull fee/FX/fractional profile. Market data from a read-only data source (see note). | Data-source key only |
| Webull Sandbox tests | Adapter contract tests against the UAT endpoints with the public shared test accounts. Never used for P&L or as a portfolio. | Public sandbox test keys only |
| LIVE | Webull production API, disabled until approval | Production App Key/Secret and token, **only** in the LIVE secret store on the LIVE host |

Note on PAPER market data: using Webull's own market data in PAPER would require a **production** App Key on the paper host. The documentation does not describe a data-only key, and Level 1 data may be used by only one device at a time. To keep production credentials on the LIVE host only, PAPER uses a separate read-only data source (decided: Alpaca market data on a free paper-only account, owner decision Q2). If Webull confirms a data-only permission, this can be revisited.

## 8. Items to verify after design approval

In the sandbox (public test accounts, no owner account needed):

| ID | Check |
|---|---|
| SB-1 | Account list, balance, positions, instruments (`fractionable`, `status`) work through the pinned SDK version. |
| SB-2 | Place / preview / replace / cancel for `LIMIT` and `MARKET`, whole and fractional (`AMOUNT` and decimal `QTY`); rejection codes for invalid cases (limit not crossing the spread, below USD 1, outside regular hours, `SHORT` in a cash account). |
| SB-3 | Duplicate `client_order_id` behaviour (expected: rejected) and lookup of an order after a simulated timeout. |
| SB-4 | gRPC events: delivery of `FILLED`, `FINAL_FILLED`, `PLACE_FAILED`, `CANCEL_SUCCESS`; `Ping` interval; behaviour on disconnect. |
| SB-5 | Whether the sandbox returns market data and simulated fills, and how realistic they are. |
| SB-6 | Which UAT host names are current. The developer page "SDKs, environments and test accounts" (read 2026-10-24) lists trading API `jp-openapi-alb.uat.webullbroker.com`, events `jp-openapi-events.uat.webullbroker.com`, MQTT `data-api.uat.webullbroker.com`; SDK 3.0.4 itself defines only production hosts for `jp`, so the adapter sets the test host with the SDK's endpoint override (TASK-008). |

With Webull support or the owner's account (Phase 6, after approval to open the account):

| ID | Check |
|---|---|
| WB-1 | Is there a data-only (read-only) API permission? |
| WB-2 | Current App Key validity period and how it is extended. |
| WB-3 | How to configure the IP allow-list. |
| WB-4 | Settlement currency behaviour for API orders (does a BUY with insufficient USD convert JPY automatically?). |
| WB-5 | Are stop orders accepted for positions that include fractional shares? Time-in-force options for fractional orders. |
| WB-6 | Market data terms for personal automated use; price of advanced subscriptions. |
| WB-7 | Behaviour of protective orders during API or system outages; phone/app cancellation procedure; support hours in JST. |
| WB-8 | Tokutei account with or without withholding — what the API needs (`account_tax_type`). |
| WB-9 | Meaning of `cash_balance` and `buying_power` for unsettled sale proceeds in a cash account, and whether position `cost_price` is per share (U16). |

## 9. Sources (accessed 2026-10-23)

Webull Securities Japan website:
- Agentic AI: https://www.webull.co.jp/agentic
- Open API overview: https://www.webull.co.jp/open-api
- Open API scope (FAQ 1728): https://www.webull.co.jp/help/faq/1728
- OpenAPI usage (FAQ 1727): https://www.webull.co.jp/help/faq/1727
- Fractional trading rules (FAQ 1404): https://www.webull.co.jp/help/faq/1404
- Fractional minimums and hours (FAQ 1389): https://www.webull.co.jp/help/faq/1389
- Fractional trading guide: https://www.webull.co.jp/guide/fractional-trading
- Pricing: https://www.webull.co.jp/pricing
- US stock service overview (local fees, account types, order limits): https://www.webull.co.jp/products/us-stocks/service
- Commission-free announcement: https://prtimes.jp/main/html/rd/p/000000073.000131836.html

Webull developer documentation (Markdown versions available by appending `.md`; index at https://developer.webull.co.jp/apis/llms.txt):
- Home: https://developer.webull.co.jp/apis/docs/
- SDKs, environments and test accounts: https://developer.webull.co.jp/apis/docs/sdk
- Authentication overview: https://developer.webull.co.jp/apis/docs/authentication/overview
- Individual application: https://developer.webull.co.jp/apis/docs/authentication/individual-application
- Token (2FA): https://developer.webull.co.jp/apis/docs/authentication/token
- Trading API overview: https://developer.webull.co.jp/apis/docs/trade-api/overview
- Stock trading: https://developer.webull.co.jp/apis/docs/trade-api/stock
- Place order: https://developer.webull.co.jp/apis/docs/reference/common-order-place
- Preview order: https://developer.webull.co.jp/apis/docs/reference/common-order-preview
- Order detail: https://developer.webull.co.jp/apis/docs/reference/order-detail
- Order history: https://developer.webull.co.jp/apis/docs/reference/order-history
- Account list / balance / positions: https://developer.webull.co.jp/apis/docs/reference/account-list , https://developer.webull.co.jp/apis/docs/reference/account-balance , https://developer.webull.co.jp/apis/docs/reference/account-position
- Instruments: https://developer.webull.co.jp/apis/docs/reference/instrument-list
- Check token: https://developer.webull.co.jp/apis/docs/reference/check-token
- Trade events: https://developer.webull.co.jp/apis/docs/reference/custom/subscribe-trade-events
- Market data overview / getting started / streaming / FAQ: https://developer.webull.co.jp/apis/docs/market-data-api/overview , https://developer.webull.co.jp/apis/docs/market-data-api/getting-started , https://developer.webull.co.jp/apis/docs/market-data-api/data-streaming-api , https://developer.webull.co.jp/apis/docs/market-data-api/faq
- Historical bars: https://developer.webull.co.jp/apis/docs/reference/historical-bars
- Earnings calendar / filings / news summary: https://developer.webull.co.jp/apis/docs/reference/earnings-calendar , https://developer.webull.co.jp/apis/docs/reference/filings , https://developer.webull.co.jp/apis/docs/reference/news-summary
- MCP / local MCP / CLI: https://developer.webull.co.jp/apis/docs/ai-friendly-resources/mcp , https://developer.webull.co.jp/apis/docs/ai-friendly-resources/local_mcp , https://developer.webull.co.jp/apis/docs/ai-friendly-resources/cli
- Change log: https://developer.webull.co.jp/apis/docs/changelog
- Deprecated docs (45-day key validity): https://developer.webull.co.jp/api-doc/

SDK and registry:
- PyPI: https://pypi.org/project/webull-openapi-python-sdk/
- Source: https://github.com/webull-inc/webull-openapi-python-sdk (commit e37c654, 2026-10-17)
- FSA registry of financial instruments business operators: https://www.fsa.go.jp/menkyo/menkyoj/kinyushohin.xlsx
