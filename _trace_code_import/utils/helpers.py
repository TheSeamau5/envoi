"""Shared utilities used across envoi-trace.

Timestamped printing (tprint), environment file loading, base64/JSON credential
handling, text truncation, token estimation, usage map merging, secret redaction,
adaptive turn timeout computation, and parallel file upload to sandboxes.
"""

from __future__ import annotations

import asyncio
import base64
import builtins
import json
import os
import re
import shlex
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sandbox.base import Sandbox

SETUP_UPLOAD_CONCURRENCY = max(
    1, int(os.environ.get("SETUP_UPLOAD_CONCURRENCY", "8"))
)
MIN_TURN_TIMEOUT_SECONDS = int(
    os.environ.get("MIN_TURN_TIMEOUT_SECONDS", "45")
)
SECONDS_PER_REMAINING_PART = int(
    os.environ.get("SECONDS_PER_REMAINING_PART", "60")
)


# ---------------------------------------------------------------------------
# Timestamped print
# ---------------------------------------------------------------------------


def ts() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


def tprint(*args: Any, **kwargs: Any) -> None:
    if "flush" not in kwargs:
        kwargs["flush"] = True
    builtins.print(f"[{ts()}]", *args, **kwargs)


print = tprint


# ---------------------------------------------------------------------------
# Environment file loading
# ---------------------------------------------------------------------------


