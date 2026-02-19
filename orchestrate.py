"""
Main orchestrator for envoi-trace.

Single-file version with all modules inlined for Modal.

Usage:
    modal run orchestrate.py
"""

from __future__ import annotations

import asyncio
import base64
import builtins
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import boto3
import modal
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

app = modal.App("envoi-trace")

DEFAULT_MODEL = "opencode/claude-sonnet-4"


def ts() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


def tprint(*args: Any, **kwargs: Any) -> None:
    if "flush" not in kwargs:
        kwargs["flush"] = True
    builtins.print(f"[{ts()}]", *args, **kwargs)


print = tprint

PROMPT = (Path(__file__).parent / "prompts" / "system.txt").read_text()
SETUP_SH = (Path(__file__).parent / "sandbox" / "setup.sh").read_text()
MCP_SERVER = (Path(__file__).parent / "sandbox" / "mcp_server.py").read_text()
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


def resolve_model(model: str) -> str:
    if "/" in model:
        return model
    return f"opencode/{model}"


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, val in value.items():
            if isinstance(key, str) and any(
                token in key.lower() for token in ["key", "token", "secret", "password"]
            ):
                redacted[key] = "***redacted***" if val else val
            else:
                redacted[key] = redact_secrets(val)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def truncate_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def parse_http_status(output: str) -> tuple[str, int | None]:
    if "HTTP_STATUS:" not in output:
        return output, None
    body, status_part = output.rsplit("HTTP_STATUS:", 1)
    status_line = status_part.strip().splitlines()[0] if status_part.strip() else ""
    try:
        status = int(status_line)
    except ValueError:
        status = None
    return body.rstrip(), status


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TestCase(BaseModel):
    name: str
    passed: bool
    duration_ms: int
    stderr: str | None = None


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
    reason: Literal["solved", "turn_limit", "timeout", "agent_error", "envoi_error"]
    total_turns: int
    final_git_commit: str | None = None


class TurnRecord(BaseModel):
    trajectory_id: str
    session_id: str
    turn: int | None
    timestamp: str
    agent_model: str
    git_commit: str | None = None
    message_id: str | None = None
    envoi_calls: list[EnvoiCall] = Field(default_factory=list)
    session_end: SessionEnd | None = None


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
# Message parsing helpers
# ---------------------------------------------------------------------------


def extract_envoi_calls(message_parts: list[dict[str, Any]]) -> list[EnvoiCall]:
    calls: list[EnvoiCall] = []
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
                        data = json.loads(content)
                        calls.append(EnvoiCall(**data))
                    except json.JSONDecodeError:
                        pass
        if part.get("type") == "tool" and part.get("tool") == "run_tests":
            state = part.get("state", {})
            if state.get("status") == "completed":
                output = state.get("output") or state.get("metadata", {}).get("output") or ""
                try:
                    if isinstance(output, str):
                        data = json.loads(output)
                    elif isinstance(output, dict):
                        data = output
                    else:
                        data = json.loads(str(output))
                    calls.append(EnvoiCall(**data))
                except Exception:
                    pass
    return calls


def has_tool_calls(message_parts: list[dict[str, Any]]) -> bool:
    for part in message_parts:
        if part.get("type") in {"tool_use", "tool"}:
            return True
    return False


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


def model_to_json(model: BaseModel) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json()
    if hasattr(model, "json"):
        return model.json()
    if hasattr(model, "dict"):
        return json.dumps(model.dict())
    return json.dumps(model)


def append_jsonl_record(trajectory_id: str, record: TurnRecord) -> None:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/trajectory.jsonl"
    line = model_to_json(record) + "\n"
    try:
        existing = s3.get_object(Bucket=bucket, Key=key)
        existing_data = existing["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            existing_data = b""
        else:
            raise
    new_data = existing_data + line.encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=new_data)
    print(f"[s3] appended trajectory record to s3://{bucket}/{key}")


def upload_file(trajectory_id: str, filename: str, data: bytes) -> str:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/{filename}"
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    return f"s3://{bucket}/{key}"


# ---------------------------------------------------------------------------
# Modal images
# ---------------------------------------------------------------------------

function_image = (
    modal.Image.debian_slim()
    .pip_install("boto3", "pydantic")
    .add_local_dir(
        Path(__file__).parent / "prompts",
        remote_path="/root/prompts",
    )
    .add_local_dir(
        Path(__file__).parent / "sandbox",
        remote_path="/root/sandbox",
    )
    .add_local_dir(
        Path(__file__).parent / "environment",
        remote_path="/root/environment",
    )
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
        "pydantic>=2.0.0",
        "mcp>=1.0.0",
    )
    .run_commands(
        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
    )
)


