# CLAUDE.md — rules for Claude in this repository

Read `docs/status.md` (where the project stands) and `docs/design/README.md` first. The design
documents in `docs/design/` are the source of truth; code must follow them, and a deliberate
deviation must be written down in the design or in the PR.

## Current roles (Codex loop active since 2026-10-24, owner instruction)

| Role | Who | Does | Never |
|---|---|---|---|
| Owner | the owner | Approves phases, sign-ups, spending, protected-path PRs, LIVE and limit changes; holds all credentials. | — |
| Designer / reviewer | Claude, cloud session in the project | Writes the task spec into `.ai/CURRENT_TASK.md` on a cycle branch; reviews PRs; merges under the merge rule; reports to the owner in Japanese. | Writes product or test code; handles credentials. |
| Operator | Claude Code on the owner's PC | Fetches the cycle branch (fast-forward only); runs Codex with the exact command below; checks `.ai/LAST_REPORT.md`; runs the checks; commits only the files the report lists; pushes; opens a draft PR. Runs commands that need the data key (`aitrader data fetch`). | Edits `~/.codex/config.toml`; uses bypass or full-access flags; force-pushes or rewrites history; anything outside the project folder. |
| Implementer / tester | Codex on the owner's PC | Implements and tests the task as `AGENTS.md` says; writes `.ai/LAST_REPORT.md`. | Git commits or pushes; network access; secrets; protected paths unless the task says so. |

Details and the cycle diagram: `docs/design/06-roles-claude-codex-muse.md`.

## How the operator starts Codex

Verified against `codex exec --help` of codex-cli 0.164.0 (2026-10-24). From the repository root
on the cycle branch:

```bash
codex exec --cd . --sandbox workspace-write \
  -c approval_policy="on-request" -c approvals_reviewer="auto_review" \
  "Read AGENTS.md, then implement .ai/CURRENT_TASK.md and write .ai/LAST_REPORT.md as AGENTS.md describes."
```

- `-c` overrides apply to this run only; `~/.codex/config.toml` is never edited.
- Never use `--dangerously-bypass-approvals-and-sandbox`, `--sandbox danger-full-access` or any
  other bypass. If the installed version refuses one of the flags above, stop and send the owner
  the exact error instead of substituting another flag.
- After a usage-limit stop or "model is at capacity", resume at most 3 times per cycle
  (`codex exec resume --last ...`). If Codex reports BLOCKED only for a fact the operator can check
  from outside (for example a dependency that must be installed), check it, pass it on and
  resume once.
- Free dependencies a task needs are installed by the operator from official sources
  (`uv add <package>==<version>`), and the report says so.

## Credentials

- The Alpaca data key lives **only on the operator PC**, as the user environment variables
  `AITRADER_DATA_API_KEY` and `AITRADER_DATA_API_SECRET`. Never in the repository, in CI, in a
  Codex or Claude configuration file, in a prompt or in a chat. Codex never needs it: all tests run
  offline. Only the operator runs `aitrader data fetch`.
- Real market data stays under `data/` (git-ignored) and is never committed. Backtest reports
  (metrics, manifests) may be committed under `reports/`.

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

Claude may merge only when CI is green on the latest commit, the task's acceptance criteria are met and evidenced in `.ai/LAST_REPORT.md` and the PR, and no review finding is open. PRs touching protected paths need the owner's approval first.

## Engineering rules

- Python 3.14, `uv`, `src/` layout, package `aitrader`.
- Money and prices are `Decimal`; floats are rejected. All datetimes are timezone-aware UTC.
- `datetime.now()`, `date.today()` and `time.time()` only inside `aitrader.clock` (ruff rule TID251).
- Dependency rules are enforced by `lint-imports` (see `pyproject.toml`): risk, execution, broker, portfolio and reconcile never import `aitrader.ai`; strategy imports only `aitrader.domain` and `aitrader.indicators`; only `aitrader.broker.webull` may import the Webull SDK; only `aitrader.clock` imports the calendar library and pandas.
- Before every push run: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`, `uv run pytest --cov=aitrader`, `uv run coverage report --include='src/aitrader/*' --fail-under=90` (see `docs/dev/setup.md`).
- Repository text (code, comments, docs, commit messages, PRs) is in English. Reports to the owner are in Japanese and short: cycle start, finish (task ID, commit, status, failures), merge, and anything the owner must do.
