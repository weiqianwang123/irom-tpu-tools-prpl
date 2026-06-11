from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import os
import signal
import sys
from datetime import datetime
from string import Template
from time import sleep

from .config import TPUEnvConfig
from .jobs import JobConfig
from .tpu import TPUManager


def _ts() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _map_v4_topology(tpu_num: int) -> str:
    mapping = {4: "2x2x1", 8: "2x2x2", 16: "2x2x4", 32: "2x4x4"}
    if tpu_num not in mapping:
        raise SystemExit(f"Error: unsupported TPU_NUM '{tpu_num}' (allowed: 4, 8, 16, 32)")
    return mapping[tpu_num]


@dataclass(frozen=True)
class WatchConfig:
    version: str
    force_run: bool
    tpu_num: int
    branch: str
    extra_args: list[str]
    setup_cmd: str = "uv sync"


def _split_repo(repo: str) -> tuple[str, str]:
    """Split 'owner/name' into (owner, name); return ('', '') if empty/invalid."""
    if "/" not in repo:
        return ("", "")
    owner, _, name = repo.partition("/")
    return (owner.strip(), name.strip())


def _build_setup_script(
    version: str,
    env: TPUEnvConfig,
    setup_cmd: str = "uv sync",
    repo: str = "",
) -> str:
    """Build the remote setup bash script.

    If `repo` is empty (bare TPU), the clone step and $SETUP_CMD execution
    are skipped — only baseline env vars and `uv` install happen. Otherwise,
    `repo` is "owner/name" and the repo is cloned under $HOME if missing.
    """
    bucket_env = {
        "v4": env.tpu_bucket_v4,
        "v5": env.tpu_bucket_v5,
        "v6": env.tpu_bucket_v6,
    }[version]
    gh_owner, gh_repo = _split_repo(repo)

    baseline = r"""set -euo pipefail

echo 'export WANDB_API_KEY="${WANDB_API_KEY}"' >> ~/.zshrc
echo 'export OPENPI_DATA_HOME="${OPENPI_DATA_HOME}"' >> ~/.zshrc
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
curl -LsSf https://astral.sh/uv/install.sh | sh
"""

    repo_block = r"""
echo 'export GH_TOKEN="${GH_TOKEN}"' >> ~/.zshrc
echo 'export GH_OWNER="${GH_OWNER}"' >> ~/.zshrc
echo 'export GH_REPO="${GH_REPO}"' >> ~/.zshrc
source ~/.zshrc
if [ ! -d "${GH_REPO}/.git" ]; then
    git clone --recurse-submodules "https://${GH_TOKEN}@github.com/${GH_OWNER}/${GH_REPO}.git"
    cd ${GH_REPO}
    ${SETUP_CMD}
fi
""" if repo else ""

    tpl = Template(baseline + repo_block)
    return tpl.safe_substitute(
        OPENPI_DATA_HOME=f"{bucket_env}/cache",
        GH_TOKEN=env.gh_token,
        WANDB_API_KEY=env.wandb_api_key,
        GH_REPO=gh_repo,
        GH_OWNER=gh_owner,
        SETUP_CMD=setup_cmd,
    )


def build_setup_cmd(
    version: str,
    env: TPUEnvConfig,
    setup_cmd: str = "uv sync",
    repo: str = "",
) -> str:
    """Build the remote setup command suitable for execution over SSH."""
    setup_script = _build_setup_script(version, env, setup_cmd, repo)
    encoded = base64.b64encode(setup_script.encode()).decode().replace("\n", "")
    return f"bash -lc 'echo {encoded} | base64 -d | bash -l -s'"


def run_setup(
    version: str,
    env: TPUEnvConfig,
    *,
    worker: str | None = "all",
    setup_cmd: str = "uv sync",
    repo: str = "",
) -> int:
    """Run the setup step on the TPU worker(s). Exposed for `tpu v4 setup`."""
    mgr = TPUManager(env)
    remote_cmd = build_setup_cmd(version, env, setup_cmd, repo)
    return mgr.raw(version, cmd=remote_cmd, worker=worker)


