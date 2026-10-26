# CURRENT_TASK — TASK-015: PAPER observation book for S3

Designer: Claude (cloud), 2026-10-24. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-014.md`.

Owner decision 2026-10-24 (decision card "S3 を観察用に並走"): run S3 1.0.0 in PAPER next to
S1 1.0.0 as an **observation book**. S3 did not pass its declared rules
(`docs/research/2026-10-24-s3-results.md`): it is not a candidate and its PAPER results cannot
promote it to LIVE. Risk per trade stays 0.5% (configuration); no hard cap or risk change.

Design sources: document 04 §11 (PAPER stage 1), `src/aitrader/app/paper.py`,
`src/aitrader/app/paper_reports.py`, `src/aitrader/config/settings.py`, `config/paper.yaml`.
No protected path is touched.

## Scope

### A. Strategy selection in PAPER settings

- `PaperSettings` gains `strategy: Literal["S1", "S3"] = "S1"` and
  `purpose: Literal["reference", "observation"] = "reference"`.
- The runner builds the strategy from `STRATEGIES[strategy]()` with default parameters (S1 1.0.0
  or S3 1.0.0), instead of always `MomentumStrategy()`. The book identity (`strategy_id`,
  `strategy_version`, `params_hash`) already prevents mixing strategies in one state directory;
  keep that and add a test.
- `config/paper.yaml` states `strategy: S1` and `purpose: reference` explicitly (same book
  identity as today: S1 default params, same start and simulation values).
- New `config/paper-s3.yaml`: same as `paper.yaml` except `strategy: S3` and
  `purpose: observation`, with a comment that S3 did not pass its declared rules and is observed
  only. Same `start` (2026-10-26) and simulation values. It must load through the existing
  `aitrader paper run --config config/paper-s3.yaml` path (base.yaml is read alongside it).

### B. Status and reports show the book

- `status/latest.json` gains `strategy_id`, `strategy_version` and `purpose`.
- The daily Markdown report's first lines name the strategy and, for `observation`, say:
  "Observation book: this strategy did not pass its research rules and is not a LIVE candidate."
- The CLI summary line includes the strategy ID.

### C. Docs

- `docs/dev/setup.md` PAPER section: the two books and their commands, each with its own state
  directory:
  - `uv run aitrader paper run` (S1, `data/paper`);
  - `uv run aitrader paper run --config config/paper-s3.yaml --state-dir data/paper-s3` (S3).
  Running S3 against `data/paper` is refused by the book identity check (exit 6); say so.
- Replace the setup.md sentence "Neither family enables S3 in PAPER" with a pointer to the
  observation book.

## Tests

1. Default settings and `config/paper.yaml` produce the same S1 book identity as before this
   task (book row values equal to a pre-task run on the fixtures).
2. `config/paper-s3.yaml` loads; a fixture run records S3 sessions whose snapshots equal a plain
   `run_backtest` of S3 over the same period with `capture_sessions=True`.
3. Running the S3 config against a state directory that holds an S1 book exits 6 and writes
   nothing; and the reverse.
4. Status and the daily report show strategy, version and purpose; the observation sentence
   appears only for `observation`.
5. An unknown `strategy` or `purpose` value is a configuration error (exit 2).

## Out of scope

- Running PAPER on real data (the operator starts both books after the first closed session on
  or after 2026-10-26).
- Scheduling, notifications, any change to strategies, risk or execution.

## Acceptance criteria

1. All tests above and every existing test pass; coverage 100% where it is 100% today.
2. The fixture backtest double run is byte-identical apart from commit-derived fields.
3. All `AGENTS.md` checks green.
4. No change to protected paths (`git diff --stat` in LAST_REPORT).
