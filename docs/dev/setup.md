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
committed. Exit codes: 2 missing key or invalid universe, 4 data source error (401, 403 = DATA-3).

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