def _do_setup_and_training(
    mgr: TPUManager,
    version: str,
    env: TPUEnvConfig,
    *,
    command: str,
    branch: str,
    setup_cmd: str,
    repo: str,
) -> bool:
    """Run setup + optionally launch training. Returns True on success."""
    print(f"{_ts()} - Running setup on workers...")
    remote_cmd = build_setup_cmd(version, env, setup_cmd, repo)
    rc = mgr.raw(version, cmd=remote_cmd, worker="all")
    if rc != 0:
        print(f"{_ts()} - Setup failed (rc={rc}).")
        return False

    if not command:
        print(f"{_ts()} - Setup complete (no training command specified).")
        return True

    print(f"{_ts()} - Starting training...")
    _, gh_repo = _split_repo(repo)
    if gh_repo:
        train_cmd = (
            f"source ~/.zshrc && cd {gh_repo} && "
            f"git fetch origin && git checkout {branch} && git pull origin {branch} && "
            f"{command}"
        )
    else:
        # Bare TPU: run the command from $HOME with no repo fetch/checkout.
        train_cmd = f"source ~/.zshrc && {command}"
    if not mgr.tmux(version, cmd=train_cmd, session="tpu"):
        print(f"{_ts()} - Launch failed/SSH timed out.")
        return False

    print(f"{_ts()} - Training started successfully!")
    return True


def _wait_for_ready(mgr: TPUManager, version: str, *, poll_secs: int = 15) -> bool:
    """Poll until TPU is READY or a terminal/error state. Returns True only on READY.

    Runs indefinitely — the caller's signal handler (SystemExit) will interrupt
    the sleep if the watcher is stopped externally.
    """
    while True:
        try:
            state = mgr.describe(version)
        except Exception as exc:
            print(f"{_ts()} - Describe error while waiting for READY: {exc}")
            sys.stdout.flush()
            sleep(poll_secs)
            continue
        print(f"{_ts()} - TPU state: {state}")
        sys.stdout.flush()
        if state == "READY":
            return True
        if state in {"PREEMPTED", "STOPPED", "NOT_FOUND", "ERROR", "PERMISSION_DENIED"}:
            print(f"{_ts()} - Unexpected state '{state}' while waiting for READY.")
            sys.stdout.flush()
            return False
        sleep(poll_secs)