# ---------------------------------------------------------------------------
# Sandbox helpers (using Modal's sandbox.exec / sandbox.open APIs)
# ---------------------------------------------------------------------------


async def sandbox_run(
    sandbox: modal.Sandbox,
    cmd: str,
    timeout: int = 60,
    quiet: bool = False,
) -> tuple[int, str, str]:
    """Execute a command inside the sandbox and return (exit_code, stdout, stderr)."""
    start = time.monotonic()
    if not quiet:
        print(f"[sandbox_run] start cmd={cmd[:200]}")
    proc = await sandbox.exec.aio("bash", "-c", cmd)
    stdout = ""
    stderr = ""
    async for chunk in proc.stdout:
        stdout += chunk
    async for chunk in proc.stderr:
        stderr += chunk
    await proc.wait.aio()
    exit_code = proc.returncode or 0
    duration = time.monotonic() - start
    if not quiet:
        print(
            f"[sandbox_run] done exit={exit_code} duration={duration:.2f}s "
            f"stdout_len={len(stdout)} stderr_len={len(stderr)}"
        )
    if exit_code != 0:
        print(f"[sandbox_run] exit_code={exit_code}")
        if stderr:
            print(f"[sandbox_run] stderr:\n{stderr}")
    return exit_code, stdout, stderr


async def sandbox_write_file(
    sandbox: modal.Sandbox,
    path: str,
    content: str,
    ensure_dir: bool = True,
) -> None:
    """Write a file inside the sandbox using Modal's filesystem API."""
    if ensure_dir:
        parent = str(Path(path).parent)
        await sandbox_run(sandbox, f"mkdir -p '{parent}'")
    async with await sandbox.open.aio(path, "w") as f:
        await f.write.aio(content)


async def sandbox_write_bytes(
    sandbox: modal.Sandbox,
    path: str,
    data: bytes,
    ensure_dir: bool = True,
) -> None:
    """Write binary data to a file inside the sandbox."""
    if ensure_dir:
        parent = str(Path(path).parent)
        await sandbox_run(sandbox, f"mkdir -p '{parent}'")
    async with await sandbox.open.aio(path, "wb") as f:
        await f.write.aio(data)


# ---------------------------------------------------------------------------
# OpenCode API helpers (via curl inside the sandbox)
# ---------------------------------------------------------------------------


def summarize_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    tail = messages[-5:]
    tail_roles = [m.get("info", {}).get("role") for m in tail]
    tail_ids = [m.get("info", {}).get("id") for m in tail]
    last = messages[-1] if messages else None
    return {
        "count": len(messages),
        "last_role": last.get("info", {}).get("role") if last else None,
        "last_id": last.get("info", {}).get("id") if last else None,
        "tail_roles": tail_roles,
        "tail_ids": tail_ids,
    }


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
    info = message.get("info", {})
    role = info.get("role", "unknown")
    msg_id = info.get("id", "unknown")
    provider = info.get("providerID") or info.get("model", {}).get("providerID")
    model_id = info.get("modelID") or info.get("model", {}).get("modelID")
    parts = message.get("parts", [])
    print(f"[msg] role={role} id={msg_id} provider={provider} model={model_id} parts={len(parts)}")
    if not parts:
        print("[msg] no parts")
        return
    for idx, part in enumerate(parts):
        ptype = part.get("type", "unknown")
        if ptype == "text":
            text = part.get("text", "")
            print(f"[msg] part[{idx}] text: {truncate_text(text, limit=500)}")
        elif ptype == "tool_use":
            name = part.get("name", "?")
            status = part.get("status", "")
            tool_id = part.get("id", "")
            summary = summarize_tool_input(name, part.get("input", {}))
            print(f"[msg] part[{idx}] tool_use: {name} status={status} id={tool_id} {summary}")
        elif ptype == "tool":
            name = part.get("tool", "?")
            call_id = part.get("callID", "")
            state = part.get("state", {})
            status = state.get("status", "")
            summary = summarize_tool_input(name, state.get("input", {}))
            output = state.get("output") or state.get("metadata", {}).get("output") or ""
            output_preview = truncate_text(str(output), limit=300) if output else ""
            print(f"[msg] part[{idx}] tool: {name} status={status} callID={call_id} {summary}")
            if output_preview:
                print(f"[msg] part[{idx}] tool_output: {output_preview}")
        elif ptype == "tool_result":
            status = part.get("status", "")
            tool_use_id = part.get("tool_use_id", "")
            content = part.get("content", "")
            print(
                f"[msg] part[{idx}] tool_result: status={status} tool_use_id={tool_use_id} "
                f"{truncate_text(str(content), limit=500)}"
            )
        elif ptype == "patch":
            files = part.get("files", [])
            print(f"[msg] part[{idx}] patch files={files}")
        elif ptype in {"step-start", "step-finish"}:
            reason = part.get("reason", "")
            print(f"[msg] part[{idx}] {ptype} reason={reason}")
        else:
            print(f"[msg] part[{idx}] {ptype}: {truncate_text(json.dumps(part), limit=500)}")


