"""
Minimal Codex CLI wrapper for non-interactive turns.

This script runs inside the Modal sandbox and executes `codex exec --json`,
then normalizes JSONL events into a message-like structure used by orchestrate.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

MEANINGFUL_PART_TYPES: set[str] = {
    "reasoning",
    "text",
    "tool",
    "tool_use",
    "tool_result",
    "patch",
}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def parse_jsonl_events(stdout_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def extract_thread_id(events: list[dict[str, Any]], fallback: str | None = None) -> str | None:
    for event in events:
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    return fallback


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def mcp_output_payload(result: Any, error: Any) -> str:
    if isinstance(error, dict) and error:
        return _json_dumps(error)
    result_obj = _as_dict(result)
    structured = result_obj.get("structured_content")
    if isinstance(structured, (dict, list)):
        return _json_dumps(structured)
    content = result_obj.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text
            if first:
                return _json_dumps(first)
        if isinstance(first, str) and first.strip():
            return first
        return _json_dumps(content)
    if result_obj:
        return _json_dumps(result_obj)
    return ""


def part_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    item_type = item.get("type")

    if item_type == "reasoning":
        text = item.get("text", "")
        return {"type": "reasoning", "text": text}

    if item_type == "agent_message":
        text = item.get("text", "")
        return {"type": "text", "text": text}

    if item_type == "command_execution":
        state: dict[str, Any] = {
            "status": item.get("status", "completed"),
            "input": {"command": item.get("command", "")},
            "output": item.get("aggregated_output", ""),
            "exit_code": item.get("exit_code"),
        }
        return {"type": "tool", "tool": "bash", "state": state}

    if item_type == "mcp_tool_call":
        tool_name = item.get("tool") or "mcp_tool_call"
        state = {
            "status": item.get("status", "completed"),
            "input": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            "output": mcp_output_payload(item.get("result"), item.get("error")),
        }
        return {"type": "tool", "tool": tool_name, "state": state}

    if item_type == "collab_tool_call":
        state = {
            "status": item.get("status", "completed"),
            "input": {
                "tool": item.get("tool"),
                "prompt": item.get("prompt"),
                "receiver_thread_ids": item.get("receiver_thread_ids", []),
            },
            "output": "",
        }
        return {"type": "tool", "tool": "collab_tool_call", "state": state}

    if item_type == "web_search":
        state = {
            "status": "completed",
            "input": {"query": item.get("query", "")},
            "output": _json_dumps({"action": item.get("action")}),
        }
        return {"type": "tool", "tool": "web_search", "state": state}

    if item_type == "file_change":
        changes = item.get("changes")
        files: list[str] = []
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    path = change.get("path")
                    if isinstance(path, str) and path:
                        files.append(path)
        return {"type": "patch", "files": files}

    return None


def normalize_parts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        part = part_from_item(item)
        if isinstance(part, dict):
            parts.append(part)
    return parts


def count_meaningful_parts(parts: list[dict[str, Any]]) -> int:
    count = 0
    for part in parts:
        if isinstance(part, dict) and part.get("type") in MEANINGFUL_PART_TYPES:
            count += 1
    return count


def extract_turn_failure(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "turn.failed":
            error_obj = event.get("error")
            if isinstance(error_obj, dict):
                message = error_obj.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            if isinstance(error_obj, str) and error_obj.strip():
                return error_obj.strip()
        if event_type == "error":
            message = event.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    return None


def build_codex_exec_command(*, session_id: str | None, model: str) -> list[str]:
    command: list[str] = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-C",
        "/workspace",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model",
        model,
        "-c",
        'model_reasoning_effort="high"',
    ]
    if session_id and not session_id.startswith("pending-"):
        command.extend(["resume", session_id])
    return command


def build_codex_env(api_key: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("CODEX_HOME", "/workspace/.codex")
    env.setdefault("RUST_LOG", "error")
    if api_key:
        env["CODEX_API_KEY"] = api_key
        env["OPENAI_API_KEY"] = api_key
    return env


def start_stream_drain_thread(stream: Any, sink: list[str]) -> threading.Thread | None:
    if stream is None:
        return None

    def _drain() -> None:
        for line in stream:
            sink.append(line)

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    return reader


def meaningful_part_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("type") != "item.completed":
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    part = part_from_item(item)
    if not isinstance(part, dict):
        return None
    if part.get("type") not in MEANINGFUL_PART_TYPES:
        return None
    return part


def parse_line_events_and_meaningful_count(line: str) -> tuple[list[dict[str, Any]], int]:
    parsed_events = parse_jsonl_events(line)
    meaningful_parts = 0
    for event in parsed_events:
        if meaningful_part_from_event(event) is not None:
            meaningful_parts += 1
    return parsed_events, meaningful_parts


def run_codex_turn(
    *,
    session_id: str | None,
    text: str,
    model: str,
    api_key: str | None,
    max_parts: int = 0,
) -> dict[str, Any]:
    command = build_codex_exec_command(session_id=session_id, model=model)
    env = build_codex_env(api_key)

    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    if proc.stdin is not None:
        proc.stdin.write(text)
        proc.stdin.close()

    events: list[dict[str, Any]] = []
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    aborted_for_part_limit = False
    meaningful_parts_seen = 0

    stderr_reader = start_stream_drain_thread(proc.stderr, stderr_lines)

    if proc.stdout is not None:
        for line in proc.stdout:
            stdout_lines.append(line)
            parsed_events, meaningful_from_line = parse_line_events_and_meaningful_count(line)
            if parsed_events:
                events.extend(parsed_events)
            meaningful_parts_seen += meaningful_from_line
            if max_parts > 0 and meaningful_parts_seen >= max_parts:
                aborted_for_part_limit = True
                proc.terminate()
                break

    if proc.stdout is not None:
        remaining_stdout = proc.stdout.read()
        if remaining_stdout:
            stdout_lines.append(remaining_stdout)
            events.extend(parse_jsonl_events(remaining_stdout))

    try:
        return_code = proc.wait(timeout=2 if aborted_for_part_limit else None)
    except subprocess.TimeoutExpired:
        proc.kill()
        return_code = proc.wait()

    if stderr_reader is not None:
        stderr_reader.join(timeout=2)

    stderr_text = "".join(stderr_lines)

    if not events and stdout_lines:
        events = parse_jsonl_events("".join(stdout_lines))

    resolved_session_id = extract_thread_id(events, fallback=session_id)
    parts = normalize_parts(events)
    if meaningful_parts_seen == 0:
        meaningful_parts_seen = count_meaningful_parts(parts)
    now_ms = int(time.time() * 1000)
    mid = (
        f"{resolved_session_id}:{now_ms}"
        if resolved_session_id
        else f"codex-message:{now_ms}"
    )

    assistant_message: dict[str, Any] = {
        "info": {
            "id": mid,
            "role": "assistant",
            "sessionID": resolved_session_id or "",
            "time": {"created": now_ms},
        },
        "parts": parts,
        "_events": events,
    }

    body = {
        "info": {"id": mid},
        "parts": parts,
        "_events": events,
        "_message": assistant_message,
        "_session_id": resolved_session_id,
        "_stream": {
            "events_observed": len(events),
            "meaningful_parts_seen": meaningful_parts_seen,
            "aborted_for_part_limit": aborted_for_part_limit,
        },
    }

    ok = return_code == 0 or aborted_for_part_limit
    failure_message = extract_turn_failure(events)
    error_text = failure_message or stderr_text.strip() or "codex exec failed"
    return {
        "ok": ok,
        "status_code": 200 if ok else return_code,
        "body": body,
        "error": None if ok else error_text,
        "stderr": stderr_text,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat_stream = subparsers.add_parser("chat-stream")
    chat_stream.add_argument("--session-id", default="")
    chat_stream.add_argument("--text-file", required=True)
    chat_stream.add_argument("--model", required=True)
    chat_stream.add_argument("--max-parts", type=int, default=0)
    chat_stream.add_argument("--api-key-file", default="")

    args = parser.parse_args()

    if args.command == "chat-stream":
        text = Path(args.text_file).read_text()
        api_key = ""
        if args.api_key_file:
            api_key = Path(args.api_key_file).read_text().strip()
        result = run_codex_turn(
            session_id=args.session_id or None,
            text=text,
            model=args.model,
            api_key=api_key or None,
            max_parts=max(0, args.max_parts),
        )
        print(_json_dumps(result))
        return


if __name__ == "__main__":
    main()
