# PRPL TPU User Guide

This guide explains how PRPL lab members can use the shared TPU queue.

Normal users do not need to SSH into the scheduler VM. The scheduler is already running separately and will pick up submitted jobs from the queue buckets.

## Before you start

Before you can submit jobs, you need access to the project and a few tools installed locally. Do these steps once.

### 1. Get access (ask an admin)

Your Google account must be added to the project before any `tpu` command will work. Ask a PRPL TPU admin(Currently Robin) through gmail(qw3601@princeton.edu) or Slack to:

```text
add your Google account to the project tpu-tsilver-20260619
grant your account read/write on the four queue buckets
```

Give the admin the exact Google account email you will use with `gcloud`. Until this is done, `tpu` commands will fail with a permission (403) error.

### 2. Install the required local tools

You need these on your laptop, workstation, or Cloud Shell:

```text
Python 3.8 or newer (3.8 to 3.13)
git
Google Cloud SDK (the gcloud command)
```

- Google Cloud SDK install guide: https://cloud.google.com/sdk/docs/install
  The `tpu` CLI talks to the queue and to TPUs through `gcloud`, so this is required.
- Python and git are already present on most systems. Check with `python3 --version` and `git --version`.
- Cloud Shell (https://shell.cloud.google.com) already has gcloud, Python, and git preinstalled, so it is the fastest way to start.

Some commands (`tpu list --live` and `tpu interactive`) use gcloud's alpha TPU commands. If gcloud asks to install the `alpha` component, accept it, or run:

```bash
gcloud components install alpha
```

### 3. Sign in to Google Cloud

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project tpu-tsilver-20260619
```

### 4. Verify your access

Confirm your account can reach a queue bucket before you submit anything:

```bash
gcloud storage ls gs://prpl-tpu-queue-us-east1-944301850228/
```

If this succeeds (even if it lists nothing), your access is working. If it fails with a permission error, your account has not been granted bucket access yet. Ask an admin (see step 1).

## Project

The current Google Cloud project is:

```text
tpu-tsilver-20260619
```

Before using the TPU CLI, set the project:

```bash
gcloud config set project tpu-tsilver-20260619
```

Check the active project:

```bash
gcloud config get-value project
```

Expected output:

```text
tpu-tsilver-20260619
```

## Install the TPU CLI

Clone the repo and install the CLI:

```bash
git clone https://github.com/weiqianwang123/irom-tpu-tools-prpl.git
cd irom-tpu-tools-prpl

python3 -m pip install --user pipx
python3 -m pipx ensurepath
export PATH="$HOME/.local/bin:$PATH"

pipx install --force .
```

Check that the CLI is available:

```bash
tpu --help
```

## Check available resources

List current jobs and live TPUs:

```bash
tpu list
```

List configured resources:

```bash
tpu list --resources v4
tpu list --resources v5
tpu list --resources v6
```

## Submit a job

From your training repo, submit a job with `tpu create`.

The code directory you pass to `--code-dir` must be a git repository. `tpu create` bundles only the files that git tracks (plus untracked files not ignored by `.gitignore`), so anything ignored by git is not uploaded. You do not need to commit first, but the directory must be a git repo. Keep large datasets and checkpoints out of this directory and in a separate bucket (see below).

Example:

```bash
tpu create v6 -n 8 --name robin-test \
  --code-dir "$PWD" \
  --setup-cmd "pip install -e ." \
  --env WANDB_PROJECT=robin-tpu-test \
  -- python scripts/train.py
```

Meaning:

```text
v6: use the v6e resource group
-n 8: request 8 TPU chips
--name robin-test: job name
--code-dir "$PWD": upload the current code directory
--setup-cmd "pip install -e .": setup command to run on the TPU VM
-- python scripts/train.py: actual training command
```

Start with a small test job before launching a large run.

## Check job status

```bash
tpu status robin-test
```

List all active jobs:

```bash
tpu list
```

## View logs

```bash
tpu logs robin-test --lines 200
```

## Cancel a job

```bash
tpu delete robin-test
```

Use this if a job is stuck, submitted incorrectly, or no longer needed.

## Complete example: from zero to a running training job

This walks through one full training run end to end. It assumes you finished
"Before you start" (access granted, gcloud installed, signed in) and installed
the CLI.

The example uses v6 (region us-east1). If you use a different TPU version, swap
the region and buckets using the table in "Data and checkpoint storage".

**1. Confirm your access works.**

```bash
gcloud storage ls gs://prpl-tpu-queue-us-east1-944301850228/
tpu list --resources v6
```

**2. Upload your dataset to the region's data bucket, under your username.**

```bash
gcloud storage cp -r ./my-dataset \
  gs://prpl-data-us-east1-944301850228/qw3601/my-dataset
gcloud storage ls gs://prpl-data-us-east1-944301850228/qw3601/my-dataset/
```

**3. Make sure your training code is a git repository.**

`tpu create` bundles the code with git, so `--code-dir` must be a git repo. You
do not need to commit, but it must be initialized.

```bash
cd /path/to/your-training-repo
git status   # if "not a git repository", run: git init && git add -A && git commit -m init
```

Your training script should read `DATA_DIR` and `OUTPUT_DIR` from the
environment (see "Data and checkpoint storage"), write checkpoints to
`OUTPUT_DIR`, and resume from the latest checkpoint on startup.

**4. Submit a small test first.**

Start with the smallest size (`-n 8`) and a short run to confirm everything
works before launching a long job.

```bash
tpu create v6 -n 8 --user qw3601 --name my-run-test \
  --code-dir "$PWD" \
  --setup-cmd "pip install -e ." \
  --env DATA_DIR=gs://prpl-data-us-east1-944301850228/qw3601/my-dataset \
  --env OUTPUT_DIR=gs://prpl-ckpt-us-east1-944301850228/qw3601/my-run-test \
  -- python train.py --max-steps 50
```

**5. Watch it reach RUNNING and check the logs.**

Check every minute or so; do not leave a follow command running.

```bash
tpu status my-run-test
tpu logs my-run-test --lines 200
```

Status moves through `PENDING` → `PROVISIONING` (`WAITING_FOR_RESOURCES` while a
spot TPU is being allocated) → `RUNNING` → `SUCCEEDED`. Spot TPUs can sit in
`WAITING_FOR_RESOURCES` for a few minutes until capacity is available.

**6. Launch the real run.**

Once the test succeeds, submit the full job with a fresh name and, if you need
more chips, a larger size (for example `-n 32`).

```bash
tpu create v6 -n 32 --user qw3601 --name my-run \
  --code-dir "$PWD" \
  --setup-cmd "pip install -e ." \
  --env DATA_DIR=gs://prpl-data-us-east1-944301850228/qw3601/my-dataset \
  --env OUTPUT_DIR=gs://prpl-ckpt-us-east1-944301850228/qw3601/my-run \
  -- python train.py
```

**7. Retrieve results.**

Your checkpoints and outputs are in `OUTPUT_DIR`. Download them if you want a
local copy:

```bash
gcloud storage ls gs://prpl-ckpt-us-east1-944301850228/qw3601/my-run/
gcloud storage cp -r gs://prpl-ckpt-us-east1-944301850228/qw3601/my-run ./my-run-output
```

## Interactive TPU access

If shared interactive TPUs are configured, list them:

```bash
tpu interactive list
```

If this is your first time using interactive TPU access, add your SSH key:

```bash
tpu interactive add-key
```

Wait a few minutes for the key to propagate.

Then SSH into an interactive TPU:

```bash
tpu interactive ssh v4-interactive --worker 0
```

Only use interactive TPUs for debugging and small experiments. Do not use them as long term personal machines.

## Data and checkpoint storage

The queue buckets (`prpl-tpu-queue-*`) are only for job specs, uploaded code bundles, logs, and status files. **Do not put datasets or checkpoints in them.**

For datasets and checkpoints, the lab provides shared buckets. **You do not create buckets** — an admin has already created them. You just upload into your own subfolder and read/write from your training script.

### The shared buckets

There is one data bucket and one checkpoint bucket per TPU region. **Use the bucket in the same region as the TPU you run on** — reading data across regions is slower and costs egress fees.

| TPU version | Region | Data bucket | Checkpoint bucket |
|---|---|---|---|
| v6 (`tpu create v6`) | us-east1 | `gs://prpl-data-us-east1-944301850228` | `gs://prpl-ckpt-us-east1-944301850228` |
| v5 (`tpu create v5`) | us-central1 | `gs://prpl-data-us-central1-944301850228` | `gs://prpl-ckpt-us-central1-944301850228` |
| v4 (`tpu create v4`) | us-central2 | `gs://prpl-data-us-central2-944301850228` | `gs://prpl-ckpt-us-central2-944301850228` |
| v6eu / v5eu (`-r v6eu-*` / `-r v5eu-*`) | europe-west4 | `gs://prpl-data-europe-west4-944301850228` | `gs://prpl-ckpt-europe-west4-944301850228` |

Rule of thumb: **the data must live in the same region as the TPU.** If your TPU is in us-east1, put the data in the us-east1 data bucket.

### Upload your data

Put your data under a subfolder named after you, so users do not collide:

```bash
gcloud storage cp -r ./my-dataset \
  gs://prpl-data-us-east1-944301850228/qw3601/my-dataset
```

Check it landed:

```bash
gcloud storage ls gs://prpl-data-us-east1-944301850228/qw3601/
```

If your data is already in another GCS bucket, copy it server-side (fast, no local download) — but make sure the destination region matches your TPU:

```bash
gcloud storage cp -r gs://some-other-bucket/my-dataset \
  gs://prpl-data-us-east1-944301850228/qw3601/my-dataset
```

Uploading a large dataset from a slow or high-latency connection can be slow. Cloud Shell (https://shell.cloud.google.com) runs inside Google's network and is much faster for both uploads and job submission.

### Point your training job at the buckets

Pass the paths as environment variables and read them in your script:

```bash
tpu create v6 -n 8 --user qw3601 --name my-run \
  --code-dir "$PWD" \
  --setup-cmd "pip install -e ." \
  --env DATA_DIR=gs://prpl-data-us-east1-944301850228/qw3601/my-dataset \
  --env OUTPUT_DIR=gs://prpl-ckpt-us-east1-944301850228/qw3601/my-run \
  -- python train.py
```

In your script:

```python
import os
data_dir = os.environ["DATA_DIR"]      # gs://prpl-data-.../qw3601/my-dataset
output_dir = os.environ["OUTPUT_DIR"]  # gs://prpl-ckpt-.../qw3601/my-run
```

Most JAX/TensorFlow data and checkpoint libraries (`tf.data`, `tensorflow-io`, `orbax`) read and write `gs://` paths directly, so you usually do not need to download anything by hand.

If you already have your own GCS bucket you would rather use, that is fine, but an admin must grant the TPU worker service account access to it, and it should be in the same region as the TPU. Ask an admin.

### Checkpointing (required for spot TPUs)

Recommended checkpoint behavior:

```text
save checkpoints to OUTPUT_DIR (a gs:// path), not the TPU local disk
save every 10 to 30 minutes for long jobs
on startup, look in OUTPUT_DIR for the latest checkpoint and resume from it
never store important outputs only on the TPU VM local disk (it is erased on preemption)
```

## Spot TPU warning

Many PRPL TPU resources are spot resources.

Spot TPUs are cheaper but can be preempted. A spot job may stop unexpectedly.

Before launching long spot jobs, make sure your code supports checkpointing and resume.

## User safety checklist

Before submitting a job, check:

```text
The job name is unique and recognizable
The requested TPU size is reasonable
The code directory does not contain secrets
The training command works locally or on a small test
The output path points to durable storage
The job can resume if interrupted
```

Never upload or commit:

```text
Gmail passwords
verification codes
service account key files
SSH private keys
gcloud token files
local credentials
API keys
wandb keys
any other secrets
```

## When to ask an admin

Ask an admin if:

```text
tpu list cannot access the queue
your job is stuck in the queue for a long time
a TPU was created but logs are missing
the scheduler appears to be down
you need access to a new dataset or checkpoint bucket
you need a larger quota than the normal user limit
```
