# TPU smoke test

A tiny, self-contained repo you can submit to the PRPL TPU queue to confirm the
whole pipeline works: packaging, provisioning, setup, JAX seeing the TPU, and a
trivial computation running on it. It trains nothing real.

This directory is its own git repository so that `tpu create --code-dir` bundles
only these files, not the whole `irom-tpu-tools` repo.

## First-time setup

```bash
cd examples/tpu-smoke-test
git init && git add -A && git commit -m "smoke test"
```

## Check it locally (no TPU)

```bash
python train.py --steps 5
```

It will report the backend as `cpu` locally, which is expected.

## Submit to the TPU queue

The `--setup-cmd` installs JAX with TPU support on each worker; the run command
executes `train.py`.

```bash
tpu create v6 -n 8 --user "$USER" --name smoke-jax \
  --code-dir "$PWD" \
  --setup-cmd "pip install 'jax[tpu]' -f https://storage.googleapis.com/jax-releases/libtpu_releases.html" \
  -- python train.py --steps 20
```

Watch it:

```bash
tpu status smoke-jax
tpu logs smoke-jax --lines 200
```

A healthy run prints the TPU device list, then a few matmul steps, then
`Smoke test finished OK.` and the job reaches `SUCCEEDED`.

## Optional: write a result to GCS

Point `--output-dir` at a bucket the worker service account can write to. Do not
use the queue buckets for this; use a scratch/output bucket.

```bash
tpu create v6 -n 8 --user "$USER" --name smoke-jax \
  --code-dir "$PWD" \
  --setup-cmd "pip install 'jax[tpu]' -f https://storage.googleapis.com/jax-releases/libtpu_releases.html" \
  --env OUTPUT_DIR=gs://YOUR-SCRATCH-BUCKET/smoke-test \
  -- python train.py --steps 20
```

Each worker writes `result-worker-<id>.txt` under that path.
