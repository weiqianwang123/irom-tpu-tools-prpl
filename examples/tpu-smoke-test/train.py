"""Minimal TPU smoke test for the PRPL TPU queue.

This is NOT a real model. It exists to prove, end to end, that:
  - the tpu queue packaged this repo and shipped it to the TPU workers,
  - the setup command installed JAX with TPU support,
  - JAX can see the TPU chips,
  - a trivial computation runs on the TPU and produces a correct result,
  - (optionally) results can be written to a GCS output path.

Run locally (CPU, no TPU) just to check the script is valid:
    python train.py --steps 5

On the TPU queue it is launched by the packaged startup script, which sets
TPU_QUEUE_JOB_ID / TPU_QUEUE_ATTEMPT and runs it on every worker.
"""

from __future__ import annotations

import argparse
import os
import platform
import socket
import sys
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PRPL TPU smoke test")
    parser.add_argument(
        "--steps",
        type=int,
        default=20,
        help="How many trivial matmul steps to run.",
    )
    parser.add_argument(
        "--matrix-size",
        type=int,
        default=1024,
        help="Side length of the square matrices used in the matmul.",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR"),
        help="Optional gs:// path (or local dir) to write a result file to.",
    )
    return parser.parse_args()


def banner() -> None:
    print("=" * 60, flush=True)
    print("PRPL TPU smoke test", flush=True)
    print(f"  hostname:  {socket.gethostname()}", flush=True)
    print(f"  python:    {platform.python_version()}", flush=True)
    print(f"  job id:    {os.environ.get('TPU_QUEUE_JOB_ID', '(not set)')}", flush=True)
    print(f"  attempt:   {os.environ.get('TPU_QUEUE_ATTEMPT', '(not set)')}", flush=True)
    print(f"  worker id: {os.environ.get('TPU_WORKER_ID', '(not set)')}", flush=True)
    print("=" * 60, flush=True)


def main() -> int:
    args = parse_args()
    banner()

    try:
        import jax
        import jax.numpy as jnp
    except ImportError:
        print(
            "ERROR: could not import jax. On the TPU queue, pass a setup command "
            'that installs it, e.g. --setup-cmd "pip install \'jax[tpu]\' -f '
            'https://storage.googleapis.com/jax-releases/libtpu_releases.html".',
            flush=True,
        )
        return 1

    print(f"jax version: {jax.__version__}", flush=True)
    devices = jax.devices()
    print(f"jax.device_count(): {jax.device_count()}", flush=True)
    print(f"jax.devices(): {devices}", flush=True)

    platform_name = devices[0].platform if devices else "unknown"
    print(f"backend platform: {platform_name}", flush=True)
    if platform_name == "tpu":
        print(f"SUCCESS: JAX sees {len(devices)} TPU device(s).", flush=True)
    else:
        print(
            f"NOTE: running on '{platform_name}', not tpu. That is fine for a "
            "local check but means no TPU was used.",
            flush=True,
        )

    # A trivial "training" loop: repeated matmuls whose result we reduce to one
    # number. This exercises the accelerator and gives us something to print.
    key = jax.random.PRNGKey(0)
    a = jax.random.normal(key, (args.matrix_size, args.matrix_size))
    b = jax.random.normal(key, (args.matrix_size, args.matrix_size))

    @jax.jit
    def step(x, y):
        return jnp.tanh(x @ y).mean()

    start = time.time()
    value = 0.0
    for i in range(args.steps):
        result = step(a, b)
        value = float(result)  # blocks until the device finishes this step
        if i == 0 or (i + 1) % 5 == 0 or i == args.steps - 1:
            print(f"  step {i + 1}/{args.steps}: mean(tanh(A@B)) = {value:.6f}", flush=True)
    elapsed = time.time() - start
    print(f"Completed {args.steps} steps in {elapsed:.2f}s.", flush=True)

    if args.output_dir:
        _write_result(args, devices, value, elapsed)

    print("Smoke test finished OK.", flush=True)
    return 0


def _write_result(args, devices, value: float, elapsed: float) -> None:
    """Write a small result file, to gs:// via gcloud or to a local dir."""
    worker = os.environ.get("TPU_WORKER_ID", "0")
    job_id = os.environ.get("TPU_QUEUE_JOB_ID", "local")
    contents = (
        f"job_id={job_id}\n"
        f"worker={worker}\n"
        f"devices={devices}\n"
        f"steps={args.steps}\n"
        f"final_value={value:.6f}\n"
        f"elapsed_seconds={elapsed:.2f}\n"
    )
    out = args.output_dir.rstrip("/") + f"/result-worker-{worker}.txt"

    if out.startswith("gs://"):
        import subprocess

        proc = subprocess.run(
            ["gcloud", "storage", "cp", "-", out],
            input=contents,
            text=True,
            capture_output=True,
        )
        if proc.returncode == 0:
            print(f"Wrote result to {out}", flush=True)
        else:
            print(f"Failed to write {out}: {proc.stderr.strip()}", flush=True)
    else:
        os.makedirs(args.output_dir, exist_ok=True)
        with open(out, "w") as handle:
            handle.write(contents)
        print(f"Wrote result to {out}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
