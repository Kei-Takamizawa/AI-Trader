# CURRENT_TASK — TASK-008: Webull adapter (sandbox only) and broker contract tests

Designer: Claude (cloud), 2026-10-24. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-011.md`.

Design sources:
- document 14: Webull OpenAPI findings; §2 features, §3 limits, §6 U5/U9–U12, §7 environments,
  §8 SB-1 to SB-6;
- document 03: module design, broker port;
- document 05: DUP-2 (unknown submit outcome), ORD-ERR-2 (unknown reject codes), AUTH-4;
- document 07: phase P3b;
- `src/aitrader/broker/port.py` and `src/aitrader/broker/sim/`.

**This PR touches a protected path** (`src/aitrader/broker/webull/`), so it needs the owner's
approval in the PR before it is merged. LIVE stays disabled: this task adds **no production code
path**.

## Dependency (installed by the operator before Codex starts)

`webull-openapi-python-sdk==3.0.4` (Apache-2.0, released 2026-10-17, Python < 3.15; document 14,
U9 and U12). The operator runs `uv add webull-openapi-python-sdk==3.0.4`. If the resolver
refuses (its pins include `protobuf < 6`, `paho-mqtt < 2` and, on Python 3.14,
`grpcio >= 1.75.1`), the operator stops and reports the exact error. Codex never installs
anything.

Codex reads the installed SDK source under `.venv/` to learn the real module names, client
classes and response shapes. Do not guess an API from memory. If the SDK's import name is not
`webull`, change the forbidden-module name in the import contract in `pyproject.toml` to the real
name. The contract must stay as strict: only `aitrader.broker.webull` may import the SDK.

## Scope

### A. `aitrader.broker.webull` (new package)

- `WebullBroker` implements `BrokerPort` for `BrokerEnvironment.SANDBOX` only. Constructing it for
  any other environment raises `LiveDisabledError` (or the existing equivalent).
- **Endpoints:**
  - an explicit allow-list containing only the sandbox (UAT) hosts the SDK defines for the Japan
    region;
  - any other host, in particular every production host, is refused before a request is built;
  - the host comes from configuration (`webull_sandbox.endpoint`, default = the SDK's UAT host
    for Japan) and is checked against the allow-list.
- **Credentials:** read only from `WEBULL_SANDBOX_APP_KEY`, `WEBULL_SANDBOX_APP_SECRET` and
  `WEBULL_SANDBOX_ACCOUNT_ID`. These are the public shared test keys from Webull's documentation,
  which the operator sets on the Mac. Never write them to a file, a log, a report or an exception
  message.
  - The SDK's token file (U10) goes to a temporary directory outside the repository; sandbox
    tokens need no 2FA. `conf/` is git-ignored.
- **Mapping:**
  - `capabilities()`: fractional orders, step 0.00001, minimum fractional buy USD 1, and order
    types `MARKET` and `LIMIT`. Stop orders are not offered because fractional stops are
    undocumented (U7).
  - `account()`: settled and unsettled USD and JPY cash from the balance call.
  - `positions()`, `open_orders()`, `order_status(client_order_id)` (lookup by client order ID),
    `cancel()`.
  - `fills_since(ts)`: order-level fills only (document 14 §3), from order history, de-duplicated
    by a deterministic fill ID made of the `client_order_id` and the cumulative filled quantity.
  - `submit()`: `LIMIT` and `MARKET`, whole and fractional (decimal quantity); `CORE` session,
    `DAY` time in force, and `account_tax_type` from configuration (default `SPECIFIC`, Q9).
  - A `client_order_id` that does not match `^[A-Za-z0-9_-]{1,32}$` is refused before sending.
- **Errors:** timeouts and 5xx raise `BrokerUnavailableError`; 401 and 403 raise
  `BrokerAuthError`; 429 or the SDK's rate-limit error raises `RateLimitedError`; a reject raises
  `OrderRejectedError(code, message, known=...)`.
  - A small `known_codes.py` table lists the codes found in the SDK source or the docs. A code
    that is not in the table is `known=False` (ORD-ERR-2).
- **No retries on submit** (U11): disable or bypass SDK retries for order placement. A submit
  timeout is `BrokerUnavailableError`, and the caller resolves it with
  `order_status(client_order_id)` (DUP-2).
- **Rate limits** (document 14 §2): a client-side limiter for balance, positions and order queries
  (2 per 2 s) driven by the injected `Clock`. Never call `time.time()`.
- Prices and quantities cross the boundary as `Decimal`, parsed from the SDK's strings. Never use
  `float`.

### B. Broker contract tests (`tests/contract/`)

- One parametrised suite that every `BrokerPort` implementation must pass. Run it against
  `SimBroker` and against `WebullBroker` backed by a **fake SDK transport** that returns responses
  in the SDK's documented shapes (written by hand from the SDK models; no network).
- Cases:
  - submit and status;
  - idempotent resubmit of the same `client_order_id`;
  - cancel;
  - partial and final fill;
  - reject with known and unknown codes;
  - timeout on submit leading to an unknown outcome, then lookup;
  - auth error;
  - rate limit;
  - fractional quantity step;
  - invalid `client_order_id`;
  - account and positions mapping;
  - a production host refused.

### C. Sandbox check (`aitrader sandbox check`)

- A CLI command run **only by the operator** on the Mac. It needs the three `WEBULL_SANDBOX_*`
  variables, else exit 2.
- It runs, against the shared sandbox accounts:
  - SB-1: account list, balance, positions, and the instrument flags for SPY;
  - SB-2 subset: preview, place, status and cancel of one small `LIMIT` buy far from the market,
    whole and fractional, and one invalid order (fractional buy below USD 1), recording the
    reject code;
  - SB-3: a duplicate `client_order_id` is rejected, and lookup by `client_order_id` works;
  - SB-6: which host answered.
- It asserts only on our own `client_order_id`s, prefixed `aitsb-`, because the accounts are
  shared and change at any time (U5).
- It writes `reports/sandbox/<UTC date>.json` with each check's pass/fail, codes, latencies and
  the SDK version. The report holds no keys, no account numbers and no raw balances: balances are
  reported as present or absent only.
- Tests for this command use the fake transport. Real sandbox calls are made only when the
  operator runs the command, never in pytest or CI.

### D. Docs

- `docs/dev/setup.md`: a "Webull sandbox" section covering the variables, the command and what
  the report holds.
- Document 14: under §8, a one-line note "SB-1, SB-2 (subset), SB-3 and SB-6 are automated by
  `aitrader sandbox check` (TASK-008); results are recorded in `reports/sandbox/`".

## Out of scope

- SB-4: the gRPC order-event stream.
- SB-5: market data and fill realism.
- Market data through Webull.
- Any production path, LIVE, LiveGuard or tokens with 2FA.
- Using Webull in PAPER (document 14 §7: PAPER stays on `SimBroker`).

## Acceptance criteria

1. The contract suite passes for both `SimBroker` and `WebullBroker` with the fake transport, with
   every case listed in B.
2. Production hosts and non-sandbox environments are refused (tests), and no test or code path can
   build a production request.
3. Credentials never appear in logs, reports, exceptions or `repr` (a test scans the captured
   output and the written report for the test key values).
4. Submit is not retried: a test shows one transport call on a timeout and the resulting
   `BrokerUnavailableError`.
5. `aitrader sandbox check` with the fake transport writes a report with the fields in C and no
   secrets. Exit 2 without the variables.
6. The import contract still allows the SDK only in `aitrader.broker.webull`
   (`uv run lint-imports`).
7. All checks in `AGENTS.md` are green; coverage stays at 100% where it is 100% today and never
   below 90% for any package.
8. The fixture backtest double run from `AGENTS.md` is byte-identical.

After the merge-ready push, the operator runs `uv run aitrader sandbox check` once on the Mac and
sends the designer the report. Its findings go into document 14 in a later commit.

## Review round 1 (designer, 2026-10-24) — answers to BLOCKED B1 and B2

Codex was right to stop on both points. These rules replace the matching parts of A and C.

### B1. Sandbox host (replaces the "Endpoints" bullet in A)

SDK 3.0.4 defines only production hosts for `jp`. The test hosts are taken from Webull's
developer page "SDKs, environments and test accounts"
(https://developer.webull.co.jp/apis/docs/sdk, read by the designer on 2026-10-24):

| Service | Test (UAT) host | Production host (never used) |
|---|---|---|
| Trading API | `jp-openapi-alb.uat.webullbroker.com` | `api.webull.co.jp` |
| Trading events (gRPC) | `jp-openapi-events.uat.webullbroker.com` | `events-api.webull.co.jp` |
| Market data streaming (MQTT) | `data-api.uat.webullbroker.com` | `data-api.webull.co.jp` |

- The allow-list in code is exactly one host: `jp-openapi-alb.uat.webullbroker.com` (trading API).
  The events and MQTT hosts are out of scope (SB-4, SB-5) and are **not** in the allow-list.
- Default for `webull_sandbox.endpoint` is that host. The value must be a bare host name: no
  scheme, port, path, user info or trailing dot. Compare after lower-casing; anything else,
  including every host in the SDK's `endpoints.json`, is refused with a clear error before any SDK
  client is built.
- Point the SDK at the host with its own endpoint override (the page shows
  `ApiClient(app_key, app_secret, "jp")` followed by `add_endpoint("jp", <host>)`; use the real
  names you find in the installed SDK). After building the client and before the first request,
  read back the host the SDK will use for the trading API and refuse to continue unless it equals
  the allowed host. A test proves that a client whose resolved host is a production host is
  refused, and that no transport call is made.
- Do not try other hosts as a fallback (no `…pre…` guess, U5). If the allowed host does not
  answer, the sandbox check records the failure under SB-6 and the allow-list is changed only by a
  later PR.

### B2. Order checks in `aitrader sandbox check` (replaces the SB-2 subset bullet in C)

Fractional limits must be marketable (document 14 §3, U7), and the sandbox has no reliable quotes
(SB-5 is unknown), so the check never sends an order that is meant to fill. Each order check
records an `outcome` and a `result`:

1. **Whole-share resting limit:** preview, then place a `LIMIT` buy of 1 share of SPY at
   `webull_sandbox.far_limit_price` (default USD 1.00, a `Decimal` in configuration). Then:
   - accepted → status by `client_order_id` → cancel → status shows cancelled:
     `outcome = accepted_cancelled`, `result = pass`;
   - rejected at preview or place → record the code and message:
     `outcome = rejected`, `result = finding` (not a failure; it tells us the sandbox price rules);
   - filled or partly filled → `outcome = filled`, `result = finding`, and cancel any rest.
2. **Fractional non-marketable limit (expected reject):** place a `LIMIT` buy of `0.01000` SPY at
   the same far price.
   - rejected → record the code: `outcome = rejected_as_expected`, `result = pass`;
   - accepted → cancel it at once, then confirm by status:
     `outcome = accepted_unexpectedly`, `result = finding`.
3. **Fractional below USD 1 (expected reject):** unchanged from C: record the reject code;
   rejected → `pass`, accepted → cancel at once and record `finding`.

- `result = fail` is used only for transport or auth errors, an order that cannot be found by its
  `client_order_id`, or a cancel that cannot be confirmed. The command exits 1 if any check has
  `result = fail`, else 0 (exit 2 without the variables, as in C).
- No `MARKET` order and no marketable fractional order is sent by this command.
- SB-3 (duplicate `client_order_id`) reuses the ID of check 1 when it was accepted; if check 1 was
  rejected, SB-3 is recorded as `outcome = skipped`, `result = finding`.

### Other notes

- The uv warning about `build_system.requires = ["uv-build>=0.11.0,<0.12"]` versus uv 0.12.1 is
  not part of this task; leave `[build-system]` unchanged. It is tracked in `docs/status.md`.
- Acceptance criteria 1–8 still apply. Add to AC 2: the resolved-host read-back test from B1.
  Add to AC 5: fake-transport tests for every `outcome` listed in B2 and the exit codes 0 / 1 / 2.

## Review round 2 (designer, 2026-10-24) — answer to BLOCKED B3, and SDK logging

Codex was right again: SDK 3.0.4 returns raw HTTP responses and has no response models. The
designer chose option (1). The response shapes are now in
**`docs/reference/webull-trading-api-v3.md`**, restated from Webull's official reference pages
(sources listed there). Build the fake transport and the parsers from that file and from the SDK
source for request building. Do not invent fields that are not in it. Where the documentation is
silent, follow the rules below, which replace the matching parts of A.

### Parsing

- Read the body with the SDK's response object (`.json()` or the equivalent the SDK exposes).
  Every number is a JSON string → `Decimal`. An absent, `null` or empty optional field is "absent".
  A required field that is missing or not parseable raises `BrokerUnavailableError` with the field
  name only (no body, no values).
- `account_id` for every call comes from `WEBULL_SANDBOX_ACCOUNT_ID`. The account list is used
  only by the sandbox check (SB-1) to confirm that this ID is listed.

### Mapping rules where the documentation is silent

1. **`account()`**: the balance has no settled/unsettled split. For the `USD` item of
   `account_currency_assets`:
   - `cash_usd_settled = max(0, min(cash_balance, buying_power))`;
   - `cash_usd_unsettled = max(0, cash_balance - cash_usd_settled)`;
   - `cash_jpy = max(0, cash_balance of the JPY item)`;
   - a missing currency item counts as zero.
   This is a conservative assumption. The designer has recorded it in document 14 as U16 [U]
   with check WB-9; do not edit document 14 for this.
2. **`positions()`**: only `instrument_type == EQUITY` and `currency == USD`. Set
   `cost_usd = cost_price × quantity`, treating `cost_price` as per share. This is also an
   assumption, also covered by U16. Positions with a quantity of zero are dropped.
3. **Order status** (`status` → `OrderStatus`): `PENDING` → `SUBMITTED`; `SUBMITTED` →
   `SUBMITTED`; `PARTIAL_FILLED` → `PARTIALLY_FILLED`; `FILLED` → `FILLED`; `CANCELLED` →
   `CANCELLED` (keep any `filled_quantity`); `FAILED` → `REJECTED`. Any other value → `UNKNOWN`,
   with the raw status string (not the body) in a log message at WARNING level.
   - `average_fill_price` is `filled_price` only when `filled_quantity > 0` and
     `filled_price > 0`, else `None`.
   - `updated_at` is `filled_time_at` if present, else `place_time_at`.
4. **`open_orders()`** and **`fills_since(ts)`** read every page using `pagination_key`, and stop
   after 50 pages (raise `BrokerUnavailableError` if more remain). `fills_since` calls order
   history with `start_time = ts`, formatted as `yyyy-MM-dd'T'HH:mm:ss.SSS'Z'`. It refuses a `ts`
   older than 6 months with `ValueError`. Each order item with `filled_quantity > 0` produces at
   most one `BrokerFill`, using the fill ID rule already in A. `fees` and `commission` map to
   `TradeCost` entries only if `actual_value` / `actual_commission` is present; otherwise there
   are no costs.
5. **`order_status(id)`**: order detail by `client_order_id`, using the first item of `orders`
   whose `client_order_id` matches. An empty `orders`, or HTTP 417 on this call, raises
   `OrderNotFoundError`. The 417 `error_code` is kept on the exception for the sandbox report.
   No code is documented for "not found", so the sandbox check records the real one.
6. **HTTP status → error**, for every call: 401 and 403 → `BrokerAuthError`; 429 →
   `RateLimitedError`; 5xx, timeouts and connection errors → `BrokerUnavailableError`. On preview,
   place and cancel, 417 → `OrderRejectedError(error_code, message, known=error_code in
   KNOWN_CODES)`. On other calls, 417 → `BrokerUnavailableError` carrying the code.
   - The `message` from Webull may be kept in the exception, after removing any occurrence of the
     app key, app secret or account ID.
   - `known_codes.py` lists exactly the codes in the reference file: `UNAUTHORIZED`,
     `INVALID_PARAMETER`, `SYSTEM_ERROR`, `OPENAPI_NO_NIGHT_TRADING_TIME` and `INVALID_TOKEN`.
     Each has a one-line meaning and the source URL.
7. **Submit** sends the order request exactly as in the reference file's "Order request" table:
   `entrust_type = QTY`, never `AMOUNT`, never `SHORT`. The quantity string has at most 5
   decimals and no exponent (`format(q, "f")` after quantizing to the step). The idempotent
   resubmit case (same `client_order_id` sent again) is handled by first calling
   `order_status(client_order_id)`. If the order exists, return its ack without a second place.
   Otherwise place once.

### SDK logging (operator's finding)

`webull/core/client.py` logs full response headers and bodies at DEBUG, and `vars(exception)` at
ERROR. Request headers include `x-app-key` and `x-app-secret`.

- At `WebullBroker` construction, set `logging.getLogger("webull")` to `logging.CRITICAL + 1`,
  set `propagate = False` and attach a `logging.NullHandler()`. Do the same for any other logger
  name the SDK uses; find them in the SDK source and list them in LAST_REPORT.
- AC 3 is extended: a test sets the root logger to DEBUG with `caplog`, drives submit, a 401, a
  417 and a 500 through the fake transport, and asserts that none of the three secret values
  appears in `caplog.text`, in exception strings or in the written report.

### Sandbox check report additions

For each call, the report also records `http_status` and `error_code` (never the message or the
body). It also records these "shape findings", with no values: whether each required field in
the reference file was present (field name → true/false), the observed order `status` strings,
and the `error_code` seen for the duplicate `client_order_id` and for an unknown
`client_order_id` lookup (`aitsb-unknown-<uuid8>`).

## Review round 3 (designer, 2026-10-24) — sandbox check returned 401 on every call

The operator ran `aitrader sandbox check` once on 1b39e58. DNS, TLS and the host all worked
(SB-6 pass), but every API call returned HTTP 401 `UNAUTHORIZED`. No order was accepted. There are
two causes. The operator and owner are fixing the first one. This round fixes the second.

1. **Wrong values on the Mac (owner side).** The lengths of the configured values do not match
   the documented test accounts (App Key 32, App Secret 32, Account ID 19 digits).
2. **No access token.** The adapter builds `ApiClient` without the SDK's client initialization.
   In SDK 3.0.4, `TradeClient` runs
   `webull/core/http/initializer/client_initializer.py::ClientInitializer.initializer`. That
   reads the token config (`token_check_enabled`) and, when it is enabled, creates or loads a
   token via `TokenManager` and calls `api_client.set_token(...)`. Only then does
   `get_response` send `x-access-token` (`webull/core/client.py`, around line 258). Webull's token
   page says tokens created in the test environment are valid at once (`NORMAL`, no 2FA). The
   developer page's own example uses `TradeClient`, so it runs this step.

### Required changes

- **A1. Token initialization in `SDKTransport`:**
  - After the host check and `set_token_dir(<temporary dir>)`, run the SDK's own initializer once
    (`ClientInitializer.initializer(self._client)`, or the equivalent you find in the installed
    source). Do not re-implement token requests.
  - Build `ApiClient` with `token_check_duration_seconds=10` and `token_check_interval_seconds=5`,
    so a token that is not `NORMAL` fails within about 10 s instead of 300 s.
  - Before initializing, re-check the resolved host. The initializer's own requests go through the
    same `jp` endpoint resolver, so they reach only the allowed UAT host. A test must prove that
    the initializer is not called when the resolved host is not the allowed one.
  - Map failures to `BrokerAuthError`: an SDK `ClientException` from the initializer (for example
    `ERROR_INIT_TOKEN`, `ERROR_CREATE_TOKEN`, `ERROR_CHECK_TOKEN`, `ERROR_CHECK_TOKEN_ENABLE`), and
    a 401 or 403 during it. Keep the SDK error code; never put the token, the message body or
    headers in the exception.
  - The token value is a secret, like the app secret. Add it to the redaction set once it is
    known (`api_client.get_token()`), and extend the AC 3 leak test with an invented token value.
  - The token file stays in the private temporary directory and is removed by `close()`. A test
    checks that the directory is gone after `close()`.
  - Still no 2FA flow, no waiting for a human and no `TradeClient`. If the SDK's initializer
    needs `TradeClient` internals, stop and report.
- **C1. Sandbox check stops on authentication failure.** If the token initialization fails, or
  the first SB-1 call returns 401 or 403:
  - record that check as `fail` with its `error_code`;
  - mark every remaining check `outcome = skipped_auth`, `result = fail`, without calling the API;
  - exit 1.
  This avoids sending orders after we already know authentication is broken.
- **C2. Token facts in the report:** add one check `token` with `token_check_enabled`
  (true/false/unknown), the token `status` string (`NORMAL`, `PENDING`, …), the SDK error code if
  any, and the latency. Never record the token, its prefix or its expiry value. A boolean
  `expires_present` is enough.

### Tests (fake transport, offline)

- Token enabled and `NORMAL` → requests carry the token (assert on the fake's received headers by
  the presence of `x-access-token` only).
- Token disabled → no token header, and the calls still proceed.
- Token `PENDING` until timeout → `BrokerAuthError` within the bounded duration. Patch the SDK's
  sleep or clock; real waiting is not allowed in tests.
- Config 401 → `BrokerAuthError`, with the check stopping as in C1.
- Leak test with an invented token value, as above.

Acceptance criteria 1–8 still apply. AC 2 adds the "initializer not called on a wrong resolved
host" test; AC 3 adds the token; AC 5 adds C1 and C2.

## Review round 4 (designer, 2026-10-24) — Codex GitHub review findings on d6dcf34

Both findings are real. The first one comes from the fill-ID rule in A, which was a designer
error. The reviewer's comments are in PR #6 on `parsing.py` lines 226 and 232.

### F1. Cumulative fill snapshots are booked as separate executions (replaces the fill rule in A and round 2 rule 4)

Webull gives only order-level cumulative `filled_quantity` and an average `filled_price`. With
the old rule (`<client_order_id>:<cumulative qty>`), each poll of a partly filled order returns
the whole cumulative quantity under a new ID. `OrderManager._book_fill` then books it twice or
reports "fills exceed the order quantity". New rule:

- `fills_since` reports a fill **only for orders in a terminal state** (`FILLED`, or `CANCELLED`
  / `FAILED` with `filled_quantity > 0`). It reports exactly one `BrokerFill` per order:
  - `fill_id = "<client_order_id>:final"`;
  - quantity = the final `filled_quantity`;
  - price = `filled_price`;
  - `ts` = `filled_time_at`, or `place_time_at` if that is absent;
  - costs from `fees` / `commission` as before.
- Working orders (`PENDING`, `SUBMITTED`, `PARTIAL_FILLED`) produce no fill.
  `order_status` still reports their partial `filled_quantity`. `OrderManager._adopt` already
  keeps such an order active until its fills are booked.
- **Cursor safety:** the manager advances its cursor to the newest fill `ts`, and a cancelled
  order's last execution can be older than that. So `fills_since(ts)` queries order history with
  `start_time = max(ts - 7 days, now - 6 months)`. Do not drop items whose `filled_time_at` is
  before `ts`; the manager de-duplicates by `fill_id` through the ledger. Keep the 50-page cap.
  The `ValueError` for `ts` older than 6 months stays.

### F2. Sell fills have no settlement date

`Ledger.record_fill` rejects a sale without `settles_on`.

- `WebullBroker` takes a required `settlement_date: Callable[[date], date]`, as `SimBroker`
  does. For a `SELL` fill, set `settles_on = settlement_date(<trade date of fill.ts in
  America/New_York>)`. For a `BUY`, set `None`. Do the time-zone conversion with an existing
  `aitrader.clock` helper if one exists; otherwise use `zoneinfo` in the adapter.
- Production wiring (the CLI sandbox check) passes `TradingCalendar().settlement_date`. Tests
  pass a simple deterministic function.

### Tests

- Contract suite, for both brokers: over the whole life of one order (partial, then final),
  the union by `fill_id` of every `fills_since(NOW)` result has a total quantity equal to the
  final `filled_quantity`, and it never exceeds the order quantity at any poll. Replace the
  current `test_partial_and_final_fills` assertion `first[0].fill.quantity == 0.5`, which assumes
  incremental fills. The new property must hold for both `SimBroker` (incremental) and Webull
  (terminal only). This is not a weakening: the property is stricter about over-booking.
- Integration test: `OrderManager` with `WebullBroker` and the fake transport. Run sync through
  partial → final for a BUY, then the same for a SELL. Assert exactly one ledger fill per order
  with the right quantity, no `MISMATCH` event, and the SELL booked with the expected
  `settles_on`.
- Cursor test: one order cancelled after a partial fill whose `filled_time_at` is older than
  another order's later fill is still booked exactly once.
- A `CANCELLED` order with zero fills produces no fill.

Acceptance criteria 1–8 still apply. AC 1 covers the new contract property.
