# CURRENT_TASK — TASK-018: Google Cloud set-up script for the daily PAPER job

Designer: Claude, 2026-10-26. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-017.md`.

Owner request 2026-10-26: Codex builds the Google Cloud set-up. Codex has no network and no
credentials, so Codex writes and tests a script; the operator runs it on the owner's PC after the
owner's approval. The commands to automate are in `docs/dev/cloud-run.md` (sections 1–7); the
design is `docs/design/15-cloud-paper-free-tier.md`. No test may call `gcloud` or the network.

No protected path is touched. Do not edit `src/aitrader/`, `config/` or `uv.lock`. No new
dependency.

## Scope

### A. `scripts/gcp_setup.py` (standard library only)

- Usage: `uv run python scripts/gcp_setup.py --project aitrader-paper [--region us-east1]
  [--bucket NAME] [--commit SHA] [--work-dir D:\my-workspace\ops\gcp] (--dry-run | --apply)
  [--step apis,bucket,registry,accounts,build,job,init,schedule]`.
  `--dry-run` is the default when neither flag is given; `--apply` must be explicit.
- Builds the exact `gcloud` argument lists of `docs/dev/cloud-run.md` §1–7 (same names, flags,
  limits, schedule and time zone) as data, then prints them (dry run) or runs them with
  `subprocess.run([...], check=False)` without a shell (apply).
- Idempotent: before each create, run the matching read-only `describe` (or
  `buckets describe`, `service-accounts describe`, `secrets describe`, ...) and skip the create
  when the resource exists; print `exists` / `created` / `would create` per resource. Bindings and
  policy updates may be re-applied.
- Pre-flight before any write: `gcloud config get-value account` is non-empty;
  `gcloud projects describe <project>` succeeds; `gcloud billing projects describe <project>`
  shows `billingEnabled: true`; both secrets exist (`gcloud secrets describe`, never `versions
  access`). Any failure stops with a clear message and exit 2; nothing is created.
- Refuse (exit 2) anything outside the design: a region other than `us-east1`, a project ID other
  than the one given, any flag that would create a VM, address, NAT or load balancer (there is no
  such code path; a test checks no generated command contains `compute`).
- `build` writes `cloudbuild.yaml` and the lifecycle/cleanup JSON files into `--work-dir`
  (never into the repository), and requires `--commit` (40 hex characters) equal to the current
  `git rev-parse HEAD` and a clean worktree (`git status --porcelain` empty).
- `init` runs the one-time `--init` execution only when the state object does not exist
  (`gcloud storage objects describe gs://BUCKET/state/paper-state.tar.gz` fails with not found);
  otherwise it prints `exists` and skips.
- Never prints, reads or passes secret values; never uses `--quiet` on deletes (there are no
  deletes).
- On the first failing `gcloud` command in apply mode, stop, print the command (no secrets
  involved) and its exit code, exit 1.
- Ends with a summary: each resource and its state; the image URI and commit.

### B. Tests (`tests/unit/scripts/test_gcp_setup.py`, offline)

- Load the script with `importlib` from its path. Inject a fake runner that records argument lists
  and returns scripted results.
- Dry run prints every command of §1–7 in order and runs only read-only commands.
- Each create is skipped when its describe succeeds and run when it fails with not found.
- Pre-flight failures (no account, no project, billing disabled, a missing secret) stop before
  any write, exit 2.
- Wrong region, malformed commit, commit not equal to HEAD, dirty worktree → exit 2, no write.
- Generated files: lifecycle keeps 30 non-current versions; cleanup keeps 2; cloudbuild uses the
  commit as tag and `GIT_COMMIT` build arg; files land only in `--work-dir`.
- Job flags exactly: 1 task, parallelism 1, max retries 1, timeout 30m, 1 CPU, 2Gi, the two
  secrets by name, state URI. Schedule `0 8 * * 2-6`, `Asia/Tokyo`, POST to the v2 `:run` URI,
  OAuth service account.
- No generated command contains `compute`, `addresses`, `routers`, `versions access` or
  `--quiet`.
- Apply mode stops at the first failing command with exit 1.

### C. Docs

- `docs/dev/cloud-run.md`: add at the top "Preferred: run `scripts/gcp_setup.py --dry-run`,
  review the output, then `--apply`"; keep the manual commands as the reference.

## Out of scope

- Console steps of §8 (alert, spend cap): stay manual for the owner.
- Running anything against Google Cloud.

## Acceptance criteria

1. All `AGENTS.md` checks green on Windows; CI green on Linux and Windows.
2. The new tests cover every branch of the script (report the coverage of the script measured
   with `--cov=scripts` in LAST_REPORT; the package gate is unchanged).
3. `git diff --stat` shows changes only in `scripts/`, `tests/`, `docs/dev/cloud-run.md`,
   `.ai/LAST_REPORT.md`.
