# CLAUDE.md — rules for Claude in this repository

Read `docs/design/README.md` first. The design documents in `docs/design/` are the source of truth; code must follow them, and a deliberate deviation must be written down in the design or in the PR.

## Current roles

- **Implementer: Claude** (owner instruction, 2026-10-23) until the owner explicitly switches to Codex. The Codex loop (`AGENTS.md`, `.ai/CURRENT_TASK.md`) is **not active**; do not create those files unless the owner says so.
- Claude also designs, reviews and reports. The owner approves phases, sign-ups, spending, LIVE and limit changes.

## Never

- Never put Webull production credentials (App Key, App Secret, token) or any `LIVE_*` secret in this repository, in CI, in a development machine, or in any AI tool configuration. Never call Webull production endpoints.
- Never connect Webull MCP, CLI or Agent Skills to a production account.
- Never enable LIVE, create LIVE configuration (`config/live*.yaml` other than `config/live.example.yaml`) or raise a hard cap without the owner's explicit approval in the PR.
- Never sign up for external services, start billing, create cloud projects or scheduled tasks without the owner's approval at that time.
- Never skip, disable or weaken tests to get CI green.
- Never force-push, rewrite history or merge outside the merge rule.

## Protected paths (owner approval required in the PR)

- `src/aitrader/risk/` limits and `src/aitrader/config/hard_caps.py`
- `src/aitrader/execution/gate*`, LiveGuard, `src/aitrader/broker/webull/` production paths
- `config/live*`
- anything that adds a paid service or an external sign-up

## Merge rule (owner decision Q13)

Claude may merge only when CI is green on the latest commit, the task's acceptance criteria are met and evidenced in the PR, and no review finding is open. PRs touching protected paths need the owner's approval first.

## Engineering rules

- Python 3.14, `uv`, `src/` layout, package `aitrader`.
- Money and prices are `Decimal`; floats are rejected. All datetimes are timezone-aware UTC.
- `datetime.now()`, `date.today()` and `time.time()` only inside `aitrader.clock` (ruff rule TID251).
- Dependency rules are enforced by `lint-imports` (see `pyproject.toml`): risk, execution, broker, portfolio and reconcile never import `aitrader.ai`; strategy imports only `aitrader.domain`; only `aitrader.broker.webull` may import the Webull SDK.
- Before every push run: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`, `uv run pytest` (see `docs/dev/setup.md`).
- Repository text (code, comments, docs, commit messages, PRs) is in English. Reports to the owner are in Japanese.
