# Daily operations of the cloud PAPER job (runbook)

Written by Claude (designer) on 2026-10-31, the day the Cloud Run job became the only PAPER
runner. Set-up of the resources is in `docs/dev/cloud-run.md`; the design is
`docs/design/15-cloud-paper-free-tier.md`. This document says what to look at, how often, and
what to do when something is wrong.

## 1. What runs, and when

| Item | Value |
|---|---|
| Project / region | `aitrader-paper` / `us-east1` |
| Trigger | Cloud Scheduler `aitrader-paper-daily`, `0 8 * * 2-6`, `Asia/Tokyo` (Tuesday to Saturday 08:00 JST) |
| Job | Cloud Run job `aitrader-paper-daily`, 1 task, 1 retry, 30 min timeout; a normal run takes about 2 minutes |
| What one run does | Restore the state archive, fetch the newest closed US session, run S1 (`paper`) and S3 (`paper-s3`), publish reports, save the archive |
| State | `gs://aitrader-paper-state/state/paper-state.tar.gz` (versioned, 30 old versions kept) |
| Status | `gs://aitrader-paper-state/reports/paper/status/latest.json` and `.../reports/paper-s3/status/latest.json` |
| Daily reports | `gs://aitrader-paper-state/reports/<book>/reports/daily/<session>.json` and `.md` |
| Alert | E-mail to the owner on any log entry of the job with severity ERROR or higher (set 2026-10-26) |
| Spend cap | Cloud Run, JPY 100 (set 2026-10-26); budget JPY 100/month with e-mails at 50/90/100% |

The 08:00 JST run processes the US session that closed the evening before (Monday to Friday
sessions). On a US market holiday the run still happens, adds no session and exits 0.

The Windows task "AI-Trader PAPER daily" is disabled, not deleted. Never enable it while the cloud
job is enabled: two writers would make different books (section 6).

## 2. Daily check (about 2 minutes)

Who: Claude each run day through the read-only report key (section 7); the owner when that
report is missing or flags a problem. When: after
08:15 JST on Tuesday to Saturday.

Open Cloud Shell in the console of project `aitrader-paper` (free; works in a phone browser) and
run:

```bash
gcloud run jobs executions list --job=aitrader-paper-daily --region=us-east1 --limit=3
for b in paper paper-s3; do
  gcloud storage cat "gs://aitrader-paper-state/reports/$b/status/latest.json" | python3 -c '
import json, sys
s = json.load(sys.stdin)
print(s["strategy_id"], s["state"], "as_of", s["as_of"], "last_session", s["last_session"],
      "sessions", s["sessions_recorded"], "stale", s["data_stale"],
      "equity_usd", s["equity_usd"], "return", s["return_since_start"],
      "spy", s["b0_return_since_start"], "positions", len(s["positions"]),
      "next_intents", len(s["next_intents"]))'
done
```

A good day looks like this:

| Check | Expected |
|---|---|
| Newest execution | Created today around 08:00 JST, status succeeded (1/1) |
| `as_of` | Today, a few minutes after 08:00 JST (23:00 UTC the previous day) |
| `state` | `RUNNING` for both books |
| `last_session` | The newest US session (yesterday's date in New York); unchanged after a US holiday |
| `sessions_recorded` | One more than the previous trading day |
| `data_stale` | `false` |

Returns and drawdown are fractions (0.01 means 1%). `equity_jpy` uses a fixed reporting rate.
Fills are modelled, not broker executions. The `.md` daily report explains the day's trades and
rejections: `gcloud storage cat gs://aitrader-paper-state/reports/paper/reports/daily/<session>.md`.

## 3. Exit codes and what to do

The job writes JSON lines; the last line is `daily.end` with `exit_code`. Read it with:

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="aitrader-paper-daily" AND jsonPayload.event:"daily."' \
  --freshness=1d --order=asc --format='value(jsonPayload)'
```

| Exit | Meaning | Action |
|---|---|---|
| 0 | Success (also when no new session) | Nothing |
| 2 | Configuration or credential error (for example a missing secret version, or a `LIVE_*` variable present) | Do not re-run. Owner checks the two secrets exist and are enabled; report to the designer |
| 4 | Data or storage error (Alpaca outage, 401/403, Cloud Storage error) | If the log says `daily.fetch` and Alpaca had an outage, re-run once by hand after 30 minutes (section 4). A 401/403 means the key is invalid or the plan changed: do not re-run; report |
| 5 | Usage, lock or state-generation conflict ("another writer changed the state") | Do not re-run. Check that the Windows task is still disabled and no manual run was in progress; report |
| 6 | Replay mismatch: the recorded history does not match a replay | **Stop.** Pause the scheduler (section 5), do not delete or overwrite any state, report the status file and the session to the designer |
| No execution today | Scheduler did not fire, or the spend cap blocked it | Check `gcloud scheduler jobs describe aitrader-paper-daily --location=us-east1` (state ENABLED, last attempt) and the billing page; then run by hand once |

A run that fails before the upload leaves the previous archive untouched, so a later successful
run catches up all missed sessions automatically. Missing a day is not harmful; running twice in
parallel is.

## 4. Re-run by hand

Only when no execution is running (`gcloud run jobs executions list ...` shows none active):

```bash
gcloud run jobs execute aitrader-paper-daily --region=us-east1 --wait
```

It must exit 0. Repeating a run with unchanged data adds nothing. Never pass `--init` to an
existing archive (it is refused anyway).

## 5. Pause and resume

```bash
gcloud scheduler jobs pause aitrader-paper-daily --location=us-east1
gcloud scheduler jobs resume aitrader-paper-daily --location=us-east1
```

Pause before any investigation of exit 6, before a manual fallback on Windows, and before a
deploy that changes the books. Paused days are caught up by the next run.

## 6. Manual fallback on the Windows PC (only with the designer)

The Windows `data/` copy is frozen at the 2026-10-30 session. Before any local run:

1. Pause the scheduler (section 5) and confirm no execution is running.
2. Copy the archive and unpack it into an empty work directory, never over the old `data/`:
   `gcloud storage cp gs://aitrader-paper-state/state/paper-state.tar.gz .` then extract it
   into an empty directory and point `--work-dir` there.
3. After the fallback, decide with the designer which copy becomes the state of record before
   resuming the scheduler. The cloud archive is never overwritten from the PC by hand.

## 7. Daily report by Claude (owner decision 2026-10-31)

The project's cloud Claude sessions have no Google Cloud credentials. For Claude to check and
report each morning, the owner creates a read-only account that can read **only**
`reports/` (not the state archive, not market data, not secrets), in Cloud Shell:

```bash
P=aitrader-paper; B=aitrader-paper-state
SA=aitrader-reader@$P.iam.gserviceaccount.com
gcloud iam service-accounts create aitrader-reader --project=$P --display-name="AI-Trader report reader"
gcloud storage buckets add-iam-policy-binding gs://$B --member="serviceAccount:$SA" \
  --role=roles/storage.objectViewer \
  --condition="expression=resource.name.startsWith(\"projects/_/buckets/$B/objects/reports/\"),title=reports-only"
gcloud iam service-accounts keys create ~/reader-key.json --iam-account=$SA
```

Then the owner pastes the one-line `base64 -w0 ~/reader-key.json` output (environment variables
are single lines) into the project's cloud environment
settings as the environment variable `AITRADER_GCP_READER_KEY`, and deletes the file
(`shred -u ~/reader-key.json`). The key is never pasted into a chat, a prompt, a file in the
repository or CI. Without list permission the reader fetches only the known object names above.

This deviates from document 15 §6 ("no key is created") for this one read-only account; the owner
chose it on 2026-10-31 (option A in the project thread, recorded in PR #17). Revoke it at any time with
`gcloud iam service-accounts keys list/delete --iam-account=$SA`; rotate it every 90 days.

The report is the table of section 2 for both books, in Japanese, posted to the project once per
run day. A failed check (stale `as_of`, `HALTED`, `data_stale`, missing file) is reported as an
action item with the matching row of section 3.

## 8. Weekly and monthly

| When | Check |
|---|---|
| Weekly (weekend) | Execution history has five successes for the week; billing page shows JPY 0 for the month; `gcloud artifacts docker images list us-east1-docker.pkg.dev/aitrader-paper/aitrader/aitrader` shows at most 2 images |
| Monthly | Bucket size `gcloud storage du -s gs://aitrader-paper-state` well under 5 GB; budget e-mails received for none of the thresholds; compare both books' returns with SPY (`b0_return_since_start`) in a short note |
| Before 2027-09-01 | Re-check the Cloud Monitoring alerting price (document 15 §5) |
| Every 90 days | Rotate the reader key if section 7 is in use |

## 9. Deploying a new image

Only from `main` with green CI, and not between 07:45 and 08:30 JST on Tuesday to Saturday.
Build and update as in `docs/dev/cloud-run.md` §5 and §6, then run once by hand (section 4) and
check section 2. A change to the books' start, strategy parameters, simulation assumptions or risk
limits needs a new state after review (`docs/dev/setup.md`, PAPER stage 1); do not deploy it onto
the existing archive.