def message_signature(message: dict[str, Any]) -> str:
    parts = message.get("parts", [])
    sig: list[tuple[Any, ...]] = []
    for part in parts:
        ptype = part.get("type", "unknown")
        if ptype == "text":
            text = part.get("text", "")
            sig.append(("text", len(text)))
        elif ptype == "tool":
            state = part.get("state", {})
            output = state.get("output") or state.get("metadata", {}).get("output") or ""
            sig.append(
                (
                    "tool",
                    part.get("tool"),
                    state.get("status"),
                    len(str(output)),
                )
            )
        elif ptype == "tool_use":
            sig.append(("tool_use", part.get("name"), part.get("status")))
        elif ptype == "tool_result":
            content = part.get("content", "")
            sig.append(("tool_result", part.get("status"), len(str(content))))
        elif ptype == "patch":
            files = part.get("files", [])
            sig.append(("patch", len(files)))
        else:
            sig.append((ptype, part.get("id")))
    return json.dumps(sig, sort_keys=True)


def find_messages_after(
    messages: list[dict[str, Any]],
    last_message_id: str | None,
) -> list[dict[str, Any]]:
    if last_message_id is None:
        return messages
    seen = False
    new_msgs: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("info", {}).get("id") == last_message_id:
            seen = True
            continue
        if seen:
            new_msgs.append(msg)
    if not seen:
        return messages
    return new_msgs


async def debug_endpoint_json(
    sandbox: modal.Sandbox,
    label: str,
    url: str,
    limit: int = 4000,
) -> None:
    print(f"[debug] {label} -> {url}")
    exit_code, stdout, stderr = await sandbox_run(
        sandbox, f"curl -sS -w '\nHTTP_STATUS:%{{http_code}}' {url}", timeout=15
    )
    body, status = parse_http_status(stdout)
    if exit_code != 0:
        print(f"[debug] {label} exit_code={exit_code}")
    if status is not None:
        print(f"[debug] {label} http_status={status}")
    if stderr:
        print(f"[debug] {label} stderr:\n{stderr}")
    if not body.strip():
        print(f"[debug] {label} no output")
        return
    try:
        data = json.loads(body)
        data = redact_secrets(data)
        text = json.dumps(data, indent=2)
    except json.JSONDecodeError:
        text = body
    print(truncate_text(text, limit=limit))


async def get_provider_connected(sandbox: modal.Sandbox) -> list[str]:
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        "curl -sS -w '\nHTTP_STATUS:%{http_code}' http://localhost:4096/provider",
        timeout=15,
    )
    body, status = parse_http_status(stdout)
    if exit_code != 0 or (status is not None and status >= 400):
        print(f"[provider] fetch failed exit={exit_code} status={status}")
        if stderr:
            print(f"[provider] stderr:\n{stderr}")
        return []
    try:
        data = json.loads(body) if body.strip() else {}
        connected = data.get("connected", [])
        print(f"[provider] connected={connected}")
        return connected
    except json.JSONDecodeError:
        print("[provider] failed to parse JSON")
        return []


async def ensure_opencode_auth(sandbox: modal.Sandbox, api_key: str) -> bool:
    if not api_key:
        print("[auth] OPENCODE_API_KEY missing, cannot set auth")
        return False
    payload = json.dumps({"apiKey": api_key})
    await sandbox_write_file(sandbox, "/tmp/opencode_auth.json", payload)
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        "curl -sS -w '\nHTTP_STATUS:%{http_code}' -X PUT "
        "http://localhost:4096/auth/opencode "
        "-H 'Content-Type: application/json' -d @/tmp/opencode_auth.json",
        timeout=15,
    )
    body, status = parse_http_status(stdout)
    print(f"[auth] PUT /auth/opencode exit={exit_code} status={status}")
    if stderr:
        print(f"[auth] stderr:\n{stderr}")
    if body.strip():
        print(truncate_text(body, limit=1000))
    return exit_code == 0 and status is not None and 200 <= status < 300


async def get_messages(
    sandbox: modal.Sandbox,
    session_id: str,
    quiet: bool = False,
) -> list[dict[str, Any]]:
    _, stdout, _ = await sandbox_run(
        sandbox,
        f"curl -sS -w '\nHTTP_STATUS:%{{http_code}}' http://localhost:4096/session/{session_id}/message",
        timeout=30,
        quiet=quiet,
    )
    body, status = parse_http_status(stdout)
    if status is not None and status >= 400:
        print(f"[get_messages] http_status={status}")
    try:
        data = json.loads(body) if body.strip() else []
        if not quiet:
            summary = summarize_messages(data)
            print(
                f"[get_messages] count={summary['count']} last_role={summary['last_role']} "
                f"last_id={summary['last_id']} tail_roles={summary['tail_roles']}"
            )
        return data
    except json.JSONDecodeError:
        print("[get_messages] failed to parse JSON")
        return []


