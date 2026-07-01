# irom-tpu-tools

Queue-backed TPU scheduling for IROM. This branch intentionally removes the
old local watcher workflow from the default CLI. Normal users submit jobs to a
central GCS queue; a central scheduler service account creates/deletes queued
resources and TPU VMs.

## Why This Branch Exists

The old workflow let every user run TPU Admin operations from their own shell.
That made IAM broad and cleanup decentralized. The new split is:

- Users: package code, submit job specs, read status/logs, request cancel/retry.
- Scheduler: creates queued resources, handles preemption, retries attempts,
  cleans up TPU VMs and QRs.
- Admins: inspect quotas, all jobs, queue-owned QRs, and delete orphan/idle
  queue resources.

Normal job lifecycle state no longer lives in `~/.tpu-jobs`. It lives in GCS:

```text
gs://.../tpu-job-queue/
  scheduler_state.json
  jobs/<job_id>/
    spec.json
    status.json
    code.tar.gz
    canceled
    retry
    running
    attempts/attempt-1/claimed
    attempts/attempt-1/heartbeat
    attempts/attempt-1/succeeded
    attempts/attempt-1/failed
    logs/attempt-1/worker-0.log
```

## Installation

```bash
pipx install --force /home/lzha/code/irom-tpu-tools
export PATH="$HOME/.local/bin:$PATH"
tpu --help
```

Optional custom config:

```bash
export TPU_QUEUE_CONFIG=/path/to/resources.yaml
```

The packaged default config is
`src/irom_tpu_tools/queue/resources.yaml`.

## User Commands

Submit a job:

```bash
tpu create v6 -n 32 --name train-openpi \
  --code-dir /home/lzha/code/ego-lap \
  --setup-cmd "uv sync --group tpu && uv pip install -e ." \
  --env WANDB_PROJECT=openpi \
  -- python scripts/train.py --config-name=my_config
```

List and inspect:

```bash
tpu list
tpu list v6
tpu list --jobs v6 --active
tpu list --jobs v6 --all
tpu list --resources v4
tpu list --live v4
tpu status <job_id_or_name>
tpu logs <job_id_or_name> --lines 200
tpu tail <job_id_or_name> --follow
```

Avoid leaving `tpu tail --follow` running as a background monitor. It polls GCS
log objects repeatedly. Use `tpu status` for routine monitoring and fetch logs
when a job changes state or requires diagnosis.

By default, `tpu list [v4|v5|v6]` shows active queued jobs and live TPU VMs
visible to the current account. Canceled, failed, and succeeded job records are
hidden from the default list; use `--all` or `--status FAILED` when you need job
history. Use `--jobs`, `--resources`, or `--live` for a strict single view.
Live TPU status is shown as `STATE/HEALTH`, for example `READY/HEALTHY` or
`READY/UNHEALTHY_MAINTENANCE`, so a ready-but-unhealthy TPU is not mistaken for
usable capacity.

Cancel or retry:

```bash
tpu delete <job_id_or_name>
tpu retry <job_id_or_name>
tpu rerun <job_id_or_name>
```

`tpu delete` writes a cancellation sentinel. The scheduler performs the actual
TPU VM and queued-resource cleanup.

## Shared Interactive TPUs

Shared v4 interactive TPUs are configured under `interactive_tpus` in
`resources.yaml`. Users can connect to and move files on these allowlisted TPUs,
but this command group intentionally has no create/delete/stop/start actions.
Admins create, stop, start, and delete these shared TPUs outside the user
command path.

```bash
tpu interactive list
tpu interactive list --live
tpu interactive info v4-interactive
tpu interactive info v4-16-interactive
tpu interactive ssh v4-interactive --worker 0
tpu interactive ssh v4-16-interactive --worker 0
tpu interactive run v4-interactive -- hostname
tpu interactive run v4-16-interactive --worker all -- hostname
tpu interactive tmux v4-interactive --session "$USER-debug" -- python scratch.py
tpu interactive tmux v4-16-interactive --session "$USER-train" --worker all -- \
  bash -lc 'cd ~/repo && uv run python scripts/train.py --fsdp-devices 4'
tpu interactive attach v4-interactive --session "$USER-debug"
tpu interactive output v4-interactive --session "$USER-debug" --lines 200
tpu interactive put v4-interactive ./local.txt ~/local.txt
tpu interactive get v4-interactive ~/remote.txt ./remote.txt
```

Interactive commands resolve only configured TPU names or aliases. The packaged
default includes `v4-16-01-interactive` plus `v4-4-01-interactive` through
`v4-4-04-interactive`. Useful aliases include `v4-interactive`,
`v4-16-interactive`, `v4-32-interactive`, and `v4-4-interactive-01` through
`v4-4-interactive-04`.

Put `run` and `tmux` launcher options before the `--` separator; options may
appear before or after the TPU name. Everything after `--` belongs to the remote
command, including nested command flags. `tmux` targets all configured workers
by default, but specifying `--worker all` makes multi-host launches explicit.
Use a unique session name per run, and add `~/.ssh/google_compute_engine` to
`ssh-agent` before targeting multiple workers.

Interactive users need read permission on existing TPU nodes plus the
project/IAP/OS Login permissions required to SSH to the TPU VM and perform
normal file I/O on it. The simplest read permission is `roles/tpu.viewer`,
which includes `tpu.nodes.get` and `tpu.nodes.list`. A narrower custom role can
use `tpu.nodes.get` for connect commands and add `tpu.nodes.list` for live
inventory.

