# irom-tpu-tools

Queue-backed TPU scheduling for IROM. Normal users submit jobs to a central
GCS queue; a scheduler identity creates/deletes queued resources and TPU VMs.

## How It Works

The tool splits responsibilities across three roles:

- Users: package code, submit job specs, read status/logs, request cancel/retry.
- Scheduler: creates queued resources, handles preemption, retries attempts,
  cleans up TPU VMs and QRs.
- Admins: inspect quotas, all jobs, queue-owned QRs, and delete orphan/idle
  queue resources.

Job lifecycle state lives in GCS:

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
    attempts/attempt-1/setup-ready/worker-0
    attempts/attempt-1/setup-complete
    attempts/attempt-1/succeeded
    attempts/attempt-1/failed
    logs/attempt-1/worker-0.log
```

## Installation

```bash
pipx install --force git+https://github.com/weiqianwang123/irom-tpu-tools-prpl.git
export PATH="$HOME/.local/bin:$PATH"
tpu --help
```

The PRPL lab configuration (project, buckets, zones, quotas) is packaged into
the wheel, so lab members do not need to touch any infrastructure config. They
do need `gcloud` authenticated (`gcloud auth application-default login`) and
their Google account granted read/write on the queue buckets.

Optional custom config:

```bash
export TPU_QUEUE_CONFIG=/path/to/resources.yaml
```

The packaged default config is
`src/irom_tpu_tools/queue/resources.yaml`.

## User Commands

Each action has exactly one command name; there are no alias subcommands.

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

`tpu delete` writes a cancellation sentinel; it works for any user on their
pending or active jobs and does not require TPU Admin. The scheduler performs
the actual TPU VM and queued-resource cleanup, including when it runs in
focused (`--focus-user`) mode for another account. If the sentinel write fails,
the command reports the error instead of claiming success.

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
tpu interactive add-key
tpu interactive attach v4-interactive --session "$USER-debug"
tpu interactive output v4-interactive --session "$USER-debug" --lines 200
tpu interactive output v4-interactive --session "$USER-debug" --follow
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
add it by calling `tpu.nodes.update`, which viewer-only users do not have.
Provision the key first instead:

- Self-service: run `tpu interactive add-key`. It uploads the local
  `~/.ssh/google_compute_engine.pub` to
  `{primary_bucket}/ssh-key-requests/<user>.pub` using only queue-bucket write
  access. The scheduler appends the key to every configured interactive TPU on
  its next sync pass (within about 5 minutes) and then deletes the request.
  The provisioned entry always uses the requesting queue username: an embedded
  `user:` prefix must match the request filename and the key comment is
  replaced with that username, so a request cannot install a key under someone
  else's login. Keys are only appended, never removed, and requests are kept
  for retry when any node cannot be read or updated.
- Admin: run `tpu admin ssh-keys --add USER=PUBKEY_FILE --yes` directly.

Do not grant users TPU Admin merely for interactive access; `tpu interactive`
never creates, deletes, stops, or starts TPU resources.

## Scheduler

The scheduler must run under an identity with TPU Admin permissions. Exactly
one scheduler serves the whole queue: it schedules every user's pending jobs
by priority (0 is highest, default 1) then submit time, with quota groups and
per-user chip limits enforcing fairness. Install it as the included user
service on the scheduler workstation:

```bash
install -Dm644 contrib/systemd/irom-tpu-scheduler.service \
  "$HOME/.config/systemd/user/irom-tpu-scheduler.service"
systemctl --user daemon-reload
systemctl --user enable --now irom-tpu-scheduler.service
systemctl --user status irom-tpu-scheduler.service
tail -n 100 "$HOME/.local/state/irom-tpu-tools/scheduler.log"
```

The unit runs:

```bash
tpu scheduler --scan-interval 30
```

Only one local scheduler can hold the scheduler lock.

`--focus-user=<name>` is an optional narrow mode for debugging or split
deployments: it performs scheduling, completion, and retry operations only for
jobs whose `submitted_by` matches the selected user, while reading other
users' nonterminal statuses for quota accounting. Cancellation sentinels and
interactive SSH key requests are exceptions: even a focused scheduler serves
every user's `tpu delete` and `tpu interactive add-key` requests. In focused
mode, terminal history is loaded at cold start but is not refreshed on every
scan, and global orphan cleanup and terminal-record retention do not run. Do
not run a focused service as the only scheduler when multiple users submit
jobs; their pending jobs would never be scheduled.

Failed queued-resource creates are backed off per job according to
`scheduler.create_failure_backoff_seconds` (300 seconds by default). This keeps
quota exhaustion or a transient API failure from producing a create request on
every scan. A scheduler restart resets this in-memory delay and permits one
immediate create attempt.

A user service survives terminal closure and starts at login. To keep it alive
after logout, an administrator can enable systemd user lingering:

```bash
loginctl enable-linger "$USER"
```

The scheduler still stops whenever the workstation is powered off, asleep, or
disconnected from GCP.

For local validation without GCP:

```bash
tpu --dry-run --base-dir /tmp/irom-tpu-queue scheduler --once \
  --lock-file /tmp/irom-tpu-scheduler-dry-run.lock