async def get_session_status(
    sandbox: modal.Sandbox,
    session_id: str,
    quiet: bool = False,
) -> str:
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        "curl -sS -w '\nHTTP_STATUS:%{http_code}' http://localhost:4096/session/status",
        timeout=10,
        quiet=quiet,
    )
    body, status = parse_http_status(stdout)
    if exit_code != 0 or (status is not None and status >= 400):
        print(f"[session_status] fetch failed exit={exit_code} status={status}")
        if stderr:
            print(f"[session_status] stderr:\n{stderr}")
        return "unknown"
    try:
        data = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError:
        print("[session_status] failed to parse JSON")
        return "unknown"
    state = data.get(session_id, {}).get("type", "idle")
    if not quiet:
        print(f"[session_status] {session_id} -> {state}")
    return state


async def wait_for_agent_idle(
    sandbox: modal.Sandbox,
    session_id: str,
    last_message_id: str | None,
    poll_interval: int = 3,
    per_turn_timeout: int = 180,
) -> tuple[list[dict[str, Any]], str | None, str, bool]:
    start = time.monotonic()
    poll_count = 0
    status = "unknown"
    last_status = None
    timed_out = False
    current_last_id = last_message_id
    message_signatures: dict[str, str] = {}
    turn_messages: dict[str, dict[str, Any]] = {}
    last_change_time = start
    last_change_desc = "start"
    last_heartbeat = start
    heartbeat_interval = 15

    print(
        f"[wait] starting wait_for_agent_idle poll_interval={poll_interval}s "
        f"per_turn_timeout={per_turn_timeout}s last_message_id={last_message_id}"
    )

    while True:
        await asyncio.sleep(poll_interval)
        poll_count += 1

        status = await get_session_status(sandbox, session_id, quiet=True)
        if status != last_status:
            print(f"[wait] status change {last_status} -> {status}")
            last_status = status
            last_change_time = time.monotonic()
            last_change_desc = f"status {status}"

        if not await is_opencode_healthy(sandbox, quiet=True):
            print("[wait] OpenCode unhealthy during wait")
            break

        messages = await get_messages(sandbox, session_id, quiet=True)
        if messages:
            current_last_id = messages[-1].get("info", {}).get("id", current_last_id)
        changed_messages: list[dict[str, Any]] = []
        for msg in messages:
            msg_id = msg.get("info", {}).get("id", "")
            if not msg_id:
                continue
            sig = message_signature(msg)
            if message_signatures.get(msg_id) != sig:
                message_signatures[msg_id] = sig
                changed_messages.append(msg)
                turn_messages[msg_id] = msg

        if changed_messages:
            print(f"[wait] message updates={len(changed_messages)} total_messages={len(messages)}")
            for msg in changed_messages:
                log_message_parts(msg)
            current_last_id = messages[-1].get("info", {}).get("id", current_last_id)
            last_change_time = time.monotonic()
            last_change_desc = f"{len(changed_messages)} update(s)"

        elapsed = time.monotonic() - start
        if time.monotonic() - last_heartbeat >= heartbeat_interval:
            since_change = time.monotonic() - last_change_time
            print(
                f"[wait] heartbeat elapsed={elapsed:.0f}s status={status} "
                f"messages={len(messages)} last_change={since_change:.0f}s ago "
                f"({last_change_desc})"
            )
            last_heartbeat = time.monotonic()

        if poll_count % 10 == 0:
            await tail_sandbox_logs(sandbox)

        if status != "busy":
            break
        if elapsed > per_turn_timeout:
            print(f"[wait] per-turn timeout after {per_turn_timeout}s")
            timed_out = True
            break

    messages = await get_messages(sandbox, session_id, quiet=True)
    if messages:
        current_last_id = messages[-1].get("info", {}).get("id", current_last_id)
    changed_messages = []
    for msg in messages:
        msg_id = msg.get("info", {}).get("id", "")
        if not msg_id:
            continue
        sig = message_signature(msg)
        if message_signatures.get(msg_id) != sig:
            message_signatures[msg_id] = sig
            changed_messages.append(msg)
            turn_messages[msg_id] = msg
    if changed_messages:
        print(f"[wait] final message updates={len(changed_messages)}")
        for msg in changed_messages:
            log_message_parts(msg)
        current_last_id = messages[-1].get("info", {}).get("id", current_last_id)

    return list(turn_messages.values()), current_last_id, status, timed_out