def watch_loop(job: JobConfig, env: TPUEnvConfig, *, force_run: bool = False) -> None:
    """Background watcher loop: monitor TPU state and recover from preemptions.

    This runs as a daemon — it never returns unless signaled.
    If force_run is True, run setup+training on the first READY encounter even
    if the TPU is already up. Otherwise, assume training is already running and
    only act on preemptions/stops.
    """
    mgr = TPUManager(env).for_tpu(job.name, job.version, env.zones[job.version])

    print(f"{_ts()} - Watcher started for TPU '{job.name}' ({job.version})")
    print(f"{_ts()} - Command: {job.command}")
    print(f"{_ts()} - Branch: {job.branch}")
    sys.stdout.flush()

    def handle_sig(signum, frame):
        print(f"{_ts()} - Watcher caught signal {signum}, exiting.")
        sys.stdout.flush()
        # Ignore further signals on self before broadcasting to the process group,
        # so we don't re-enter this handler from the killpg below.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            # Kill all child processes (e.g. gcloud create/delete still in flight).
            os.killpg(os.getpgid(0), signal.SIGTERM)
        except OSError:
            pass
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    from .jobs import record_preemption, record_running

    # training_launched=False means we need to (re-)run setup+training on next READY.
    # Start False only when --force is requested; otherwise assume training is running.
    training_launched = not force_run

    while True:
        try:
            state = mgr.describe(job.version)
        except Exception as exc:
            print(f"{_ts()} - Describe error: {exc}")
            sys.stdout.flush()
            sleep(mgr.sleep_secs)
            continue

        print(f"{_ts()} - TPU '{job.name}' state: {state}")
        sys.stdout.flush()

        if state == "READY":
            if not training_launched:
                print(f"{_ts()} - TPU READY; running setup and launching command...")
                sys.stdout.flush()
                ok = _do_setup_and_training(
                    mgr, job.version, env,
                    command=job.command, branch=job.branch, setup_cmd=job.setup_cmd, repo=job.repo,
                )
                if ok:
                    training_launched = True
                    record_running(job.name)
                    print(f"{_ts()} - Setup and launch complete.")
                else:
                    print(f"{_ts()} - Setup/launch failed, will retry next cycle.")
                sys.stdout.flush()
            sleep(mgr.sleep_secs)
            continue

        if state in {"PREEMPTED", "STOPPED", "NOT_FOUND"}:
            training_launched = False
            if state == "PREEMPTED":
                record_preemption(job.name)
                print(f"{_ts()} - Preemption recorded.")
            print(f"{_ts()} - Creating/recovering TPU...")
            if state != "NOT_FOUND" and not mgr.delete(job.version):
                print(f"{_ts()} - Delete failed/timed out.")
                sys.stdout.flush()
                sleep(mgr.sleep_secs)
                continue

            topo = job.topology or (_map_v4_topology(job.tpu_num) if job.version == "v4" else None)
            print(f"{_ts()} - Creating TPU...")
            if not mgr.create(job.version, tpu_num=job.tpu_num, topology=topo):
                print(f"{_ts()} - Create failed/timed out, will retry.")
                sys.stdout.flush()
                sleep(mgr.sleep_secs)
                continue

            print(f"{_ts()} - Waiting for TPU to be READY...")
            if not _wait_for_ready(mgr, job.version):
                sleep(mgr.sleep_secs)
                continue

            ok = _do_setup_and_training(
                mgr, job.version, env,
                command=job.command, branch=job.branch, setup_cmd=job.setup_cmd, repo=job.repo,
            )
            if ok:
                training_launched = True
                record_running(job.name)
                print(f"{_ts()} - TPU ready, training launched.")
            else:
                print(f"{_ts()} - Setup/launch failed, will retry next cycle.")
            sys.stdout.flush()

        elif state == "PERMISSION_DENIED":
            print(f"{_ts()} - PERMISSION_DENIED. Check IAM/API enablement.")
            sys.stdout.flush()

        else:
            print(f"{_ts()} - TPU in state '{state}' (not actionable now).")
            sys.stdout.flush()

        sleep(mgr.sleep_secs)


def spawn_watcher(job: JobConfig, env: TPUEnvConfig, *, force_run: bool = False) -> int:
    """Fork a background watcher daemon. Returns the daemon PID.

    Enforces the invariant of one watcher per TPU name: if a watcher is
    already running for this job, it is stopped before the new one is forked.
    """
    from .jobs import is_watcher_running, log_path, save_pid, stop_watcher

    if is_watcher_running(job.name):
        print(f"Stopping existing watcher for '{job.name}'...")
        stop_watcher(job.name)

    log_file = log_path(job.name)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    pid = os.fork()
    if pid > 0:
        # Parent — record daemon PID and return
        save_pid(job.name, pid)
        return pid

    # Child — become a daemon
    os.setsid()

    # Redirect stdout/stderr to log file
    fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)  # stdout
    os.dup2(fd, 2)  # stderr
    os.close(fd)
    # Close stdin
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    try:
        watch_loop(job, env, force_run=force_run)
    except SystemExit:
        pass
    except Exception as exc:
        print(f"{_ts()} - Watcher crashed: {exc}")
    finally:
        os._exit(0)


