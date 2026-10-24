# Developer setup

## Requirements

- [uv](https://docs.astral.sh/uv/) 0.11 or later. It installs the right Python (3.14) by itself.
- No accounts, API keys or network services are needed for development and tests.

## Set up

```bash
uv sync            # creates .venv with Python 3.14 and the locked dependencies
```

## Checks (the same as CI)

```bash
uv run ruff check                  # lint, including the ban on reading the system clock
uv run ruff format --check         # formatting
uv run mypy                        # strict type checks for src/ and tests/
uv run lint-imports                # dependency rules (risk never imports AI, etc.)
uv run pytest --cov=aitrader       # tests with coverage
uv run coverage report --include='src/aitrader/*' --fail-under=90   # coverage gate
```

Fix formatting automatically with `uv run ruff format`.

## Command-line interface

```bash
uv run aitrader --version
uv run aitrader config validate --mode backtest --config-dir config
uv run aitrader config validate --mode paper --config-dir config
uv run aitrader live               # always refuses: LIVE is disabled in this build (exit code 3)
uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir results/s2 \
  --start 2023-01-03 --end 2024-12-31   # backtest on the synthetic fixture bars
```

`backtest` writes `manifest.yaml` (run ID, commit, config hash, strategy and parameter hash,
data snapshot hash, period, cost profile), `metrics.json` and `equity.csv`. The same inputs give
byte-identical files. The bars in `tests/fixtures/bars/` are **synthetic** (seeded random walk,
`scripts/make_synthetic_bars.py`); they only exercise the code and say nothing about any
strategy. Exit codes: 2 configuration error, 4 data error, 5 unknown strategy.

### Market data (Alpaca, read-only)

```bash
uv run aitrader data fetch --end 2024-12-31          # universe from config/universe.yaml
```

`data fetch` needs `AITRADER_DATA_API_KEY` and `AITRADER_DATA_API_SECRET` in the environment.
They are set only on the operator PC, as user environment variables (see `CLAUDE.md`,
"Credentials"); never in a file, a cloud environment, a prompt or a chat. Only the operator runs
this command; tests never need the key. It only sends GET requests to `data.alpaca.markets`. It writes `data/bars/raw/` (traded prices),
`data/bars/adjusted/` (adjusted for splits and distributions) and `data/bars/fetch_report.json`
(rows, missing sessions, large moves, corporate actions, snapshot hashes). `data/` is never
committed. `--end` accepts the latest session whose calendar close is at least 16 minutes
before the CLI clock, including half days. A non-session or a more recent close is refused
with exit code 2 and the last closed session in the message. Historical SIP queries send UTC
timestamps, with `end` capped at the earlier of 23:59:59 UTC on that date and now minus
16 minutes, to respect the free-plan delay.
Exit codes: 2 missing key, invalid universe or invalid end session; 4 data source error
(401, 403 = DATA-3).

Backtests accept the fetched layout (both `raw/` and `adjusted/` are required):

```bash
uv run aitrader backtest --data-dir data/bars --out-dir results/s2 \
  --start 2016-01-04 --end 2024-12-31
```

Signals use point-in-time adjusted history; fills, participation volumes and equity marks use
raw prices. Estimated dividends are credited as settled cash on the ex-date (actual pay dates
are later), after the assumed withholding in `config/backtest.yaml`. Confirm those tax
assumptions with the broker's documents. Splits and unexplained adjustments fail closed when
a position is held or an intent is pending. The flat `<SYMBOL>.csv` fixture layout shown above
still runs without adjustments or dividends.

`config validate` prints the mode, the broker environment and the configuration hash; an invalid
configuration exits with code 2 and names the offending field.

## Configuration and secrets

- `config/base.yaml` holds the shared defaults; `config/<mode>.yaml` overrides them.
- Limits may only be made stricter; anything above the hard caps in
  `src/aitrader/config/hard_caps.py` is rejected.
- Secrets never go into files. They come only from environment variables:
  `AITRADER_DATA_API_KEY`, `AITRADER_DATA_API_SECRET`, `AITRADER_DATA_REPO_TOKEN`.
- Outside LIVE, the program refuses to start if any `LIVE_*` or Webull production variable
  (`WEBULL_*` other than `WEBULL_SANDBOX_*`) is present.

## Walk-forward

Run the predeclared S1 grid (or `--strategy S2` for its defaults) offline:

```bash
uv run aitrader walkforward --data-dir data/bars --out-dir results/walkforward --strategy S1 --start 2017-01-03
```

Training windows default to 36 calendar months, tests to 6 months, and the locked hold-out
to the last 24 months. Override these with `--train-months`, `--test-months`, and
`--holdout-months` before running. No hold-out session is evaluated.
`walkforward.json` contains every training score, the selected variant and test metrics per
fold, the chained test equity curve and its return/risk metrics, and the SPY buy-and-hold
benchmark over those same test sessions. `manifest.yaml` records the commit, configuration,
parameter grid, data snapshots, corporate-action counts and hold-out boundary.

### S1 v1.1 research families (TASK-012)

`--cash-proxy <SYMBOL>` fills empty S1 slots without ranking the proxy (S1 only).
`--risk-per-trade-pct <Decimal>` overrides only this BACKTEST run's in-memory risk
settings; it must be greater than zero and no greater than the hard cap (1.0%).
**C2 runs require the owner's approval** (research plan §1); configuration files stay unchanged.
`--prior-variants <int>` defaults to zero and must be nonnegative. The manifest records
`risk_override` (or null), proxy parameters, this grid's `variants_tried`, and
`variants_tried_total = prior_variants + len(grid)`.

```bash
# Baseline: five variants were tried before this run.
uv run aitrader walkforward --data-dir data/bars --out-dir results/v11-baseline --prior-variants 5
# C1: SHY is the cash proxy.
uv run aitrader walkforward --data-dir data/bars --out-dir results/v11-c1 --cash-proxy SHY --prior-variants 5
# C2: run only after owner approval.
uv run aitrader walkforward --data-dir data/bars --out-dir results/v11-c2 --risk-per-trade-pct 1.0 --prior-variants 5
# C1+C2: run only after owner approval.
uv run aitrader walkforward --data-dir data/bars --out-dir results/v11-c1-c2 --cash-proxy SHY --risk-per-trade-pct 1.0 --prior-variants 5
```

For the declared final comparison with 17 total variants, supply `--prior-variants 13`
for each four-point grid. The examples above record nine trials, not 17.
The chained risk summary includes `sharpe_per_session` and `deflated_sharpe` (four-decimal
strings or null when undefined). DSR uses unrounded training Sharpes for every grid point
in every fold, sample variance across those scores, and empirical central-moment skewness
and non-excess kurtosis of chained daily returns. No undefined training Sharpe is replaced
with zero. B0 includes `sharpe_rf0`; `fold_summary` lists test return, profit factor,
exposure and the selected top N / skip for each fold. Synthetic fixtures have no SHY;
use QQQ to exercise the proxy path offline, without drawing investment conclusions.

Both `backtest` and `walkforward` accept `--git-commit <commit>` to record an explicit commit.
Otherwise they look up HEAD using Git on PATH. If that lookup fails, `backtest` records
`unknown`; `walkforward` exits with code 4 and asks for `--git-commit`.

## Webull sandbox

The official `webull-openapi-python-sdk==3.0.4` is installed by the operator. Tests use
handwritten responses from `docs/reference/webull-trading-api-v3.md` and never call Webull.
Only `aitrader.broker.webull` imports the SDK; PAPER continues to use SimBroker and LIVE stays
disabled.

The operator sets `WEBULL_SANDBOX_APP_KEY`, `WEBULL_SANDBOX_APP_SECRET` and
`WEBULL_SANDBOX_ACCOUNT_ID` in their environment using the public shared test account details
from Webull's developer documentation. Do not paste the values into files, commands saved in
shell history, logs, reports or issue descriptions. Missing variables cause exit code 2.

Run this command **only as the operator**, on the Mac:

```bash
uv run aitrader sandbox check
```

It checks account membership, balance availability, positions and SPY instrument flags, then
previews and places a whole-share SPY limit buy at USD 1.00, queries it by its own client order
ID and cancels it. It also probes two expected fractional rejections, the duplicate ID and an
unknown ID. Only this run's `aitsb-` order IDs are used. The sandbox is shared, so rejection and
unexpected acceptance are recorded as findings. No market or deliberately marketable fractional
order is sent. Transport/authentication errors, missing own orders and unconfirmed cancellations
cause exit code 1; otherwise the command exits 0. A timeout is never resubmitted automatically.

Optional non-secret settings belong under `webull_sandbox` in `config/base.yaml`:

```yaml
webull_sandbox:
  endpoint: jp-openapi-alb.uat.webullbroker.com
  account_tax_type: SPECIFIC
  far_limit_price: 1.00
```

The endpoint is a bare hostname, case-insensitive. Only that trading UAT host is allowed;
production, events and market data hosts, URLs, paths and ports are refused. The adapter checks
SDK endpoint resolution before sending, disables SDK retries and SDK logging, and uses a
private temporary token directory outside the repository. It does not initialize 2FA or use the
SDK's default `conf/` directory; the temporary directory is cleaned up when the command exits.
The SDK's own initializer checks token configuration and creates or loads a sandbox token before
trading calls. Non-NORMAL tokens are checked for at most about 10 seconds, at 5-second intervals;
no human verification flow is started. Initialization failure or authentication failure during
SB-1 stops the check: all remaining checks are recorded as `skipped_auth` and exit code is 1.

The command writes `reports/sandbox/<UTC date>.json` (another run on the same date replaces it).
Use `--out-dir` to choose another report directory and `--config-dir` for another base.yaml.
The report contains the SDK version, allowed host, per-check outcome and pass/fail/finding,
per-call HTTP status, error code and latency in milliseconds, documented field presence flags,
observed order statuses, and balance presence only. It contains no keys, tokens, account numbers,
raw balances, provider messages or response bodies. The `token` check includes only whether token
checking is enabled (null means unknown), token status, whether expiry was present, HTTP status,
SDK error code and latency; neither token content nor expiry value is recorded. Send this report
to the designer; offline tests establish request construction and mapping, while only this operator
run establishes actual
sandbox behaviour. SB-4 event streaming and SB-5 market data/fill realism remain out of scope.
