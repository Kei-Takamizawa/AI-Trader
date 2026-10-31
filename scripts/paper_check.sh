#!/usr/bin/env bash
# Read-only daily PAPER check with the reports-only reader key.
set -u
unset CLOUDSDK_AUTH_ACCESS_TOKEN
export CLOUDSDK_CONFIG="$(mktemp -d)"
KEY="$CLOUDSDK_CONFIG/k.json"
trap 'rm -rf "$CLOUDSDK_CONFIG"' EXIT
printf '%s' "$AITRADER_GCP_READER_KEY" | tr -d '<>"'"'"' \r\n' | base64 -d > "$KEY" || { echo "KEY_DECODE_FAILED"; exit 1; }
gcloud auth activate-service-account --key-file="$KEY" --quiet >/dev/null 2>&1 || { echo "AUTH_FAILED"; exit 1; }
for b in paper paper-s3; do
  echo "== $b"
  gcloud storage cat "gs://aitrader-paper-state/reports/$b/status/latest.json" 2>&1 | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    s = json.loads(raw)
except Exception:
    print("READ_FAILED", raw[:300]); sys.exit(0)
for k in ("strategy_id","state","reason","as_of","last_session","sessions_recorded","data_end","data_stale","equity_usd","equity_jpy","return_since_start","max_drawdown_since_start","b0_return_since_start","git_commit"):
    print(k, s.get(k))
print("positions", [(p.get("symbol"), p.get("quantity", p.get("qty"))) for p in s.get("positions", [])])
print("next_intents", [(i.get("symbol"), i.get("side")) for i in s.get("next_intents", [])])'
done
