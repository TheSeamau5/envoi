"""
Main orchestrator for envoi-trace.

This runs as a Modal function and:
1. Creates an e2b sandbox with envoi + OpenCode
2. Uploads environment files
3. Polls OpenCode API for new turns
4. Writes JSONL records to S3
5. Handles "agent done" detection and re-injection
6. Uploads final artifacts to S3

Usage:
    modal run orchestrate.py
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import modal
from dotenv import load_dotenv
from e2b import Sandbox

from models import REQUIRED_PATHS, EnvoiCall, SessionEnd, TurnRecord
from poller import (
    check_git_has_changes,
    create_session,
    detect_new_turn,
    get_git_commit,
    get_messages,
    is_opencode_healthy,
    send_initial_prompt,
    send_user_message,
)
from s3_upload import append_jsonl_record, upload_file
from tracker import SolveTracker
from writer import extract_envoi_calls, has_tool_calls

load_dotenv()

app = modal.App("envoi-trace")

PROMPT = (Path(__file__).parent / "prompts" / "system.txt").read_text()
SETUP_SH = (Path(__file__).parent / "sandbox" / "setup.sh").read_text()
MCP_SERVER = (Path(__file__).parent / "sandbox" / "mcp_server.py").read_text()
OPENCODE_CONFIG = (Path(__file__).parent / "sandbox" / "opencode.jsonc").read_text()

ENVIRONMENT_DIR = Path(__file__).parent / "environment"

DEFAULT_MODEL = "glm-5-free"


@app.function(
    timeout=14400,
    secrets=[modal.Secret.from_dotenv()],
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

    sandbox = Sandbox.create(timeout=timeout_seconds)
    start_time = time.monotonic()

    try:
        await setup_sandbox(sandbox, model, opencode_api_key)

        session_id = create_session(sandbox, title=f"trajectory-{trajectory_id}")
        if not session_id:
            raise RuntimeError("Failed to create OpenCode session")

        print(f"OpenCode session: {session_id}")
        send_initial_prompt(sandbox, session_id, PROMPT)

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

    finally:
        try:
            sandbox.kill()
        except Exception:
            pass

    return trajectory_id


async def setup_sandbox(sandbox: Sandbox, model: str, opencode_api_key: str) -> None:
    print("Setting up sandbox...")

    sandbox.files.write("/tmp/upload/setup.sh", SETUP_SH)
    sandbox.files.write("/tmp/upload/mcp_server.py", MCP_SERVER)
    sandbox.files.write("/tmp/upload/opencode.jsonc", OPENCODE_CONFIG)
    sandbox.files.write("/tmp/upload/opencode_api_key.txt", opencode_api_key)

    for py_file in ENVIRONMENT_DIR.rglob("*.py"):
        rel = py_file.relative_to(ENVIRONMENT_DIR)
        content = py_file.read_text()
        sandbox.files.write(f"/environment/{rel}", content)

    for c_file in ENVIRONMENT_DIR.rglob("*.c"):
        rel = c_file.relative_to(ENVIRONMENT_DIR)
        content = c_file.read_text()
        sandbox.files.write(f"/environment/{rel}", content)

    for txt_file in ENVIRONMENT_DIR.rglob("*.txt"):
        rel = txt_file.relative_to(ENVIRONMENT_DIR)
        content = txt_file.read_text()
        sandbox.files.write(f"/environment/{rel}", content)

    config_override = OPENCODE_CONFIG.replace('"model": "glm-5-free"', f'"model": "{model}"')
    sandbox.files.write("/tmp/upload/opencode.jsonc", config_override)

    result = sandbox.commands.run("bash /tmp/upload/setup.sh", timeout=300)
    print(f"Setup output:\n{result.stdout}")
    if result.exit_code != 0:
        raise RuntimeError(f"Setup failed: {result.stderr}")


async def run_all_tests(sandbox: Sandbox, session_id: str) -> dict[str, Any]:
    calls: list[EnvoiCall] = []

    for path in REQUIRED_PATHS:
        result = sandbox.commands.run(
            f"curl -sf -X POST http://localhost:8000/session/{session_id}/test/{path}",
            timeout=300,
        )
        import json as json_mod

        try:
            data = json_mod.loads(result.stdout)
            from models import TestResult

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
    sandbox: Sandbox,
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
        result = sandbox.commands.run(
            "cd /workspace && git bundle create /tmp/repo.bundle --all",
            timeout=60,
        )
        if result.exit_code == 0:
            bundle_data = sandbox.files.read("/tmp/repo.bundle", format="bytes")
            upload_file(trajectory_id, "repo.bundle", bytes(bundle_data))
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
