"""
Main orchestrator for envoi-trace.

Single-file version with all modules inlined for Modal.

Usage:
    modal run orchestrate.py
"""

from __future__ import annotations

import asyncio
import base64
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

DEFAULT_MODEL = "opencode/glm-5-free"

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
    return calls


def has_tool_calls(message_parts: list[dict[str, Any]]) -> bool:
    for part in message_parts:
        if part.get("type") == "tool_use":
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


def append_jsonl_record(trajectory_id: str, record: TurnRecord) -> None:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/trajectory.jsonl"
    line = record.model_dump_json() + "\n"
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


async def sandbox_run(sandbox: modal.Sandbox, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    """Execute a command inside the sandbox and return (exit_code, stdout, stderr)."""
    print(f"[sandbox_run] cmd={cmd[:200]}")
    proc = await sandbox.exec.aio("bash", "-c", cmd)
    stdout = ""
    stderr = ""
    async for chunk in proc.stdout:
        stdout += chunk
    async for chunk in proc.stderr:
        stderr += chunk
    await proc.wait.aio()
    exit_code = proc.returncode or 0
    if exit_code != 0:
        print(f"[sandbox_run] exit_code={exit_code}")
        if stderr:
            print(f"[sandbox_run] stderr:\n{stderr}")
    return exit_code, stdout, stderr


async def sandbox_write_file(sandbox: modal.Sandbox, path: str, content: str) -> None:
    """Write a file inside the sandbox using Modal's filesystem API."""
    parent = str(Path(path).parent)
    await sandbox_run(sandbox, f"mkdir -p '{parent}'")
    async with await sandbox.open.aio(path, "w") as f:
        await f.write.aio(content)


async def sandbox_write_bytes(sandbox: modal.Sandbox, path: str, data: bytes) -> None:
    """Write binary data to a file inside the sandbox."""
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


async def debug_endpoint_json(
    sandbox: modal.Sandbox,
    label: str,
    url: str,
    limit: int = 4000,
) -> None:
    print(f"[debug] {label} -> {url}")
    exit_code, stdout, stderr = await sandbox_run(sandbox, f"curl -sf {url}", timeout=15)
    if exit_code != 0:
        print(f"[debug] {label} exit_code={exit_code}")
    if stderr:
        print(f"[debug] {label} stderr:\n{stderr}")
    if not stdout.strip():
        print(f"[debug] {label} no output")
        return
    try:
        data = json.loads(stdout)
        data = redact_secrets(data)
        text = json.dumps(data, indent=2)
    except json.JSONDecodeError:
        text = stdout
    print(truncate_text(text, limit=limit))


async def get_messages(sandbox: modal.Sandbox, session_id: str) -> list[dict[str, Any]]:
    _, stdout, _ = await sandbox_run(
        sandbox, f"curl -sf http://localhost:4096/session/{session_id}/message", timeout=30
    )
    try:
        data = json.loads(stdout)
        summary = summarize_messages(data)
        print(
            f"[get_messages] count={summary['count']} last_role={summary['last_role']} "
            f"last_id={summary['last_id']} tail_roles={summary['tail_roles']}"
        )
        return data
    except json.JSONDecodeError:
        print("[get_messages] failed to parse JSON")
        return []


async def send_user_message(
    sandbox: modal.Sandbox,
    session_id: str,
    message: str,
    model: str,
) -> None:
    payload = json.dumps({"model": model, "parts": [{"type": "text", "text": message}]})
    await sandbox_write_file(sandbox, "/tmp/msg_payload.json", payload)
    print(f"[send_user_message] sending prompt_async model={model}")
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        f"curl -sf -X POST http://localhost:4096/session/{session_id}/prompt_async "
        f"-H 'Content-Type: application/json' -d @/tmp/msg_payload.json",
        timeout=120,
    )
    print(f"[send_user_message] exit={exit_code} stdout_len={len(stdout)} stderr_len={len(stderr)}")


async def create_session(sandbox: modal.Sandbox, title: str = "C Compiler Build") -> str | None:
    payload = json.dumps({"title": title})
    await sandbox_write_file(sandbox, "/tmp/create_session.json", payload)
    _, stdout, _ = await sandbox_run(
        sandbox,
        "curl -sf -X POST http://localhost:4096/session "
        "-H 'Content-Type: application/json' -d @/tmp/create_session.json",
        timeout=30,
    )
    try:
        data = json.loads(stdout)
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
    payload = json.dumps({"model": model, "parts": [{"type": "text", "text": prompt}]})
    await sandbox_write_file(sandbox, "/tmp/prompt_payload.json", payload)
    print(f"[send_initial_prompt] sending prompt_async model={model}")
    exit_code, stdout, stderr = await sandbox_run(
        sandbox,
        f"curl -sf -X POST http://localhost:4096/session/{session_id}/prompt_async "
        f"-H 'Content-Type: application/json' -d @/tmp/prompt_payload.json",
        timeout=120,
    )
    print(
        f"[send_initial_prompt] exit={exit_code} stdout_len={len(stdout)} stderr_len={len(stderr)}"
    )


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


