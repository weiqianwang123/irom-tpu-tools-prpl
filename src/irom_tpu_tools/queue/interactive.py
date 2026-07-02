from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import subprocess
import sys

from ..ssh import SSHOptions, gcloud_tpu_ssh_stream, run_streaming
from .config import QueueConfig
from .types import InteractiveTPUConfig

SSH_KEY_REQUEST_PREFIX = "ssh-key-requests"
_SSH_USER_RE = re.compile(r"^[a-z_][a-z0-9._-]{0,31}$")


def key_request_url(config: QueueConfig, user: str) -> str:
    return f"{config.primary_bucket}/{SSH_KEY_REQUEST_PREFIX}/{user}.pub"


def ssh_key_identity(line: str) -> tuple[str, str] | None:
    """Return (user, key_blob) for a metadata ssh-keys line, or None if unparseable."""
    line = line.strip()
    if not line or ":" not in line:
        return None
    user, rest = line.split(":", 1)
    parts = rest.strip().split()
    if len(parts) < 2 or not user.strip():
        return None
    return user.strip(), parts[1]


def ssh_key_fingerprint(blob: str) -> str:
    try:
        digest = hashlib.sha256(base64.b64decode(blob)).digest()
    except ValueError:
        return "(unparseable)"
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def normalized_key_entry(user: str, text: str) -> str:
    """Validate a public key file's content and return a 'user:type blob user' line.

    Any embedded 'user:' prefix must match `user`, and the key comment is
    replaced with the username, so a request file cannot smuggle a different
    login name onto the shared TPUs.
    """
    if not _SSH_USER_RE.match(user):
        raise ValueError(f"invalid username: {user!r}")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if len(lines) != 1:
        raise ValueError("key file must contain exactly one public key line")
    line = lines[0]
    if ":" in line.split(None, 1)[0]:
        prefix, line = line.split(":", 1)
        if prefix.strip() != user:
            raise ValueError(
                f"embedded username {prefix.strip()!r} does not match {user!r}"
            )
    parts = line.strip().split()
    if len(parts) < 2 or not parts[0].startswith(("ssh-", "ecdsa-")):
        raise ValueError("unrecognized public key format")
    try:
        base64.b64decode(parts[1], validate=True)
    except ValueError as exc:
        raise ValueError("public key blob is not valid base64") from exc
    return f"{user}:{parts[0]} {parts[1]} {user}"


def _permission_hint(tpu: InteractiveTPUConfig) -> str:
    return (
        "\n[hint] Interactive TPU access is connect-only. `roles/tpu.viewer` (or "
        f"a custom role with `tpu.nodes.get` for zone `{tpu.zone}`) provides read "
        f"access on project `{tpu.project}`, but the default gcloud SSH path also "
        "attempts `tpu.nodes.update` when the exact local SSH key is absent from "
        "project or node metadata. Run `tpu interactive add-key` to request key "
        "provisioning through the queue, or ask an admin to run `tpu admin "
        "ssh-keys`, plus the required OS Login/IAP permissions; otherwise a "
        "narrow role must also include `tpu.nodes.update`. No TPU Admin role is "
        "required."
    )


def _with_access_hint(rc: int, tpu: InteractiveTPUConfig) -> int:
    if rc != 0:
        print(_permission_hint(tpu), file=sys.stderr)
    return rc


def resolve_interactive_tpu(
    config: QueueConfig, name_or_alias: str
) -> InteractiveTPUConfig:
    matches = []
    for tpu in config.interactive_tpus.values():
        if name_or_alias == tpu.name or name_or_alias in tpu.aliases:
            matches.append(tpu)
    if not matches:
        available = ", ".join(sorted(config.interactive_tpus)) or "(none configured)"
        raise SystemExit(
            f"Interactive TPU is not allowlisted: {name_or_alias}\n"
            f"Configured shared TPUs: {available}"
        )
    if len(matches) > 1:
        names = ", ".join(t.name for t in matches)
        raise SystemExit(f"Interactive TPU alias is ambiguous: {name_or_alias} ({names})")
    return matches[0]