```

For intentional administrator diagnosis, stop the personal service before a
focused one-shot and restart it immediately afterward:

```bash
systemctl --user stop irom-tpu-scheduler.service
tpu scheduler --once --focus-job JOB_ID
systemctl --user start irom-tpu-scheduler.service
```

Focused reconciliation still scans authoritative queue state, but it reconciles
and schedules only the named job. It skips scheduling, cancellation,
completion, polling, retention, and orphan cleanup for unrelated jobs; run the
normal scheduler afterward for full global reconciliation and queue ordering.
Do not use a one-shot loop for normal monitoring or run it beside the service.

The scheduler loop:

1. Scans regional queue buckets for jobs.
2. Handles cancel/retry/completion sentinels.
3. Provisions pending interactive SSH key requests (every 5 minutes; also in
   focused mode, like cancellations).
4. Polls queue-owned QRs.
5. Requeues preempted, suspended, missing, or unhealthy infrastructure attempts
   until `max_attempts`.
6. Enforces quota groups and per-user chip limits.
7. Deletes terminal or orphaned queue-owned resources.
8. Writes `scheduler_state.json` for fast CLI listing.

The packaged config sets `admin: null` under `user_limits.users`, which means
jobs submitted as user `admin` are not capped by the per-user chip limit. Global
quota-group limits still apply.

Attempt records distinguish `INFRASTRUCTURE_PREEMPTION`, `SETUP_ERROR`, and
`APPLICATION_ERROR`. Infrastructure failures are retried automatically from the
same immutable job spec. Setup and application errors are terminal: run
`tpu status JOB`, inspect the indicated worker logs, and diagnose the code,
configuration, data, checkpoint, and metrics before requesting `tpu retry` or
submitting a corrected job.

For multi-worker jobs, each worker publishes an attempt-scoped setup-ready
marker and waits for worker 0 to release the setup barrier before entering the
user command. Worker 0 starts the attempt heartbeat before setup, so a slow
package manager or dependency install on one host cannot make faster hosts
enter distributed initialization early. The barrier fails as `SETUP_ERROR`
after 30 minutes and preserves the first worker failure report.

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
tpu admin ssh-keys
tpu admin ssh-keys --add ah4775=./ah4775.pub
tpu admin ssh-keys --yes
```

Cleanup is dry-run by default. It only targets queue-owned resources whose names
start with the configured `qr_prefix`.

`tpu admin ssh-keys` keeps interactive TPU SSH keys provisioned. It reads the
`ssh-keys` metadata of every configured interactive TPU, computes the union of
user keys across nodes plus any `--add USER=PUBKEY_FILE` entries, and reports
which nodes are missing which keys. With `--yes` it appends the missing entries;
it never removes keys, keeps unrecognized entries untouched, and skips nodes it
cannot describe. Run it with `--yes` whenever an interactive TPU is added to
`interactive_tpus` or a new user is onboarded. This prevents the first-connect
failure where gcloud, not finding the user's local key in node metadata, tries
to add it via `tpu.nodes.update` and fails for viewer-only users.

`tpu admin activity` is read-only. It shows live TPU status, any stale local
watcher processes, and worker tmux/python commands via SSH when the TPU is
reachable.

## IAM Model

Normal users need:

- `storage.objects.create/get/list` on queue prefixes.
- Read access to their logs/status.
- For allowlisted shared interactive TPUs: `tpu.nodes.get` on the existing TPU
  nodes, usually via `roles/tpu.viewer`; `tpu.nodes.list` is needed for live
  inventory/listing. They also need SSH/IAP/OS Login access. An admin should
  pre-provision the exact gcloud SSH key entry with
  `tpu admin ssh-keys --add USER=PUBKEY_FILE --yes`; otherwise the default
  gcloud connection path additionally needs `tpu.nodes.update` to add it.
- No TPU Admin role. Deleting a pending or active job goes through
  `tpu delete`, which only writes a queue sentinel; the scheduler identity
  performs the TPU VM and queued-resource deletion.

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
