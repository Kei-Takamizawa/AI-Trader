# AGENTS.md — rules for Codex (implementer and tester)

You implement and test **one task**: the one in `.ai/CURRENT_TASK.md`. Read it fully before you
start. The design in `docs/design/` explains why things are the way they are; `docs/status.md`
says what exists. Do not change the design documents unless the task tells you to.

## Before you finish, run all of these and report the results

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run lint-imports
uv run pytest --cov=aitrader
uv run coverage report --include='src/aitrader/*' --fail-under=90
```

If the task touches the backtest, also run the fixture backtest twice and compare:

```bash
uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31
uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31
diff -r bt-a bt-b && rm -rf bt-a bt-b
```

Fix formatting with `uv run ruff format`. All checks must pass; never skip, disable, mark as
expected-to-fail or weaken a test to get there.

## Coding rules

- Python 3.14, `uv`, package `aitrader` under `src/`. Match the style of the surrounding code:
  typed (mypy strict), small pure functions, pydantic models with `frozen=True, extra="forbid"`,
  docstrings that say *why*, English only.
- Money, prices and quantities are `decimal.Decimal`; never floats. Datetimes are timezone-aware
  UTC. Never read the clock directly (`datetime.now()`, `date.today()`, `time.time()`): take a
  `Clock` or a timestamp argument.
- Respect the import contracts in `pyproject.toml` (`uv run lint-imports`); do not loosen them.
- No `assert` in `src/` (use explicit checks and typed errors); no bare `except`.
- Every new behaviour gets tests: rules at, just below and just above thresholds; error paths;
  property tests (hypothesis) where the task asks. Coverage must stay at or above 90% for every
  package (today it is 100%).
- Tests run offline: no network, no real credentials, no real market data. Use the synthetic
  fixtures in `tests/fixtures/` or recorded-format responses.

## Never

- Never run `git commit`, `git push`, change branches or rewrite history. The operator does all
  Git work.
- Never use the network (downloads, API calls, `pip`/`uv add`). If the task needs a new
  dependency, stop with BLOCKED and name the exact package, version and reason.
- Never read, print, create or store secrets or credentials, and never touch `~/.codex/`.
- Never edit protected paths unless the task explicitly says so: `src/aitrader/risk/` limits,
  `src/aitrader/config/hard_caps.py`, `src/aitrader/execution/gate*`, `config/live*`.
- Never enable LIVE, add live broker code paths or call Webull endpoints.
- Never work outside the repository folder.

## `.ai/LAST_REPORT.md` (write it at the end, always, also when BLOCKED)

```markdown
# LAST_REPORT — <TASK-ID>

Status: DONE | BLOCKED | PARTIAL

## Summary
<two to five sentences: what changed and why>

## Files changed
<every added, modified or deleted path, one per line; the operator commits exactly these>

## Commands run
<each command from "Before you finish" with its result line, e.g. "pytest: 912 passed">

## Acceptance criteria
- [x] <criterion from the task> — <evidence: test name, command output>
- [ ] <criterion not met> — <why>

## Deviations from the task or the design
<none, or each deviation with the reason>

## Open issues and questions
<none, or what the designer must decide>
```

## Stop with BLOCKED (write the report, change nothing else) when

- the task needs a new dependency, network access or a credential;
- the task conflicts with the design, `CLAUDE.md` or these rules;
- a protected path would have to change and the task does not say so;
- an acceptance criterion cannot be met without weakening a test or a check.