def load_environment_files(
    env_dir: Path,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Load all source files from an environment directory for sandbox upload.

    Returns three dicts mapping relative paths to file contents: (py, c, txt).
    These get passed to environment_upload_items() to produce sandbox paths.
    """
    py_files = {
        str(p.relative_to(env_dir)): p.read_text()
        for p in env_dir.rglob("*.py")
    }
    c_files = {
        str(p.relative_to(env_dir)): p.read_text()
        for p in env_dir.rglob("*.c")
    }
    txt_files = {
        str(p.relative_to(env_dir)): p.read_text()
        for p in env_dir.rglob("*.txt")
    }
    return py_files, c_files, txt_files


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def iso_from_epoch_ms(epoch_ms: int | None) -> str:
    if isinstance(epoch_ms, int) and epoch_ms > 0:
        return datetime.fromtimestamp(epoch_ms / 1000, UTC).isoformat()
    return datetime.now(UTC).isoformat()


def decode_b64_to_text(encoded: str, *, label: str) -> str:
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except Exception as e:
        raise RuntimeError(
            f"Invalid base64 content for {label}: {e}"
        ) from e
    try:
        return raw.decode("utf-8")
    except Exception as e:
        raise RuntimeError(
            f"Invalid UTF-8 content for {label}: {e}"
        ) from e


def parse_codex_auth_json(raw_text: str, *, label: str) -> str:
    try:
        parsed = json.loads(raw_text)
    except Exception as e:
        raise RuntimeError(
            f"Invalid JSON content for {label}: {e}"
        ) from e
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{label} must contain a JSON object")
    return raw_text


def load_local_codex_auth_json_b64(path_str: str) -> str | None:
    candidate = Path(path_str).expanduser()
    if not candidate.exists() or not candidate.is_file():
        return None
    raw = candidate.read_text()
    parsed = parse_codex_auth_json(raw, label=str(candidate))
    return base64.b64encode(parsed.encode("utf-8")).decode("ascii")


def truncate_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


WORD_RE = re.compile(r"[A-Za-z0-9_']+")


def word_count(text: str | None) -> int:
    if not isinstance(text, str) or not text:
        return 0
    return len(WORD_RE.findall(text))


def token_estimate(text: str | None) -> int:
    if not isinstance(text, str) or not text:
        return 0
    return max(1, round(len(text) / 4))


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def merge_usage_maps(
    base: dict[str, Any], incoming: dict[str, Any],
) -> dict[str, Any]:
    for key, value in incoming.items():
        if key not in base:
            base[key] = value
            continue
        existing = base[key]
        if isinstance(existing, dict) and isinstance(value, dict):
            merge_usage_maps(existing, value)
        elif is_number(existing) and is_number(value):
            base[key] = existing + value
        else:
            base[key] = value
    return base


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, val in value.items():
            if isinstance(key, str) and any(
                token in key.lower()
                for token in ["key", "token", "secret", "password"]
            ):
                redacted[key] = "***" if val else val
            else:
                redacted[key] = redact_secrets(val)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def compute_turn_timeout_seconds(
    *,
    remaining_parts: int,
    remaining_run_seconds: float,
    message_timeout_seconds: int,
) -> int:
    """Derive an adaptive per-turn timeout based on remaining budget.

    Takes the minimum of: the hard cap (message_timeout_seconds), the
    parts-based estimate (remaining_parts * SECONDS_PER_REMAINING_PART),
    and the wall-clock budget remaining. Ensures at least 1 second.
    """
    timeout_from_parts = max(
        MIN_TURN_TIMEOUT_SECONDS,
        remaining_parts * SECONDS_PER_REMAINING_PART,
    )
    timeout_from_run_budget = max(1, int(remaining_run_seconds))
    return max(
        1,
        min(
            message_timeout_seconds,
            timeout_from_parts,
            timeout_from_run_budget,
        ),
    )


# ---------------------------------------------------------------------------
# Environment upload items
# ---------------------------------------------------------------------------


def environment_upload_items(
    py_files: dict[str, str],
    c_files: dict[str, str],
    txt_files: dict[str, str],
) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for rel, content in py_files.items():
        items.append((f"/environment/{rel}", content))
    for rel, content in c_files.items():
        items.append((f"/environment/{rel}", content))
    for rel, content in txt_files.items():
        items.append((f"/environment/{rel}", content))
    return items


# ---------------------------------------------------------------------------
# Parallel upload
# ---------------------------------------------------------------------------


async def upload_files_parallel(
    sandbox: Sandbox,
    uploads: list[tuple[str, str]],
    *,
    concurrency: int = SETUP_UPLOAD_CONCURRENCY,
    log_upload: bool = True,
) -> None:
    if not uploads:
        return

    bounded = max(1, concurrency)
    dirs = sorted({str(Path(path).parent) for path, _ in uploads})
    if dirs:
        mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(d) for d in dirs)
        await sandbox.run(mkdir_cmd, quiet=True)

    print(
        f"[setup] uploading {len(uploads)} files "
        f"with concurrency={bounded}"
    )

    semaphore = asyncio.Semaphore(bounded)

    async def _upload(path: str, content: str) -> None:
        if log_upload:
            print(f"[setup][upload] {path}")
        async with semaphore:
            await sandbox.write_file(
                path,
                content,
                ensure_dir=False,
                log_upload=False,
            )

    await asyncio.gather(
        *[_upload(path, content) for path, content in uploads],
    )


# ---------------------------------------------------------------------------
# Sandbox client runner
# ---------------------------------------------------------------------------


async def run_sandbox_client(
    sandbox: Sandbox,
    script_path: str,
    label: str,
    args: list[str],
    *,
    timeout: int = 60,
    quiet: bool = False,
    stream_output: bool = False,
    on_stderr_line: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any] | None:
    """Run an agent client script inside the sandbox and parse its JSON output.

    Executes a Python script (e.g. codex.py, opencode.py) with the given args.
    The script is expected to print a single JSON object to stdout. Returns
    None on non-zero exit or invalid JSON.
    """
    command = (
        f"python3 -u {shlex.quote(script_path)} "
        + " ".join(shlex.quote(a) for a in args)
    )
    exit_code, stdout, stderr = (
        await sandbox.run(
            command,
            timeout=timeout,
            quiet=quiet,
            stream_output=stream_output,
            on_stderr_line=on_stderr_line,
        )
    ).unpack()
    if exit_code != 0:
        if stderr:
            builtins.print(
                f"[{label}] command failed: {stderr[:500]}",
                flush=True,
            )
        return None

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        stdout_preview = (
            stdout[:500] + "...[truncated]"
            if len(stdout) > 500
            else stdout
        )
        builtins.print(
            f"[{label}] invalid JSON response: {stdout_preview}",
            flush=True,
        )
        return None
