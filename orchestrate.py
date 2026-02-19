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

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.txt"
SETUP_SH_PATH = Path(__file__).parent / "sandbox" / "setup.sh"
MCP_SERVER_PATH = Path(__file__).parent / "sandbox" / "mcp_server.py"
OPENCODE_CONFIG_PATH = Path(__file__).parent / "sandbox" / "opencode.jsonc"
ENVIRONMENT_DIR = Path(__file__).parent / "environment"

DEFAULT_MODEL = "glm-5-free"

REQUIRED_PATHS: list[str] = [
    "basics",
    *[f"wacct/chapter_{i}" for i in range(1, 21)],
    *[f"c_testsuite/part_{i}" for i in range(1, 6)],
    *[f"torture/part_{i}" for i in range(1, 11)],
]


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


def upload_file(trajectory_id: str, filename: str, data: bytes) -> str:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/{filename}"
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    return f"s3://{bucket}/{key}"


function_image = modal.Image.debian_slim().pip_install("boto3")

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


def sandbox_exec(sandbox: modal.Sandbox, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    result = sandbox.exec(f"bash -c {repr(cmd)}", timeout=timeout)
    stdout = ""
    stderr = ""
    for line in result.stdout:
        stdout += line
    for line in result.stderr:
        stderr += line
    return result.exit_code or 0, stdout, stderr


def sandbox_run(sandbox: modal.Sandbox, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    return sandbox_exec(sandbox, cmd, timeout)


def sandbox_write_file(sandbox: modal.Sandbox, path: str, content: str) -> None:
    escaped = content.replace("'", "'\\''")
    sandbox_exec(
        sandbox, f"mkdir -p $(dirname '{path}') && echo '{escaped}' > '{path}'", timeout=30
    )


def get_messages(sandbox: modal.Sandbox, session_id: str) -> list[dict[str, Any]]:
    _, stdout, _ = sandbox_run(
        sandbox, f"curl -sf http://localhost:4096/session/{session_id}/message", timeout=30
    )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return []


def send_user_message(sandbox: modal.Sandbox, session_id: str, message: str) -> None:
    payload = json.dumps({"parts": [{"type": "text", "text": message}]})
    escaped = payload.replace("'", "'\\''")
    sandbox_run(
        sandbox,
        f"curl -sf -X POST http://localhost:4096/session/{session_id}/message "
        f"-H 'Content-Type: application/json' -d '{escaped}'",
        timeout=60,
    )


def create_session(sandbox: modal.Sandbox, title: str = "C Compiler Build") -> str | None:
    payload = json.dumps({"title": title})
    escaped = payload.replace("'", "'\\''")
    _, stdout, _ = sandbox_run(
        sandbox,
        f"curl -sf -X POST http://localhost:4096/session -H 'Content-Type: application/json' -d '{escaped}'",
        timeout=30,
    )
    try:
        data = json.loads(stdout)
        return data.get("id")
    except json.JSONDecodeError:
        return None


def send_initial_prompt(sandbox: modal.Sandbox, session_id: str, prompt: str) -> None:
    payload = json.dumps({"parts": [{"type": "text", "text": prompt}]})
    escaped = payload.replace("'", "'\\''")
    sandbox_run(
        sandbox,
        f"curl -sf -X POST http://localhost:4096/session/{session_id}/message "
        f"-H 'Content-Type: application/json' -d '{escaped}'",
        timeout=120,
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


def is_opencode_healthy(sandbox: modal.Sandbox) -> bool:
    _, stdout, _ = sandbox_run(sandbox, "curl -sf http://localhost:4096/global/health", timeout=10)
    try:
        data = json.loads(stdout)
        return data.get("healthy", False)
    except json.JSONDecodeError:
        return False


def get_git_commit(sandbox: modal.Sandbox) -> str | None:
    _, stdout, _ = sandbox_run(
        sandbox, "cd /workspace && git rev-parse HEAD 2>/dev/null || echo 'none'", timeout=10
    )
    commit = stdout.strip()
    if commit == "none" or not commit:
        return None
    return commit[:16]


def check_git_has_changes(sandbox: modal.Sandbox) -> bool:
    _, stdout, _ = sandbox_run(sandbox, "cd /workspace && git status --porcelain", timeout=10)
    return bool(stdout.strip())


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
    print(f"Model: {model}, max_turns: {max_turns}, timeout: {timeout_seconds}s")

    opencode_api_key = os.environ.get("OPENCODE_API_KEY", "")
    prompt = PROMPT_PATH.read_text()
    setup_sh = SETUP_SH_PATH.read_text()
    mcp_server = MCP_SERVER_PATH.read_text()
    opencode_config = OPENCODE_CONFIG_PATH.read_text()

    with modal.Sandbox.create(
        "bash",
        "-c",
        "sleep infinity",
        image=sandbox_image,
        timeout=timeout_seconds,
        app=app,
    ) as sandbox:
        start_time = time.monotonic()

        try:
            await setup_sandbox(
                sandbox, model, opencode_api_key, setup_sh, mcp_server, opencode_config, prompt
            )

            session_id = create_session(sandbox, title=f"trajectory-{trajectory_id}")
            if not session_id:
                raise RuntimeError("Failed to create OpenCode session")

            print(f"OpenCode session: {session_id}")
            send_initial_prompt(sandbox, session_id, prompt)

            tracker = SolveTracker()
            last_message_id: str | None = None
            turn_count = 0
            consecutive_idle_turns = 0
            MAX_IDLE_TURNS = 3

            while True:
                await asyncio.sleep(5)

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
                        model,
                    )
                    return trajectory_id

                if not is_opencode_healthy(sandbox):
                    await end_session(
                        sandbox,
                        trajectory_id,
                        session_id,
                        turn_count,
                        "agent_error",
                        tracker,
                        last_message_id,
                        model,
                    )
                    return trajectory_id

                messages = get_messages(sandbox, session_id)
                new_turn = detect_new_turn(messages, last_message_id)

                if new_turn:
                    turn_count += 1
                    info = new_turn.get("info", {})
                    last_message_id = info.get("id")
                    parts = new_turn.get("parts", [])

                    envoi_calls = extract_envoi_calls(parts)
                    tracker.update(envoi_calls)

                    git_commit = get_git_commit(sandbox)

                    record = TurnRecord(
                        trajectory_id=trajectory_id,
                        session_id=session_id,
                        turn=turn_count,
                        timestamp=datetime.now(UTC).isoformat(),
                        agent_model=model,
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
                            model,
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
                            model,
                        )
                        return trajectory_id

                    if has_tool_calls(parts):
                        consecutive_idle_turns = 0
                    else:
                        consecutive_idle_turns += 1

                    if turn_count >= 5 and consecutive_idle_turns >= MAX_IDLE_TURNS:
                        git_has_changes = check_git_has_changes(sandbox)
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
                                        model,
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

                                reinject_msg = f"""Some tests are still failing.

Failed test suites:
{chr(10).join(details)}

Please continue working and pass ALL tests."""
                                send_user_message(sandbox, session_id, reinject_msg)
                                consecutive_idle_turns = 0
                                print(f"Re-injected with {len(failed_paths)} failed paths")

        except Exception as e:
            print(f"Error: {e}")
            raise

    return trajectory_id


async def setup_sandbox(
    sandbox: modal.Sandbox,
    model: str,
    opencode_api_key: str,
    setup_sh: str,
    mcp_server: str,
    opencode_config: str,
    prompt: str,
) -> None:
    print("Setting up sandbox...")

    sandbox_write_file(sandbox, "/tmp/upload/setup.sh", setup_sh)
    sandbox_write_file(sandbox, "/tmp/upload/mcp_server.py", mcp_server)
    sandbox_write_file(sandbox, "/tmp/upload/opencode.jsonc", opencode_config)
    sandbox_write_file(sandbox, "/tmp/upload/opencode_api_key.txt", opencode_api_key)

    for py_file in ENVIRONMENT_DIR.rglob("*.py"):
        rel = py_file.relative_to(ENVIRONMENT_DIR)
        content = py_file.read_text()
        sandbox_write_file(sandbox, f"/environment/{rel}", content)

    for c_file in ENVIRONMENT_DIR.rglob("*.c"):
        rel = c_file.relative_to(ENVIRONMENT_DIR)
        content = c_file.read_text()
        sandbox_write_file(sandbox, f"/environment/{rel}", content)

    for txt_file in ENVIRONMENT_DIR.rglob("*.txt"):
        rel = txt_file.relative_to(ENVIRONMENT_DIR)
        content = txt_file.read_text()
        sandbox_write_file(sandbox, f"/environment/{rel}", content)

    config_override = opencode_config.replace('"model": "glm-5-free"', f'"model": "{model}"')
    sandbox_write_file(sandbox, "/tmp/upload/opencode.jsonc", config_override)

    exit_code, stdout, stderr = sandbox_run(sandbox, "bash /tmp/upload/setup.sh", timeout=600)
    print(f"Setup output:\n{stdout}")
    if exit_code != 0:
        raise RuntimeError(f"Setup failed: {stderr}")


async def run_all_tests(sandbox: modal.Sandbox, session_id: str) -> dict[str, Any]:
    calls: list[EnvoiCall] = []

    for path in REQUIRED_PATHS:
        _, stdout, _ = sandbox_run(
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

    final_commit = get_git_commit(sandbox)

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
        sandbox_run(
            sandbox, "cd /workspace && git bundle create /tmp/repo.bundle --all", timeout=60
        )
        _, bundle_stdout, _ = sandbox_run(sandbox, "cat /tmp/repo.bundle | base64", timeout=60)
        bundle_data = base64.b64decode(bundle_stdout)
        upload_file(trajectory_id, "repo.bundle", bundle_data)
        print("Uploaded git bundle to S3")
    except Exception as e:
        print(f"Failed to upload git bundle: {e}")

    print(f"Session ended: {reason}, {turn_count} turns")


@app.local_entrypoint()
def main(
    model: str = DEFAULT_MODEL,
    max_turns: int = 1000,
    trajectory_id: str | None = None,
) -> None:
    result = run_trajectory.remote(
        model=model,
        max_turns=max_turns,
        trajectory_id=trajectory_id,
    )
    print(f"Completed trajectory: {result}")
