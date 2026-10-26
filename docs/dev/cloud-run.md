# Google Cloud set-up for the daily PAPER job (runbook)

Design: `docs/design/15-cloud-paper-free-tier.md` (approved 2026-10-26). Written by the designer
on 2026-10-26 for project `aitrader-paper`; run by the operator (Claude Code on the owner's PC,
with the owner signed in to `gcloud`) only after the owner approves "create the resources" for
this runbook. Every command is shown for PowerShell; replace nothing except where it says so.

Before you start, check: the project is linked to the owner's billing account, the JPY 100
budget exists, and the two secrets `aitrader-data-api-key` and `aitrader-data-api-secret` exist
(created by the owner). Do not print secret values; never run `gcloud secrets versions access`.

```powershell
$P = "aitrader-paper"; $R = "us-east1"
$BUCKET = "$P-state"            # if the name is taken, use "$P-state-<4 digits>" and record it
$JOB_SA = "aitrader-job@$P.iam.gserviceaccount.com"
$SCH_SA = "aitrader-scheduler@$P.iam.gserviceaccount.com"
$REPO = "$R-docker.pkg.dev/$P/aitrader/aitrader"
gcloud config set project $P
New-Item -ItemType Directory -Force D:\my-workspace\ops\gcp | Out-Null
```

## 1. APIs

```powershell
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com `
  artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com `
  storage.googleapis.com logging.googleapis.com monitoring.googleapis.com
```

## 2. State bucket (private, versioned, 30 old versions)

```powershell
gcloud storage buckets create "gs://$BUCKET" --location=$R --default-storage-class=STANDARD `
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$BUCKET" --versioning
'{"rule":[{"action":{"type":"Delete"},"condition":{"isLive":false,"numNewerVersions":30}}]}' |
  Set-Content -Encoding ascii D:\my-workspace\ops\gcp\lifecycle.json
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file=D:\my-workspace\ops\gcp\lifecycle.json
```

## 3. Image repository (keep the 2 newest images)

```powershell
gcloud artifacts repositories create aitrader --repository-format=docker --location=$R
'[{"name":"keep-2","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":2}},
  {"name":"delete-rest","action":{"type":"Delete"},"condition":{"tagState":"any"}}]' |
  Set-Content -Encoding ascii D:\my-workspace\ops\gcp\cleanup.json
gcloud artifacts repositories set-cleanup-policies aitrader --location=$R `
  --policy=D:\my-workspace\ops\gcp\cleanup.json --no-dry-run
```

## 4. Service accounts and least-privilege bindings

```powershell
gcloud iam service-accounts create aitrader-job --display-name="AI-Trader PAPER job"
gcloud iam service-accounts create aitrader-scheduler --display-name="AI-Trader scheduler"
foreach ($s in "aitrader-data-api-key","aitrader-data-api-secret") {
  gcloud secrets add-iam-policy-binding $s --member="serviceAccount:$JOB_SA" `
    --role=roles/secretmanager.secretAccessor
}
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$JOB_SA" `
  --role=roles/storage.objectUser
```

No project-level role is granted to either account, and no key is created for either.

## 5. Build the image from `main`

From `D:\my-workspace\AI-Trader` on an up-to-date `main` with green CI:

```powershell
$C = git rev-parse HEAD
@"
steps:
- name: gcr.io/cloud-builders/docker
  args: [build, --build-arg, GIT_COMMIT=$C, -t, ${REPO}:$C, .]
images: [${REPO}:$C]
"@ | Set-Content -Encoding ascii D:\my-workspace\ops\gcp\cloudbuild.yaml
gcloud builds submit --region=$R --config=D:\my-workspace\ops\gcp\cloudbuild.yaml .
```

`.gcloudignore` keeps `data/`, `.git`, `.venv` and secrets out of the upload. Record in the
report: the commit, the image digest and the base image digest shown in the build log, the build
minutes and the image size (`gcloud artifacts docker images list $REPO --include-tags`). If the
build fails with a permission error on pushing to Artifact Registry, stop and report the exact
message; do not grant broad roles.

## 6. The job

```powershell
gcloud run jobs create aitrader-paper-daily --region=$R --image="${REPO}:$C" `
  --service-account=$JOB_SA --tasks=1 --parallelism=1 --max-retries=1 --task-timeout=30m `
  --cpu=1 --memory=2Gi `
  --set-secrets="AITRADER_DATA_API_KEY=aitrader-data-api-key:latest,AITRADER_DATA_API_SECRET=aitrader-data-api-secret:latest" `
  --args="--state-uri=gs://$BUCKET/state/paper-state.tar.gz"
```

First run, once, creating the cloud books from `paper.start`:

```powershell
gcloud run jobs execute aitrader-paper-daily --region=$R --wait `
  --args="--state-uri=gs://$BUCKET/state/paper-state.tar.gz,--init"
```

Then one normal run (`gcloud run jobs execute aitrader-paper-daily --region=$R --wait`), which
must exit 0 and add no session. Record both executions' duration and the peak memory from the
console's job metrics. A later image update is
`gcloud run jobs update aitrader-paper-daily --region=$R --image="${REPO}:<commit>"`.

## 7. Schedule (Tuesday to Saturday 08:00 JST)

```powershell
gcloud run jobs add-iam-policy-binding aitrader-paper-daily --region=$R `
  --member="serviceAccount:$SCH_SA" --role=roles/run.invoker
gcloud scheduler jobs create http aitrader-paper-daily --location=$R `
  --schedule="0 8 * * 2-6" --time-zone="Asia/Tokyo" --http-method=POST `
  --uri="https://run.googleapis.com/v2/projects/$P/locations/$R/jobs/aitrader-paper-daily:run" `
  --oauth-service-account-email=$SCH_SA
```

## 8. Alert and spend cap (console, owner)

- **Alert** (Logging → Log Explorer → Create alert): query
  `resource.type="cloud_run_job" AND resource.labels.job_name="aitrader-paper-daily" AND severity>=ERROR`,
  notification at most once per hour, email channel = the owner's address.
- **Spend cap** (Billing → Budgets & alerts): a spend cap budget on project `aitrader-paper`
  for Cloud Run, JPY 100. Billing permissions are the owner's, so the owner sets it.

## 9. Shadow run and cut-over

For 5 trading days the Windows task and the cloud job both run. Each day the designer compares
`gs://$BUCKET/reports/paper/status/latest.json` (and `paper-s3`) with the Windows
`data/paper*/status/latest.json`: equity, positions, last session and session count must match.
Cut-over (disabling the Windows task, keeping it for manual fallback) needs the owner's approval.

## Not in this runbook

No VM, external IP, NAT, load balancer or other region. Anything not listed here needs the
designer first.
