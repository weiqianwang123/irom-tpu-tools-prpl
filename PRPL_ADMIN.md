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

  gcloud storage buckets create "$data" --project="$PROJECT" --location="$region"
  gcloud storage buckets create "$ckpt" --project="$PROJECT" --location="$region"

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

To start, you only need the regions you actually use (for example just `us-east1` for v6). Add the others when needed.

If a user prefers to use their own existing GCS bucket, that works too, but you must grant the worker service account access to it, and it should be in the same region as the TPU:

```bash
gcloud storage buckets add-iam-policy-binding gs://their-own-bucket \
  --member="serviceAccount:${WORKER_SA}" --role=roles/storage.objectViewer   # objectAdmin if they write to it
```

## Scheduler permissions

The scheduler identity needs enough permission to create and delete TPU VMs, attach the worker service account to TPU VMs, and read or write the queue buckets.

The scheduler identity needs:

```text
roles/tpu.admin
roles/iam.serviceAccountUser on tpu-worker
roles/storage.objectAdmin on all queue buckets
```

The scheduler should not use personal credentials long term. A dedicated scheduler service account is preferred.

Shared Gmail credentials were only used during initial setup and should not be committed to the repository.

## Checking TPU quota

To see how many TPU chips each region is *allowed* to run, use the helper
script (it reads the Cloud Quotas API and, if the `tpu` CLI is available, also
prints the queue's current usage per group):

```bash
./contrib/prpl-quota.sh
# or for another project:
PROJECT=other-project ./contrib/prpl-quota.sh
```

Reading quota may require `roles/cloudquotas.viewer` (or `serviceusage.quotas.get`);
if it errors, run it with an admin account. The current PRPL quotas are 64 spot
chips each for v6e (us-east1-d and europe-west4-a), v5e-litepod (us-central1-a
and europe-west4-b), and v4 (us-central2-b), plus 64 on-demand v4 in
us-central2-b.

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
