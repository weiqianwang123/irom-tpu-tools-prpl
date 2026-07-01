# Worklog

## 2026-06-30 - Exclusive Focused Scheduling

- Changed `tpu scheduler --once --focus-job JOB` to schedule only `JOB` when it is pending. Previously, the focused pass walked and attempted every older pending job before the target, which could create unrelated TPU requests while an operator was monitoring a quota-blocked job.
- Global unfocused scheduling retains priority and submission-time ordering.
- Added a regression test that leaves the older job pending and provisions only the focused job.

## 2026-06-26 - Queued TPU Toolbox Branch

Goal: migrate `irom-tpu-tools` to a queue-backed TPU workflow on branch
`codex/queued-tpu-toolbox`, removing local watcher state and moving TPU
Admin operations behind central scheduler/admin commands.

Plan:
- Add GCS-backed queue job specs, status, logs, sentinels, and scheduler state.
- Keep normal users on queue submission/cancel/log commands that only need queue
  bucket access.
- Group direct TPU Admin operations under scheduler/admin paths.
- Validate with local dry-run backend and unit tests; no real TPU jobs launched.

Result:
- Added `src/irom_tpu_tools/queue/` with config, typed job state, GCP and
  dry-run backends, code packaging, startup script generation, scheduler, and
  queue CLI.
- Replaced the top-level `tpu` CLI path with queue submission/status/log/admin
  commands.
- Added default IROM resource/quota config and tests for scheduling,
  preemption requeue, user chip limits, and no local watcher state in startup
  scripts.

Validation:
- `python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m unittest discover -s tests`
- Dry-run submit/scheduler/list/admin QR smoke with
  `/tmp/irom-tpu-queue-smoke`.

No real TPU or GCP queued resource was launched.

Implementation commit: `1b646295e94352a98526a5151d8ef0b25cd7775f`.

## 2026-06-26 - Restricted Shared Interactive TPU Commands

Goal: allow users to use pre-existing shared v4 interactive TPUs without
restoring direct TPU lifecycle commands.

Plan:
- Add an `interactive_tpus` allowlist to queue config.
- Add `tpu interactive` commands for list/info/ssh/run/tmux/attach/output and
  file copy.
- Do not add create/delete/stop/start under `tpu interactive`.
- Validate parsing and allowlist behavior locally; do not launch or mutate TPU
  resources.

Result:
- Added `InteractiveTPUConfig` and `interactive_tpus` parsing with v4-only
  validation.
- Added connect-only `tpu interactive` subcommands:
  `list`, `info`, `ssh`, `run`, `tmux`, `attach`, `output`, `tail`,
  `tmux-ls`, `put`, and `get`.
- Added default allowlist entry `v4-4-01-interactive` with alias
  `v4-interactive`.
- Added tests for allowlist resolution, default config, and rejection of
  lifecycle verbs under `tpu interactive`.

