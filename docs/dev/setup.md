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
uv run coverage report \
  --include='src/aitrader/domain/*,src/aitrader/config/*,src/aitrader/clock/*' \
  --fail-under=90                  # coverage gate for the core packages
```

Fix formatting automatically with `uv run ruff format`.

## Command-line interface

```bash
uv run aitrader --version
uv run aitrader config validate --mode backtest --config-dir config
uv run aitrader config validate --mode paper --config-dir config
uv run aitrader live               # always refuses: LIVE is disabled in this build (exit code 3)
```

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
