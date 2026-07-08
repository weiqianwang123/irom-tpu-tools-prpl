#!/usr/bin/env bash
#
# prpl-quota.sh — show PRPL TPU *quota* per region, plus the queue's own usage.
#
# IMPORTANT: quota is the maximum you are ALLOWED to run. It is NOT the amount
# of TPU capacity GCP has free right now. GCP does not expose real-time spot
# capacity through any API. Whether a given size can actually be provisioned is
# only discoverable by submitting a job and watching whether it moves from
# WAITING_FOR_RESOURCES to ACTIVE.
#
# Section 1 (TPU QUOTA): from the Cloud Quotas API — the per-region limits.
# Section 2 (QUEUE USAGE): from `tpu admin resources` — how many chips this
#           queue is currently using per quota group (needs queue bucket read
#           access; skipped if the tpu CLI or access is unavailable).
#
# Usage:
#   ./contrib/prpl-quota.sh                       # default project below
#   PROJECT=my-project ./contrib/prpl-quota.sh    # override project
#
# Requirements: gcloud (authenticated), python3. Reading quota may need
# roles/cloudquotas.viewer or serviceusage.quotas.get; if you get a permission
# error, run it with an admin account.

set -euo pipefail

PROJECT="${PROJECT:-tpu-tsilver-20260619}"

echo "PRPL TPU quota for project: ${PROJECT}"
echo

echo "=== 1. TPU QUOTA (max you are ALLOWED to run — NOT live free capacity) ==="

token="$(gcloud auth print-access-token 2>/dev/null || true)"
if [[ -z "${token}" ]]; then
  echo "  Could not get an access token. Run 'gcloud auth login' first."
  exit 1
fi

url="https://cloudquotas.googleapis.com/v1/projects/${PROJECT}/locations/global/services/tpu.googleapis.com/quotaInfos?pageSize=200"

curl -s -H "Authorization: Bearer ${token}" "${url}" | python3 -c '
import sys, json

try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    print("  Could not parse quota API response (permission denied?).")
    sys.exit(0)

if "error" in data:
    print("  Quota API error:", data["error"].get("message", data["error"]))
    sys.exit(0)

rows = []
for q in data.get("quotaInfos", []):
    metric = q.get("metric", "")
    # Only accelerator-count metrics for TPU; skip qps / queuedResources.
    if "/tpu-v" not in metric:
        continue
    accel = metric.rsplit("/", 1)[-1]  # e.g. tpu-v6e-preemptible
    for dim in q.get("dimensionsInfos", []):
        dims = dim.get("dimensions", {})
        # Only rows anchored to a real zone (skip region-level dupes and GLOBAL).
        zone = dims.get("zone")
        if not zone:
            continue
        value = dim.get("details", {}).get("value")
        if value is None:
            continue
        try:
            ival = int(value)
        except (TypeError, ValueError):
            continue
        # -1 means "no explicit limit"; skip the noise, show real positive caps.
        if ival <= 0:
            continue
        rows.append((accel, zone, ival))

if not rows:
    print("  No zone-anchored positive TPU quotas found.")
    print("  (You may lack cloudquotas.viewer, or none are set on this project.)")
    sys.exit(0)

rows.sort(key=lambda r: (r[0], r[1]))
head = "  %-26s %-18s %12s" % ("ACCELERATOR", "ZONE", "QUOTA(chips)")
print(head)
print("  %-26s %-18s %12s" % ("-"*26, "-"*18, "-"*12))
for accel, zone, val in rows:
    print("  %-26s %-18s %12d" % (accel, zone, val))
'

echo
echo "=== 2. QUEUE USAGE (chips this queue is currently using, per group) ==="
if command -v tpu >/dev/null 2>&1; then
  # tpu admin resources prints quota-group USED/LIMIT and per-user chips.
  if ! tpu admin resources 2>/dev/null; then
    echo "  Could not read queue state (queue bucket access or scheduler_state missing)."
  fi
else
  echo "  tpu CLI not on PATH; skipping. Install it to see live queue usage."
fi

echo
echo "NOTE: Quota is a ceiling, not availability. GCP does not expose real-time"
echo "spot capacity. To learn if a size can be provisioned now, submit a job and"
echo "watch 'tpu status' for WAITING_FOR_RESOURCES -> ACTIVE."
