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
# Section 1 (TPU QUOTA): from the Service Usage consumer-quota API — the
#           effective per-zone and per-region limits used by this queue.
# Section 2 (QUEUE USAGE): from `tpu admin resources` — how many chips this
#           queue is currently using per quota group (needs queue bucket read
#           access; skipped if the tpu CLI or access is unavailable).
#
# Usage:
#   ./contrib/prpl-quota.sh                       # default project below
#   PROJECT=my-project ./contrib/prpl-quota.sh    # override project
#
# Requirements: gcloud (authenticated, with the alpha component), python3.

set -euo pipefail

PROJECT="${PROJECT:-tpu-tsilver-20260619}"

echo "PRPL TPU quota for project: ${PROJECT}"
echo

echo "=== 1. TPU QUOTA (max you are ALLOWED to run — NOT live free capacity) ==="

if ! quota_json="$(
  gcloud alpha services quota list \
    --service=tpu.googleapis.com \
    --consumer="projects/${PROJECT}" \
    --format=json
)"; then
  echo "  Could not read TPU quota. Check gcloud authentication and project access."
  exit 1
fi

printf "%s\n" "${quota_json}" | python3 -c '
import sys, json

try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    print("  Could not parse Service Usage quota response.")
    sys.exit(1)

metrics = {item.get("metric"): item for item in data}

# Keep this mapping aligned with quota_groups/resources in resources.yaml.
queue_quotas = [
    ("v4",   "v4 spot",      "tpu.googleapis.com/tpu-v4s-pod-preemptible", "us-central2-b"),
    ("v4od", "v4 on-demand", "tpu.googleapis.com/tpu-v4s-pod",             "us-central2-b"),
    ("v6",   "v6e spot",     "tpu.googleapis.com/tpu-v6e-preemptible",     "us-east1-d"),
    ("v5",   "v5e spot",     "tpu.googleapis.com/tpu-v5s-litepod-preemptible", "us-central1-a"),
    ("v6eu", "v6e spot",     "tpu.googleapis.com/tpu-v6e-preemptible",     "europe-west4-a"),
    ("v5eu", "v5e spot",     "tpu.googleapis.com/tpu-v5s-litepod-preemptible", "europe-west4-b"),
]

def effective_limit(metric, dimension, location):
    item = metrics.get(metric, {})
    for limit in item.get("consumerQuotaLimits", []):
        for bucket in limit.get("quotaBuckets", []):
            if (bucket.get("dimensions") or {}).get(dimension) == location:
                return bucket.get("effectiveLimit")
    return None

def default_zone_limit(metric):
    item = metrics.get(metric, {})
    for limit in item.get("consumerQuotaLimits", []):
        if "{zone}" not in limit.get("unit", ""):
            continue
        for bucket in limit.get("quotaBuckets", []):
            if not (bucket.get("dimensions") or {}):
                return bucket.get("effectiveLimit")
    return None

def shown(value):
    if value is None:
        return "not found"
    if value == -1:
        return "unlimited"
    return str(value)

print("  %-6s %-14s %-18s %12s %14s" %
      ("GROUP", "TYPE", "ZONE", "ZONE LIMIT", "REGION LIMIT"))
print("  %-6s %-14s %-18s %12s %14s" %
      ("-" * 6, "-" * 14, "-" * 18, "-" * 12, "-" * 14))
for group, kind, metric, zone in queue_quotas:
    region = zone.rsplit("-", 1)[0]
    zone_limit = effective_limit(metric, "zone", zone)
    region_limit = effective_limit(metric, "region", region)
    print("  %-6s %-14s %-18s %12s %14s" %
          (group, kind, zone, shown(zone_limit), shown(region_limit)))

print()
print("  Separate v5e serving quotas (not queue training quota groups):")
for kind, metric in [
    ("spot serving", "tpu.googleapis.com/tpu-v5s-litepod-serving-preemptible"),
    ("on-demand serving", "tpu.googleapis.com/tpu-v5s-litepod-serving"),
]:
    print("    %-18s default per-zone effective limit: %s" %
          (kind, shown(default_zone_limit(metric))))
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
