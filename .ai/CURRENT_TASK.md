# CURRENT_TASK — TASK-018: Cloud Build upload drops `src/aitrader/data`

Designer: Claude, 2026-10-26. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-017.md`.

## Problem (seen on the first cloud run, 2026-10-26)

`.gcloudignore` uses gitignore rules: a pattern without a leading `/` matches at any depth. The
line `data/` therefore also excluded `src/aitrader/data/` from the Cloud Build upload. The image
built from 5ed20a7 fails at start with `ModuleNotFoundError: No module named 'aitrader.data'`.
No state was written. `tests/integration/test_daily.py::test_container_files` asserts the
unanchored lines, so it encoded the bug.

`.dockerignore` uses Docker rules (patterns are relative to the context root), so `data/` there
matches only the root directory; leave its meaning unchanged.

## Scope

1. `.gcloudignore`: anchor every repository-root entry with a leading `/` (`/.git`, `/.venv`,
   `/data/`, `/results/`, `/bt-*`, `/.hypothesis`, `/.import_linter_cache`, `/.coverage*`,
   `/htmlcov/`, `/config/live*.yaml`, `!/config/live.example.yaml`, `/.ai/`); keep `.env*`,
   `**/*token*`, `**/*credential*` and the `**/` cache patterns matching at any depth. Add a
   short comment saying why the root entries are anchored.
2. `test_container_files`: update the expected `.gcloudignore` lines to the anchored form (the
   `.dockerignore` expectations stay as they are).
3. New test: implement a small gitignore-style matcher **in the test** (only what these files
   use: leading `/` anchors to the root, trailing `/` matches directories, `*` within a segment,
   `**/` any depth, `!` negation, last match wins) and apply `.gcloudignore` to every path from
   `git ls-files`-equivalent enumeration of the repository (walk the tree, skipping `.git`). Assert:
   - every file under `src/`, plus `pyproject.toml`, `uv.lock`, `README.md`, `Dockerfile` and
     every file under `config/` except `config/live*.yaml` (the example stays) is **uploaded**;
   - a root `data/x.csv`, `results/x`, `bt-a/x`, `.env`, `.env.local`, `.ai/CURRENT_TASK.md`,
     `config/live.yaml` and any `*token*` file are **excluded** (create these paths in a
     temporary copy or test the matcher on path strings).
   Run the same check for `.dockerignore` with Docker semantics where they differ only by
   anchoring (Docker patterns are root-relative; `**` matches any depth), asserting the same
   uploaded/excluded sets.

No product code changes. Nothing else.

## Acceptance criteria

1. All `AGENTS.md` checks green on Windows; CI green on Linux and Windows.
2. The new test fails on the old `.gcloudignore` (show this in LAST_REPORT by running it against
   the old content, for example with a parametrised fixture) and passes on the new one.
3. `git diff --stat` shows only `.gcloudignore`, `tests/integration/test_daily.py` (or a new test
   file under `tests/`) and `.ai/LAST_REPORT.md`.
