"""
Main orchestrator for envoi-trace.

Runs an agent backend with a part budget and saves a full `agent_trace.json`
artifact containing per-part records, git checkpoints, and parsed envoi test
calls.

Usage:
    modal run runner.py --agent opencode --max-parts 1000 --model opencode/gpt-5-nano
    modal run runner.py --agent codex --max-parts 1000
    modal run runner.py --agent codex --max-parts 1000 --codex-auth-file /path/to/auth.json
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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import boto3
import modal
from pydantic import BaseModel, Field

from agents.opencode import OPENCODE_CONFIG_TEMPLATE
from task import (
    TASK_REQUIRED_TEST_PATHS,
    TASK_SANDBOX_SETUP_SH,
    TASK_SYSTEM_PROMPT,
    build_followup_prompt,
)

app = modal.App("envoi-trace")

DEFAULT_OPENCODE_MODEL = "opencode/gpt-5-nano"
DEFAULT_CODEX_MODEL = "gpt-5.3-codex"
DEFAULT_AGENT = "codex"
DEFAULT_MODEL = DEFAULT_CODEX_MODEL
CODEX_HOME_DIR = "/tmp/codex-home"
MESSAGE_TIMEOUT_SECONDS = int(
    os.environ.get("MESSAGE_TIMEOUT_SECONDS", "600")
)  # hard cap per message turn
MIN_TURN_TIMEOUT_SECONDS = int(
    os.environ.get("MIN_TURN_TIMEOUT_SECONDS", "45")
)  # don't go lower than this for healthy request/response exchanges
SECONDS_PER_REMAINING_PART = int(
    os.environ.get("SECONDS_PER_REMAINING_PART", "60")
)  # adaptive cap as part budget gets tight
TRACE_EVENT_PREFIX = "TRACE_EVENT "
SETUP_UPLOAD_CONCURRENCY = max(1, int(os.environ.get("SETUP_UPLOAD_CONCURRENCY", "8")))


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

SETUP_SH = (Path(__file__).parent / "sandbox" / "modal" / "setup.sh").read_text()
MCP_SERVER = (Path(__file__).parent / "sandbox" / "modal" / "mcp_server.py").read_text()
OPENCODE_CLIENT = (Path(__file__).parent / "agents" / "opencode.py").read_text()
CODEX_CLIENT = (Path(__file__).parent / "agents" / "codex.py").read_text()
OPENCODE_CONFIG = OPENCODE_CONFIG_TEMPLATE
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
.codex/
"""

