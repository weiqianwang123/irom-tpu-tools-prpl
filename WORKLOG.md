# Worklog

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
