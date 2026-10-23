# 10. First Development Task for Codex (draft)

> **Status: in progress — implementer Claude** (design approved 2026-10-23; the owner asked Claude to implement until switching to Codex). When Codex takes over, later tasks are issued through `.ai/CURRENT_TASK.md`.

---

## TASK-001: Project skeleton, domain model, configuration with hard caps, CI

### Objective

Create the foundation that every later task builds on: a Python package with strict tooling, the core domain types, a validated configuration system that enforces compiled-in hard caps, a clock abstraction, a minimal CLI and a CI pipeline. **No trading logic, no network access, no external services.**

### Background (read first)

- `docs/design/01-system-architecture.md` (principles)
- `docs/design/03-module-design.md` (layout, dependency rules, interfaces, data model)
- `docs/design/04-mode-switching.md` (modes; LIVE is disabled)
- `docs/design/05-risk-management-spec.md` (section 1 parameters and section 13 hard caps)

### In scope

1. **Project set-up**
   - `pyproject.toml` managed by `uv`, `src/` layout, package `aitrader`, lock file committed.
   - Python version: **3.14 if** a dry-run resolution (`uv pip compile`, not installed into the project) shows that `pandas`, `pyarrow`, `exchange_calendars` and `webull-openapi-python-sdk` all resolve on 3.14; otherwise 3.13. If the Codex environment has no network access, use 3.13, mark the check as "left to operator" and say so in the report. (Background: document 14, U12.)
   - Runtime dependencies (only these): `pydantic` v2, `pydantic-settings`, `pyyaml`, `typer`.
   - Dev dependencies (only these): `pytest`, `pytest-cov`, `hypothesis`, `mypy`, `ruff`, `import-linter`, `time-machine`.
   - `.gitignore` covering `data/`, `*.db`, `.env*`, `config/live*.yaml` (but **not** `config/live.example.yaml`), `conf/` and `**/token.txt` (Webull SDK token files), caches.
2. **Package skeleton** for every package in document 03, section 1. Packages not implemented in this task contain only an `__init__.py` with a one-line docstring.
3. **Domain types** (`aitrader.domain`), immutable pydantic models or frozen dataclasses with validation:
   - `Currency` (USD, JPY) and `Money` (Decimal amount + currency; arithmetic only between equal currencies; no floats accepted).
   - `Mode` (BACKTEST, PAPER, LIVE), `BrokerEnvironment` (SIM, WEBULL_SANDBOX, WEBULL_LIVE) and the allowed combinations from document 04 §3 (`BACKTEST→SIM`, `PAPER→SIM`, `LIVE→WEBULL_LIVE`; `WEBULL_SANDBOX` only for the contract-test command), `Side` (BUY, SELL only), `OrderType`, `TimeInForce`, `OrderStatus` (all states in document 03 §5), `SystemState` (SAFE, RUNNING, DEGRADED, HALTED, KILLED).
   - `Instrument`, `Bar` (rejects high < low, negative volume, non-positive prices, naive datetimes), `Quote`.
   - `OrderIntent`, `RuleResult`, `RiskDecision`, `AdvisorySignal` (`size_multiplier` must be within [0, 1]; `valid_until` > `valid_from`; `veto` boolean).
   - `make_intent_id(...)`: deterministic SHA-256-based ID from strategy ID, version, symbol, side, signal timestamp (UTC) and sequence number.
   - `make_client_order_id(intent_id, attempt)`: 26-character base32 prefix of the intent hash + `-` + attempt number; always ≤ 32 characters and only `[A-Za-z0-9_-]` (Webull rule, document 05 DUP-2).
   - Cost record types: `TradeCost` (type ∈ COMMISSION, CAT_FEE, FINRA_FEE, SEC_FEE, ADR_FEE, OTHER; amount; currency; actual or estimated) and `FxConversion` (JPY amount, USD amount, applied rate, reference rate, spread cost).
4. **Configuration** (`aitrader.config`)
   - Settings schema for `base`, `backtest`, `paper` files; layered loading (base → mode file → environment variables for secrets only).
   - `RiskLimits` model with the initial values from document 05 §1.
   - `HARD_CAPS` constants from document 05 §1 and §13 (including approved capital ≤ ¥100,000, order notional ≤ ¥50,000, `SHORT` never allowed, `CORE` session only). Loading a config whose limit exceeds a hard cap raises `ConfigError` naming the field, the value and the cap.
   - The PAPER and BACKTEST config schemas have **no fields** for Webull production credentials; adding one must fail validation.
   - `config_hash()`: SHA-256 of canonical JSON (sorted keys, Decimal as string) of the **effective non-secret** settings. Secrets are `SecretStr` and excluded from the hash and from any `repr`.
   - Loading `Mode.LIVE` raises `LiveDisabledError("LIVE is disabled in this build")`.
   - Example files: `config/base.yaml`, `config/backtest.yaml`, `config/paper.yaml`, `config/live.example.yaml` (template with comments, values not loadable as LIVE).