async def send_user_message(
    sandbox: modal.Sandbox,
    session_id: str,
    message: str,
    model: str,
) -> None:
    payload = json.dumps({"parts": [{"type": "text", "text": message}]})
    await sandbox_write_file(sandbox, "/tmp/msg_payload.json", payload)
    print(f"[send_user_message] sending prompt_async model={model}")
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        f"curl -sS -w '\nHTTP_STATUS:%{{http_code}}' -X POST "
        f"http://localhost:4096/session/{session_id}/prompt_async "
        f"-H 'Content-Type: application/json' -d @/tmp/msg_payload.json",
        timeout=120,
    )
    body, status = parse_http_status(stdout)
    print(
        f"[send_user_message] exit={exit_code} http_status={status} "
        f"body_len={len(body)} stderr_len={len(stderr)}"
    )
    if body.strip():
        print(truncate_text(body, limit=2000))
    if exit_code != 0 or (status is not None and status >= 400):
        await send_blocking_message_background(sandbox, session_id, "/tmp/msg_payload.json")


async def create_session(sandbox: modal.Sandbox, title: str = "C Compiler Build") -> str | None:
    payload = json.dumps({"title": title})
    await sandbox_write_file(sandbox, "/tmp/create_session.json", payload)
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        "curl -sS -w '\nHTTP_STATUS:%{http_code}' -X POST http://localhost:4096/session "
        "-H 'Content-Type: application/json' -d @/tmp/create_session.json",
        timeout=30,
    )
    body, status = parse_http_status(stdout)
    print(
        f"[create_session] exit={exit_code} http_status={status} "
        f"body_len={len(body)} stderr_len={len(stderr)}"
    )
    if body.strip():
        print(truncate_text(body, limit=2000))
    if exit_code != 0 or (status is not None and status >= 400):
        return None
    try:
        data = json.loads(body)
        return data.get("id")
    except json.JSONDecodeError:
        print("[create_session] failed to parse JSON")
        return None


async def send_initial_prompt(
    sandbox: modal.Sandbox,
    session_id: str,
    prompt: str,
    model: str,
) -> None:
    payload = json.dumps({"parts": [{"type": "text", "text": prompt}]})
    await sandbox_write_file(sandbox, "/tmp/prompt_payload.json", payload)
    print(f"[send_initial_prompt] sending prompt_async model={model}")
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        f"curl -sS -w '\nHTTP_STATUS:%{{http_code}}' -X POST "
        f"http://localhost:4096/session/{session_id}/prompt_async "
        f"-H 'Content-Type: application/json' -d @/tmp/prompt_payload.json",
        timeout=120,
    )
    body, status = parse_http_status(stdout)
    print(
        f"[send_initial_prompt] exit={exit_code} http_status={status} "
        f"body_len={len(body)} stderr_len={len(stderr)}"
    )
    if body.strip():
        print(truncate_text(body, limit=2000))
    if exit_code != 0 or (status is not None and status >= 400):
        await send_blocking_message_background(sandbox, session_id, "/tmp/prompt_payload.json")


async def send_blocking_message_background(
    sandbox: modal.Sandbox,
    session_id: str,
    payload_path: str,
) -> None:
    log_path = f"/tmp/opencode_message_{session_id}.log"
    script_path = f"/tmp/send_message_{session_id}.sh"
    script = (
        "#!/bin/bash\n"
        f"curl -sS -X POST http://localhost:4096/session/{session_id}/message "
        f"-H 'Content-Type: application/json' -d @{payload_path} "
        f"> {log_path} 2>&1\n"
    )
    await sandbox_write_file(sandbox, script_path, script)
    await sandbox_run(sandbox, f"nohup bash {script_path} >/dev/null 2>&1 &", timeout=10)
    print(f"[send_blocking_message_background] started, log at {log_path}")


def detect_new_turn(
    messages: list[dict[str, Any]], last_message_id: str | None
) -> dict[str, Any] | None:
    for msg in reversed(messages):
        info = msg.get("info", {})
        if info.get("role") != "assistant":
            continue
        msg_id = info.get("id")
        if msg_id == last_message_id:
            return None
        parts = msg.get("parts", [])
        pending = any(p.get("status") == "pending" for p in parts if p.get("type") == "tool_use")
        if not pending:
            return msg
    return None


async def is_opencode_healthy(sandbox: modal.Sandbox, quiet: bool = False) -> bool:
    _, stdout, _ = await sandbox_run(
        sandbox, "curl -sf http://localhost:4096/global/health", timeout=10, quiet=quiet
    )
    try:
        data = json.loads(stdout)
        healthy = data.get("healthy", False)
        if not quiet:
            print(f"[opencode_health] healthy={healthy}")
        return healthy
    except json.JSONDecodeError:
        if not quiet:
            print("[opencode_health] failed to parse JSON")
        return False