Validation:
- `python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `PYTHONPATH=src python3 -m irom_tpu_tools.cli interactive --help`
- `PYTHONPATH=src python3 -m irom_tpu_tools.cli interactive list`
- Parser smoke for `tpu interactive run v4-interactive -- hostname`

No TPU command was executed against GCP.

## 2026-06-27 - Health-Aware TPU Requeue

Goal: make the queue scheduler requeue jobs when a TPU VM is still reported as
`READY` but health has moved to `UNHEALTHY_MAINTENANCE`, because worker SSH is
unavailable in that state and queue status can otherwise remain stale.

Result:
- Added `TpuVmStatus` with state, health, and health description.
- Extended the GCP and dry-run backends to expose TPU VM health from
  `gcloud alpha compute tpus tpu-vm describe`.
- Changed scheduler active-QR polling to treat
  `TPU_VM_HEALTH_UNHEALTHY_MAINTENANCE` as a retry/preemption signal.
- Added a dry-run scheduler test for `READY` plus `UNHEALTHY_MAINTENANCE`.

Validation:
- `python3 -m py_compile src/irom_tpu_tools/queue/backend.py src/irom_tpu_tools/queue/scheduler.py tests/test_queue_scheduler.py`
- `.venv/bin/python -m unittest tests.test_queue_scheduler`
- `PYTHONPATH=src python3 -m unittest tests.test_queue_scheduler`
- `git diff --check`

## 2026-06-30 - Interactive TPU Launch Parsing And Access Guidance

Agent: `interactive-access-20260630`

Goal: repair the connect-only interactive TPU CLI so documented `run` and
`tmux` options work after the TPU name, preserve nested shell argument
boundaries, and accurately explain the SSH-key permission path without granting
users TPU Admin.

Base revision: `bafaf331bbc610fd599fe23866e2ee4056a48deb` on isolated branch
`codex/interactive-access-20260630`.

Findings and changes:
- `argparse.REMAINDER` consumed `--worker` and `--session` when they followed
  the TPU name. Replaced it with one-or-more command arguments, retaining `--`
  as the launcher/remote-command separator and preserving both option orders.
- Plain string joining discarded nested `bash -lc` quoting. Added a shared
  `shlex.join` helper for queued and interactive commands.
- Single-worker SSH also passed `bash`, `-lc`, and the raw script as separate
  trailing SSH arguments. Because OpenSSH flattens them, shell operators could
  escape into the outer shell. Both streaming and bounded SSH paths now pass
  one safely quoted remote shell command.
- Current gcloud TPU-VM SSH checks project/node `ssh-keys` metadata and calls
  `tpu.nodes.update` when the exact local key is missing. Updated the runtime
  hint and README to prefer admin key pre-provisioning plus viewer/OS Login/IAP
  access, with a narrow update permission only when pre-provisioning is not
  possible. TPU Admin remains unnecessary for interactive users.
- Added focused quoting, parser compatibility, command-flag preservation, and
  permission-hint tests, plus an explicit four-worker tmux example.

Validation:
- Six focused unit tests for command quoting, single-worker SSH wrapping,
  post-name parsing, pre-name compatibility, and permission guidance passed.
- `PYTHONPATH=src python3 -m unittest discover -s tests` passed all 25 tests.
- `PYTHONPATH=src python3 -m compileall -q src tests` passed.
- `ruff check src/irom_tpu_tools/queue/cli.py src/irom_tpu_tools/queue/interactive.py src/irom_tpu_tools/ssh.py tests/test_queue_scheduler.py`
  passed.
- `git diff --check` passed.

No TPU, GCP, scheduler, SSH, tmux, or remote command was launched. No local
long-running process remains.

Implementation commits:
- `a1fb0367ab10762eabfe5d29f61acc34f8d7b02c`
- `4df0f8cf06ebfaf82a6933fc29b12629a678624b`

## 2026-06-30 - Correct v6e Worker Topology

Agent: `gemma3-oxe-b2048-20260630`

Goal: make queue resource metadata match the current four-chip-per-worker v6e
topology observed by multi-host JAX and the TPU VM SSH API.

Findings and changes:
- A live `v6e-64` allocation exposed workers 0 through 15, each with four local
  TPU devices. JAX reported 16 processes and 64 global devices. Existing queue
  metadata incorrectly advertised eight workers.
- Updated all v6e resource entries consistently: `v6-8` has 2 workers,
  `v6-16` has 4, `v6-32` has 8, `v6-64` has 16, and disabled `v6-128` has 32.
  Chip counts, accelerator types, quotas, and launch behavior are unchanged.
- Added a default-config regression test that checks every v6e worker count and
  enforces four chips per worker.

Validation:
- `uv run --with pytest pytest -q`: 26 passed.
- `uvx ruff check tests/test_queue_scheduler.py`: passed.
- `git diff --check`: passed.

## 2026-07-01 - Personal Scheduler Auto-Resume And Failure Classification

Agent: `personal-tpu-autoresume-20260701`

Goal: replace agent-owned one-shot reconciliation loops with one local
single-user scheduler that automatically recovers infrastructure preemptions,
while keeping setup and application errors terminal and actionable.

Base revision: `96170c83631cb247478041c7e5359b65db1d96fc`, containing the
13 unmerged queue/preemption fixes currently installed on this workstation.
The separate command-quoting branch was patch-equivalent to later commits in
this base and required no additional integration commit.

Changes:
- Added continuous `--focus-user` scheduling. Other users' status is refreshed
  for quota accounting, but lifecycle operations, scheduling, cancellation,
  completion, and retries are restricted to the selected user.
- Added a local `flock` singleton guard and a systemd user-service template so
  per-job scheduler loops cannot race the personal scheduler.
- Added structured attempt outcomes for infrastructure preemption, setup
  errors, and application errors, including retryability, phase, worker, and
  exit code. `tpu status` now prints the recovery policy and exact log command.
- Changed new workers to use attempt-scoped success/failure markers. Every
  worker can report a terminal error; infrastructure state is reconciled before
  worker markers so preemption wins over secondary distributed-process exits.
- Versioned new startup attempts so stale legacy root markers cannot terminate
  a replacement attempt, while preserving old-attempt compatibility.
- Verified each downloaded code archive against the immutable SHA-256 checksum
  stored in the submitted job spec before setup or training begins.
- Documented installation, scope, retry policy, workstation availability, and
  log-polling constraints.

Validation:
- `PYTHONPATH=src python3 -m unittest discover -s tests`: 32 passed.
- `PYTHONPATH=src python3 -m compileall -q src tests`: passed.
- `uvx ruff check src tests`: passed.
- `systemd-analyze --user verify contrib/systemd/irom-tpu-scheduler.service`:
  passed.
- Focused-user dry-run scheduler smoke under
  `/tmp/irom-personal-scheduler-smoke-20260701`: passed.
- `git diff --check`: passed.

No new TPU job was submitted and no live TPU resource was modified during code
validation. Live installation and replacement of legacy scheduler loops remain
pending until the validated commit is integrated into `main`.
