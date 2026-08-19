# PRPL TPU Admin Guide

This repository is configured for the PRPL lab TPU queue in the Google Cloud project:

`tpu-tsilver-20260619`

The current TPU queue scheduler is temporarily deployed on a small Google Compute Engine VM:

`prpl-tpu-scheduler`

This VM does not run training jobs itself. It only runs the queue scheduler. The scheduler continuously scans the TPU queue buckets, picks up pending jobs, creates the requested Cloud TPU VMs, monitors job state, and cleans up resources after completion.

There should be exactly one active scheduler for the PRPL TPU queue at any time.

## Current architecture

```text
Users or Cloud Shell
  install the `tpu` CLI
  submit jobs with `tpu create`

GCS queue buckets
  store job specs, code bundles, logs, and status files

GCP scheduler VM
  instance name: prpl-tpu-scheduler
  runs the systemd user service: irom-tpu-scheduler.service

Cloud TPU VMs
  created and deleted by the scheduler
  run the actual training code
```

Users do not need to SSH into the scheduler VM to submit jobs. Users only need the `tpu` CLI and write access to the queue buckets.

The scheduler VM should only be accessed for scheduler maintenance, debugging, restart, migration, or log inspection.

## Scheduler VM identity and SSH access

The current scheduler runs on this Google Compute Engine VM:

```text
Project: tpu-tsilver-20260619
Instance name: prpl-tpu-scheduler
Zone: us-central1-a
Linux user: theprplgroup
```

To SSH into the scheduler VM, use:

```bash
gcloud compute ssh theprplgroup@prpl-tpu-scheduler \
  --project=tpu-tsilver-20260619 \
  --zone=us-central1-a
```

After logging in, verify that you are the correct Linux user:

```bash
whoami
```

The expected output is:

```text
theprplgroup
```

This matters because the scheduler is installed as a systemd user service under the `theprplgroup` Linux user. If you SSH as a different Linux user, `systemctl --user status irom-tpu-scheduler.service` may not show or manage the active scheduler service.

## Queue buckets

The scheduler uses one queue bucket per TPU region:

```text
gs://prpl-tpu-queue-us-central2-944301850228
gs://prpl-tpu-queue-us-east1-944301850228
gs://prpl-tpu-queue-us-central1-944301850228
gs://prpl-tpu-queue-europe-west4-944301850228
```

These buckets are queue and metadata buckets. They are intended for job specs, uploaded code bundles, job status, and logs.

They should not be used as long term dataset storage or checkpoint storage.

## Worker service account

TPU worker VMs should run as:

```text
tpu-worker@tpu-tsilver-20260619.iam.gserviceaccount.com
```

This service account needs permission to read job code, write logs, and access any dataset or checkpoint buckets used by training jobs.

## V4 network in us-central2

The `default` auto-mode VPC does not automatically provide a subnet in the
private `us-central2` region. Both the v4 spot (`v4-*`) and v4 on-demand
(`v4od-*`) queue resources therefore explicitly use:

```text
Network:    default
Subnetwork: prpl-tpu-us-central2
Region:     us-central2
CIDR:       10.10.0.0/20
```

Create the subnet once with a project identity that has
`compute.subnetworks.create` permission:

```bash
gcloud compute networks subnets create prpl-tpu-us-central2 \
  --project=tpu-tsilver-20260619 \
  --network=default \
  --region=us-central2 \
  --range=10.10.0.0/20 \
  --enable-private-ip-google-access
```

Do not try to name this manually created subnet `default`: that name is
reserved for automatically created subnets in an auto-mode VPC. The scheduler
must be installed from a version of this repository that passes the configured
network and subnetwork to queued-resource creation. Without that code, v4
requests remain `PENDING` and the scheduler log reports that subnetwork
`default` does not exist.

Verify the subnet:

```bash
gcloud compute networks subnets describe prpl-tpu-us-central2 \
  --project=tpu-tsilver-20260619 \
  --region=us-central2
```

## Data and checkpoint buckets

Users need somewhere to put datasets and read/write checkpoints. The queue buckets are not for this. Instead, the lab provides one data bucket and one checkpoint bucket per TPU region. Users do not create buckets; they upload into a subfolder named after themselves. Creating these buckets is an admin task because each one must also be granted to the worker service account.

Naming convention (one pair per region):