CODEX_CONFIG_TOML = """\
model = "MODEL_PLACEHOLDER"
model_reasoning_effort = "high"

[mcp_servers.tests]
command = "python3"
args = ["/sandbox/mcp_server.py"]
enabled = true
required = false
tool_timeout_sec = 3600
"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def resolve_model(agent: str, model: str | None) -> str:
    if agent == "opencode":
        raw = model or DEFAULT_OPENCODE_MODEL
        if "/" in raw:
            return raw
        return f"opencode/{raw}"
    if agent == "codex":
        return model or DEFAULT_CODEX_MODEL
    raise ValueError(f"Unsupported agent: {agent}")


def iso_from_epoch_ms(epoch_ms: int | None) -> str:
    if isinstance(epoch_ms, int) and epoch_ms > 0:
        return datetime.fromtimestamp(epoch_ms / 1000, UTC).isoformat()
    return datetime.now(UTC).isoformat()


def decode_b64_to_text(encoded: str, *, label: str) -> str:
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except Exception as e:
        raise RuntimeError(f"Invalid base64 content for {label}: {e}") from e
    try:
        return raw.decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Invalid UTF-8 content for {label}: {e}") from e


def parse_codex_auth_json(raw_text: str, *, label: str) -> str:
    try:
        parsed = json.loads(raw_text)
    except Exception as e:
        raise RuntimeError(f"Invalid JSON content for {label}: {e}") from e
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


def compute_turn_timeout_seconds(
    *,
    remaining_parts: int,
    remaining_run_seconds: float,
    message_timeout_seconds: int,
) -> int:
    """Derive an adaptive per-turn timeout from remaining part and run budget."""
    timeout_from_parts = max(
        MIN_TURN_TIMEOUT_SECONDS,
        remaining_parts * SECONDS_PER_REMAINING_PART,
    )
    timeout_from_run_budget = max(1, int(remaining_run_seconds))
    return max(1, min(message_timeout_seconds, timeout_from_parts, timeout_from_run_budget))


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


class TestingState(BaseModel):
    solved_paths: int
    total_paths: int
    latest_path: str | None = None
    latest_passed: int | None = None
    latest_total: int | None = None
    latest_status_code: int | None = None
    latest_error: str | None = None


class SessionEnd(BaseModel):
    reason: Literal["solved", "part_limit", "timeout", "agent_error", "envoi_error"]
    total_parts: int
    total_turns: int | None = None
    final_git_commit: str | None = None


class RepoCheckpoint(BaseModel):
    commit_before: str | None = None
    commit_after: str | None = None
    committed: bool = False
    changed_files: list[str] = Field(default_factory=list)


class PartRecord(BaseModel):
    trajectory_id: str
    session_id: str
    agent: str = DEFAULT_AGENT
    part: int | None = None
    timestamp: str
    role: Literal["assistant", "user"] = "assistant"
    part_type: str | None = None
    item_type: str | None = None
    summary: str | None = None
    duration_ms: int | None = None
    agent_model: str
    git_commit: str | None = None
    envoi_calls: list[EnvoiCall] = Field(default_factory=list)
    testing_state: TestingState | None = None
    repo_checkpoint: RepoCheckpoint | None = None


class TurnRecord(BaseModel):
    trajectory_id: str
    session_id: str
    agent: str = DEFAULT_AGENT
    turn: int
    part_start: int | None = None
    part_end: int | None = None
    timestamp: str
    agent_model: str
    prompt: str | None = None
    git_commit: str | None = None
    repo_checkpoint: RepoCheckpoint | None = None
    parts: list[PartRecord] = Field(default_factory=list)
    session_end: SessionEnd | None = None


class AgentTrace(BaseModel):
    trajectory_id: str
    session_id: str
    agent: str = DEFAULT_AGENT
    agent_model: str
    started_at: str
    parts: list[PartRecord] = Field(default_factory=list)
    turns: list[TurnRecord] = Field(default_factory=list)
    artifacts: dict[str, str | None] = Field(default_factory=dict)
    session_end: SessionEnd | None = None


class AgentTurnOutcome(BaseModel):
    session_id: str
    response: dict[str, Any]
    session_objects: list[dict[str, Any]] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    new_messages: list[dict[str, Any]] = Field(default_factory=list)


class AgentBackend(Protocol):
    name: str

    def resolve_model(self, model: str | None) -> str:
        ...

    async def setup_sandbox(
        self,
        sandbox: modal.Sandbox,
        model: str,
        api_key: str,
        auth_json: str | None = None,
    ) -> None:
        ...

    async def create_session(self, sandbox: modal.Sandbox, trajectory_id: str) -> str | None:
        ...

    async def ensure_authenticated(
        self,
        sandbox: modal.Sandbox,
        api_key: str,
        auth_json: str | None = None,
    ) -> None:
        ...

    async def run_turn(
        self,
        sandbox: modal.Sandbox,
        session_id: str,
        model: str,
        prompt_text: str,
        seen_message_ids: set[str],
        timeout: int,
        remaining_parts_budget: int,
        on_stream_part: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AgentTurnOutcome | None:
        ...

    async def collect_crash_messages(
        self,
        sandbox: modal.Sandbox,
        session_id: str,
        seen_message_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        ...


# ---------------------------------------------------------------------------
# Solve tracker
# ---------------------------------------------------------------------------


class SolveTracker:
    def __init__(self, required_paths: list[str]) -> None:
        self.required_paths = required_paths
        self.required_paths_set = set(required_paths)
        self.solved: set[str] = set()
        self.all_calls: list[EnvoiCall] = []
        self._seen_call_keys: set[str] = set()

    def _call_key(self, call: EnvoiCall) -> str:
        return f"{call.path}|{call.timestamp}|{call.status_code}|{call.duration_ms}"

    def update(self, envoi_calls: list[EnvoiCall]) -> None:
        for call in envoi_calls:
            key = self._call_key(call)
            if key in self._seen_call_keys:
                continue
            self._seen_call_keys.add(key)
            self.all_calls.append(call)
            if call.result and call.result.total > 0 and call.result.passed == call.result.total:
                self.solved.add(call.path)

    def is_fully_solved(self) -> bool:
        return self.solved >= self.required_paths_set

    def get_unsolved_paths(self) -> list[str]:
        return [p for p in self.required_paths if p not in self.solved]

    def get_latest_call_for_path(self, path: str) -> EnvoiCall | None:
        for call in reversed(self.all_calls):
            if call.path == path:
                return call
        return None

    def snapshot(self) -> TestingState:
        latest = self.all_calls[-1] if self.all_calls else None
        latest_passed = latest.result.passed if latest and latest.result else None
        latest_total = latest.result.total if latest and latest.result else None
        return TestingState(
            solved_paths=len(self.solved),
            total_paths=len(self.required_paths),
            latest_path=latest.path if latest else None,
            latest_passed=latest_passed,
            latest_total=latest_total,
            latest_status_code=latest.status_code if latest else None,
            latest_error=latest.error if latest else None,
        )


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


MEANINGFUL_PART_TYPES: set[str] = {
    "reasoning",
    "text",
    "tool",
    "tool_use",
    "tool_result",
    "patch",
}


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
        elif ptype == "reasoning":
            text = part.get("text", "").strip()
            if text:
                print(f"  [{role}] reasoning: {truncate_text(text, limit=220)}")
        elif ptype == "tool":
            name = part.get("tool", "?")
            state = part.get("state", {})
            status = state.get("status", "?")
            summary = summarize_tool_input(name, state.get("input", {}))
            output = state.get("output") or state.get("metadata", {}).get("output") or ""
            output_str = truncate_text(str(output), limit=200) if output else ""
            print(f"  [{role}] {name} ({status})")
            if summary:
                print(f"         input: {summary}")
            if output_str and status == "completed":
                print(f"         -> {output_str}")
        elif ptype == "tool_use":
            name = part.get("name", "?")
            status = part.get("status", "?")
            summary = summarize_tool_input(name, part.get("input", {}))
            print(f"  [{role}] {name} ({status})")
            if summary:
                print(f"         input: {summary}")
        elif ptype == "tool_result":
            content = str(part.get("content", ""))
            if content:
                print(f"         -> {truncate_text(content, limit=200)}")
        elif ptype == "patch":
            files = part.get("files", [])
            names: list[str] = []
            for f in files:
                if isinstance(f, str):
                    names.append(f)
                elif isinstance(f, dict):
                    name = f.get("path") or f.get("filename") or f.get("name") or "?"
                    names.append(str(name))
            print(f"  [{role}] patch: {', '.join(names)} ({len(names)} files)")
        elif isinstance(ptype, str) and (ptype.endswith("-start") or ptype.endswith("-finish")):
            pass  # skip noise
        else:
            print(f"  [{role}] {ptype}")


def extract_envoi_calls(message_parts: list[dict[str, Any]]) -> list[EnvoiCall]:
    """Extract envoi test calls from message parts."""
    calls: list[EnvoiCall] = []

    def parse_envoi_call(value: Any) -> EnvoiCall | None:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return None
            return parse_envoi_call(parsed)

        if isinstance(value, dict):
            if "path" in value and "timestamp" in value:
                try:
                    return EnvoiCall(**value)
                except Exception:
                    pass
            for nested_key in ("result", "data", "output", "structured_content"):
                if nested_key in value:
                    parsed_nested = parse_envoi_call(value.get(nested_key))
                    if parsed_nested is not None:
                        return parsed_nested
        return None

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
                parsed_call = parse_envoi_call(content)
                if parsed_call is not None:
                    calls.append(parsed_call)
        # Handle "tool" type parts (OpenCode's actual format)
        if part.get("type") == "tool" and part.get("tool") == "run_tests":
            state = part.get("state", {})
            if state.get("status") == "completed":
                output = state.get("output") or state.get("metadata", {}).get("output") or ""
                parsed_call = parse_envoi_call(output)
                if parsed_call is not None:
                    calls.append(parsed_call)
    return calls


def count_meaningful_parts(messages: list[dict[str, Any]]) -> int:
    """Count meaningful assistant parts from newly observed messages."""
    count = 0
    for message in messages:
        info = message.get("info", {})
        role = info.get("role")
        if role != "assistant":
            continue
        parts = message.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and part.get("type") in MEANINGFUL_PART_TYPES:
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


def save_agent_trace_snapshot(
    trajectory_id: str,
    trace: AgentTrace,
    *,
    allow_empty: bool = False,
) -> None:
    part_count = len(trace.parts)
    turn_count = len(trace.turns)
    if not allow_empty and turn_count == 0 and part_count == 0:
        return

    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/agent_trace.json"
    payload = trace.model_dump(mode="json")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    print(
        f"[s3] saved agent trace (parts={part_count}) to s3://{bucket}/{key}"
    )


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
    .add_local_file(Path(__file__).parent / "task.py", remote_path="/root/task.py")
    .add_local_dir(Path(__file__).parent / "agents", remote_path="/root/agents")
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


async def dump_sandbox_logs(
    sandbox: modal.Sandbox,
    *,
    agent: str,
    tail: int = 50,
) -> None:
    """Print the tail of agent + envoi logs from the sandbox."""
    log_files = ["/tmp/envoi.log"]
    if agent == "opencode":
        log_files.insert(0, "/tmp/opencode.log")
    elif agent == "codex":
        log_files.insert(0, "/tmp/codex.log")

    for log_file in log_files:
        try:
            _, stdout, _ = await sandbox_run(
                sandbox,
                f"[ -f {shlex.quote(log_file)} ] && tail -n {tail} {shlex.quote(log_file)} || true",
                timeout=10,
                quiet=True,
            )
            if stdout.strip():
                label = log_file.split("/")[-1]
                print(f"[logs] === {label} (last {tail} lines) ===")
                for line in stdout.strip().splitlines():
                    builtins.print(f"  {line}", flush=True)
        except Exception:
            pass


async def sandbox_run(
    sandbox: modal.Sandbox,
    cmd: str,
    timeout: int = 60,
    quiet: bool = False,
    stream_output: bool = False,
    on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
    on_stderr_line: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[int, str, str]:
    """Execute a command inside the sandbox."""
    if not quiet:
        print(f"[run] {cmd[:200]}")
    proc = await sandbox.exec.aio("bash", "-c", cmd, timeout=timeout)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    async def drain_stream(
        stream: Any,
        sink: list[str],
        live: bool = False,
        line_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        line_buffer = ""
        async for chunk in stream:
            sink.append(chunk)
            if live and chunk:
                builtins.print(chunk, end="", flush=True)
            if line_callback is None:
                continue
            line_buffer += chunk
            while "\n" in line_buffer:
                line, line_buffer = line_buffer.split("\n", 1)
                await line_callback(line.rstrip("\r"))
        if line_callback is not None and line_buffer:
            await line_callback(line_buffer.rstrip("\r"))

    await asyncio.gather(
        drain_stream(
            proc.stdout,
            stdout_chunks,
            line_callback=on_stdout_line,
        ),
        drain_stream(
            proc.stderr,
            stderr_chunks,
            live=stream_output,
            line_callback=on_stderr_line,
        ),
    )

    await proc.wait.aio()
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    exit_code = proc.returncode or 0
    if exit_code in {124, -1}:
        print(f"[run] TIMEOUT after {timeout}s: {cmd[:100]}")
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
    log_upload: bool = False,
) -> None:
    if log_upload:
        print(f"[setup][upload] {path}")
    if ensure_dir:
        await sandbox_run(sandbox, f"mkdir -p '{Path(path).parent}'", quiet=True)
    async with await sandbox.open.aio(path, "w") as f:
        await f.write.aio(content)


def environment_upload_items() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for rel, content in ENVIRONMENT_PY_FILES.items():
        items.append((f"/environment/{rel}", content))
    for rel, content in ENVIRONMENT_C_FILES.items():
        items.append((f"/environment/{rel}", content))
    for rel, content in ENVIRONMENT_TXT_FILES.items():
        items.append((f"/environment/{rel}", content))
    return items


async def upload_files_parallel(
    sandbox: modal.Sandbox,
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
        await sandbox_run(sandbox, mkdir_cmd, quiet=True)

    print(
        f"[setup] uploading {len(uploads)} files with concurrency={bounded}"
    )

    semaphore = asyncio.Semaphore(bounded)

    async def _upload(path: str, content: str) -> None:
        if log_upload:
            print(f"[setup][upload] {path}")
        async with semaphore:
            await sandbox_write_file(
                sandbox,
                path,
                content,
                ensure_dir=False,
                log_upload=False,
            )

    await asyncio.gather(*[_upload(path, content) for path, content in uploads])


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
    on_stderr_line: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any] | None:
    command = "python3 /sandbox/opencode_client.py " + " ".join(shlex.quote(a) for a in args)
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        command,
        timeout=timeout,
        quiet=quiet,
        stream_output=stream_output,
        on_stderr_line=on_stderr_line,
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


async def run_codex_client(
    sandbox: modal.Sandbox,
    args: list[str],
    *,
    timeout: int = 60,
    quiet: bool = False,
    stream_output: bool = False,
    on_stderr_line: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any] | None:
    command = "python3 -u /sandbox/codex_client.py " + " ".join(shlex.quote(a) for a in args)
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        command,
        timeout=timeout,
        quiet=quiet,
        stream_output=stream_output,
        on_stderr_line=on_stderr_line,
    )
    if exit_code != 0:
        if stderr:
            print(f"[codex-cli] command failed: {stderr[:500]}")
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        print(f"[codex-cli] invalid JSON response: {truncate_text(stdout, limit=500)}")
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
    remaining_parts_budget: int = 0,
    on_stream_part: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any] | None:
    prompt_path = "/tmp/prompt.txt"
    await sandbox_write_file(sandbox, prompt_path, text, ensure_dir=False)
    print(f"[prompt] sending message ({len(text)} chars), waiting up to {timeout}s...")

    async def handle_stderr_line(line: str) -> None:
        if on_stream_part is None:
            return
        stripped = line.strip()
        if not stripped.startswith(TRACE_EVENT_PREFIX):
            return
        payload = stripped[len(TRACE_EVENT_PREFIX):].strip()
        if not payload:
            return
        try:
            event_obj = json.loads(payload)
        except json.JSONDecodeError:
            return
        if isinstance(event_obj, dict):
            await on_stream_part(event_obj)

    response = await run_opencode_client(
        sandbox,
        [
            "chat-stream",
            "--session-id",
            session_id,
            "--text-file",
            prompt_path,
            "--max-parts",
            str(remaining_parts_budget),
        ],
        timeout=timeout,
        stream_output=True,
        on_stderr_line=handle_stderr_line,
    )
    if response is None:
        return None

    status_code = response.get("status_code")
    ok = bool(response.get("ok"))
    body = response.get("body")
    meta = response.get("meta")
    meta_obj = meta if isinstance(meta, dict) else {}
    aborted_for_part_limit = bool(meta_obj.get("aborted_for_part_limit"))
    print(f"[prompt] done http={status_code} ok={ok}")

    if not ok:
        if aborted_for_part_limit:
            print("[prompt] part limit reached during stream; ending current turn")
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
        if aborted_for_part_limit:
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


async def collect_turn_messages(
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


async def create_part_checkpoint(
    sandbox: modal.Sandbox,
    trajectory_id: str,
    part: int,
    changed_files_hint: list[str] | None = None,
    commit_before_hint: str | None = None,
) -> RepoCheckpoint:
    commit_before = (
        commit_before_hint
        if commit_before_hint is not None
        else await get_git_commit(sandbox)
    )
    changed_files = (
        [f for f in changed_files_hint if isinstance(f, str) and f]
        if changed_files_hint is not None
        else await get_changed_files(sandbox)
    )
    if not changed_files:
        return RepoCheckpoint(
            commit_before=commit_before,
            commit_after=commit_before,
            committed=False,
            changed_files=[],
        )

    # Re-read actual dirty state so hints from streamed events never cause
    # a spurious commit attempt when git is already clean.
    actual_changed_files = await get_changed_files(sandbox)
    if not actual_changed_files:
        return RepoCheckpoint(
            commit_before=commit_before,
            commit_after=commit_before,
            committed=False,
            changed_files=changed_files,
        )
    changed_files = actual_changed_files

    commit_message = f"part {part} checkpoint"
    exit_code, _, stderr = await sandbox_run(
        sandbox,
        "cd /workspace && git add -A && "
        f"git commit -m {shlex.quote(commit_message)}",
        quiet=True,
    )
    if exit_code != 0:
        print(f"[git] checkpoint commit failed on part {part}: {truncate_text(stderr, limit=400)}")
        return RepoCheckpoint(
            commit_before=commit_before,
            commit_after=commit_before,
            committed=False,
            changed_files=changed_files,
        )

    commit_after = await get_git_commit(sandbox)

    print(f"[git] committed part {part}: {commit_after} files={len(changed_files)}")
    return RepoCheckpoint(
        commit_before=commit_before,
        commit_after=commit_after,
        committed=True,
        changed_files=changed_files,
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
# Agent backends + sandbox setup
# ---------------------------------------------------------------------------


async def upload_environment_files(sandbox: modal.Sandbox) -> None:
    await upload_files_parallel(
        sandbox,
        environment_upload_items(),
        log_upload=True,
    )

    print(
        f"[setup] uploaded {len(ENVIRONMENT_PY_FILES)} py, "
        f"{len(ENVIRONMENT_C_FILES)} c, {len(ENVIRONMENT_TXT_FILES)} txt files"
    )


async def run_setup_script(sandbox: modal.Sandbox, agent: str) -> None:
    print(f"[setup] running setup.sh (agent={agent})...")

    async def handle_setup_line(line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        if stripped.startswith("[setup]") or stripped.startswith("[fixtures]"):
            print(stripped)
            return
        if stripped.startswith("ERROR:"):
            print(f"[setup] {stripped}")

    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        f"AGENT_KIND={shlex.quote(agent)} bash /tmp/upload/setup.sh",
        timeout=1800,
        on_stdout_line=handle_setup_line,
        on_stderr_line=handle_setup_line,
    )
    if exit_code != 0:
        print(f"[setup] FAILED:\n{stdout}\n{stderr}")
        raise RuntimeError(f"Setup failed (exit {exit_code})")
    print("[setup] done")


async def setup_sandbox_opencode(sandbox: modal.Sandbox, model: str, api_key: str) -> None:
    print(f"[setup] agent=opencode model={model}")
    config = OPENCODE_CONFIG.replace("MODEL_PLACEHOLDER", model)
    await upload_files_parallel(
        sandbox,
        [
            ("/tmp/upload/setup.sh", SETUP_SH),
            ("/tmp/upload/task_setup.sh", TASK_SANDBOX_SETUP_SH),
            ("/sandbox/mcp_server.py", MCP_SERVER),
            ("/sandbox/opencode_client.py", OPENCODE_CLIENT),
            ("/tmp/upload/opencode_api_key.txt", api_key),
            ("/workspace/opencode.jsonc", config),
            ("/workspace/.gitignore", WORKSPACE_GITIGNORE),
        ],
        log_upload=True,
    )
    await upload_environment_files(sandbox)
    await run_setup_script(sandbox, "opencode")


async def setup_sandbox_codex(
    sandbox: modal.Sandbox,
    model: str,
    api_key: str,
    auth_json: str | None = None,
) -> None:
    print(f"[setup] agent=codex model={model}")
    codex_config = CODEX_CONFIG_TOML.replace("MODEL_PLACEHOLDER", model)
    setup_uploads: list[tuple[str, str]] = [
        ("/tmp/upload/setup.sh", SETUP_SH),
        ("/tmp/upload/task_setup.sh", TASK_SANDBOX_SETUP_SH),
        ("/sandbox/mcp_server.py", MCP_SERVER),
        ("/sandbox/codex_client.py", CODEX_CLIENT),
        (f"{CODEX_HOME_DIR}/config.toml", codex_config),
        ("/workspace/.gitignore", WORKSPACE_GITIGNORE),
    ]
    if api_key:
        setup_uploads.append(("/tmp/upload/codex_api_key.txt", api_key))
    if auth_json:
        setup_uploads.append((f"{CODEX_HOME_DIR}/auth.json", auth_json))

    await upload_files_parallel(
        sandbox,
        setup_uploads,
        log_upload=True,
    )
    await upload_environment_files(sandbox)
    await run_setup_script(sandbox, "codex")


class OpenCodeBackend:
    name = "opencode"

    def resolve_model(self, model: str | None) -> str:
        return resolve_model(self.name, model)

    async def setup_sandbox(
        self,
        sandbox: modal.Sandbox,
        model: str,
        api_key: str,
        auth_json: str | None = None,
    ) -> None:
        await setup_sandbox_opencode(sandbox, model, api_key)

    async def create_session(self, sandbox: modal.Sandbox, trajectory_id: str) -> str | None:
        return await create_session(sandbox, title=f"trajectory-{trajectory_id}")

    async def ensure_authenticated(
        self,
        sandbox: modal.Sandbox,
        api_key: str,
        auth_json: str | None = None,
    ) -> None:
        await ensure_provider_connected(sandbox, api_key)

    async def run_turn(
        self,
        sandbox: modal.Sandbox,
        session_id: str,
        model: str,
        prompt_text: str,
        seen_message_ids: set[str],
        timeout: int,
        remaining_parts_budget: int,
        on_stream_part: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AgentTurnOutcome | None:
        response = await send_message_blocking(
            sandbox,
            session_id,
            prompt_text,
            timeout=timeout,
            remaining_parts_budget=remaining_parts_budget,
            on_stream_part=on_stream_part,
        )
        if response is None:
            return None
        session_objects, session_ids, new_messages = await collect_turn_messages(
            sandbox,
            session_id,
            seen_message_ids,
        )
        return AgentTurnOutcome(
            session_id=session_id,
            response=response,
            session_objects=session_objects,
            session_ids=session_ids,
            new_messages=new_messages,
        )

    async def collect_crash_messages(
        self,
        sandbox: modal.Sandbox,
        session_id: str,
        seen_message_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        return await collect_turn_messages(sandbox, session_id, seen_message_ids)


class CodexBackend:
    name = "codex"

    def __init__(self) -> None:
        self._api_key_file: str | None = None

    def resolve_model(self, model: str | None) -> str:
        return resolve_model(self.name, model)

    async def setup_sandbox(
        self,
        sandbox: modal.Sandbox,
        model: str,
        api_key: str,
        auth_json: str | None = None,
    ) -> None:
        self._api_key_file = "/tmp/upload/codex_api_key.txt" if api_key else None
        await setup_sandbox_codex(sandbox, model, api_key, auth_json)

    async def create_session(self, sandbox: modal.Sandbox, trajectory_id: str) -> str | None:
        return f"pending-{trajectory_id}"

    async def ensure_authenticated(
        self,
        sandbox: modal.Sandbox,
        api_key: str,
        auth_json: str | None = None,
    ) -> None:
        return None

    async def run_turn(
        self,
        sandbox: modal.Sandbox,
        session_id: str,
        model: str,
        prompt_text: str,
        seen_message_ids: set[str],
        timeout: int,
        remaining_parts_budget: int,
        on_stream_part: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AgentTurnOutcome | None:
        prompt_path = "/tmp/prompt.txt"
        await sandbox_write_file(sandbox, prompt_path, prompt_text, ensure_dir=False)
        args = [
            "chat-stream",
            "--session-id",
            session_id,
            "--text-file",
            prompt_path,
            "--model",
            model,
            "--max-parts",
            str(remaining_parts_budget),
        ]
        if self._api_key_file:
            args.extend(["--api-key-file", self._api_key_file])
        async def handle_stderr_line(line: str) -> None:
            if on_stream_part is None:
                return
            stripped = line.strip()
            if not stripped.startswith(TRACE_EVENT_PREFIX):
                return
            payload = stripped[len(TRACE_EVENT_PREFIX):].strip()
            if not payload:
                return
            try:
                event_obj = json.loads(payload)
            except json.JSONDecodeError:
                return
            if isinstance(event_obj, dict):
                await on_stream_part(event_obj)

        response = await run_codex_client(
            sandbox,
            args,
            timeout=timeout,
            stream_output=True,
            on_stderr_line=handle_stderr_line,
        )
        if response is None:
            return None
        if not response.get("ok"):
            print(f"[codex] turn failed: {truncate_text(str(response.get('error')), limit=800)}")
            return None
        body = response.get("body")
        if not isinstance(body, dict):
            print("[codex] missing body in response")
            return None

        updated_session_id = body.get("_session_id")
        effective_session_id = (
            updated_session_id
            if isinstance(updated_session_id, str) and updated_session_id
            else session_id
        )

        message_obj = body.get("_message")
        new_messages: list[dict[str, Any]] = []
        if isinstance(message_obj, dict):
            mid = message_id(message_obj)
            if mid and mid in seen_message_ids:
                pass
            else:
                if mid:
                    seen_message_ids.add(mid)
                new_messages.append(message_obj)
        if not new_messages:
            fallback_msg = {
                "info": {
                    "id": f"{effective_session_id}:{int(time.time() * 1000)}",
                    "role": "assistant",
                    "sessionID": effective_session_id,
                    "time": {"created": int(time.time() * 1000)},
                },
                "parts": body.get("parts", []),
            }
            fallback_mid = message_id(fallback_msg)
            if fallback_mid:
                seen_message_ids.add(fallback_mid)
            new_messages.append(fallback_msg)

        session_obj = {"id": effective_session_id, "provider": "codex"}
        return AgentTurnOutcome(
            session_id=effective_session_id,
            response=body,
            session_objects=[session_obj],
            session_ids=[effective_session_id],
            new_messages=new_messages,
        )

    async def collect_crash_messages(
        self,
        sandbox: modal.Sandbox,
        session_id: str,
        seen_message_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        return [], [session_id], []


def get_agent_backend(agent: str) -> AgentBackend:
    if agent == "opencode":
        return OpenCodeBackend()
    if agent == "codex":
        return CodexBackend()
    raise ValueError(f"Unsupported agent: {agent}")


# ---------------------------------------------------------------------------
# Stream callback
# ---------------------------------------------------------------------------


def make_stream_part_callback(
    *,
    sandbox: modal.Sandbox,
    trajectory_id: str,
    agent_trace: AgentTrace,
    tracker: SolveTracker,
    agent_name: str,
    resolved_model: str,
    effective_max_parts: int,
    part_counter: list[int],
    git_commit_ref: list[str | None],
    last_part_timestamp_ms_ref: list[int | None],
    turn_record: TurnRecord | None,
    session_id: str,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    async def on_stream_part(stream_event: dict[str, Any]) -> None:
        if turn_record is None:
            return
        if stream_event.get("event") != "part.completed":
            return
        if part_counter[0] >= effective_max_parts:
            return

        try:
            part_counter[0] += 1
            absolute_part = part_counter[0]
            raw_files = stream_event.get("files")
            files = (
                [f for f in raw_files if isinstance(f, str) and f]
                if isinstance(raw_files, list)
                else []
            )
            part_type_value = stream_event.get("part_type")
            part_type = part_type_value if isinstance(part_type_value, str) else None
            item_type_value = stream_event.get("item_type")
            item_type = item_type_value if isinstance(item_type_value, str) else None
            summary_value = stream_event.get("summary")
            summary = summary_value if isinstance(summary_value, str) and summary_value else None
            role_value = stream_event.get("role")
            role: Literal["assistant", "user"] = (
                role_value
                if role_value in {"assistant", "user"}
                else "assistant"
            )
            event_timestamp_ms_value = stream_event.get("timestamp_ms")
            event_timestamp_ms = (
                event_timestamp_ms_value
                if isinstance(event_timestamp_ms_value, int)
                else int(time.time() * 1000)
            )
            prev_ts = last_part_timestamp_ms_ref[0]
            duration_ms = (
                event_timestamp_ms - prev_ts
                if isinstance(prev_ts, int) and event_timestamp_ms >= prev_ts
                else None
            )
            last_part_timestamp_ms_ref[0] = event_timestamp_ms
            has_file_change = bool(stream_event.get("has_file_change"))
            changed_files = await get_changed_files(sandbox)
            detected_file_change = bool(changed_files)
            checkpoint: RepoCheckpoint | None = None
            should_checkpoint = has_file_change or detected_file_change
            if should_checkpoint:
                if not has_file_change:
                    print(
                        "[stream] corrected has_file_change=false with git dirty "
                        f"state on part {absolute_part}"
                    )
                checkpoint = await create_part_checkpoint(
                    sandbox=sandbox,
                    trajectory_id=trajectory_id,
                    part=absolute_part,
                    changed_files_hint=(changed_files or files),
                    commit_before_hint=git_commit_ref[0],
                )
                if (
                    has_file_change
                    and not checkpoint.committed
                    and not detected_file_change
                ):
                    print(
                        "[git] part signaled file change but git was clean; "
                        f"recorded metadata only on part {absolute_part}"
                    )
                if files and not checkpoint.changed_files:
                    checkpoint.changed_files = files
                git_commit_ref[0] = (
                    checkpoint.commit_after
                    or checkpoint.commit_before
                    or git_commit_ref[0]
                )

            part_envoi_calls: list[EnvoiCall] = []
            raw_test_call = stream_event.get("test_call")
            if isinstance(raw_test_call, dict):
                try:
                    part_envoi_calls.append(EnvoiCall(**raw_test_call))
                except Exception:
                    pass
            if part_envoi_calls:
                tracker.update(part_envoi_calls)

            part_record = PartRecord(
                trajectory_id=trajectory_id,
                session_id=session_id,
                agent=agent_name,
                part=absolute_part,
                timestamp=iso_from_epoch_ms(event_timestamp_ms),
                role=role,
                part_type=part_type,
                item_type=item_type,
                summary=summary,
                duration_ms=duration_ms,
                agent_model=resolved_model,
                git_commit=git_commit_ref[0],
                envoi_calls=part_envoi_calls,
                testing_state=tracker.snapshot(),
                repo_checkpoint=checkpoint,
            )
            turn_record.parts.append(part_record)
            agent_trace.parts.append(part_record)
            if turn_record.part_start is None:
                turn_record.part_start = absolute_part
            turn_record.part_end = absolute_part
            turn_record.git_commit = git_commit_ref[0]
            if checkpoint is not None:
                turn_record.repo_checkpoint = checkpoint
            save_agent_trace_snapshot(trajectory_id, agent_trace)
        except Exception as callback_err:
            print(f"[stream] failed to process live part event: {callback_err}")

    return on_stream_part


# ---------------------------------------------------------------------------
# End session
# ---------------------------------------------------------------------------


async def end_session(
    sandbox: modal.Sandbox,
    agent_trace: AgentTrace,
    part_count: int,
    turn_count: int,
    reason: Literal["solved", "part_limit", "timeout", "agent_error", "envoi_error"],
) -> None:
    print(f"[end] reason={reason} parts={part_count}")

    if part_count == 0 and turn_count == 0:
        print("[end] nothing to save (0 parts), skipping S3 upload")
        return

    final_commit = await get_git_commit(sandbox)
    trace_s3_uri = artifact_uri(agent_trace.trajectory_id, "agent_trace.json")
    bundle_s3_uri: str | None = None

    agent_trace.session_end = SessionEnd(
        reason=reason,
        total_parts=part_count,
        total_turns=turn_count,
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

    agent_trace.artifacts = {
        "agent_trace": trace_s3_uri,
        "repo_bundle": bundle_s3_uri,
    }
    save_agent_trace_snapshot(agent_trace.trajectory_id, agent_trace)

    print(
        f"[end] session ended: {reason}, {part_count} parts, commit={final_commit}"
    )


# ---------------------------------------------------------------------------
# Modal images
# ---------------------------------------------------------------------------


async def _run_trajectory_impl(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
) -> str:
    if trajectory_id is None:
        trajectory_id = str(uuid.uuid4())
    agent = (agent or DEFAULT_AGENT).strip().lower()

    effective_max_parts = max_parts
    backend = get_agent_backend(agent)
    resolved_model = backend.resolve_model(model)
    trace_s3_uri = artifact_uri(trajectory_id, "agent_trace.json")
    bundle_s3_uri = artifact_uri(trajectory_id, "repo.bundle")
    banner = "=" * 72
    print(banner)
    print(f"TRAJECTORY_ID: {trajectory_id}")
    print(f"TRACE_S3_URI: {trace_s3_uri}")
    print(f"BUNDLE_S3_URI: {bundle_s3_uri}")
    print(
        f"agent={agent} model={resolved_model} max_parts={effective_max_parts} "
        f"timeout={timeout_seconds}s message_timeout={message_timeout_seconds}s"
    )
    print(banner)

    agent_api_key = ""
    codex_auth_json: str | None = None
    if agent == "opencode":
        agent_api_key = os.environ.get("OPENCODE_API_KEY", "").strip()
        if not agent_api_key:
            raise RuntimeError("OPENCODE_API_KEY not set")
    else:
        env_codex_auth_b64 = os.environ.get("CODEX_AUTH_JSON_B64", "").strip()
        env_codex_auth_raw = os.environ.get("CODEX_AUTH_JSON", "").strip()
        if codex_auth_json_b64:
            decoded = decode_b64_to_text(codex_auth_json_b64, label="codex_auth_json_b64 arg")
            codex_auth_json = parse_codex_auth_json(decoded, label="codex_auth_json_b64 arg")
        elif env_codex_auth_b64:
            decoded = decode_b64_to_text(env_codex_auth_b64, label="CODEX_AUTH_JSON_B64")
            codex_auth_json = parse_codex_auth_json(decoded, label="CODEX_AUTH_JSON_B64")
        elif env_codex_auth_raw:
            codex_auth_json = parse_codex_auth_json(env_codex_auth_raw, label="CODEX_AUTH_JSON")

        agent_api_key = (
            os.environ.get("CODEX_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )
        if not codex_auth_json and not agent_api_key:
            raise RuntimeError(
                "No Codex credentials found. Provide one of: "
                "~/.codex/auth.json via --codex-auth-file, "
                "CODEX_AUTH_JSON_B64/CODEX_AUTH_JSON, or CODEX_API_KEY/OPENAI_API_KEY."
            )

    sandbox: modal.Sandbox | None = None
    agent_trace: AgentTrace | None = None
    turn_count = 0
    part_count = 0
    end_reason: str | None = None

    try:
        sandbox = await modal.Sandbox.create.aio(
            "bash",
            "-c",
            "sleep infinity",
            image=sandbox_image,
            timeout=timeout_seconds,
            app=app,
        )
        start_time = time.monotonic()

        # --- Setup ---
        await backend.setup_sandbox(sandbox, resolved_model, agent_api_key, codex_auth_json)

        session_id = await backend.create_session(sandbox, trajectory_id)
        if not session_id:
            raise RuntimeError(f"Failed to create session for agent={agent}")

        await backend.ensure_authenticated(sandbox, agent_api_key, codex_auth_json)

        agent_trace = AgentTrace(
            trajectory_id=trajectory_id,
            session_id=session_id,
            agent=agent,
            agent_model=resolved_model,
            started_at=datetime.now(UTC).isoformat(),
        )
        save_agent_trace_snapshot(trajectory_id, agent_trace)

        # --- Main loop: blocking message calls with part budget ---
        tracker = SolveTracker(list(TASK_REQUIRED_TEST_PATHS))
        seen_message_ids: set[str] = set()
        prompt_text = TASK_SYSTEM_PROMPT

        try:
            while part_count < effective_max_parts:
                elapsed = time.monotonic() - start_time
                if elapsed > timeout_seconds:
                    end_reason = "timeout"
                    break
                remaining_run_seconds = timeout_seconds - elapsed
                remaining_parts = max(1, effective_max_parts - part_count)
                if agent == "codex":
                    # For Codex, don't short-circuit long productive turns with the
                    # per-turn message timeout; only the overall run timeout applies.
                    turn_timeout_seconds = max(1, int(remaining_run_seconds))
                else:
                    turn_timeout_seconds = compute_turn_timeout_seconds(
                        remaining_parts=remaining_parts,
                        remaining_run_seconds=remaining_run_seconds,
                        message_timeout_seconds=message_timeout_seconds,
                    )

                banner = "=" * 60
                builtins.print(f"\n{banner}", flush=True)
                builtins.print(
                    f" TURN {turn_count + 1}  "
                    f"(part_count {part_count}/{effective_max_parts}, "
                    f"timeout {turn_timeout_seconds}s)",
                    flush=True,
                )
                builtins.print(banner, flush=True)

                turn_started_at = datetime.now(UTC).isoformat()
                per_call_parts_budget = remaining_parts
                previous_part_count = part_count
                streamed_parts = 0
                observed_parts = 0
                part_limit_abort = False
                git_commit = await get_git_commit(sandbox)
                turn_record: TurnRecord | None = None

                turn_count += 1
                turn_record = TurnRecord(
                    trajectory_id=trajectory_id,
                    session_id=session_id,
                    agent=agent,
                    turn=turn_count,
                    part_start=None,
                    part_end=None,
                    timestamp=turn_started_at,
                    agent_model=resolved_model,
                    prompt=prompt_text,
                    git_commit=git_commit,
                    repo_checkpoint=None,
                    parts=[],
                )
                agent_trace.turns.append(turn_record)

                stream_part_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None
                stream_part_counter: list[int] = [part_count]
                stream_git_commit_ref: list[str | None] = [git_commit]
                stream_last_part_ts_ref: list[int | None] = [None]
                stream_part_cb = make_stream_part_callback(
                    sandbox=sandbox,
                    trajectory_id=trajectory_id,
                    agent_trace=agent_trace,
                    tracker=tracker,
                    agent_name=agent,
                    resolved_model=resolved_model,
                    effective_max_parts=effective_max_parts,
                    part_counter=stream_part_counter,
                    git_commit_ref=stream_git_commit_ref,
                    last_part_timestamp_ms_ref=stream_last_part_ts_ref,
                    turn_record=turn_record,
                    session_id=session_id,
                )

                # Send message and BLOCK until agent finishes
                turn_outcome = await backend.run_turn(
                    sandbox=sandbox,
                    session_id=session_id,
                    model=resolved_model,
                    prompt_text=prompt_text,
                    seen_message_ids=seen_message_ids,
                    timeout=turn_timeout_seconds,
                    remaining_parts_budget=per_call_parts_budget,
                    on_stream_part=stream_part_cb,
                )
                part_count = stream_part_counter[0]
                git_commit = stream_git_commit_ref[0]

                if turn_outcome is None:
                    if turn_record is not None and not turn_record.parts:
                        agent_trace.turns.pop()
                    print("[progress] no response from agent")
                    await dump_sandbox_logs(sandbox, agent=agent)
                    end_reason = "agent_error"
                    break

                response = turn_outcome.response
                session_id = turn_outcome.session_id
                if agent_trace.session_id != session_id:
                    agent_trace.session_id = session_id

                # Log what the agent did
                info = response.get("info", {})
                parts = response.get("parts", [])
                response_message_id = info.get("id")
                print(f"[progress] response id={response_message_id} parts={len(parts)}")
                log_message_parts(response)

                session_ids = turn_outcome.session_ids
                new_messages = turn_outcome.new_messages
                print(f"[progress] new_messages={len(new_messages)} sessions={len(session_ids)}")

                # Extract envoi calls from newly observed messages only.
                new_envoi_calls: list[EnvoiCall] = []
                for msg in new_messages:
                    msg_parts = msg.get("parts", [])
                    if isinstance(msg_parts, list):
                        new_envoi_calls.extend(extract_envoi_calls(msg_parts))

                tracker.update(new_envoi_calls)

                stream_meta = response.get("_stream", {}) if isinstance(response, dict) else {}
                stream_meta_obj = stream_meta if isinstance(stream_meta, dict) else {}
                streamed_parts = int(stream_meta_obj.get("meaningful_parts_seen", 0) or 0)
                part_limit_abort = bool(stream_meta_obj.get("aborted_for_part_limit"))
                observed_parts = count_meaningful_parts(new_messages)

                new_parts = part_count - previous_part_count
                if turn_record is not None:
                    target_parts = min(max(observed_parts, streamed_parts), remaining_parts)
                    missing_stream_parts = max(0, target_parts - new_parts)
                    while missing_stream_parts > 0 and part_count < effective_max_parts:
                        part_count += 1
                        absolute_part = part_count
                        placeholder = PartRecord(
                            trajectory_id=trajectory_id,
                            session_id=session_id,
                            agent=agent,
                            part=absolute_part,
                            timestamp=datetime.now(UTC).isoformat(),
                            role="assistant",
                            part_type=None,
                            item_type=None,
                            summary="placeholder",
                            duration_ms=None,
                            agent_model=resolved_model,
                            git_commit=git_commit,
                            envoi_calls=[],
                            testing_state=tracker.snapshot(),
                            repo_checkpoint=None,
                        )
                        turn_record.parts.append(placeholder)
                        agent_trace.parts.append(placeholder)
                        if turn_record.part_start is None:
                            turn_record.part_start = absolute_part
                        turn_record.part_end = absolute_part
                        save_agent_trace_snapshot(trajectory_id, agent_trace)
                        missing_stream_parts -= 1
                    new_parts = part_count - previous_part_count

                    turn_record.session_id = session_id
                    for record in turn_record.parts:
                        record.session_id = session_id
                    if turn_record.parts:
                        last_part_record = turn_record.parts[-1]
                        existing_keys = {
                            tracker._call_key(call) for call in last_part_record.envoi_calls
                        }
                        for call in new_envoi_calls:
                            key = tracker._call_key(call)
                            if key not in existing_keys:
                                last_part_record.envoi_calls.append(call)
                                existing_keys.add(key)
                        last_part_record.testing_state = tracker.snapshot()
                        if turn_record.git_commit is None:
                            turn_record.git_commit = last_part_record.git_commit
                    else:
                        turn_record.git_commit = git_commit
                save_agent_trace_snapshot(trajectory_id, agent_trace)

                solved_count = len(tracker.solved)
                total_count = len(tracker.required_paths)
                print(
                    f"[progress] turn={turn_count} commit={git_commit} "
                    f"parts=+{new_parts} total={part_count}/{effective_max_parts} "
                    f"(observed_parts={observed_parts} streamed_parts={streamed_parts}) "
                    f"envoi_calls={len(new_envoi_calls)} "
                    f"solved={solved_count}/{total_count} "
                    f"started={turn_started_at}"
                )

                if tracker.is_fully_solved():
                    end_reason = "solved"
                    break

                if part_limit_abort and part_count >= effective_max_parts:
                    end_reason = "part_limit"
                    break

                if part_count >= effective_max_parts:
                    end_reason = "part_limit"
                    break

                # Build re-injection prompt for next turn
                unsolved = tracker.get_unsolved_paths()
                details: list[str] = []
                for p in unsolved[:10]:
                    call = tracker.get_latest_call_for_path(p)
                    if call and call.result:
                        details.append(f"  - {p}: {call.result.passed}/{call.result.total}")
                    else:
                        details.append(f"  - {p}: not run")

                prompt_text = build_followup_prompt(details)

            if end_reason is None:
                end_reason = "part_limit"

        except Exception as loop_err:
            print(f"[error] crash during main loop: {loop_err}")
            await dump_sandbox_logs(sandbox, agent=agent)
            end_reason = "agent_error"
            # Save whatever messages we have
            try:
                _, _, crash_new_messages = (
                    await backend.collect_crash_messages(
                        sandbox,
                        session_id,
                        seen_message_ids,
                    )
                )
                if crash_new_messages:
                    crash_record = TurnRecord(
                        trajectory_id=trajectory_id,
                        session_id=session_id,
                        agent=agent,
                        turn=turn_count + 1,
                        part_start=part_count + 1,
                        part_end=part_count,
                        timestamp=datetime.now(UTC).isoformat(),
                        agent_model=resolved_model,
                        prompt=prompt_text,
                        git_commit=await get_git_commit(sandbox),
                        parts=[],
                    )
                    agent_trace.turns.append(crash_record)
                    save_agent_trace_snapshot(trajectory_id, agent_trace)
                    print(f"[error] saved {len(crash_new_messages)} new messages before crash")
            except Exception:
                print("[error] could not save crash messages")

        # Always end the session and save final state
        await end_session(
            sandbox,
            agent_trace,
            part_count,
            turn_count,
            end_reason,
        )
        return trajectory_id

    except Exception as e:
        print(f"[error] {e}")
        if sandbox is not None and agent_trace is not None:
            try:
                await end_session(
                    sandbox,
                    agent_trace,
                    part_count,
                    turn_count,
                    "agent_error",
                )
            except Exception as end_err:
                print(f"[error] failed to finalize session after exception: {end_err}")
        return trajectory_id
    finally:
        if sandbox is not None:
            try:
                await sandbox.terminate.aio()
            except Exception:
                pass

    return trajectory_id


@app.function(
    timeout=14400,
    secrets=[modal.Secret.from_dotenv()],
    image=function_image,
)
async def run_trajectory(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
) -> str:
    return await _run_trajectory_impl(
        agent=agent,
        model=model,
        max_parts=max_parts,
        message_timeout_seconds=message_timeout_seconds,
        timeout_seconds=timeout_seconds,
        trajectory_id=trajectory_id,
        codex_auth_json_b64=codex_auth_json_b64,
    )


@app.function(
    timeout=14400,
    secrets=[modal.Secret.from_dotenv()],
    image=function_image,
    nonpreemptible=True,
    name="run_trajectory_non_preemptible",
)
async def run_trajectory_non_preemptible(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
) -> str:
    return await _run_trajectory_impl(
        agent=agent,
        model=model,
        max_parts=max_parts,
        message_timeout_seconds=message_timeout_seconds,
        timeout_seconds=timeout_seconds,
        trajectory_id=trajectory_id,
        codex_auth_json_b64=codex_auth_json_b64,
    )


def get_non_preemptible_runner() -> Any:
    return run_trajectory_non_preemptible


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


@app.local_entrypoint()
async def main(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    non_preemptible: bool = True,
    trajectory_id: str | None = None,
    codex_auth_file: str = "~/.codex/auth.json",
) -> None:
    normalized_agent = (agent or DEFAULT_AGENT).strip().lower()
    codex_auth_json_b64: str | None = None
    if normalized_agent == "codex" and codex_auth_file.strip():
        codex_auth_json_b64 = load_local_codex_auth_json_b64(codex_auth_file.strip())
        if codex_auth_json_b64:
            print(f"[auth] loaded codex auth from {Path(codex_auth_file).expanduser()}")
        else:
            print(f"[auth] no codex auth file found at {Path(codex_auth_file).expanduser()}")

    runner = get_non_preemptible_runner() if non_preemptible else run_trajectory
    try:
        result = await runner.remote.aio(
            agent=normalized_agent,
            model=model,
            max_parts=max_parts,
            message_timeout_seconds=message_timeout_seconds,
            trajectory_id=trajectory_id,
            codex_auth_json_b64=codex_auth_json_b64,
        )
        print(f"Completed trajectory: {result}")
    except Exception as e:
        message = str(e).strip()
        if not message:
            message = "remote run stopped or failed"
        print(f"[error] {message}")
