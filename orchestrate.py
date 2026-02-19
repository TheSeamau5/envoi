"""
Main orchestrator for envoi-trace.

Drives OpenCode with a step budget and saves a full `agent_trace.json` artifact
containing all newly observed messages (including child sessions), per-step
git checkpoints, and parsed envoi test calls.

Usage:
    modal run orchestrate.py --max-steps 1000 --model opencode/gpt-5-nano
"""

from __future__ import annotations

import asyncio
import base64
import builtins
import json
import os
import shlex
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import boto3
import modal
from pydantic import BaseModel, Field

app = modal.App("envoi-trace")

DEFAULT_MODEL = "opencode/gpt-5-nano"
MESSAGE_TIMEOUT_SECONDS = int(
    os.environ.get("MESSAGE_TIMEOUT_SECONDS", "600")
)  # hard cap per message cycle
MIN_CYCLE_TIMEOUT_SECONDS = int(
    os.environ.get("MIN_CYCLE_TIMEOUT_SECONDS", "45")
)  # don't go lower than this for healthy round-trips
SECONDS_PER_REMAINING_STEP = int(
    os.environ.get("SECONDS_PER_REMAINING_STEP", "60")
)  # adaptive cap as step budget gets tight


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
# Load files at import time (Modal serializes these into the function image)
# ---------------------------------------------------------------------------

PROMPT = (Path(__file__).parent / "prompts" / "system.txt").read_text()
SETUP_SH = (Path(__file__).parent / "sandbox" / "setup.sh").read_text()
MCP_SERVER = (Path(__file__).parent / "sandbox" / "mcp_server.py").read_text()
OPENCODE_CLIENT = (Path(__file__).parent / "sandbox" / "opencode_client.py").read_text()
OPENCODE_CONFIG = (Path(__file__).parent / "sandbox" / "opencode.jsonc").read_text()
ENVIRONMENT_DIR = Path(__file__).parent / "environment"

ENVIRONMENT_PY_FILES = {
    str(py_file.relative_to(ENVIRONMENT_DIR)): py_file.read_text()
    for py_file in ENVIRONMENT_DIR.rglob("*.py")
}
ENVIRONMENT_C_FILES = {
    str(c_file.relative_to(ENVIRONMENT_DIR)): c_file.read_text()
    for c_file in ENVIRONMENT_DIR.rglob("*.c")
}
ENVIRONMENT_TXT_FILES = {
    str(txt_file.relative_to(ENVIRONMENT_DIR)): txt_file.read_text()
    for txt_file in ENVIRONMENT_DIR.rglob("*.txt")
}

REQUIRED_PATHS: list[str] = [
    "basics",
    *[f"wacct/chapter_{i}" for i in range(1, 21)],
    *[f"c_testsuite/part_{i}" for i in range(1, 6)],
    *[f"torture/part_{i}" for i in range(1, 11)],
]

