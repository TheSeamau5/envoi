"""Git operations inside the sandbox.

Runs git commands via SandboxBackend.run() to manage the agent's workspace repo.
Handles checkpoint commits (only when files changed), patch/diff extraction,
bundle creation for portable history, and retry logic for transient failures.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shlex
from typing import Any

from models import RepoCheckpoint
from sandbox.base import SandboxBackend
from utils.helpers import tprint, truncate_text
from utils.storage import upload_file

print = tprint

GIT_RETRY_ATTEMPTS = max(
    1, int(os.environ.get("GIT_RETRY_ATTEMPTS", "4"))
)
GIT_RETRY_BACKOFF_SECONDS = float(
    os.environ.get("GIT_RETRY_BACKOFF_SECONDS", "0.5")
)
INCREMENTAL_BUNDLE_UPLOAD = (
    os.environ.get("INCREMENTAL_BUNDLE_UPLOAD", "1").strip().lower()
    not in {"0", "false", "no"}
)


async def run_git_command_with_retry(
    sb: SandboxBackend,
    cmd: str,
    *,
    attempts: int = GIT_RETRY_ATTEMPTS,
    timeout: int = 60,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    last: tuple[int, str, str] = (1, "", "")
    for attempt in range(1, max(1, attempts) + 1):
        exit_code, stdout, stderr = (
            await sb.run(
                cmd,
                timeout=timeout,
                quiet=True,
                cwd=cwd,
            )
        ).unpack()
        last = (exit_code, stdout, stderr)
        if exit_code == 0:
            return last

        stderr_text = (stderr or "").lower()
        retryable = (
            exit_code == 128
            or "index.lock" in stderr_text
            or "unable to create" in stderr_text
            or "another git process" in stderr_text
        )
        if not retryable or attempt >= max(1, attempts):
            return last

        delay_seconds = GIT_RETRY_BACKOFF_SECONDS * attempt
        print(
            f"[git] transient failure exit={exit_code} "
            f"attempt={attempt}/{attempts}; "
            f"retrying in {delay_seconds:.1f}s"
        )
        await asyncio.sleep(delay_seconds)

    return last


async def get_git_commit(sb: SandboxBackend) -> str | None:
    _, stdout, _ = await run_git_command_with_retry(
        sb,
        "git rev-parse HEAD 2>/dev/null || echo none",
        cwd="/workspace",
    )
    commit = stdout.strip()
    return commit if commit and commit != "none" else None


async def get_changed_files(sb: SandboxBackend) -> list[str]:
    exit_code, stdout, stderr = await run_git_command_with_retry(
        sb,
        "git status --porcelain",
        cwd="/workspace",
    )
    if exit_code != 0:
        print(
            "[git] unable to read working tree state after retries: "
            f"{truncate_text(stderr or '(no stderr)', limit=300)}"
        )
        return []
    files: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        entry = line[3:].strip() if len(line) >= 4 else line.strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if entry:
            files.append(entry)
    return files


def parse_numstat_output(stdout: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        columns = line.split("\t")
        if len(columns) < 3:
            continue
        added_raw, deleted_raw, path = (
            columns[0],
            columns[1],
            columns[2],
        )
        added = int(added_raw) if added_raw.isdigit() else None
        deleted = int(deleted_raw) if deleted_raw.isdigit() else None
        rows.append(
            {
                "path": path,
                "added": added,
                "deleted": deleted,
            }
        )
    return rows


async def get_commit_patch_payload(
    sb: SandboxBackend,
    commit: str,
) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    quoted_commit = shlex.quote(commit)
    patch_text: str | None = None
    stats_text: str | None = None
    numstat_rows: list[dict[str, Any]] = []

    patch_exit, patch_stdout, _ = (
        await sb.run(
            f"git show --format= --no-color --patch {quoted_commit}",
            quiet=True,
            cwd="/workspace",
        )
    ).unpack()
    if patch_exit == 0:
        patch_text = patch_stdout

    stats_exit, stats_stdout, _ = (
        await sb.run(
            f"git show --format= --no-color --stat {quoted_commit}",
            quiet=True,
            cwd="/workspace",
        )
    ).unpack()
    if stats_exit == 0:
        stats_text = stats_stdout

    numstat_exit, numstat_stdout, _ = (
        await sb.run(
            f"git show --format= --no-color --numstat {quoted_commit}",
            quiet=True,
            cwd="/workspace",
        )
    ).unpack()
    if numstat_exit == 0:
        numstat_rows = parse_numstat_output(numstat_stdout)

    return patch_text, stats_text, numstat_rows


async def upload_repo_bundle_snapshot(
    *,
    sb: SandboxBackend,
    trajectory_id: str,
    reason: str,
) -> str | None:
    if not INCREMENTAL_BUNDLE_UPLOAD:
        return None

    exit_code, _, stderr = await run_git_command_with_retry(
        sb,
        "git bundle create /tmp/repo.bundle --all",
        attempts=2,
        cwd="/workspace",
    )
    if exit_code != 0:
        print(
            "[bundle] snapshot create failed: "
            f"{truncate_text(stderr or '(no stderr)', limit=240)}"
        )
        return None

    _, size_out, _ = (
        await sb.run(
            "stat -c %s /tmp/repo.bundle 2>/dev/null || echo 0",
            quiet=True,
        )
    ).unpack()
    bundle_size = int(size_out.strip() or "0")
    if bundle_size <= 0:
        return None

    b64_exit, b64, b64_stderr = (
        await sb.run(
            "base64 /tmp/repo.bundle",
            quiet=True,
        )
    ).unpack()
    if b64_exit != 0:
        print(
            "[bundle] snapshot encode failed: "
            f"{truncate_text(b64_stderr or '(no stderr)', limit=240)}"
        )
        return None

    data = base64.b64decode(b64.strip())
    uri = upload_file(trajectory_id, "repo.bundle", data)
    print(
        f"[bundle] snapshot uploaded ({len(data)} bytes) reason={reason}"
    )
    return uri


async def create_part_checkpoint(
    sb: SandboxBackend,
    trajectory_id: str,
    part: int,
    changed_files_hint: list[str] | None = None,
    commit_before_hint: str | None = None,
) -> RepoCheckpoint:
    commit_before = (
        commit_before_hint
        if commit_before_hint is not None
        else await get_git_commit(sb)
    )
    changed_files = (
        [f for f in changed_files_hint if isinstance(f, str) and f]
        if changed_files_hint is not None
        else await get_changed_files(sb)
    )
    if not changed_files:
        return RepoCheckpoint(
            commit_before=commit_before,
            commit_after=commit_before,
            committed=False,
            changed_files=[],
        )

    actual_changed_files = await get_changed_files(sb)
    if not actual_changed_files:
        return RepoCheckpoint(
            commit_before=commit_before,
            commit_after=commit_before,
            committed=False,
            changed_files=changed_files,
        )
    changed_files = actual_changed_files

    commit_message = f"part {part} checkpoint"
    exit_code, _, stderr = await run_git_command_with_retry(
        sb,
        "git add -A && "
        f"git commit -m {shlex.quote(commit_message)}",
        attempts=GIT_RETRY_ATTEMPTS,
        cwd="/workspace",
    )
    if exit_code != 0:
        print(
            f"[git] checkpoint commit failed on part {part}: "
            f"{truncate_text(stderr, limit=400)}"
        )
        return RepoCheckpoint(
            commit_before=commit_before,
            commit_after=commit_before,
            committed=False,
            changed_files=changed_files,
        )

    commit_after = await get_git_commit(sb)
    patch_text: str | None = None
    stats_text: str | None = None
    numstat_rows: list[dict[str, Any]] = []
    if commit_after:
        patch_text, stats_text, numstat_rows = (
            await get_commit_patch_payload(sb, commit_after)
        )
        await upload_repo_bundle_snapshot(
            sb=sb,
            trajectory_id=trajectory_id,
            reason=f"part {part}",
        )

    print(
        f"[git] committed part {part}: "
        f"{commit_after} files={len(changed_files)}"
    )
    return RepoCheckpoint(
        commit_before=commit_before,
        commit_after=commit_after,
        committed=True,
        changed_files=changed_files,
        patch=patch_text,
        stats=stats_text,
        numstat=numstat_rows,
    )