```text
gs://prpl-data-<region>-944301850228     # datasets (read-only for workers)
gs://prpl-ckpt-<region>-944301850228     # checkpoints/outputs (writable by workers)
```

Soft delete is disabled on all shared data and checkpoint buckets. This is an
intentional cost-control policy: training jobs overwrite checkpoints frequently,
and retaining every deleted checkpoint for the default seven days caused several
TiB of hidden, billable storage. Deletions from these buckets are therefore
permanent. Queue buckets keep the default seven-day soft delete policy because
they are small and contain scheduler metadata that can be useful for recovery.

Regions that need buckets (match the TPU regions):

```text
us-east1       for v6
us-central1    for v5
us-central2    for v4
europe-west4   for v6eu / v5eu
```

The data bucket must be in the same region as the TPU that reads it. Cross-region reads are slower and incur egress charges; cross-continent is worse. Tell users: data lives in the same region as the TPU.

Create the buckets and grant access. Run this in Cloud Shell (fast, inside Google's network). It creates both buckets for each region, grants the worker service account read on data and read/write on checkpoints, and grants the users group read/write on both so members can upload and retrieve:

```bash
PROJECT=tpu-tsilver-20260619
WORKER_SA=tpu-worker@tpu-tsilver-20260619.iam.gserviceaccount.com
USERS_GROUP=group:prpl-tpu-users@googlegroups.com   # or grant per user with user:EMAIL

for region in us-east1 us-central1 us-central2 europe-west4; do
  data="gs://prpl-data-${region}-944301850228"
  ckpt="gs://prpl-ckpt-${region}-944301850228"

  gcloud storage buckets create "$data" --project="$PROJECT" --location="$region" \
    --soft-delete-duration=0
  gcloud storage buckets create "$ckpt" --project="$PROJECT" --location="$region" \
    --soft-delete-duration=0

  # Worker service account: read datasets, read/write checkpoints.
  gcloud storage buckets add-iam-policy-binding "$data" \
    --member="serviceAccount:${WORKER_SA}" --role=roles/storage.objectViewer
  gcloud storage buckets add-iam-policy-binding "$ckpt" \
    --member="serviceAccount:${WORKER_SA}" --role=roles/storage.objectAdmin

  # Lab users: upload data and retrieve checkpoints.
  gcloud storage buckets add-iam-policy-binding "$data" \
    --member="$USERS_GROUP" --role=roles/storage.objectAdmin
  gcloud storage buckets add-iam-policy-binding "$ckpt" \
    --member="$USERS_GROUP" --role=roles/storage.objectAdmin
done
```

Verify the deletion policy after creating or changing a bucket:

```bash
gcloud storage buckets describe gs://prpl-data-us-central2-944301850228 \
  --format='value(soft_delete_policy.retentionDurationSeconds)'
gcloud storage buckets describe gs://prpl-ckpt-us-central2-944301850228 \
  --format='value(soft_delete_policy.retentionDurationSeconds)'
```

The expected value is `0`. To disable soft delete on an existing shared data or
checkpoint bucket, run:

```bash
gcloud storage buckets update gs://BUCKET_NAME --clear-soft-delete
```

Changing this setting does not immediately remove objects that were already
soft-deleted. Those objects remain billable and recoverable until the retention
deadline assigned when they were deleted expires.

### Per-user storage audit and advisory limits

Cloud Storage does not provide a capacity quota for a username prefix inside a
shared bucket. The lab therefore uses a daily advisory audit; it does not block
uploads and never deletes objects. Usage is the sum of live objects under a
user's top-level prefix across all configured `prpl-data-*` and `prpl-ckpt-*`
buckets. The scan is read-only and does not restart the scheduler or interrupt
running TPU jobs. Because active jobs can write checkpoints while a scan is in
progress, the result is an approximate point-in-time total.

The policy is:

```text
lj8040:                 2 TB
ss1824:                 2 TB
all future/new users:   1 TB by default
```

Here TB is decimal (`1 TB = 1,000,000,000,000 bytes`), matching storage billing
units. Users who need more must request an individual exception with an
expected size and retention period. Record an approved exception under
`storage_audit.users` in `src/irom_tpu_tools/queue/resources.yaml`; do not raise
the default for everyone.

The timer publishes its latest JSON report to:

```text
gs://prpl-tpu-queue-us-central2-944301850228/tpu-job-queue/admin/storage-audit/latest.json
```

Admins normally read the report as a table from any authenticated machine with
queue-bucket access:

```bash
tpu admin storage
```

To scan immediately, publish a new report, and optionally retrieve raw JSON:

```bash
tpu admin storage --refresh
tpu admin storage --json
```

Install and enable the daily timer on the scheduler VM after installing the
current package from this repository:

```bash
install -d ~/.config/systemd/user
install -m 0644 contrib/systemd/irom-tpu-storage-audit.service \
  ~/.config/systemd/user/
install -m 0644 contrib/systemd/irom-tpu-storage-audit.timer \
  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now irom-tpu-storage-audit.timer
systemctl --user start irom-tpu-storage-audit.service
```

The scheduled scan runs daily at 03:15 UTC with up to 15 minutes of randomized
delay and catches up after VM downtime. Verify both the schedule and the last
run:

```bash
systemctl --user list-timers irom-tpu-storage-audit.timer
systemctl --user status irom-tpu-storage-audit.service
tpu admin storage
```

To start, you only need the regions you actually use (for example just `us-east1` for v6). Add the others when needed.

If a user prefers to use their own existing GCS bucket, that works too, but you must grant the worker service account access to it, and it should be in the same region as the TPU:

```bash
gcloud storage buckets add-iam-policy-binding gs://their-own-bucket \
  --member="serviceAccount:${WORKER_SA}" --role=roles/storage.objectViewer   # objectAdmin if they write to it
```

## Incident record: scheduler shutdown and storage cost review (2026-08-17 to 2026-08-19)

### Impact and scheduler root cause

On 2026-08-17, queued jobs remained `PENDING` and cancellation requests did not
complete because the central scheduler VM was not running. No scheduler code
change preceded the outage.

Compute Engine records show that `prpl-tpu-scheduler` was stopped at
2026-08-17 00:44:24 UTC (08:44:24 China Standard Time). The stop operations were
issued by Google control-plane identities
`gce-control-project-clh-flows@prod.google.com` and
`cloud-cluster-manager@prod.google.com`. Inside the guest, systemd received a
virtual power-button event and performed a clean shutdown; that event was the
mechanism used by the GCE stop operation, not evidence of a person pressing a
physical button or logging into the VM.

The stop coincided with the Cloud Billing account's $300 Free Trial credit
reaching its end on 2026-08-17. The evidence is consistent with the billing
lifecycle control plane stopping project resources after the trial became
inactive. `theprplgroup@gmail.com` manually started the VM at
2026-08-17 19:59:25 UTC (2026-08-18 03:59:25 China Standard Time), after which
the scheduler resumed processing and the stuck cancellation completed. Billing
is currently enabled for the project.

### Storage cost findings

TRC-granted Cloud TPU capacity is free, but supporting GCP services such as
Cloud Storage and the scheduler VM are billable. The storage investigation found:

```text
Checkpoint data written:              approximately 18.5 TB
Cloud Storage DeleteObject requests:  approximately 22,391
Latest live checkpoint storage:       approximately 1.74 TiB
Latest soft-deleted checkpoints:      approximately 4.58 TiB
Estimated historical soft-delete storage cost: approximately $72.70
```

The dollar figure is an estimate from Cloud Monitoring byte-seconds and public
regional Standard Storage pricing, not a replacement for the Cloud Billing SKU
report.

The deletion volume was programmatic rather than evidence of users manually
deleting thousands of objects. The inspected job specifications used
`--overwrite` and saved every 2,000 steps. The training code removes the previous
checkpoint directory when starting in overwrite mode, and Orbax is configured
with `max_to_keep=1`, which deletes the previous checkpoint when a new one is
saved. Those operations ran from TPU workers using the shared worker service
account. Cloud Storage Data Access audit logs were not enabled, so historical
individual object deletes cannot be attributed to a specific human identity.

### Mitigation applied on 2026-08-19

Soft delete was disabled on all eight shared `prpl-data-*` and `prpl-ckpt-*`
buckets in `us-east1`, `us-central1`, `us-central2`, and `europe-west4`. All four
`prpl-tpu-queue-*` buckets retain the default seven-day policy. Existing
soft-deleted objects cannot be purged early and will be permanently deleted as
their previously assigned seven-day retention windows expire.

Because shared data and checkpoint deletions are now permanent, admins must
make this policy clear during onboarding and users must keep independent copies
of irreplaceable source data.

## Incident record: scheduler cold-start reconciliation (2026-08-19)

After restarting the scheduler while investigating the v5e Spot request flags, its
cold-start scan launched eight concurrent `gcloud storage` readers on the
two-vCPU scheduler VM. Some reads reached the 15-second timeout. The backend
represented both a missing object and a failed read as `None`, so four old
terminal jobs whose `status.json` reads timed out were incorrectly loaded as new
`PENDING` jobs. The incomplete in-memory job map then caused orphan
reconciliation to delete four legitimate v4 on-demand queued resources.

All eight affected queued resources were still `WAITING_FOR_RESOURCES`; none had
an active TPU VM, so no running training process or user data was stopped. The
four legitimate jobs did lose their Google queue positions. Their job states
were backed up and reset to `PENDING` with attempt 0 so they could be safely
requeued without charging a failed attempt. The four historical jobs were
restored to their original terminal states from their attempt success/failure
markers, and the four incorrectly created waiting resources were deleted.

The scheduler now distinguishes missing GCS objects from read/list failures,
treats an empty prefix as a successful empty listing, and performs no creates,
deletes, cancellations, retries, or status writes after an incomplete scan.
Orphan reconciliation has an additional incomplete-scan guard. Cold-start scan
concurrency is limited to two workers to match the VM's two vCPUs. Regression
tests cover status-read failure, job-list failure, partially loaded queues,
empty prefixes, and the v5e Spot command.

## Scheduler permissions

The scheduler identity needs enough permission to create and delete TPU VMs, attach the worker service account to TPU VMs, and read or write the queue buckets.

The scheduler identity needs:

```text
roles/tpu.admin
roles/iam.serviceAccountUser on tpu-worker
roles/storage.objectAdmin on all queue buckets
roles/storage.objectViewer on all shared data/checkpoint buckets (storage audit)
```

The scheduler should not use personal credentials long term. A dedicated scheduler service account is preferred.

Shared Gmail credentials were only used during initial setup and should not be committed to the repository.

## Checking TPU quota

To see how many TPU quota units each configured zone and region is *allowed* to
run, use the helper script. It reads each quota's current `effectiveLimit` from
the Service Usage consumer-quota API and, if the `tpu` CLI is available, also
prints the queue's current usage per group:

```bash
./contrib/prpl-quota.sh
# or for another project:
PROJECT=other-project ./contrib/prpl-quota.sh
```

The command requires an authenticated `gcloud` installation with its alpha
commands available. It intentionally uses `gcloud alpha services quota list`
instead of the newer Cloud Quotas API because the latter requires an additional
`cloudquotas.quotas.get` permission that ordinary project users may not have.

The live values last verified on **2026-08-20** are:

| Queue group | GCP quota metric | Zone | Effective zone / region limit |
|---|---|---|---:|
| `v4` | `tpu-v4s-pod-preemptible` | `us-central2-b` | 64 / 64 |
| `v4od` | `tpu-v4s-pod` | `us-central2-b` | 64 / 64 |
| `v6` | `tpu-v6e-preemptible` | `us-east1-d` | 64 / 64 |
| `v5` | `tpu-v5s-litepod-preemptible` | `us-central1-a` | 64 / 64 |
| `v6eu` | `tpu-v6e-preemptible` | `europe-west4-a` | 64 / 64 |
| `v5eu` | `tpu-v5s-litepod-preemptible` | `europe-west4-b` | 64 / 64 |

Both the zone and region limits apply, so record and check both. Treat this
table as a dated snapshot; the helper script is the source of truth and should
be rerun after any quota request or Google-side quota change. If a value changes,
update this table and the matching `quota_groups.*.total_chips` value in
`src/irom_tpu_tools/queue/resources.yaml` together.

Do not confuse the normal v5e Spot metric above with the separate serving
metrics. The current default per-zone limits for
`tpu-v5s-litepod-serving-preemptible` and `tpu-v5s-litepod-serving` are both 4.
The `Limit: 4` error names the serving-preemptible metric, not the configured
64-chip v5 training metric. Live tests on 2026-08-19 showed that this distinction
cannot currently be bypassed with scheduler flags: Google's documented GA
`... queued-resources create --spot`, the legacy v2alpha1 Spot payload, and a
best-effort/preemptible payload were all enforced against the 4-chip serving
quota for `v5litepod-8`. A `v5litepod-4` probe was separately rejected because
this project is not permitted to submit that accelerator type. Therefore an
8-chip v5e queued resource requires Google to raise
`TPUV5sPreemptibleLitepodServingPerProjectPerZoneForTPUAPI` from 4 to at least 8;
the 64-chip metric displayed for `v5` does not make the current request
schedulable. Until Google corrects the quota assignment/routing, use a permitted
v4 or v6 resource instead of leaving v5 jobs pending.

The scheduler still uses the documented GA command. The alpha command with only
`--spot` defaulted the node provisioning model to `STANDARD` and was rejected as
incompatible, while adding `--provisioning-model SPOT` did not avoid the serving
quota. Keep the live probe result and the named quota violation as the source of
truth when the configured quota table and API enforcement disagree.

Quota is a ceiling, not availability. GCP does not expose real-time spot
capacity through any API. Whether a specific size can be provisioned right now
is only discoverable by submitting a job and watching `tpu status` move from
`WAITING_FOR_RESOURCES` to `ACTIVE`.

## Current systemd service

The scheduler is managed as a systemd user service:

```text
irom-tpu-scheduler.service
```

The service file is installed at:

```text
~/.config/systemd/user/irom-tpu-scheduler.service
```

The service runs:

```bash
tpu scheduler --scan-interval 30 --log-file ~/.local/state/irom-tpu-tools/scheduler.log
```

The service sets:

```text
TPU_QUEUE_USER=robin
GOOGLE_CLOUD_PROJECT=tpu-tsilver-20260619
```

## Checking scheduler health

SSH into the scheduler VM first:

```bash
gcloud compute ssh theprplgroup@prpl-tpu-scheduler \
  --project=tpu-tsilver-20260619 \
  --zone=us-central1-a
```

Then check the service:

```bash
systemctl --user status irom-tpu-scheduler.service
```

Expected healthy state:

```text
Active: active (running)
```

To follow the systemd logs:

```bash
journalctl --user -u irom-tpu-scheduler.service -f
```

To inspect the scheduler log file:

```bash
tail -n 100 ~/.local/state/irom-tpu-tools/scheduler.log
```

To follow the scheduler log live:

```bash
tail -f ~/.local/state/irom-tpu-tools/scheduler.log
```

To restart the scheduler:

```bash
systemctl --user restart irom-tpu-scheduler.service
```

To stop the scheduler:

```bash
systemctl --user stop irom-tpu-scheduler.service
```

To start the scheduler again:

```bash
systemctl --user start irom-tpu-scheduler.service
```

To enable the scheduler after reboot:

```bash
systemctl --user enable irom-tpu-scheduler.service
```

If the service must continue running after logout, enable linger for the scheduler VM user:

```bash
sudo loginctl enable-linger "$USER"
```

## SSH key notes

Do not commit SSH private keys, gcloud credentials, Gmail credentials, service account keys, local token files, or any other secrets to this repository.

If `gcloud compute ssh` prompts to generate an SSH key, it is safe to proceed. The generated private key should stay on the local machine or Cloud Shell environment and should never be added to git.

If SSH access fails, first confirm the project and instance:

```bash
gcloud config set project tpu-tsilver-20260619

gcloud compute instances describe prpl-tpu-scheduler \
  --project=tpu-tsilver-20260619 \
  --zone=us-central1-a
```

If the VM exists but SSH still fails, check whether the active Google account has permission to access the project and update SSH metadata. If needed, ask the project admin to grant access or add the public SSH key to the VM or project metadata.

## VM preservation note

Do not delete `prpl-tpu-scheduler` unless the scheduler has been migrated elsewhere.

Stopping this VM will pause queue processing. Existing TPU jobs may keep running, but new queued jobs will not be picked up until the scheduler is running again.

If the scheduler is migrated to another VM or a Princeton lab server, stop the old scheduler before starting the new one.

There should be exactly one active scheduler for the PRPL TPU queue.

## Cloud Shell note

Cloud Shell is useful for setup and debugging, but it should not be used as the long running scheduler host.

The current scheduler VM is the temporary cloud hosted solution. In the future, the scheduler can be migrated to a persistent Princeton lab server or another managed VM.

## Admin safety checklist

Before committing admin documentation or configuration changes, verify that the repo does not include:

```text
Gmail passwords
verification codes
service account key files
SSH private keys
gcloud token files
local credentials
any other secrets
```