WORKSPACE_GITIGNORE = """\
target/
cc
debug_artifacts/
test_*
*.o
*.out
*.s
opencode.jsonc
.opencode/
"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def resolve_model(model: str) -> str:
    if "/" in model:
        return model
    return f"opencode/{model}"


def truncate_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, val in value.items():
            if isinstance(key, str) and any(
                token in key.lower() for token in ["key", "token", "secret", "password"]
            ):
                redacted[key] = "***" if val else val
            else:
                redacted[key] = redact_secrets(val)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def compute_cycle_timeout_seconds(
    *,
    remaining_steps: int,
    remaining_run_seconds: float,
    message_timeout_seconds: int,
) -> int:
    """Derive an adaptive per-cycle timeout from remaining step and run budget."""
    timeout_from_steps = max(
        MIN_CYCLE_TIMEOUT_SECONDS,
        remaining_steps * SECONDS_PER_REMAINING_STEP,
    )
    timeout_from_run_budget = max(1, int(remaining_run_seconds))
    return max(1, min(message_timeout_seconds, timeout_from_steps, timeout_from_run_budget))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TestResult(BaseModel):
    passed: int
    failed: int
    total: int
    tests: list[dict[str, Any]] = Field(default_factory=list)


class EnvoiCall(BaseModel):
    path: str
    timestamp: str
    duration_ms: int
    status_code: int
    error: str | None = None
    result: TestResult | None = None


class SessionEnd(BaseModel):
    reason: Literal["solved", "step_limit", "timeout", "agent_error", "envoi_error"]
    total_steps: int
    total_turns: int | None = None  # backward compatibility for older consumers
    final_git_commit: str | None = None


class RepoCheckpoint(BaseModel):
    commit_before: str | None = None
    commit_after: str | None = None
    committed: bool = False
    changed_files: list[str] = Field(default_factory=list)
    patch_s3_uri: str | None = None


class StepRecord(BaseModel):
    trajectory_id: str
    session_id: str
    step: int | None = None
    turn: int | None
    timestamp: str
    agent_model: str
    prompt: str | None = None
    git_commit: str | None = None
    message_id: str | None = None
    sessions: list[dict[str, Any]] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    new_messages: list[dict[str, Any]] = Field(default_factory=list)
    envoi_calls: list[EnvoiCall] = Field(default_factory=list)
    repo_checkpoint: RepoCheckpoint | None = None
    session_end: SessionEnd | None = None


class AgentTrace(BaseModel):
    trajectory_id: str
    session_id: str
    agent_model: str
    started_at: str
    steps: list[StepRecord] = Field(default_factory=list)
    turns: list[StepRecord] = Field(default_factory=list)
    artifacts: dict[str, str | None] = Field(default_factory=dict)
    session_end: SessionEnd | None = None


# Backward compatibility alias for references using the old name.
TurnRecord = StepRecord


# ---------------------------------------------------------------------------
# Solve tracker
# ---------------------------------------------------------------------------


class SolveTracker:
    def __init__(self) -> None:
        self.solved: set[str] = set()
        self.all_calls: list[EnvoiCall] = []

    def update(self, envoi_calls: list[EnvoiCall]) -> None:
        self.all_calls.extend(envoi_calls)
        for call in envoi_calls:
            if call.result and call.result.total > 0 and call.result.passed == call.result.total:
                self.solved.add(call.path)

    def is_fully_solved(self) -> bool:
        return self.solved >= set(REQUIRED_PATHS)

    def get_unsolved_paths(self) -> list[str]:
        return [p for p in REQUIRED_PATHS if p not in self.solved]

    def get_latest_call_for_path(self, path: str) -> EnvoiCall | None:
        for call in reversed(self.all_calls):
            if call.path == path:
                return call
        return None


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------


def summarize_tool_input(name: str, input_data: Any) -> str:
    if not isinstance(input_data, dict):
        return truncate_text(str(input_data), limit=200)
    if name == "bash":
        return truncate_text(input_data.get("command", ""), limit=200)
    if name == "read":
        return str(input_data.get("filePath") or input_data.get("path") or "?")
    if name in {"write", "edit"}:
        path = input_data.get("filePath") or input_data.get("path") or "?"
        content = input_data.get("content") or input_data.get("newString") or ""
        return f"{path} ({len(content)} bytes)"
    if name == "run_tests":
        return input_data.get("test_path", truncate_text(json.dumps(input_data), limit=200))
    return truncate_text(json.dumps(input_data), limit=200)


def log_message_parts(message: dict[str, Any]) -> None:
    """Print a human-readable summary of every part in a message."""
    info = message.get("info", {})
    role = info.get("role", "?")
    parts = message.get("parts", [])
    if not parts:
        return
    for part in parts:
        ptype = part.get("type", "?")
        if ptype == "text":
            text = part.get("text", "").strip()
            if text:
                print(f"  [{role}] {truncate_text(text, limit=300)}")
        elif ptype == "tool":
            name = part.get("tool", "?")
            state = part.get("state", {})
            status = state.get("status", "?")
            summary = summarize_tool_input(name, state.get("input", {}))
            output = state.get("output") or state.get("metadata", {}).get("output") or ""
            output_str = truncate_text(str(output), limit=200) if output else ""
            print(f"  [{role}] {name} ({status}) {summary}")
            if output_str and status == "completed":
                print(f"         -> {output_str}")
        elif ptype == "tool_use":
            name = part.get("name", "?")
            status = part.get("status", "?")
            summary = summarize_tool_input(name, part.get("input", {}))
            print(f"  [{role}] {name} ({status}) {summary}")
        elif ptype == "tool_result":
            content = str(part.get("content", ""))
            if content:
                print(f"         -> {truncate_text(content, limit=200)}")
        elif ptype == "patch":
            files = part.get("files", [])
            print(f"  [{role}] patch: {files}")
        elif ptype in {"step-start", "step-finish"}:
            pass  # skip noise
        else:
            print(f"  [{role}] {ptype}")


def extract_envoi_calls(message_parts: list[dict[str, Any]]) -> list[EnvoiCall]:
    """Extract envoi test calls from message parts."""
    calls: list[EnvoiCall] = []
    # Handle tool_use + tool_result pairs (older format)
    tool_results: dict[str, dict[str, Any]] = {}
    for part in message_parts:
        if part.get("type") == "tool_result":
            tool_results[part.get("tool_use_id", "")] = part
    for part in message_parts:
        if part.get("type") == "tool_use" and part.get("name") == "run_tests":
            tool_result = tool_results.get(part.get("id", ""))
            if tool_result:
                content = tool_result.get("content", "")
                if isinstance(content, str):
                    try:
                        calls.append(EnvoiCall(**json.loads(content)))
                    except (json.JSONDecodeError, Exception):
                        pass
        # Handle "tool" type parts (OpenCode's actual format)
        if part.get("type") == "tool" and part.get("tool") == "run_tests":
            state = part.get("state", {})
            if state.get("status") == "completed":
                output = state.get("output") or state.get("metadata", {}).get("output") or ""
                try:
                    data = json.loads(output) if isinstance(output, str) else output
                    calls.append(EnvoiCall(**data))
                except Exception:
                    pass
    return calls


def count_step_finishes(messages: list[dict[str, Any]]) -> int:
    """Count completed agent steps from message parts."""
    count = 0
    for message in messages:
        parts = message.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "step-finish":
                count += 1
    return count


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

_s3_client = None


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
    return _s3_client


def get_bucket() -> str:
    return os.environ.get("AWS_S3_BUCKET", "envoi-trace-data")


def save_agent_trace_snapshot(trajectory_id: str, trace: AgentTrace) -> None:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/agent_trace.json"
    payload = trace.model_dump(mode="json")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    record_count = max(len(trace.steps), len(trace.turns))
    print(f"[s3] saved agent trace ({record_count} records) to s3://{bucket}/{key}")


def upload_file(trajectory_id: str, filename: str, data: bytes) -> str:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/{filename}"
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    return f"s3://{bucket}/{key}"


def artifact_uri(trajectory_id: str, filename: str) -> str:
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/{filename}"
    return f"s3://{bucket}/{key}"


# ---------------------------------------------------------------------------
# Modal images
# ---------------------------------------------------------------------------

function_image = (
    modal.Image.debian_slim()
    .pip_install("boto3", "pydantic")
    .add_local_dir(Path(__file__).parent / "prompts", remote_path="/root/prompts")
    .add_local_dir(Path(__file__).parent / "sandbox", remote_path="/root/sandbox")
    .add_local_dir(Path(__file__).parent / "environment", remote_path="/root/environment")
)

sandbox_image = (
    modal.Image.from_registry("ubuntu:24.04", add_python="3.12")
    .apt_install(
        "build-essential",
        "gcc",
        "g++",
        "clang",
        "git",
        "curl",
        "wget",
        "pkg-config",
        "libssl-dev",
    )
    .pip_install(
        "envoi @ git+https://github.com/TheSeamau5/envoi.git",
        "httpx>=0.27.0",
        "opencode-ai>=0.1.0a36",
        "pydantic>=2.0.0",
        "mcp>=1.0.0",
    )
    .run_commands("curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y")
)


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------


async def sandbox_run(
    sandbox: modal.Sandbox,
    cmd: str,
    timeout: int = 60,
    quiet: bool = False,
    stream_output: bool = False,
) -> tuple[int, str, str]:
    """Execute a command inside the sandbox."""
    if not quiet:
        print(f"[run] {cmd[:200]}")
    proc = await sandbox.exec.aio("bash", "-c", cmd)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    async def drain_stream(stream: Any, sink: list[str]) -> None:
        async for chunk in stream:
            sink.append(chunk)
            if stream_output and chunk:
                builtins.print(chunk, end="", flush=True)

    await asyncio.gather(
        drain_stream(proc.stdout, stdout_chunks),
        drain_stream(proc.stderr, stderr_chunks),
    )
    await proc.wait.aio()
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    exit_code = proc.returncode or 0
    if exit_code != 0:
        print(f"[run] FAILED exit={exit_code} cmd={cmd[:100]}")
        if stderr:
            print(f"[run] stderr: {stderr[:500]}")
    return exit_code, stdout, stderr


async def sandbox_write_file(
    sandbox: modal.Sandbox,
    path: str,
    content: str,
    ensure_dir: bool = True,
) -> None:
    if ensure_dir:
        await sandbox_run(sandbox, f"mkdir -p '{Path(path).parent}'", quiet=True)
    async with await sandbox.open.aio(path, "w") as f:
        await f.write.aio(content)


# ---------------------------------------------------------------------------
# OpenCode API helpers — Python SDK wrapper
# ---------------------------------------------------------------------------


async def run_opencode_client(
    sandbox: modal.Sandbox,
    args: list[str],
    *,
    timeout: int = 60,
    quiet: bool = False,
    stream_output: bool = False,
) -> dict[str, Any] | None:
    command = "python3 /sandbox/opencode_client.py " + " ".join(shlex.quote(a) for a in args)
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        command,
        timeout=timeout,
        quiet=quiet,
        stream_output=stream_output,
    )
    if exit_code != 0:
        if stderr:
            print(f"[opencode-sdk] command failed: {stderr[:500]}")
        return None

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        print(f"[opencode-sdk] invalid JSON response: {truncate_text(stdout, limit=500)}")
        return None


async def create_session(sandbox: modal.Sandbox, title: str) -> str | None:
    response = await run_opencode_client(
        sandbox,
        ["create-session", "--title", title],
        timeout=60,
    )
    if response is None:
        return None

    if not response.get("ok"):
        print(f"[session] create failed: {response.get('error')}")
        return None

    body = response.get("body", {})
    session_id = body.get("id") if isinstance(body, dict) else None
    print(f"[session] created id={session_id}")
    return session_id


async def send_message_blocking(
    sandbox: modal.Sandbox,
    session_id: str,
    text: str,
    timeout: int = MESSAGE_TIMEOUT_SECONDS,
    max_steps_this_cycle: int = 0,
) -> dict[str, Any] | None:
    prompt_path = "/tmp/prompt.txt"
    await sandbox_write_file(sandbox, prompt_path, text, ensure_dir=False)
    print(f"[prompt] sending message ({len(text)} chars), waiting up to {timeout}s...")

    response = await run_opencode_client(
        sandbox,
        [
            "chat-stream",
            "--session-id",
            session_id,
            "--text-file",
            prompt_path,
            "--max-steps",
            str(max_steps_this_cycle),
        ],
        timeout=timeout,
        stream_output=True,
    )
    if response is None:
        return None

    status_code = response.get("status_code")
    ok = bool(response.get("ok"))
    body = response.get("body")
    meta = response.get("meta")
    meta_obj = meta if isinstance(meta, dict) else {}
    aborted_for_step_limit = bool(meta_obj.get("aborted_for_step_limit"))
    print(f"[prompt] done http={status_code} ok={ok}")

    if not ok:
        if aborted_for_step_limit:
            print("[prompt] step limit reached during stream; ending current cycle")
            if isinstance(body, dict):
                stream_meta = body.get("_stream")
                stream_obj = stream_meta if isinstance(stream_meta, dict) else {}
                stream_obj.update(meta_obj)
                body["_stream"] = stream_obj
                return body
            return {"_stream": dict(meta_obj)}
        print(f"[prompt] ERROR: {truncate_text(str(response.get('error') or body), limit=1000)}")
        return None

    if not isinstance(body, dict):
        if aborted_for_step_limit:
            return {"_stream": dict(meta_obj)}
        print(f"[prompt] unexpected response type: {type(body).__name__}")
        return None

    stream_meta = body.get("_stream")
    stream_obj = stream_meta if isinstance(stream_meta, dict) else {}
    stream_obj.update(meta_obj)
    body["_stream"] = stream_obj

    return body


async def get_all_messages(sandbox: modal.Sandbox, session_id: str) -> list[dict[str, Any]]:
    response = await run_opencode_client(
        sandbox,
        ["list-messages", "--session-id", session_id],
        timeout=60,
        quiet=True,
    )
    if response is None or not response.get("ok"):
        return []
    body = response.get("body")
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "messages", "data", "results"):
            value = body.get(key)
            if isinstance(value, list):
                return value
    return []


async def get_all_sessions(sandbox: modal.Sandbox) -> list[dict[str, Any]]:
    response = await run_opencode_client(
        sandbox,
        ["list-sessions"],
        timeout=60,
        quiet=True,
    )
    if response is None or not response.get("ok"):
        return []
    body = response.get("body")
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "sessions", "data", "results"):
            value = body.get(key)
            if isinstance(value, list):
                return value
    return []


def session_object_id(session: dict[str, Any]) -> str | None:
    for key in ("id", "sessionID", "session_id"):
        value = session.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def session_object_parent_id(session: dict[str, Any]) -> str | None:
    for key in ("parentID", "parent_id", "parentId"):
        value = session.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def get_session_family(root_session_id: str, sessions: list[dict[str, Any]]) -> list[str]:
    known_ids = {
        session_id
        for session in sessions
        if isinstance(session, dict)
        if (session_id := session_object_id(session))
    }
    if root_session_id not in known_ids:
        return [root_session_id]

    children_by_parent: dict[str, list[str]] = {}
    for session in sessions:
        if not isinstance(session, dict):
            continue
        session_id = session_object_id(session)
        parent_id = session_object_parent_id(session)
        if session_id and parent_id:
            children_by_parent.setdefault(parent_id, []).append(session_id)

    family: list[str] = []
    queue = [root_session_id]
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        family.append(current)
        queue.extend(children_by_parent.get(current, []))
    return family


def message_created_ms(message: dict[str, Any]) -> int:
    info = message.get("info", {})
    time_info = info.get("time", {})
    created = time_info.get("created")
    return int(created) if isinstance(created, int) else 0


def message_id(message: dict[str, Any]) -> str | None:
    info = message.get("info", {})
    message_id_value = info.get("id")
    if isinstance(message_id_value, str) and message_id_value:
        return message_id_value
    return None


async def collect_step_messages(
    sandbox: modal.Sandbox,
    root_session_id: str,
    seen_message_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    sessions = await get_all_sessions(sandbox)
    session_ids = get_session_family(root_session_id, sessions)
    session_map: dict[str, dict[str, Any]] = {}
    for session in sessions:
        if not isinstance(session, dict):
            continue
        sid = session_object_id(session)
        if sid and sid in session_ids:
            session_map[sid] = session

    message_results = await asyncio.gather(
        *[get_all_messages(sandbox, session_id) for session_id in session_ids]
    )

    all_messages: list[dict[str, Any]] = []
    for session_id, messages in zip(session_ids, message_results, strict=False):
        for message in messages:
            if not isinstance(message, dict):
                continue
            info = message.setdefault("info", {})
            if "sessionID" not in info:
                info["sessionID"] = session_id
            all_messages.append(message)

    all_messages.sort(key=message_created_ms)

    new_messages: list[dict[str, Any]] = []
    for message in all_messages:
        mid = message_id(message)
        if mid and mid in seen_message_ids:
            continue
        if mid:
            seen_message_ids.add(mid)
        new_messages.append(message)

    session_objects = [session_map[sid] for sid in session_ids if sid in session_map]

    return session_objects, session_ids, new_messages


async def get_git_commit(sandbox: modal.Sandbox) -> str | None:
    _, stdout, _ = await sandbox_run(
        sandbox,
        "cd /workspace && git rev-parse HEAD 2>/dev/null || echo none",
        quiet=True,
    )
    commit = stdout.strip()
    return commit if commit and commit != "none" else None


async def get_changed_files(sandbox: modal.Sandbox) -> list[str]:
    _, stdout, _ = await sandbox_run(
        sandbox,
        "cd /workspace && git status --porcelain",
        quiet=True,
    )
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


async def create_step_checkpoint(
    sandbox: modal.Sandbox,
    trajectory_id: str,
    step: int,
) -> RepoCheckpoint:
    commit_before = await get_git_commit(sandbox)
    changed_files = await get_changed_files(sandbox)
    if not changed_files:
        return RepoCheckpoint(
            commit_before=commit_before,
            commit_after=commit_before,
            committed=False,
            changed_files=[],
            patch_s3_uri=None,
        )

    commit_message = f"step {step} checkpoint"
    exit_code, _, stderr = await sandbox_run(
        sandbox,
        "cd /workspace && git add -A && "
        f"git commit -m {shlex.quote(commit_message)}",
        quiet=True,
    )
    if exit_code != 0:
        print(f"[git] checkpoint commit failed on step {step}: {truncate_text(stderr, limit=400)}")
        return RepoCheckpoint(
            commit_before=commit_before,
            commit_after=commit_before,
            committed=False,
            changed_files=changed_files,
            patch_s3_uri=None,
        )

    commit_after = await get_git_commit(sandbox)
    patch_s3_uri: str | None = None
    if commit_after:
        _, patch_text, _ = await sandbox_run(
            sandbox,
            "cd /workspace && git show --binary --format=fuller --no-color HEAD",
            quiet=True,
        )
        if patch_text.strip():
            patch_s3_uri = upload_file(
                trajectory_id,
                f"steps/{step:04d}.patch",
                patch_text.encode("utf-8"),
            )

    print(f"[git] committed step {step}: {commit_after} files={len(changed_files)}")
    return RepoCheckpoint(
        commit_before=commit_before,
        commit_after=commit_after,
        committed=True,
        changed_files=changed_files,
        patch_s3_uri=patch_s3_uri,
    )


async def ensure_provider_connected(sandbox: modal.Sandbox, api_key: str) -> None:
    response = await run_opencode_client(
        sandbox,
        ["provider-status"],
        timeout=30,
        quiet=True,
    )
    if response and response.get("ok") and isinstance(response.get("body"), dict):
        connected = response["body"].get("connected", [])
        print(f"[provider] connected={connected}")
        if isinstance(connected, list) and "opencode" in connected:
            return

    print("[provider] opencode not connected, setting auth...")
    api_key_path = "/tmp/auth_opencode_api_key.txt"
    await sandbox_write_file(sandbox, api_key_path, api_key, ensure_dir=False)
    auth_response = await run_opencode_client(
        sandbox,
        ["provider-auth", "--api-key-file", api_key_path],
        timeout=30,
    )
    if auth_response is None or not auth_response.get("ok"):
        raise RuntimeError(f"Failed to authenticate provider: {auth_response}")


# ---------------------------------------------------------------------------
# Sandbox setup
# ---------------------------------------------------------------------------


async def setup_sandbox(sandbox: modal.Sandbox, model: str, api_key: str) -> None:
    print(f"[setup] model={model}")

    # Write files to sandbox
    await sandbox_write_file(sandbox, "/tmp/upload/setup.sh", SETUP_SH)
    await sandbox_write_file(sandbox, "/sandbox/mcp_server.py", MCP_SERVER)
    await sandbox_write_file(sandbox, "/sandbox/opencode_client.py", OPENCODE_CLIENT)
    await sandbox_write_file(sandbox, "/tmp/upload/opencode_api_key.txt", api_key)

    config = OPENCODE_CONFIG.replace("MODEL_PLACEHOLDER", model)
    await sandbox_write_file(sandbox, "/workspace/opencode.jsonc", config)
    await sandbox_write_file(sandbox, "/workspace/.gitignore", WORKSPACE_GITIGNORE)

    # Upload environment files (precreate dirs in one call)
    env_paths = (
        [f"/environment/{r}" for r in ENVIRONMENT_PY_FILES]
        + [f"/environment/{r}" for r in ENVIRONMENT_C_FILES]
        + [f"/environment/{r}" for r in ENVIRONMENT_TXT_FILES]
    )
    env_dirs = sorted({str(Path(p).parent) for p in env_paths})
    if env_dirs:
        await sandbox_run(sandbox, f"mkdir -p {' '.join(repr(d) for d in env_dirs)}", quiet=True)

    for rel, content in ENVIRONMENT_PY_FILES.items():
        await sandbox_write_file(sandbox, f"/environment/{rel}", content, ensure_dir=False)
    for rel, content in ENVIRONMENT_C_FILES.items():
        await sandbox_write_file(sandbox, f"/environment/{rel}", content, ensure_dir=False)
    for rel, content in ENVIRONMENT_TXT_FILES.items():
        await sandbox_write_file(sandbox, f"/environment/{rel}", content, ensure_dir=False)

    print(
        f"[setup] uploaded {len(ENVIRONMENT_PY_FILES)} py, "
        f"{len(ENVIRONMENT_C_FILES)} c, {len(ENVIRONMENT_TXT_FILES)} txt files"
    )

    # Run setup script (starts envoi + opencode)
    print("[setup] running setup.sh...")
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        "bash /tmp/upload/setup.sh",
        timeout=600,
    )
    if exit_code != 0:
        print(f"[setup] FAILED:\n{stdout}\n{stderr}")
        raise RuntimeError(f"Setup failed (exit {exit_code})")
    print("[setup] done")


# ---------------------------------------------------------------------------
# End session
# ---------------------------------------------------------------------------


async def end_session(
    sandbox: modal.Sandbox,
    agent_trace: AgentTrace,
    step_count: int,
    reason: Literal["solved", "step_limit", "timeout", "agent_error", "envoi_error"],
) -> None:
    print(f"[end] reason={reason} steps={step_count}")

    final_commit = await get_git_commit(sandbox)
    trace_s3_uri = artifact_uri(agent_trace.trajectory_id, "agent_trace.json")
    bundle_s3_uri: str | None = None

    agent_trace.session_end = SessionEnd(
        reason=reason,
        total_steps=step_count,
        total_turns=step_count,
        final_git_commit=final_commit,
    )
    save_agent_trace_snapshot(agent_trace.trajectory_id, agent_trace)

    # Upload git bundle
    try:
        exit_code, _, _ = await sandbox_run(
            sandbox,
            "cd /workspace && git bundle create /tmp/repo.bundle --all",
            quiet=True,
        )
        _, size_out, _ = await sandbox_run(
            sandbox,
            "stat -c %s /tmp/repo.bundle 2>/dev/null || echo 0",
            quiet=True,
        )
        bundle_size = int(size_out.strip() or "0")
        print(f"[bundle] size={bundle_size} bytes")

        if bundle_size > 0:
            _, b64, _ = await sandbox_run(sandbox, "base64 /tmp/repo.bundle", quiet=True)
            data = base64.b64decode(b64.strip())
            bundle_s3_uri = upload_file(agent_trace.trajectory_id, "repo.bundle", data)
            print(f"[bundle] uploaded ({len(data)} bytes)")
    except Exception as e:
        print(f"[bundle] failed: {e}")

    step_patch_prefix = artifact_uri(agent_trace.trajectory_id, "steps/")
    manifest = {
        "trajectory_id": agent_trace.trajectory_id,
        "session_id": agent_trace.session_id,
        "agent_model": agent_trace.agent_model,
        "ended_at": datetime.now(UTC).isoformat(),
        "session_end": (
            agent_trace.session_end.model_dump(mode="json") if agent_trace.session_end else None
        ),
        "artifacts": {
            "agent_trace": trace_s3_uri,
            "repo_bundle": bundle_s3_uri,
            "step_patch_prefix": step_patch_prefix,
            "turn_patch_prefix": step_patch_prefix,
        },
    }
    manifest_data = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    manifest_s3_uri = upload_file(agent_trace.trajectory_id, "artifacts.json", manifest_data)

    agent_trace.artifacts = {
        "agent_trace": trace_s3_uri,
        "repo_bundle": bundle_s3_uri,
        "step_patch_prefix": step_patch_prefix,
        "turn_patch_prefix": step_patch_prefix,
        "artifacts_manifest": manifest_s3_uri,
    }
    save_agent_trace_snapshot(agent_trace.trajectory_id, agent_trace)
    print(f"[s3] saved artifacts manifest to {manifest_s3_uri}")

    print(f"[end] session ended: {reason}, {step_count} steps, commit={final_commit}")


# ---------------------------------------------------------------------------
# Modal images
# ---------------------------------------------------------------------------


@app.function(
    timeout=14400,
    secrets=[modal.Secret.from_dotenv()],
    image=function_image,
)
async def run_trajectory(
    model: str = DEFAULT_MODEL,
    max_steps: int = 1000,
    max_turns: int | None = None,  # deprecated; kept for backward compatibility
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
) -> str:
    if trajectory_id is None:
        trajectory_id = str(uuid.uuid4())

    effective_max_steps = max_turns if max_turns is not None else max_steps

    resolved_model = resolve_model(model)
    print(f"Starting trajectory {trajectory_id}")
    print(
        f"model={resolved_model} max_steps={effective_max_steps} "
        f"timeout={timeout_seconds}s message_timeout={message_timeout_seconds}s"
    )

    opencode_api_key = os.environ.get("OPENCODE_API_KEY", "")
    if not opencode_api_key:
        raise RuntimeError("OPENCODE_API_KEY not set")

    sandbox = await modal.Sandbox.create.aio(
        "bash",
        "-c",
        "sleep infinity",
        image=sandbox_image,
        timeout=timeout_seconds,
        app=app,
    )
    start_time = time.monotonic()

    try:
        # --- Setup ---
        await setup_sandbox(sandbox, resolved_model, opencode_api_key)

        session_id = await create_session(sandbox, title=f"trajectory-{trajectory_id}")
        if not session_id:
            raise RuntimeError("Failed to create OpenCode session")

        await ensure_provider_connected(sandbox, opencode_api_key)

        agent_trace = AgentTrace(
            trajectory_id=trajectory_id,
            session_id=session_id,
            agent_model=resolved_model,
            started_at=datetime.now(UTC).isoformat(),
        )
        save_agent_trace_snapshot(trajectory_id, agent_trace)

        # --- Main loop: blocking message cycles with step budget ---
        tracker = SolveTracker()
        last_message_id: str | None = None
        seen_message_ids: set[str] = set()
        cycle_count = 0
        step_count = 0
        prompt_text = PROMPT
        end_reason: str | None = None

        try:
            while step_count < effective_max_steps:
                elapsed = time.monotonic() - start_time
                if elapsed > timeout_seconds:
                    end_reason = "timeout"
                    break
                remaining_run_seconds = timeout_seconds - elapsed
                remaining_steps = max(1, effective_max_steps - step_count)
                cycle_timeout_seconds = compute_cycle_timeout_seconds(
                    remaining_steps=remaining_steps,
                    remaining_run_seconds=remaining_run_seconds,
                    message_timeout_seconds=message_timeout_seconds,
                )

                print(f"\n{'=' * 60}")
                print(
                    f"CYCLE {cycle_count + 1}  "
                    f"(steps {step_count}/{effective_max_steps}, "
                    f"timeout {cycle_timeout_seconds}s, elapsed {int(elapsed)}s)"
                )
                print(f"{'=' * 60}")

                cycle_started_at = datetime.now(UTC).isoformat()

                # Send message and BLOCK until agent finishes
                response = await send_message_blocking(
                    sandbox,
                    session_id,
                    prompt_text,
                    timeout=cycle_timeout_seconds,
                    max_steps_this_cycle=remaining_steps,
                )

                if response is None:
                    print("[cycle] no response from agent")
                    end_reason = "agent_error"
                    break

                cycle_count += 1

                # Log what the agent did
                info = response.get("info", {})
                parts = response.get("parts", [])
                last_message_id = info.get("id")
                print(f"[cycle] response id={last_message_id} parts={len(parts)}")
                log_message_parts(response)

                session_objects, session_ids, new_messages = await collect_step_messages(
                    sandbox,
                    session_id,
                    seen_message_ids,
                )
                print(
                    f"[cycle] new_messages={len(new_messages)} "
                    f"sessions={len(session_ids)}"
                )

                # Extract envoi calls from newly observed messages only.
                new_envoi_calls: list[EnvoiCall] = []
                for msg in new_messages:
                    msg_parts = msg.get("parts", [])
                    if isinstance(msg_parts, list):
                        new_envoi_calls.extend(extract_envoi_calls(msg_parts))

                tracker.update(new_envoi_calls)

                checkpoint = await create_step_checkpoint(
                    sandbox=sandbox,
                    trajectory_id=trajectory_id,
                    step=cycle_count,
                )
                git_commit = checkpoint.commit_after or checkpoint.commit_before

                stream_meta = response.get("_stream", {}) if isinstance(response, dict) else {}
                stream_meta_obj = stream_meta if isinstance(stream_meta, dict) else {}
                streamed_steps = int(stream_meta_obj.get("step_finishes_seen", 0) or 0)
                step_limit_abort = bool(stream_meta_obj.get("aborted_for_step_limit"))

                new_steps = max(count_step_finishes(new_messages), streamed_steps)
                if new_steps == 0 and not step_limit_abort:
                    # Fallback for providers that do not emit explicit step markers.
                    new_steps = 1
                step_count += new_steps

                record = TurnRecord(
                    trajectory_id=trajectory_id,
                    session_id=session_id,
                    step=cycle_count,
                    turn=cycle_count,
                    timestamp=datetime.now(UTC).isoformat(),
                    agent_model=resolved_model,
                    prompt=prompt_text,
                    git_commit=git_commit,
                    message_id=last_message_id,
                    sessions=session_objects,
                    session_ids=session_ids,
                    new_messages=new_messages,
                    envoi_calls=new_envoi_calls,
                    repo_checkpoint=checkpoint,
                )
                agent_trace.steps.append(record)
                agent_trace.turns.append(record)
                save_agent_trace_snapshot(trajectory_id, agent_trace)

                solved_count = len(tracker.solved)
                total_count = len(REQUIRED_PATHS)
                print(
                    f"[cycle] cycle={cycle_count} commit={git_commit} "
                    f"steps=+{new_steps} total={step_count}/{effective_max_steps} "
                    f"envoi_calls={len(new_envoi_calls)} "
                    f"solved={solved_count}/{total_count} "
                    f"started={cycle_started_at}"
                )

                if tracker.is_fully_solved():
                    end_reason = "solved"
                    break

                if step_limit_abort:
                    end_reason = "step_limit"
                    break

                if step_count >= effective_max_steps:
                    end_reason = "step_limit"
                    break

                # Build re-injection prompt for next cycle
                unsolved = tracker.get_unsolved_paths()
                details: list[str] = []
                for p in unsolved[:10]:
                    call = tracker.get_latest_call_for_path(p)
                    if call and call.result:
                        details.append(f"  - {p}: {call.result.passed}/{call.result.total}")
                    else:
                        details.append(f"  - {p}: not run")

                prompt_text = "Continue working on the compiler. Run tests and pass ALL suites."
                if details:
                    prompt_text += "\n\nCurrent test status:\n" + "\n".join(details)

            if end_reason is None:
                end_reason = "step_limit"

        except Exception as loop_err:
            print(f"[error] crash during main loop: {loop_err}")
            end_reason = "agent_error"
            # Save whatever messages we have
            try:
                session_objects, session_ids, crash_new_messages = await collect_step_messages(
                    sandbox,
                    session_id,
                    seen_message_ids,
                )
                if crash_new_messages:
                    crash_record = TurnRecord(
                        trajectory_id=trajectory_id,
                        session_id=session_id,
                        step=cycle_count + 1,
                        turn=cycle_count + 1,
                        timestamp=datetime.now(UTC).isoformat(),
                        agent_model=resolved_model,
                        prompt=prompt_text,
                        git_commit=await get_git_commit(sandbox),
                        message_id=last_message_id,
                        sessions=session_objects,
                        session_ids=session_ids,
                        new_messages=crash_new_messages,
                        envoi_calls=[],
                    )
                    agent_trace.steps.append(crash_record)
                    agent_trace.turns.append(crash_record)
                    save_agent_trace_snapshot(trajectory_id, agent_trace)
                    print(f"[error] saved {len(crash_new_messages)} new messages before crash")
            except Exception:
                print("[error] could not save crash messages")

        # Always end the session and save final state
        await end_session(
            sandbox,
            agent_trace,
            step_count,
            end_reason,
        )
        return trajectory_id

    except Exception as e:
        print(f"[error] {e}")
        raise
    finally:
        try:
            await sandbox.terminate.aio()
        except Exception:
            pass

    return trajectory_id


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


@app.local_entrypoint()
async def main(
    model: str = DEFAULT_MODEL,
    max_steps: int = 1000,
    max_turns: int | None = None,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    trajectory_id: str | None = None,
) -> None:
    result = await run_trajectory.remote.aio(
        model=model,
        max_steps=max_steps,
        max_turns=max_turns,
        message_timeout_seconds=message_timeout_seconds,
        trajectory_id=trajectory_id,
    )
    print(f"Completed trajectory: {result}")