async def is_opencode_healthy(sandbox: modal.Sandbox) -> bool:
    _, stdout, _ = await sandbox_run(
        sandbox, "curl -sf http://localhost:4096/global/health", timeout=10
    )
    try:
        data = json.loads(stdout)
        healthy = data.get("healthy", False)
        print(f"[opencode_health] healthy={healthy}")
        return healthy
    except json.JSONDecodeError:
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
        await send_initial_prompt(sandbox, session_id, PROMPT, resolved_model)
        await tail_sandbox_logs(sandbox)

        await debug_endpoint_json(sandbox, "config", "http://localhost:4096/config")
        await debug_endpoint_json(sandbox, "provider", "http://localhost:4096/provider")
        await debug_endpoint_json(sandbox, "session_status", "http://localhost:4096/session/status")
        await debug_endpoint_json(sandbox, "session", f"http://localhost:4096/session/{session_id}")
        await debug_endpoint_json(
            sandbox, "messages", f"http://localhost:4096/session/{session_id}/message"
        )

        tracker = SolveTracker()
        last_message_id: str | None = None
        turn_count = 0
        consecutive_idle_turns = 0
        MAX_IDLE_TURNS = 3

        while True:
            await asyncio.sleep(5)

            print(
                f"[poll] elapsed={int(time.monotonic() - start_time)}s turn={turn_count} "
                f"idle={consecutive_idle_turns} last_msg={last_message_id}"
            )

            elapsed = time.monotonic() - start_time
            if elapsed > timeout_seconds:
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

            if not await is_opencode_healthy(sandbox):
                await end_session(
                    sandbox,
                    trajectory_id,
                    session_id,
                    turn_count,
                    "agent_error",
                    tracker,
                    last_message_id,
                    resolved_model,
                )
                return trajectory_id

            messages = await get_messages(sandbox, session_id)
            print(f"[poll] messages_received={len(messages)}")
            new_turn = detect_new_turn(messages, last_message_id)

            if (time.monotonic() - start_time) % 30 < 5:
                await tail_sandbox_logs(sandbox)
                await debug_endpoint_json(
                    sandbox, "session_status", "http://localhost:4096/session/status", limit=2000
                )

            if new_turn:
                turn_count += 1
                info = new_turn.get("info", {})
                last_message_id = info.get("id")
                parts = new_turn.get("parts", [])

                print(
                    f"[turn] new message id={last_message_id} parts={len(parts)} tools={has_tool_calls(parts)}"
                )

                envoi_calls = extract_envoi_calls(parts)
                tracker.update(envoi_calls)

                git_commit = await get_git_commit(sandbox)

                record = TurnRecord(
                    trajectory_id=trajectory_id,
                    session_id=session_id,
                    turn=turn_count,
                    timestamp=datetime.now(UTC).isoformat(),
                    agent_model=resolved_model,
                    git_commit=git_commit,
                    message_id=last_message_id,
                    envoi_calls=envoi_calls,
                )
                append_jsonl_record(trajectory_id, record)

                print(f"Turn {turn_count}: {len(envoi_calls)} envoi calls, commit={git_commit}")

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

                if has_tool_calls(parts):
                    consecutive_idle_turns = 0
                else:
                    consecutive_idle_turns += 1

                if turn_count >= 5 and consecutive_idle_turns >= MAX_IDLE_TURNS:
                    git_has_changes = await check_git_has_changes(sandbox)
                    if not git_has_changes:
                        print("Agent appears done. Running all tests...")
                        all_results = await run_all_tests(sandbox, session_id)

                        if all_results["all_passed"]:
                            tracker.update(all_results["calls"])
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

                        failed_paths = tracker.get_unsolved_paths()
                        if failed_paths:
                            details = []
                            for p in failed_paths[:5]:
                                call = tracker.get_latest_call_for_path(p)
                                if call and call.result:
                                    details.append(
                                        f"  - {p}: {call.result.passed}/{call.result.total}"
                                    )
                                else:
                                    details.append(f"  - {p}: not run")

                            reinject_msg = (
                                "Some tests are still failing.\n\n"
                                "Failed test suites:\n"
                                + "\n".join(details)
                                + "\n\nPlease continue working and pass ALL tests."
                            )
                            await send_user_message(
                                sandbox, session_id, reinject_msg, resolved_model
                            )
                            consecutive_idle_turns = 0
                            print(f"Re-injected with {len(failed_paths)} failed paths")
            else:
                print("[poll] no new assistant turn yet")

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

    print(f"[setup] uploading env py files: {len(ENVIRONMENT_PY_FILES)}")
    for rel, content in ENVIRONMENT_PY_FILES.items():
        await sandbox_write_file(sandbox, f"/environment/{rel}", content)

    print(f"[setup] uploading env c files: {len(ENVIRONMENT_C_FILES)}")
    for rel, content in ENVIRONMENT_C_FILES.items():
        await sandbox_write_file(sandbox, f"/environment/{rel}", content)

    print(f"[setup] uploading env txt files: {len(ENVIRONMENT_TXT_FILES)}")
    for rel, content in ENVIRONMENT_TXT_FILES.items():
        await sandbox_write_file(sandbox, f"/environment/{rel}", content)

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
        await sandbox_run(
            sandbox, "cd /workspace && git bundle create /tmp/repo.bundle --all", timeout=60
        )
        _, bundle_b64, _ = await sandbox_run(sandbox, "base64 /tmp/repo.bundle", timeout=60)
        bundle_data = base64.b64decode(bundle_b64.strip())
        upload_file(trajectory_id, "repo.bundle", bundle_data)
        print("Uploaded git bundle to S3")
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
