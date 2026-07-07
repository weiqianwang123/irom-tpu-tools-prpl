# PRPL TPU User Guide

This guide explains how PRPL lab members can use the shared TPU queue.

Normal users do not need to SSH into the scheduler VM. The scheduler is already running separately and will pick up submitted jobs from the queue buckets.

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

The queue buckets are not for datasets or checkpoints.

The queue buckets are only for job specs, uploaded code bundles, logs, and status files.

For training data and checkpoints, use a separate dataset or checkpoint bucket approved by the lab.

For spot TPU jobs, make sure your training script saves checkpoints frequently and can resume after interruption.

Recommended checkpoint behavior:

```text
save checkpoints to durable storage
save checkpoints every 10 to 30 minutes for long jobs
make the training script resume automatically from the latest checkpoint
avoid storing important outputs only on the TPU VM local disk
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