async def get_git_commit(sandbox: modal.Sandbox) -> str | None:
    _, stdout, _ = await sandbox_run(
        sandbox, "cd /workspace && git rev-parse HEAD 2>/dev/null || echo 'none'", timeout=10
    )
    commit = stdout.strip()
    if commit == "none" or not commit:
        return None
    return commit[:16]


async def check_git_has_changes(sandbox: modal.Sandbox) -> bool:
    _, stdout, _ = await sandbox_run(sandbox, "cd /workspace && git status --porcelain", timeout=10)
    return bool(stdout.strip())


async def tail_sandbox_logs(sandbox: modal.Sandbox) -> None:
    print("[logs] tailing /tmp/envoi.log and /tmp/opencode.log")
    _, stdout, _ = await sandbox_run(
        sandbox,
        "tail -n 20 /tmp/envoi.log /tmp/opencode.log 2>/dev/null || true",
        timeout=10,
    )
    if stdout.strip():
        print(stdout)
    _, msg_logs, _ = await sandbox_run(
        sandbox,
        "ls -1 /tmp/opencode_message_*.log 2>/dev/null | tail -n 3 | xargs -I{} sh -c 'echo \"==> {} <==\"; tail -n 10 {}' || true",
        timeout=10,
    )
    if msg_logs.strip():
        print(msg_logs)
    _, proc_out, _ = await sandbox_run(
        sandbox,
        "if [ -f /tmp/envoi.pid ]; then ps -o pid,etimes,cmd -p $(cat /tmp/envoi.pid); fi; "
        "if [ -f /tmp/opencode.pid ]; then ps -o pid,etimes,cmd -p $(cat /tmp/opencode.pid); fi;",
        timeout=10,
    )
    if proc_out.strip():
        print("[logs] process status:\n" + proc_out)


# ---------------------------------------------------------------------------
# Main trajectory function
# ---------------------------------------------------------------------------


