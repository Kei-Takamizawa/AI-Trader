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
