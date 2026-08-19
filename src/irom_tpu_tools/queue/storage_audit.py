from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import re
import subprocess
from typing import Any

from .types import StorageAuditConfig


RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run_gcloud(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
    )


def _failure(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"exit {result.returncode}").strip()


def _users_from_listing(bucket: str, output: str) -> set[str]:
    root = f"{bucket.rstrip('/')}/"
    users: set[str] = set()
    for line in output.splitlines():
        url = line.strip()
        if not url.startswith(root):
            continue
        relative = url[len(root) :].rstrip("/")
        if not relative:
            continue
        user = relative.split("/", 1)[0]
        if re.fullmatch(r"[A-Za-z0-9._-]+", user):
            users.add(user)
    return users


def _bytes_from_du(output: str) -> int:
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)\s+gs://", line)
        if match:
            return int(match.group(1))
    raise ValueError("gcloud storage du returned no byte count")


def collect_storage_report(
    config: StorageAuditConfig,
    *,
    run: RunCommand = _run_gcloud,
) -> dict[str, Any]:
    if not config.buckets:
        raise ValueError("storage_audit.buckets is empty")
    if not config.report_url:
        raise ValueError("storage_audit.report_url is empty")

    discovered: dict[str, set[str]] = {}
    errors: list[dict[str, str]] = []
    all_users = set(config.users)
    for bucket in config.buckets:
        result = run(["gcloud", "storage", "ls", f"{bucket}/"])
        if result.returncode != 0:
            errors.append(
                {
                    "bucket": bucket,
                    "operation": "list",
                    "message": _failure(result),
                }
            )
            continue
        users = _users_from_listing(bucket, result.stdout)
        discovered[bucket] = users
        all_users.update(users)

    usage: dict[str, dict[str, int]] = {user: {} for user in all_users}
    for bucket, users in discovered.items():
        for user in sorted(users):
            url = f"{bucket}/{user}/**"
            result = run(["gcloud", "storage", "du", "--summarize", url])
            if result.returncode != 0:
                errors.append(
                    {
                        "bucket": bucket,
                        "user": user,
                        "operation": "du",
                        "message": _failure(result),
                    }
                )
                continue
            try:
                usage[user][bucket] = _bytes_from_du(result.stdout)
            except ValueError as exc:
                errors.append(
                    {
                        "bucket": bucket,
                        "user": user,
                        "operation": "du",
                        "message": str(exc),
                    }
                )

    user_rows = []
    for user in sorted(all_users):
        total_bytes = sum(usage[user].values())
        limit_bytes = config.limit_bytes_for(user)
        user_rows.append(
            {
                "user": user,
                "total_bytes": total_bytes,
                "limit_bytes": limit_bytes,
                "percent_used": round(total_bytes * 100 / limit_bytes, 2),
                "over_limit": total_bytes > limit_bytes,
                "buckets": usage[user],
            }
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "complete": not errors,
        "default_limit_bytes": config.default_limit_bytes,
        "users": user_rows,
        "errors": errors,
    }


def format_decimal_storage(byte_count: int) -> str:
    if byte_count >= 1_000_000_000_000:
        return f"{byte_count / 1_000_000_000_000:.2f} TB"
    if byte_count >= 1_000_000_000:
        return f"{byte_count / 1_000_000_000:.2f} GB"
    if byte_count >= 1_000_000:
        return f"{byte_count / 1_000_000:.2f} MB"
    return f"{byte_count} B"
