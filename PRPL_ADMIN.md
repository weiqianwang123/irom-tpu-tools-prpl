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
