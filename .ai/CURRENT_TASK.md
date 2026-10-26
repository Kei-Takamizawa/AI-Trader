# CURRENT_TASK — TASK-017: one-command daily PAPER run, ready for a Cloud Run job

Designer: Claude, 2026-10-26. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-016.md`.

Owner decision 2026-10-26: the daily PAPER run moves to a Google Cloud Run job started by Cloud
Scheduler, at zero cost (design `docs/design/15-cloud-paper-free-tier.md`, read sections 3, 4
and 5). This task builds everything that can be built and tested offline. Nothing in Google Cloud
exists yet; no test may reach the network or Google Cloud.

No protected path is touched. Do not edit `src/aitrader/broker/`, `src/aitrader/risk/`,
`src/aitrader/execution/`, `src/aitrader/config/hard_caps.py` or `config/`. Do not add a
dependency: the Cloud Storage client is written on the standard library (`urllib`, `json`,
`tarfile`), like the Alpaca client in `src/aitrader/data/alpaca.py`.

## Scope

### A. Latest closed session (refactor, no behaviour change)

- Move the "last closed session at least 16 minutes ago" loop from `data_fetch` in
  `src/aitrader/cli/main.py` into a pure function (for example
  `last_closed_session(calendar, now) -> date` next to the CLI or in `aitrader.app`) and use it in
  `data_fetch`. Same results; existing tests keep passing.
- Tests: a weekday after close, a weekday before close + 16 minutes, a weekend, a market holiday,
  the minute boundary (exactly 16 minutes after close is closed).

### B. `aitrader paper daily` (new command, product code)

Runs the whole daily job in one process, in this order, with the existing functions (no
subprocesses):

1. If `--state-uri gs://BUCKET/OBJECT` is given: download the archive (section C), remember its
   generation, and unpack it into `--work-dir` (default: a new temporary directory). Without
   `--state-uri`, work in place on the existing directories (this is how the Windows task can
   use the same command).
2. Fetch: same as `aitrader data fetch` with `--end` = `last_closed_session(...)` for the
   `WallClock` now, into `<work-dir>/data/bars`.
3. S1 book: same as `aitrader paper run` with `config/paper.yaml` and `<work-dir>/data/paper`.
4. S3 book: same as `aitrader paper run --config config/paper-s3.yaml` with
   `<work-dir>/data/paper-s3`.
5. If `--state-uri` is given: pack `data/bars`, `data/paper` and `data/paper-s3` and upload with
   `ifGenerationMatch=<remembered generation>`; then upload each book's `status/latest.json` and
   the daily report files created in this run to `--reports-uri gs://BUCKET/PREFIX` (default:
   `reports/` in the same bucket), under `<prefix>/<book>/…` with `<book>` = `paper` or
   `paper-s3`. Report files that already exist in the bucket are not rewritten (upload with
   `ifGenerationMatch=0`; 412 for a report is not an error).

Rules:

- `--config-dir` (default `config`) and `--git-commit` (default: env `AITRADER_GIT_COMMIT`, then
  the existing `_git_commit`) are passed through, so the container needs no `.git`.
- If fetch fails, do not run the books and do not upload; exit with the fetch exit code.
- If fetch succeeds, run both books even if the first one fails, then upload (a HALTED book
  must persist its state and reports). Exit code = the larger of the two book exit codes
  (0, 4, 5 or 6), or the upload's exit code if the upload fails.
- Missing archive (404) is an error (exit 4, message says to pass `--init`), unless `--init` is
  given: then start from empty directories and upload with `ifGenerationMatch=0` (create only).
  `--init` with an existing archive is refused (exit 5). This prevents silently resetting the
  books.
- Upload conflict (412 on the archive) → exit 5 with a message that another writer changed the
  state; nothing is retried.
- Logging: one JSON object per line on stdout for each step (`event`: `daily.start`,
  `daily.restore`, `daily.fetch`, `daily.book`, `daily.upload`, `daily.end`), with `ts` (UTC ISO),
  `severity` (`INFO`, `WARNING` or `ERROR`; Cloud Logging reads this field), `exit_code`, and the
  step's summary values (session date, symbols, snapshot hashes, book state, sessions added,
  equity, object generation). `daily.end` carries the final `exit_code`. Exit 6 adds
  `"action": "stop; do not delete state; report to the designer"`. Never log secrets, tokens or
  signed URLs.
- `assert_no_live_credentials` runs first, as in `data fetch`.

### C. Cloud Storage state store (product code, standard library only)

New package `aitrader.cloud` (or module, your choice) with:

- A small `StateStore` protocol: `download(name) -> (bytes, generation)`,
  `upload(name, data, if_generation_match) -> generation`, with typed errors `NotFound` and
  `PreconditionFailed`.
- `GcsStateStore(bucket, transport=..., token_provider=...)` using the Cloud Storage JSON API:
  - download: `GET https://storage.googleapis.com/storage/v1/b/{bucket}/o/{object}?alt=media`
    (object name URL-encoded with `quote(name, safe="")`); generation from the
    `x-goog-generation` response header;
  - upload: `POST https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o?uploadType=media&name={object}&ifGenerationMatch={n}`
    with `Content-Type: application/gzip` (or `application/json` / `text/markdown` for reports);
    generation from the JSON response;
  - 404 → `NotFound`, 412 → `PreconditionFailed`, other non-2xx → a `DataSourceError`-like typed
    error with status code only (no response body in the message).
  - Token: `GET http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token`
    with header `Metadata-Flavor: Google`; cache it until 60 s before `expires_in`. The token is
    never logged or written to disk.
  - Timeouts on every request; transport injectable like `AlpacaDataClient`.