def describe_interactive_tpu(tpu: InteractiveTPUConfig) -> dict:
    proc = subprocess.run(
        [
            "gcloud",
            "alpha",
            "compute",
            "tpus",
            "tpu-vm",
            "describe",
            tpu.name,
            "--project",
            tpu.project,
            "--zone",
            tpu.zone,
            "--format",
            "json(name,state,health,acceleratorType,networkEndpoints)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        error = (proc.stderr or "").strip()
        if "tpu.nodes.get" in error or "PERMISSION_DENIED" in error:
            error = f"{error}{_permission_hint(tpu)}"
        return {"state": "UNKNOWN", "health": "-", "error": error}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"state": "UNKNOWN", "health": "-", "error": "invalid gcloud JSON"}


def list_rows(config: QueueConfig, *, live: bool = False) -> list[list[str]]:
    rows: list[list[str]] = []
    for tpu in sorted(config.interactive_tpus.values(), key=lambda x: x.name):
        state = "-"
        health = "-"
        accel = tpu.version
        if live:
            data = describe_interactive_tpu(tpu)
            state = str(data.get("state") or "UNKNOWN")
            health = str(data.get("health") or "-")
            accel = str(data.get("acceleratorType") or accel).rsplit("/", 1)[-1]
        rows.append(
            [
                tpu.name,
                ",".join(tpu.aliases) or "-",
                tpu.zone,
                str(tpu.workers),
                accel,
                state,
                health,
                tpu.description or "-",
            ]
        )
    return rows


def ssh_shell(tpu: InteractiveTPUConfig, *, worker: int = 0) -> int:
    return _with_access_hint(
        gcloud_tpu_ssh_stream(
            tpu_name=tpu.name,
            project=tpu.project,
            zone=tpu.zone,
            worker=str(worker),
            ssh=SSHOptions(),
            allocate_tty=True,
        ),
        tpu,
    )


def run_command(
    tpu: InteractiveTPUConfig, *, command: str, worker: int | str = 0
) -> int:
    return _with_access_hint(
        gcloud_tpu_ssh_stream(
            tpu_name=tpu.name,
            project=tpu.project,
            zone=tpu.zone,
            worker=str(worker),
            command=command,
            ssh=SSHOptions(),
            allocate_tty=False,
        ),
        tpu,
    )


def attach_tmux(
    tpu: InteractiveTPUConfig, *, session: str = "tpu", worker: int = 0
) -> int:
    return _with_access_hint(
        gcloud_tpu_ssh_stream(
            tpu_name=tpu.name,
            project=tpu.project,
            zone=tpu.zone,
            worker=str(worker),
            command=(
                "command -v tmux >/dev/null || "
                "(sudo apt-get update && sudo apt-get install -y tmux); "
                f"exec tmux new -As {shlex.quote(session)}"
            ),
            ssh=SSHOptions(),
            allocate_tty=True,
            no_shell_rc=True,
        ),
        tpu,
    )


def tmux_command(
    tpu: InteractiveTPUConfig,
    *,
    command: str,
    session: str = "tpu",
    worker: int | str = "all",
) -> int:
    session_q = shlex.quote(session)
    line = f"set -eo pipefail; export PYTHONUNBUFFERED=1; {command} 2>&1 | tee -a $LOG"
    remote = (
        "command -v tmux >/dev/null || "
        "(sudo apt-get update && sudo apt-get install -y tmux);"
        "mkdir -p $HOME/interactive_logs;"
        "TS=$(date +%Y%m%d-%H%M%S);"
        f"LOG=$HOME/interactive_logs/{session}_$TS.log;"
        f"if ! tmux has-session -t {session_q} 2>/dev/null; then "
        f"tmux new-session -ds {session_q} -e SSH_AUTH_SOCK=$SSH_AUTH_SOCK -e LOG=$LOG; "
        "else "
        f"tmux set-environment -t {session_q} LOG $LOG; "
        "fi;"
        f"tmux send-keys -t {session_q} {shlex.quote(line)} Enter;"
        "echo LOG=$LOG"
    )
    return _with_access_hint(
        gcloud_tpu_ssh_stream(
            tpu_name=tpu.name,
            project=tpu.project,
            zone=tpu.zone,
            worker=str(worker),
            command=remote,
            ssh=SSHOptions(),
        ),
        tpu,
    )


def tail_output(
    tpu: InteractiveTPUConfig,
    *,
    session: str = "tpu",
    worker: int = 0,
    lines: int = 200,
    follow: bool = False,
) -> int:
    tail_args = f"-n {int(lines)}" + (" -f" if follow else "")
    session_q = shlex.quote(session)
    remote = (
        f"SESSION={session_q}; "
        'LOG_FILE="$(tmux show-environment -t "$SESSION" LOG 2>/dev/null | sed -n "s/^LOG=//p")"; '
        f'[ -n "$LOG_FILE" ] && [ -f "$LOG_FILE" ] && {{ tail {tail_args} "$LOG_FILE"; exit $?; }}; '
        "LOG_DIR=$HOME/interactive_logs; "
        'if [ -d "$LOG_DIR" ]; then '
        'F="$(ls -1t "$LOG_DIR" | head -n1 || true)"; '
        f'[ -n "$F" ] && {{ tail {tail_args} "$LOG_DIR/$F"; exit $?; }}; '
        "fi; "
        'echo "[ERROR] No interactive log files found"; exit 1'
    )
    return _with_access_hint(
        gcloud_tpu_ssh_stream(
            tpu_name=tpu.name,
            project=tpu.project,
            zone=tpu.zone,
            worker=str(worker),
            command=remote,
            ssh=SSHOptions(),
        ),
        tpu,
    )


def tmux_ls(tpu: InteractiveTPUConfig, *, worker: int = 0) -> int:
    return run_command(tpu, command="tmux ls 2>/dev/null || true", worker=worker)


def scp_to(
    tpu: InteractiveTPUConfig,
    *,
    local_path: str,
    remote_path: str,
    worker: int = 0,
    recurse: bool = False,
) -> int:
    args = [
        "gcloud",
        "alpha",
        "compute",
        "tpus",
        "tpu-vm",
        "scp",
    ]
    if recurse:
        args.append("--recurse")
    args.extend(
        [
            local_path,
            f"{tpu.name}:{remote_path}",
            "--project",
            tpu.project,
            "--zone",
            tpu.zone,
            "--worker",
            str(worker),
        ]
    )
    return _with_access_hint(run_streaming(args), tpu)


def scp_from(
    tpu: InteractiveTPUConfig,
    *,
    remote_path: str,
    local_path: str,
    worker: int = 0,
    recurse: bool = False,
) -> int:
    args = [
        "gcloud",
        "alpha",
        "compute",
        "tpus",
        "tpu-vm",
        "scp",
    ]
    if recurse:
        args.append("--recurse")
    args.extend(
        [
            f"{tpu.name}:{remote_path}",
            local_path,
            "--project",
            tpu.project,
            "--zone",
            tpu.zone,
            "--worker",
            str(worker),
        ]
    )
    return _with_access_hint(run_streaming(args), tpu)
