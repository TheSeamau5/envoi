from __future__ import annotations

import json
from typing import Any


def get_messages(sandbox: Any, session_id: str) -> list[dict[str, Any]]:
    result = sandbox.commands.run(
        f"curl -sf http://localhost:4096/session/{session_id}/message",
        timeout=30,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def get_session_status(sandbox: Any, session_id: str) -> dict[str, Any]:
    result = sandbox.commands.run(
        "curl -sf http://localhost:4096/session/status",
        timeout=30,
    )
    try:
        data = json.loads(result.stdout)
        return data.get(session_id, {})
    except json.JSONDecodeError:
        return {}


def send_user_message(sandbox: Any, session_id: str, message: str) -> None:
    payload = json.dumps({"parts": [{"type": "text", "text": message}]})
    escaped = payload.replace("'", "'\"'\"'")
    sandbox.commands.run(
        f"curl -sf -X POST http://localhost:4096/session/{session_id}/message "
        f"-H 'Content-Type: application/json' -d '{escaped}'",
        timeout=60,
    )


def create_session(sandbox: Any, title: str = "C Compiler Build") -> str | None:
    payload = json.dumps({"title": title})
    escaped = payload.replace("'", "'\"'\"'")
    result = sandbox.commands.run(
        "curl -sf -X POST http://localhost:4096/session "
        f"-H 'Content-Type: application/json' -d '{escaped}'",
        timeout=30,
    )
    try:
        data = json.loads(result.stdout)
        return data.get("id")
    except json.JSONDecodeError:
        return None


def send_initial_prompt(sandbox: Any, session_id: str, prompt: str) -> None:
    payload = json.dumps({"parts": [{"type": "text", "text": prompt}]})
    escaped = payload.replace("'", "'\"'\"'")
    sandbox.commands.run(
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


def is_opencode_healthy(sandbox: Any) -> bool:
    result = sandbox.commands.run(
        "curl -sf http://localhost:4096/global/health",
        timeout=10,
    )
    try:
        data = json.loads(result.stdout)
        return data.get("healthy", False)
    except json.JSONDecodeError:
        return False


def get_git_commit(sandbox: Any) -> str | None:
    result = sandbox.commands.run(
        "cd /workspace && git rev-parse HEAD 2>/dev/null || echo 'none'",
        timeout=10,
    )
    commit = result.stdout.strip()
    if commit == "none" or not commit:
        return None
    return commit[:16]


def check_git_has_changes(sandbox: Any) -> bool:
    result = sandbox.commands.run(
        "cd /workspace && git status --porcelain",
        timeout=10,
    )
    return bool(result.stdout.strip())