- Archive helpers: `pack(work_dir) -> bytes` (gzip tar of the three directories, relative paths
  only, no symlinks) and `unpack(data, work_dir)` with `tarfile` `filter="data"`; refuse members
  outside the three directories.
- `parse_gs_uri("gs://bucket/path") -> (bucket, object)` with clear errors for anything else.
- Import contract in `pyproject.toml`: only `aitrader.cli` may import the new package (add a
  `forbidden` contract listing every other top-level package as source). Do not loosen any
  existing contract.

### D. Container files

- `Dockerfile` at the repository root:
  - `FROM python:3.14-slim` (the operator pins the digest at build time; leave a comment saying so);
  - uv from `ghcr.io/astral-sh/uv:0.11.32` (the CI version) via `COPY --from=`;
  - `uv sync --frozen --no-dev` into the image; non-root user; `WORKDIR /app` with `config/`
    copied in;
  - `ARG GIT_COMMIT` → `ENV AITRADER_GIT_COMMIT=$GIT_COMMIT`;
  - `ENTRYPOINT ["aitrader", "paper", "daily"]` (arguments come from the job definition).
- `.dockerignore` and `.gcloudignore` excluding `.git`, `.venv`, `data/`, `results/`, `bt-*`,
  caches, `.env*`, `config/live*.yaml` (except the example) and token files.
- No secret, bucket name or project ID in these files.

### E. Docs

- `docs/dev/setup.md`, section "PAPER stage 1": add `aitrader paper daily` (local use on Windows
  and the exit codes above). Do not write Google Cloud set-up commands; the designer writes that
  runbook after the owner creates the project.

## Tests (all offline)

1. `last_closed_session` cases in A.
2. `GcsStateStore` against a local HTTP server (as in
   `tests/unit/data/test_alpaca.py::test_urllib_transport_against_a_local_server`) or an
   injected transport: download with generation, 404, 412 on upload, other status, URL encoding of
   object names with `/`, token caching and refresh, metadata header present, token never in
   exception text. Close every response (no `ResourceWarning` under `-W error`).
3. Archive: pack/unpack round trip byte-identical files; members with `..`, absolute paths,
   symlinks or outside the three directories are refused.
4. `paper daily` end to end with a fake `StateStore` and fake fetch (fixture bars, no network):
   first run with `--init`; second run restores and adds no session; missing archive without
   `--init` → 4; `--init` with an archive → 5; upload conflict → 5 and the store unchanged;
   fetch failure → books not run and nothing uploaded; one book exit 6 → the other book still
   runs, state uploaded, final exit 6 and the `action` field logged; reports uploaded once and not
   rewritten on the next run. Every stdout line parses as JSON with `ts`, `severity`, `event`.
5. Local mode (no `--state-uri`) on fixture data produces the same book as two separate
   `paper run` calls.
6. A test reads the `Dockerfile` and `.dockerignore` and checks the required lines in D
   (entrypoint, `--no-dev`, non-root user, `data/` and `.env*` ignored).

## Out of scope

- Creating anything in Google Cloud, `gcloud` commands, Terraform, CI deploy jobs.
- Changes to strategies, risk, execution, broker adapters, configuration values or the replay
  logic of `run_paper`.
- Notifications (alerts come from Cloud Logging in document 15 §5).

## Acceptance criteria

1. All `AGENTS.md` checks green on the operator PC (Windows); CI green on Linux and Windows.
2. Coverage of the new package and command at 100% (branch coverage as configured), overall
   gate unchanged.
3. The fixture backtest double run is byte-identical.
4. `git diff --stat` in LAST_REPORT shows no protected path, no change under
   `src/aitrader/broker/`, `src/aitrader/risk/`, `src/aitrader/execution/` or `config/`, and no
   change to `uv.lock` dependencies.
5. LAST_REPORT lists any command whose behaviour differs between Windows and Linux.

## Review round 1 (2026-10-26, Codex GitHub review of e9e1865)

Finding (valid, P2): if the state archive upload succeeds and a later report upload fails, the
next run restores those reports from the archive, counts them in `before`, and never publishes
them.

Required change (keep the create-only precondition; do not upload every report on every run,
because that would grow Class A operations past the free tier within about three months):
- In cloud mode, publish reports **before** the state archive upload: first each book's
  `status/latest.json`, then the daily report files created in this run (create-only,
  `ifGenerationMatch=0`, 412 ignored as today), then the archive with its generation
  precondition.
- If any report upload fails (other than 412 on a daily report), do not upload the archive; log
  `daily.upload` with the error and exit 4. The next run restores the previous archive, replays
  the same sessions again, recreates their reports as new files and publishes them; an already
  published daily report is not rewritten (412 ignored).
- Update `docs/dev/setup.md` to say reports are published before the state is saved.
- Tests: (1) a report upload failure leaves the archive generation unchanged and exits 4; the
  next run with a working store publishes the missing report and uploads the archive;
  (2) an archive upload conflict (412) after reports were published exits 5, and the next run
  does not rewrite the published daily reports; (3) existing tests keep passing.
- Coverage stays 100% for `aitrader.cloud` and `aitrader.cli`.