Read permission alone does not guarantee that the default gcloud SSH path can
connect for the first time. If the user's exact gcloud SSH public-key entry is
absent from both project and TPU-node `ssh-keys` metadata, gcloud attempts to
add it by calling `tpu.nodes.update`. Prefer having an admin pre-provision that
key so the user remains read-only. If that is not possible, a narrow custom role
must also include `tpu.nodes.update`. Do not grant users TPU Admin merely for
interactive access; `tpu interactive` never creates, deletes, stops, or starts
TPU resources.

## Scheduler

The scheduler must run under an identity with TPU Admin permissions:

```bash
tpu scheduler --scan-interval 30
```

For a personal scheduler on a workstation that must not reconcile other users'
jobs, install the included user service and scope it to the local account:

```bash
install -Dm644 contrib/systemd/irom-tpu-scheduler.service \
  "$HOME/.config/systemd/user/irom-tpu-scheduler.service"
systemctl --user daemon-reload
systemctl --user enable --now irom-tpu-scheduler.service
systemctl --user status irom-tpu-scheduler.service
```

The unit runs:

```bash
tpu scheduler --focus-user="$USER" --scan-interval 30
```

Only one local scheduler can hold the scheduler lock. Stop legacy per-job
`scheduler --once` loops before enabling the service. `--focus-user` reads
other users' statuses for quota accounting but performs lifecycle operations
only for jobs whose `submitted_by` matches the selected user. It does not run
global orphan cleanup or terminal-record retention.

A user service survives terminal closure and starts at login. To keep it alive
after logout, an administrator can enable systemd user lingering:

```bash
loginctl enable-linger "$USER"
```

The scheduler still stops whenever the workstation is powered off, asleep, or
disconnected from GCP.

For local validation without GCP:

```bash
tpu --dry-run --base-dir /tmp/irom-tpu-queue scheduler --once
```

To reconcile one preempted job without waiting on unrelated lifecycle cleanup:

```bash
tpu scheduler --once --focus-job JOB_ID
```

Focused reconciliation still scans authoritative queue state, but it reconciles
and schedules only the named job. It skips scheduling, cancellation,
completion, polling, retention, and orphan cleanup for unrelated jobs; run the
normal scheduler afterward for full global reconciliation and queue ordering.

The scheduler loop:

1. Scans regional queue buckets for jobs.
2. Handles cancel/retry/completion sentinels.
3. Polls queue-owned QRs.
4. Requeues preempted, suspended, missing, or unhealthy infrastructure attempts
   until `max_attempts`.
5. Enforces quota groups and per-user chip limits.
6. Deletes terminal or orphaned queue-owned resources.
7. Writes `scheduler_state.json` for fast CLI listing.

The packaged config sets `admin: null` under `user_limits.users`, which means
jobs submitted as user `admin` are not capped by the per-user chip limit. Global
quota-group limits still apply.

New attempt records distinguish `INFRASTRUCTURE_PREEMPTION`, `SETUP_ERROR`, and
`APPLICATION_ERROR`. Infrastructure failures are retried automatically from the
same immutable job spec. Setup and application errors are terminal: run
`tpu status JOB`, inspect the indicated worker logs, and diagnose the code,
configuration, data, checkpoint, and metrics before requesting `tpu retry` or
submitting a corrected job.

## Admin Commands

These commands are intended for the central admin account:

```bash
tpu admin resources
tpu admin qrs
tpu admin activity
tpu admin activity --version v6
tpu admin activity v6-32-04-lzha v6-32-06-lzha --worker 0
tpu admin activity --no-ssh v6-32-04-lzha
tpu admin cleanup --idle-minutes 30
tpu admin cleanup --idle-minutes 30 --yes
```

Cleanup is dry-run by default. It only targets queue-owned resources whose names
start with the configured `qr_prefix`.

`tpu admin activity` is read-only. It shows live TPU status, matching old local
`tpu watch` processes, and worker tmux/python commands via SSH when the TPU is
reachable. Stop old `tpu watch` processes before deleting resources they own;
otherwise the old watcher may recreate the TPU or queued resource.

## IAM Model

Normal users need:

- `storage.objects.create/get/list` on queue prefixes.
- Read access to their logs/status.
- For allowlisted shared interactive TPUs: `tpu.nodes.get` on the existing TPU
  nodes, usually via `roles/tpu.viewer`; `tpu.nodes.list` is needed for live
  inventory/listing. They also need SSH/IAP/OS Login access. An admin should
  pre-provision the exact gcloud SSH key entry; otherwise the default gcloud
  connection path additionally needs `tpu.nodes.update` to add it.
- No TPU Admin role.

Common built-in read-role grant for an interactive user after the SSH key is
pre-provisioned:

```bash
gcloud projects add-iam-policy-binding mae-irom-lab-guided-data \
  --member=user:ah4775@princeton.edu \
  --role=roles/tpu.viewer
```

For tighter read access, create/bind a custom role with `tpu.nodes.get` and
optionally `tpu.nodes.list`, then restrict the binding with an IAM condition for
`us-central2-b` TPU node resource names if desired. If key pre-provisioning is
not available, add `tpu.nodes.update` deliberately instead of granting the
broader TPU Admin role.

Scheduler/admin identity needs:

- TPU Admin on the TPU project.
- Service Account User for TPU worker service accounts.
- Secret Manager access for configured worker secrets.
- Read/write/delete access to queue buckets.

TPU worker service accounts need:

- Read access to queued code archives.
- Write access to job logs/sentinels.
- Dataset/checkpoint bucket permissions required by the training code.
- Secret Manager access for configured secrets, such as `WANDB_API_KEY`.

## Development Validation

```bash
python3 -m compileall src tests
PYTHONPATH=src python3 -m unittest discover -s tests
```