@app.function(
    timeout=14400,
    secrets=[modal.Secret.from_dotenv()],
    image=function_image,
)
async def run_trajectory(
    model: str = DEFAULT_MODEL,
    max_turns: int = 1000,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
) -> str:
    if trajectory_id is None:
        trajectory_id = str(uuid.uuid4())

    print(f"Starting trajectory {trajectory_id}")
    resolved_model = resolve_model(model)
    print(
        f"Model: {model} (resolved: {resolved_model}), max_turns: {max_turns}, "
        f"timeout: {timeout_seconds}s"
    )

    opencode_api_key = os.environ.get("OPENCODE_API_KEY", "")
    if opencode_api_key:
        print(f"[setup] OPENCODE_API_KEY length={len(opencode_api_key)}")
    else:
        print("[setup] WARNING: OPENCODE_API_KEY is empty")

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
        await setup_sandbox(sandbox, resolved_model, opencode_api_key)

        session_id = await create_session(sandbox, title=f"trajectory-{trajectory_id}")
        if not session_id:
            raise RuntimeError("Failed to create OpenCode session")

        print(f"OpenCode session: {session_id}")
        await debug_endpoint_json(sandbox, "config", "http://localhost:4096/config")
        await debug_endpoint_json(sandbox, "provider", "http://localhost:4096/provider")

        connected = await get_provider_connected(sandbox)
        if "opencode" not in connected:
            print("[provider] opencode not connected, attempting auth")
            await ensure_opencode_auth(sandbox, opencode_api_key)
            await debug_endpoint_json(sandbox, "provider", "http://localhost:4096/provider")
            connected = await get_provider_connected(sandbox)

        await send_initial_prompt(sandbox, session_id, PROMPT, resolved_model)
        await tail_sandbox_logs(sandbox)

        await debug_endpoint_json(sandbox, "session_status", "http://localhost:4096/session/status")
        await debug_endpoint_json(sandbox, "session", f"http://localhost:4096/session/{session_id}")
        await debug_endpoint_json(
            sandbox, "messages", f"http://localhost:4096/session/{session_id}/message"
        )

        tracker = SolveTracker()
        last_message_id: str | None = None
        turn_count = 0
        TURN_POLL_SECONDS = 3
        TURN_TIMEOUT_SECONDS = 180

        while True:
            elapsed_total = time.monotonic() - start_time
            print(
                f"[loop] start turn={turn_count} elapsed_total={int(elapsed_total)}s "
                f"max_turns={max_turns}"
            )
            print("[loop] waiting for agent idle; S3 write happens after turn completes")

            if elapsed_total > timeout_seconds:
                await end_session(
                    sandbox,
                    trajectory_id,
                    session_id,
                    turn_count,
                    "timeout",
                    tracker,
                    last_message_id,
                    resolved_model,
                )
                return trajectory_id

            new_messages, last_message_id, status, timed_out = await wait_for_agent_idle(
                sandbox,
                session_id,
                last_message_id,
                poll_interval=TURN_POLL_SECONDS,
                per_turn_timeout=TURN_TIMEOUT_SECONDS,
            )

            if timed_out and status == "busy":
                print("[loop] per-turn timeout while busy; ending session")
                await end_session(
                    sandbox,
                    trajectory_id,
                    session_id,
                    turn_count,
                    "timeout",
                    tracker,
                    last_message_id,
                    resolved_model,
                )
                return trajectory_id

            turn_count += 1

            all_envoi_calls: list[EnvoiCall] = []
            all_parts: list[dict[str, Any]] = []
            for msg in new_messages:
                parts = msg.get("parts", [])
                all_parts.extend(parts)
                all_envoi_calls.extend(extract_envoi_calls(parts))

            if not new_messages:
                print("[turn] no new messages collected this turn")

            tracker.update(all_envoi_calls)

            git_commit = await get_git_commit(sandbox)

            record = TurnRecord(
                trajectory_id=trajectory_id,
                session_id=session_id,
                turn=turn_count,
                timestamp=datetime.now(UTC).isoformat(),
                agent_model=resolved_model,
                git_commit=git_commit,
                message_id=last_message_id,
                envoi_calls=all_envoi_calls,
            )
            append_jsonl_record(trajectory_id, record)

            print(
                f"[turn] completed turn={turn_count} messages={len(new_messages)} "
                f"parts={len(all_parts)} envoi_calls={len(all_envoi_calls)} "
                f"commit={git_commit} status={status}"
            )

            if tracker.is_fully_solved():
                await end_session(
                    sandbox,
                    trajectory_id,
                    session_id,
                    turn_count,
                    "solved",
                    tracker,
                    last_message_id,
                    resolved_model,
                )
                return trajectory_id

            if turn_count >= max_turns:
                await end_session(
                    sandbox,
                    trajectory_id,
                    session_id,
                    turn_count,
                    "turn_limit",
                    tracker,
                    last_message_id,
                    resolved_model,
                )
                return trajectory_id

            failed_paths = tracker.get_unsolved_paths()
            details: list[str] = []
            for p in failed_paths[:5]:
                call = tracker.get_latest_call_for_path(p)
                if call and call.result:
                    details.append(f"  - {p}: {call.result.passed}/{call.result.total}")
                else:
                    details.append(f"  - {p}: not run")

            reinject_msg = "Continue working on the compiler. Run tests and pass ALL suites."
            if details:
                reinject_msg += "\n\nCurrent test status:\n" + "\n".join(details)

            print("[turn] sending continue prompt")
            await send_user_message(sandbox, session_id, reinject_msg, resolved_model)

    except Exception as e:
        print(f"Error: {e}")
        raise
    finally:
        try:
            await sandbox.terminate.aio()
        except Exception:
            pass

    return trajectory_id


# ---------------------------------------------------------------------------
# Sandbox setup
# ---------------------------------------------------------------------------


async def setup_sandbox(
    sandbox: modal.Sandbox,
    model: str,
    opencode_api_key: str,
) -> None:
    print("Setting up sandbox...")
    print(f"[setup] resolved model={model}")

    print("[setup] writing setup.sh")
    await sandbox_write_file(sandbox, "/tmp/upload/setup.sh", SETUP_SH)
    print("[setup] writing mcp_server.py")
    await sandbox_write_file(sandbox, "/sandbox/mcp_server.py", MCP_SERVER)
    print("[setup] writing opencode_api_key")
    await sandbox_write_file(sandbox, "/tmp/upload/opencode_api_key.txt", opencode_api_key)

    config_override = OPENCODE_CONFIG.replace("MODEL_PLACEHOLDER", model)
    print("[setup] writing opencode config")
    await sandbox_write_file(sandbox, "/workspace/opencode.jsonc", config_override)

    env_paths = [f"/environment/{rel}" for rel in ENVIRONMENT_PY_FILES]
    env_paths += [f"/environment/{rel}" for rel in ENVIRONMENT_C_FILES]
    env_paths += [f"/environment/{rel}" for rel in ENVIRONMENT_TXT_FILES]
    env_dirs = sorted({str(Path(p).parent) for p in env_paths})
    if env_dirs:
        print(f"[setup] precreating env dirs: {len(env_dirs)}")
        dir_args = " ".join([f"'{d}'" for d in env_dirs])
        await sandbox_run(sandbox, f"mkdir -p {dir_args}")

    print(f"[setup] uploading env py files: {len(ENVIRONMENT_PY_FILES)}")
    for rel, content in ENVIRONMENT_PY_FILES.items():
        await sandbox_write_file(sandbox, f"/environment/{rel}", content, ensure_dir=False)

    print(f"[setup] uploading env c files: {len(ENVIRONMENT_C_FILES)}")
    for rel, content in ENVIRONMENT_C_FILES.items():
        await sandbox_write_file(sandbox, f"/environment/{rel}", content, ensure_dir=False)

    print(f"[setup] uploading env txt files: {len(ENVIRONMENT_TXT_FILES)}")
    for rel, content in ENVIRONMENT_TXT_FILES.items():
        await sandbox_write_file(sandbox, f"/environment/{rel}", content, ensure_dir=False)

    print("[setup] running setup.sh")
    exit_code, stdout, stderr = await sandbox_run(sandbox, "bash /tmp/upload/setup.sh", timeout=600)
    print(f"Setup stdout:\n{stdout}")
    if stderr:
        print(f"Setup stderr:\n{stderr}")
    if exit_code != 0:
        raise RuntimeError(f"Setup failed (exit {exit_code}): {stderr}")