def watch_and_run(cfg: WatchConfig, env: TPUEnvConfig) -> None:
    """Foreground watch loop used by `tpu watch`.

    This intentionally does not daemonize: it keeps trying to create the TPU
    until capacity becomes available, then runs setup and launches the command.
    """
    if not env.gh_owner or not env.gh_repo_name:
        raise SystemExit("GH_OWNER and GH_REPO_NAME must be set for `tpu watch`.")

    repo = f"{env.gh_owner}/{env.gh_repo_name}"
    mgr = TPUManager(env)
    zone = env.zones[cfg.version]

    print("Starting TPU foreground watch with:")
    print(f"  TPU Name: {env.tpu_name}")
    print(f"  Version: {cfg.version}")
    print(f"  Zone: {zone}")
    print(f"  Project: {env.tpu_project}")
    print(f"  Service Account: {env.service_account_for_zone(zone)}")
    print(f"  Repo: {repo}")
    print(f"  Branch: {cfg.branch}")
    print(f"  Setup cmd: {cfg.setup_cmd}")
    print(f"  TPU Num: {cfg.tpu_num}")
    print(f"  Force run: {cfg.force_run}")
    if cfg.extra_args:
        print(f"  Command: {' '.join(cfg.extra_args)}")
    print()

    def handle_sig(signum, frame):
        print(f"{_ts()} - Caught signal {signum}, exiting.")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    training_launched = False
    while True:
        print(f"{_ts()} - Checking TPU state...")
        try:
            state = mgr.describe(cfg.version)
        except Exception as exc:
            print(f"{_ts()} - Describe error: {exc}")
            sys.stdout.flush()
            sleep(mgr.sleep_secs)
            continue

        print(f"{_ts()} - TPU {env.tpu_name} state: {state}")
        sys.stdout.flush()

        run_setup_and_training = False
        if state in {"NOT_FOUND", "PREEMPTED", "STOPPED"}:
            print(f"{_ts()} - Need to (re)create TPU...")
            if state != "NOT_FOUND" and not mgr.delete(cfg.version):
                print(f"{_ts()} - Delete failed/timed out.")
                sys.stdout.flush()
                sleep(mgr.sleep_secs)
                continue

            print(f"{_ts()} - Creating new TPU...")
            topo = _map_v4_topology(cfg.tpu_num) if cfg.version == "v4" else None
            if not mgr.create(cfg.version, tpu_num=cfg.tpu_num, topology=topo):
                print(f"{_ts()} - Create failed/timed out; will retry.")
                sys.stdout.flush()
                sleep(mgr.sleep_secs)
                continue

            print(f"{_ts()} - Waiting for TPU to be READY...")
            if not _wait_for_ready(mgr, cfg.version):
                sys.stdout.flush()
                sleep(mgr.sleep_secs)
                continue
            run_setup_and_training = True
        elif state == "READY":
            run_setup_and_training = cfg.force_run and not training_launched
            if not run_setup_and_training:
                print(f"{_ts()} - TPU READY; training already launched or force not requested.")
        elif state == "PERMISSION_DENIED":
            print(f"{_ts()} - PERMISSION_DENIED from describe. Check IAM/API enablement.")
        else:
            print(f"{_ts()} - TPU in state: {state} (not actionable now).")

        if run_setup_and_training:
            ok = _do_setup_and_training(
                mgr,
                cfg.version,
                env,
                command=" ".join(cfg.extra_args),
                branch=cfg.branch,
                setup_cmd=cfg.setup_cmd,
                repo=repo,
            )
            if ok:
                training_launched = True
                print(f"{_ts()} - Training launch complete.")
                if cfg.force_run:
                    print(f"{_ts()} - Force run requested; exiting.")
                    return
            else:
                print(f"{_ts()} - Setup/launch failed; will retry.")

        sys.stdout.flush()
        sleep(mgr.sleep_secs)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tpu watch")
    parser.add_argument("version", choices=["v4", "v5", "v6"], help="TPU version to target")
    parser.add_argument("--force", "-f", action="store_true", help="Force setup/training if READY")
    parser.add_argument("--tpu-num", "-n", type=int, default=8, help="TPU chips")
    parser.add_argument("--setup-cmd", "-s", default="uv sync", help="Setup command to run after clone")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser()
    ns, extra = parser.parse_known_args(argv)
    if extra and extra[0] == "--":
        extra = extra[1:]

    branch = "main"
    if extra and not extra[0].startswith("-"):
        branch = extra[0]
        extra = extra[1:]

    if not extra:
        raise SystemExit("No run command provided. Usage: tpu watch <version> [flags] <branch> <run_command...>")

    env = TPUEnvConfig.from_env()
    cfg = WatchConfig(
        version=ns.version,
        force_run=ns.force,
        tpu_num=ns.tpu_num,
        branch=branch,
        extra_args=extra,
        setup_cmd=ns.setup_cmd,
    )
    watch_and_run(cfg, env)
    return 0