5. **Clock** (`aitrader.clock`): `Clock` protocol, `SimClock` (settable, monotonic, refuses to go backwards), `WallClock` (UTC, timezone-aware). A ruff `banned-api` rule forbids `datetime.now`, `datetime.utcnow`, `date.today` and `time.time` outside `aitrader.clock`.
6. **CLI** (`aitrader.cli`, entry point `aitrader`):
   - `aitrader --version`
   - `aitrader config validate --mode backtest --config-dir config/` → prints effective mode and config hash; exit code 0, or 2 with the error message on invalid config.
   - `aitrader live` → prints "LIVE is disabled in this build" and exits with code 3.
7. **Dependency rules**: `import-linter` contracts from document 03 §3 (all forbidden imports of `aitrader.ai` from `risk`, `execution`, `broker`, `portfolio`, `reconcile`; `strategy` may import only `domain`).
8. **CI**: `.github/workflows/ci.yml` on pull requests and pushes: `uv sync --frozen`, `ruff check`, `ruff format --check`, `mypy` (strict for `aitrader.domain`, `aitrader.config`, `aitrader.clock`), `lint-imports`, `pytest` with coverage.
9. **Developer guide**: `docs/dev/setup.md` with the exact commands to set up and run the checks.

### Out of scope

Risk engine logic, ledger, order manager, brokers, market data, backtest loop, AI, network calls, cloud deployment, any external account.

### Constraints

- No network access in code or tests. No new runtime dependency beyond the list above without stopping and reporting.
- Use `Decimal` for all money and prices; floats are rejected by the domain types.
- All datetimes are timezone-aware UTC.
- Do not create `CLAUDE.md`, `AGENTS.md` or anything that enables LIVE.
- If something in the design is contradictory or impossible, stop and report it as **BLOCKED** with the exact question.

### Acceptance criteria

| # | Criterion | How it is checked |
|---|---|---|
| AC1 | `uv sync --frozen` works on a clean checkout | CI log |
| AC2 | `ruff check`, `ruff format --check`, `mypy`, `lint-imports` pass | CI log |
| AC3 | `pytest` passes; line coverage ≥ 90% for `domain`, `config`, `clock` | CI coverage report |
| AC4 | Domain validation tests: invalid bars, naive datetimes, float money, mixed-currency arithmetic, multiplier outside [0, 1] are rejected | test names listed in report |
| AC5 | Property test (hypothesis): `make_intent_id` is deterministic and changes when any input changes | test |
| AC6 | Config tests: each limit above its hard cap raises `ConfigError`; config hash is stable across runs and key order; secrets do not change the hash and never appear in `repr` or logs | tests |
| AC7 | `Mode.LIVE` cannot be loaded; `aitrader live` exits with code 3 | tests |
| AC8 | An import of `aitrader.ai` from `aitrader.risk` (added in a temporary test fixture or checked via contract test) fails `lint-imports` | test or documented manual check |
| AC9 | `SimClock` refuses to move backwards; banned-API rule catches `datetime.now` outside `clock` | tests |
| AC10 | `docs/dev/setup.md` commands reproduce the CI checks locally | report |
| AC11 | `make_client_order_id` always returns ≤ 32 characters from `[A-Za-z0-9_-]` and is deterministic (property test) | test |
| AC12 | Only the allowed `Mode`/`BrokerEnvironment` combinations load; PAPER/BACKTEST configs with a Webull production credential field are rejected | tests |

### Report (`.ai/LAST_REPORT.md`)

- Task ID, final status (DONE / BLOCKED / PARTIAL).
- List of files created or changed (the operator commits exactly these).
- Commands run and their results (copy of the final summary lines).
- Coverage numbers per package.
- Deviations from this specification and why.
- Open questions for Claude.

---

## Next tasks (outline only, specified after TASK-001 is merged)

| Task | Summary |
|---|---|
| TASK-002 | Risk engine: rules R-*, DQ-*, CB-*, DUP-* that can be evaluated on fixture data; sizing with fees (document 05 §2); full audit of rule results; property tests for hard caps. |
| TASK-003 | Ledger: double-entry cash per currency, settled vs. unsettled cash (T+1), fees, FX, dividends, operating costs as a separate account; property tests. |
| TASK-004 | Order manager state machine with write-ahead records, order gate, `SimBroker`; fault tests (crash points, timeouts, duplicates). |
| TASK-005 | Backtest event loop with `SimClock`, exchange calendar, fill/spread/slippage/fee models, metrics, run manifest, `aitrader backtest` on fixture data. |