# ---------------------------------------------------------------------------
# Run all tests (for "agent done" detection)
# ---------------------------------------------------------------------------


async def run_all_tests(sandbox: modal.Sandbox, session_id: str) -> dict[str, Any]:
    calls: list[EnvoiCall] = []

    for path in REQUIRED_PATHS:
        _, stdout, _ = await sandbox_run(
            sandbox,
            f"curl -sf -X POST http://localhost:8000/session/{session_id}/test/{path}",
            timeout=300,
        )

        try:
            data = json.loads(stdout)
            parsed_result = TestResult(**data) if isinstance(data, dict) else None
            call = EnvoiCall(
                path=path,
                timestamp=datetime.now(UTC).isoformat(),
                duration_ms=0,
                status_code=200,
                error=None,
                result=parsed_result,
            )
            calls.append(call)
        except Exception:
            pass
        await asyncio.sleep(0.5)

    failed = [c for c in calls if c.result and c.result.passed != c.result.total]
    return {
        "all_passed": len(failed) == 0,
        "failed_paths": [c.path for c in failed],
        "calls": calls,
    }


# ---------------------------------------------------------------------------
# End session
# ---------------------------------------------------------------------------


async def end_session(
    sandbox: modal.Sandbox,
    trajectory_id: str,
    session_id: str,
    turn_count: int,
    reason: Literal["solved", "turn_limit", "timeout", "agent_error", "envoi_error"],
    tracker: SolveTracker,
    last_message_id: str | None,
    model: str,
) -> None:
    print(f"Ending session: {reason}")

    final_commit = await get_git_commit(sandbox)

    end_record = TurnRecord(
        trajectory_id=trajectory_id,
        session_id=session_id,
        turn=None,
        timestamp=datetime.now(UTC).isoformat(),
        agent_model=model,
        git_commit=final_commit,
        message_id=None,
        envoi_calls=[],
        session_end=SessionEnd(
            reason=reason,
            total_turns=turn_count,
            final_git_commit=final_commit,
        ),
    )
    append_jsonl_record(trajectory_id, end_record)

    try:
        exit_code, _, stderr = await sandbox_run(
            sandbox, "cd /workspace && git bundle create /tmp/repo.bundle --all", timeout=60
        )
        print(f"[bundle] git bundle exit={exit_code}")
        if stderr:
            print(f"[bundle] stderr:\n{stderr}")

        _, size_out, _ = await sandbox_run(
            sandbox, "stat -c %s /tmp/repo.bundle 2>/dev/null || echo 0", timeout=10
        )
        try:
            bundle_size = int(size_out.strip() or "0")
        except ValueError:
            bundle_size = 0
        print(f"[bundle] size={bundle_size} bytes")

        if bundle_size > 0:
            _, bundle_b64, _ = await sandbox_run(sandbox, "base64 /tmp/repo.bundle", timeout=60)
            bundle_data = base64.b64decode(bundle_b64.strip())
            print(f"[bundle] decoded size={len(bundle_data)} bytes")
            upload_file(trajectory_id, "repo.bundle", bundle_data)
            print("[bundle] uploaded git bundle to S3")
        else:
            print("[bundle] WARNING: bundle is 0 bytes, skipping upload")
    except Exception as e:
        print(f"Failed to upload git bundle: {e}")

    print(f"Session ended: {reason}, {turn_count} turns")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


@app.local_entrypoint()
async def main(
    model: str = DEFAULT_MODEL,
    max_turns: int = 1000,
    trajectory_id: str | None = None,
) -> None:
    result = await run_trajectory.remote.aio(
        model=model,
        max_turns=max_turns,
        trajectory_id=trajectory_id,
    )
    print(f"Completed trajectory: {result}")
