"""
Codex agent backend -- wraps the Codex app-server for non-interactive turns.

This module has two roles:
1. As a script running inside the sandbox: starts `codex app-server` over stdio,
   sends JSON-RPC requests (create_session, send_message), and parses streamed
   item notifications into TRACE_EVENT lines on stderr.
2. As the CodexAgent class implementing Agent: uploads itself into the
   sandbox, manages sessions, and translates turn results for runner.py.

The TRACE_EVENT protocol is how parts flow from the agent to the orchestrator
in real time. Each event is a JSON line prefixed with "TRACE_EVENT " on stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

MEANINGFUL_PART_TYPES: set[str] = {
    "reasoning",
    "text",
    "tool",
    "tool_use",
    "tool_result",
    "patch",
}

TRACE_EVENT_PREFIX = "TRACE_EVENT "


class TraceEvent(BaseModel):
    """A single part-completion event emitted over stderr for runner.py."""

    event: Literal["part.completed"] = "part.completed"
    role: Literal["assistant", "user"] = "assistant"
    part_type: str | None = None
    item_type: str | None = None
    summary: str | None = None
    content: str | None = None
    has_file_change: bool = False
    files: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    tool_status: str | None = None
    tool_input: Any = None
    tool_output: Any = None
    tool_error: Any = None
    tool_exit_code: int | None = None
    token_usage: dict[str, Any] | None = None
    provider_part: dict[str, Any] | None = None
    provider_item: dict[str, Any] | None = None
    provider_event: dict[str, Any] | None = None
    test_call: dict[str, Any] | None = None
    timestamp_ms: int = 0


class CodexTurnResult(BaseModel):
    """Return value of run_codex_turn, serialized to stdout as JSON."""

    ok: bool
    status_code: int
    body: dict[str, Any] | None = None
    error: str | None = None
    stderr: str = ""


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def parse_json_maybe(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def parse_int_maybe(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def truncate_for_trace(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def canonical_token(value: str) -> str:
    converted = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    converted = converted.replace("-", "_").replace(".", "_")
    converted = re.sub(r"[^A-Za-z0-9_/]", "_", converted)
    converted = re.sub(r"_+", "_", converted)
    return converted.strip("_").lower()


def method_key(method: str) -> str:
    normalized = method.replace(".", "/")
    return "/".join(canonical_token(segment) for segment in normalized.split("/") if segment)


def item_type_key(value: Any) -> str:
    return canonical_token(value) if isinstance(value, str) else "unknown"


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def mcp_output_payload(result: Any, error: Any) -> str:
    if isinstance(error, dict) and error:
        return json_dumps(error)

    result_obj = as_dict(result)

    for key in ("structured_content", "structuredContent"):
        structured = result_obj.get(key)
        if isinstance(structured, dict | list):
            return json_dumps(structured)

    content = result_obj.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text
            if first:
                return json_dumps(first)
        if isinstance(first, str) and first.strip():
            return first
        return json_dumps(content)

    if result_obj:
        return json_dumps(result_obj)
    return ""


def normalize_run_tests_payload(value: Any) -> dict[str, Any] | None:
    parsed = parse_json_maybe(value)
    if not isinstance(parsed, dict):
        return None
    if "path" in parsed and "timestamp" in parsed:
        return parsed
    for nested_key in (
        "result",
        "data",
        "output",
        "structured_content",
        "structuredContent",
    ):
        if nested_key in parsed:
            nested = normalize_run_tests_payload(parsed.get(nested_key))
            if isinstance(nested, dict):
                return nested
    return None


def extract_run_tests_call(item: dict[str, Any]) -> dict[str, Any] | None:
    if item_type_key(item.get("type")) != "mcp_tool_call":
        return None
    tool_name = item.get("tool")
    if str(tool_name or "") != "run_tests":
        return None

    result_obj = as_dict(item.get("result"))
    payload: Any = result_obj.get("structured_content")
    if payload is None:
        payload = result_obj.get("structuredContent")
    if payload is None:
        payload = mcp_output_payload(item.get("result"), item.get("error"))

    parsed = normalize_run_tests_payload(payload)
    if not isinstance(parsed, dict):
        return None

    path = parsed.get("path")
    timestamp = parsed.get("timestamp")
    duration_ms = parse_int_maybe(parsed.get("duration_ms"))
    status_code = parse_int_maybe(parsed.get("status_code"))
    if not isinstance(path, str) or not path:
        return None
    if not isinstance(timestamp, str) or not timestamp:
        return None
    if duration_ms is None:
        return None
    if status_code is None:
        return None

    return {
        "path": path,
        "timestamp": timestamp,
        "duration_ms": duration_ms,
        "status_code": status_code,
        "error": parsed.get("error") if isinstance(parsed.get("error"), str) else None,
        "result": parsed.get("result"),
    }


def format_generic_structured(value: Any) -> str:
    parsed = parse_json_maybe(value)
    if isinstance(parsed, dict | list):
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    return str(parsed)


def format_run_tests_output(value: Any) -> str:
    parsed = parse_json_maybe(value)
    if isinstance(parsed, dict) and "result" in parsed:
        parsed = parse_json_maybe(parsed.get("result"))

    if not isinstance(parsed, dict):
        return format_generic_structured(parsed)

    lines: list[str] = []
    path = parsed.get("path")
    status_code = parsed.get("status_code")
    duration_ms = parsed.get("duration_ms")
    error = parsed.get("error")

    if isinstance(path, str):
        lines.append(f"path: {path}")
    if isinstance(status_code, int):
        lines.append(f"status_code: {status_code}")
    if isinstance(duration_ms, int | float):
        lines.append(f"duration_ms: {duration_ms}")
    if error:
        lines.append(f"error: {error}")

    result_obj = parse_json_maybe(parsed.get("result"))
    if isinstance(result_obj, dict):
        passed = result_obj.get("passed")
        failed = result_obj.get("failed")
        total = result_obj.get("total")
        if isinstance(passed, int) and isinstance(failed, int) and isinstance(total, int):
            lines.append(f"summary: passed={passed} failed={failed} total={total}")

        cases = result_obj.get("cases")
        if isinstance(cases, list):
            failed_cases = [
                case
                for case in cases
                if isinstance(case, dict) and not bool(case.get("passed"))
            ]
            if failed_cases:
                lines.append("failed_cases:")
                for case in failed_cases:
                    name = str(case.get("name") or "?")
                    phase = str(case.get("phase") or "?")
                    lines.append(f"  - {name} ({phase})")
                    stderr_value = case.get("stderr")
                    if isinstance(stderr_value, str) and stderr_value.strip():
                        stderr_lines = stderr_value.strip().splitlines()
                        lines.append(f"    stderr: {stderr_lines[0]}")
                        for extra_line in stderr_lines[1:]:
                            lines.append(f"      {extra_line}")
            elif isinstance(passed, int) and isinstance(total, int) and passed == total:
                lines.append("failed_cases: []")

    if not lines:
        return format_generic_structured(parsed)
    return "\n".join(lines)


def format_mcp_output_for_log(tool_name: str, result: Any, error: Any) -> str:
    if isinstance(error, dict) and error:
        return format_generic_structured(error)
    if tool_name == "run_tests":
        return format_run_tests_output(mcp_output_payload(result, error))
    return format_generic_structured(mcp_output_payload(result, error))


def merge_usage_maps(base: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if key not in base:
            base[key] = value
            continue
        existing = base[key]
        if isinstance(existing, dict) and isinstance(value, dict):
            merge_usage_maps(existing, value)
        elif isinstance(existing, int | float) and isinstance(value, int | float):
            base[key] = existing + value
        else:
            base[key] = value


def collect_usage_candidates(value: Any, sink: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lower_key = key.lower()
            if isinstance(nested, dict) and ("token" in lower_key or "usage" in lower_key):
                sink.append(nested)
            collect_usage_candidates(nested, sink)
        return
    if isinstance(value, list):
        for item in value:
            collect_usage_candidates(item, sink)


def extract_usage_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        params = as_dict(event.get("params"))
        for key in ("usage", "token_usage", "tokenUsage", "tokens"):
            value = params.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        collect_usage_candidates(params, candidates)
    if not candidates:
        return None
    merged: dict[str, Any] = {}
    for candidate in candidates:
        merge_usage_maps(merged, candidate)
    return merged or None


def append_text_fragments(value: Any, sink: list[str]) -> None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            sink.append(text)
        return
    if isinstance(value, list):
        for item in value:
            append_text_fragments(item, sink)
        return
    if not isinstance(value, dict):
        return

    text_value = value.get("text")
    if isinstance(text_value, str):
        text = text_value.strip()
        if text:
            sink.append(text)

    for key in ("summary", "content", "parts", "reasoning"):
        if key in value:
            append_text_fragments(value.get(key), sink)


def item_id(item: dict[str, Any]) -> str | None:
    value = item.get("id")
    if isinstance(value, str) and value:
        return value
    return None


def extract_delta_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(extract_delta_text(item) for item in value)
    if isinstance(value, dict):
        chunks: list[str] = []
        for key in (
            "delta",
            "text",
            "textDelta",
            "summaryTextDelta",
            "chunk",
            "output",
            "content",
            "value",
        ):
            if key in value:
                chunks.append(extract_delta_text(value.get(key)))
        return "".join(chunks)
    return ""


def extract_delta_item_id(params: dict[str, Any]) -> str | None:
    for key in ("itemId", "item_id"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    item = params.get("item")
    if isinstance(item, dict):
        return item_id(item)
    return None


def joined_delta(chunks_by_item_id: dict[str, list[str]], key: str | None) -> str:
    if not isinstance(key, str) or not key:
        return ""
    chunks = chunks_by_item_id.get(key, [])
    if not chunks:
        return ""
    return "".join(chunks)


def reasoning_text_from_item(item: dict[str, Any], deltas: dict[str, list[str]]) -> str:
    direct = item.get("text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    fragments: list[str] = []
    for key in ("summary", "content", "reasoning"):
        if key in item:
            append_text_fragments(item.get(key), fragments)

    delta_text = joined_delta(deltas, item_id(item))
    if delta_text.strip():
        fragments.append(delta_text.strip())

    if fragments:
        return "\n".join(fragments)

    fallback = json_dumps(item)
    return fallback if fallback else ""


def agent_message_text_from_item(item: dict[str, Any], deltas: dict[str, list[str]]) -> str:
    direct = item.get("text")
    if isinstance(direct, str) and direct.strip():
        return direct

    fragments: list[str] = []
    for key in ("content", "parts"):
        if key in item:
            append_text_fragments(item.get(key), fragments)

    delta_text = joined_delta(deltas, item_id(item))
    if delta_text.strip():
        fragments.append(delta_text)

    if fragments:
        return "\n".join(fragments)
    return ""


def command_output_from_item(item: dict[str, Any], deltas: dict[str, list[str]]) -> str:
    for key in ("aggregated_output", "aggregatedOutput"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    delta = joined_delta(deltas, item_id(item))
    return delta


def files_from_file_change_item(item: dict[str, Any]) -> list[str]:
    files: list[str] = []
    changes = item.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict):
                path = change.get("path")
                if isinstance(path, str) and path:
                    files.append(path)
    return files


def part_from_item(
    item: dict[str, Any],
    *,
    agent_message_deltas: dict[str, list[str]],
    reasoning_deltas: dict[str, list[str]],
    command_output_deltas: dict[str, list[str]],
) -> dict[str, Any] | None:
    kind = item_type_key(item.get("type"))

    if kind == "reasoning":
        return {"type": "reasoning", "text": reasoning_text_from_item(item, reasoning_deltas)}

    if kind == "agent_message":
        return {"type": "text", "text": agent_message_text_from_item(item, agent_message_deltas)}

    if kind == "command_execution":
        command = str(item.get("command") or "")
        output = command_output_from_item(item, command_output_deltas)
        state: dict[str, Any] = {
            "status": item.get("status", "completed"),
            "input": {"command": command},
            "output": output,
            "exit_code": item.get("exit_code") if "exit_code" in item else item.get("exitCode"),
        }
        return {"type": "tool", "tool": "bash", "state": state}

    if kind == "mcp_tool_call":
        tool_name = item.get("tool") or "mcp_tool_call"
        state = {
            "status": item.get("status", "completed"),
            "input": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            "output": mcp_output_payload(item.get("result"), item.get("error")),
            "error": item.get("error"),
        }
        return {"type": "tool", "tool": tool_name, "state": state}

    if kind == "collab_tool_call":
        receiver_thread_id = item.get("receiverThreadId") or item.get(
            "receiver_thread_id"
        )
        state = {
            "status": item.get("status", "completed"),
            "input": {
                "tool": item.get("tool"),
                "prompt": item.get("prompt"),
                "receiver_thread_id": receiver_thread_id,
            },
            "output": "",
        }
        return {"type": "tool", "tool": "collab_tool_call", "state": state}

    if kind == "web_search":
        state = {
            "status": "completed",
            "input": {"query": item.get("query", "")},
            "output": json_dumps({"action": item.get("action")}),
        }
        return {"type": "tool", "tool": "web_search", "state": state}

    if kind == "file_change":
        return {"type": "patch", "files": files_from_file_change_item(item)}

    return None


def event_token_usage(notification: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    params = as_dict(notification.get("params"))
    for source in (
        params.get("usage"),
        params.get("token_usage"),
        params.get("tokenUsage"),
        params.get("tokens"),
        item.get("usage"),
        item.get("token_usage"),
        item.get("tokenUsage"),
        item.get("tokens"),
    ):
        if isinstance(source, dict) and source:
            return source
    return None


def trace_event_from_item(
    *,
    notification: dict[str, Any],
    item: dict[str, Any],
    agent_message_deltas: dict[str, list[str]],
    reasoning_deltas: dict[str, list[str]],
    command_output_deltas: dict[str, list[str]],
) -> TraceEvent | None:
    part = part_from_item(
        item,
        agent_message_deltas=agent_message_deltas,
        reasoning_deltas=reasoning_deltas,
        command_output_deltas=command_output_deltas,
    )
    if not isinstance(part, dict):
        return None

    part_type = part.get("type")
    if part_type not in MEANINGFUL_PART_TYPES:
        return None

    item_type = item_type_key(item.get("type"))
    has_file_change = item_type == "file_change" or part_type == "patch"
    files = files_from_file_change_item(item) if has_file_change else []
    if part_type == "patch" and not files:
        part_files = part.get("files")
        if isinstance(part_files, list):
            files = [f for f in part_files if isinstance(f, str) and f]

    summary: str | None = None
    content: str | None = None
    tool_name: str | None = None
    tool_status: str | None = None
    tool_input: Any = None
    tool_output: Any = None
    tool_error: Any = None
    tool_exit_code: int | None = None

    if part_type in {"reasoning", "text"}:
        text = str(part.get("text") or "").strip()
        content = text or None
        summary = truncate_for_trace(text) if text else None
    elif part_type == "patch":
        summary = f"patch ({len(files)} files)"
    elif part_type == "tool":
        tool_name_raw = part.get("tool")
        tool_name = str(tool_name_raw) if tool_name_raw is not None else None
        state = as_dict(part.get("state"))
        status_raw = state.get("status")
        tool_status = str(status_raw) if status_raw is not None else None
        tool_input = state.get("input")
        tool_output = state.get("output")
        tool_error = state.get("error")
        tool_exit_code = parse_int_maybe(state.get("exit_code"))

        if tool_name == "bash" and isinstance(tool_input, dict):
            summary = truncate_for_trace(str(tool_input.get("command") or ""))
        elif tool_name == "run_tests" and isinstance(tool_input, dict):
            test_path = str(tool_input.get("test_path") or "").strip()
            summary = f"run_tests {test_path}" if test_path else "run_tests"
        else:
            summary = tool_name

    test_call = extract_run_tests_call(item) if item_type == "mcp_tool_call" else None

    return TraceEvent(
        part_type=part_type,
        item_type=item_type,
        summary=summary,
        content=content,
        has_file_change=has_file_change,
        files=files,
        tool_name=tool_name,
        tool_status=tool_status,
        tool_input=tool_input,
        tool_output=tool_output,
        tool_error=tool_error,
        tool_exit_code=tool_exit_code,
        token_usage=event_token_usage(notification, item),
        provider_part=part,
        provider_item=item,
        provider_event=notification,
        test_call=test_call,
        timestamp_ms=int(time.time() * 1000),
    )


def start_stream_drain_thread(stream: Any, sink: list[str]) -> threading.Thread | None:
    if stream is None:
        return None

    def _drain() -> None:
        for line in stream:
            sink.append(line)

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    return reader


def format_elapsed(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours} h {minutes} m {secs} s"
    if minutes > 0:
        return f"{minutes} m {secs} s"
    return f"{secs} s"


def clean_progress_content(value: Any, *, truncate_content: bool = True, limit: int = 240) -> str:
    text = str(value or "")
    if not truncate_content:
        return text
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def log_progress(
    *,
    parts_seen: int,
    max_parts: int,
    part_offset: int = 0,
    part_total: int = 0,
    turn_number: int = 0,
    turn_total: int = 0,
    elapsed_seconds: int | None = None,
    description: str,
    content: str | None = None,
    truncate_content: bool = True,
) -> None:
    shown_parts = max(0, part_offset) + max(0, parts_seen)
    if part_total > 0:
        parts_label = f"parts={shown_parts}/{part_total}"
    elif max_parts > 0:
        parts_label = f"parts={shown_parts}/{max(0, part_offset) + max_parts}"
    else:
        parts_label = f"parts={shown_parts}"
    if turn_total > 0 and turn_number > 0:
        turn_label = f"turn={turn_number}/{turn_total}"
    elif turn_number > 0:
        turn_label = f"turn={turn_number}"
    else:
        turn_label = ""
    counters_label = (
        f"{turn_label} {parts_label}".strip()
        if turn_label
        else parts_label
    )
    elapsed_label = (
        f"[{format_elapsed(elapsed_seconds)}] "
        if isinstance(elapsed_seconds, int)
        else ""
    )
    print(
        f"{elapsed_label}[{counters_label}] {description}",
        file=sys.stderr,
        flush=True,
    )
    if content:
        print(
            clean_progress_content(content, truncate_content=truncate_content),
            file=sys.stderr,
            flush=True,
        )


def start_progress_heartbeat(
    stats: dict[str, int | float],
    stop_event: threading.Event,
    *,
    max_parts: int,
    part_offset: int = 0,
    part_total: int = 0,
    turn_number: int = 0,
    turn_total: int = 0,
    interval_sec: int = 15,
) -> threading.Thread:
    def _heartbeat() -> None:
        while not stop_event.wait(interval_sec):
            elapsed_seconds = int(
                time.monotonic() - float(stats["started_at"])
            )
            log_progress(
                parts_seen=int(stats["meaningful_parts"]),
                max_parts=max_parts,
                part_offset=part_offset,
                part_total=part_total,
                turn_number=turn_number,
                turn_total=turn_total,
                elapsed_seconds=elapsed_seconds,
                description="heartbeat",
            )

    thread = threading.Thread(target=_heartbeat, daemon=True)
    thread.start()
    return thread


def emit_trace_event(payload: TraceEvent) -> None:
    print(
        f"{TRACE_EVENT_PREFIX}{payload.model_dump_json()}",
        file=sys.stderr,
        flush=True,
    )


def summarize_notification(notification: dict[str, Any]) -> tuple[str, str | None] | None:
    method = notification.get("method")
    if not isinstance(method, str):
        return None
    key = method_key(method)
    params = as_dict(notification.get("params"))

    if key in {"thread/started", "turn/started", "turn/completed"}:
        if key == "thread/started":
            thread_obj = as_dict(params.get("thread"))
            thread_id = thread_obj.get("id")
            if isinstance(thread_id, str) and thread_id:
                return (key, f"thread_id={thread_id}")
        if key == "turn/completed":
            turn_obj = as_dict(params.get("turn"))
            status = turn_obj.get("status")
            error_obj = turn_obj.get("error")
            if isinstance(error_obj, dict):
                message = error_obj.get("message")
                if isinstance(message, str) and message:
                    return (key, f"status={status} error={message}")
            return (key, f"status={status}")
        return (key, None)

    if key == "item/completed":
        item = as_dict(params.get("item"))
        kind = item_type_key(item.get("type"))
        if kind == "command_execution":
            command = str(item.get("command") or "").strip()
            return (f"{key} {kind}", command or None)
        if kind == "mcp_tool_call":
            tool_name = str(item.get("tool") or "mcp_tool_call")
            status = str(item.get("status") or "")
            details_lines: list[str] = [f"tool={tool_name}"]
            if status:
                details_lines.append(f"status={status}")
            output_preview = format_mcp_output_for_log(
                tool_name,
                item.get("result"),
                item.get("error"),
            )
            if output_preview:
                details_lines.append("output:")
                details_lines.append(output_preview)
            return (f"{key} {kind}", "\n".join(details_lines))
        if kind == "agent_message":
            text = str(item.get("text") or "").strip()
            return (f"{key} {kind}", text or None)
        return (f"{key} {kind}", None)

    if key == "thread/token_usage/updated":
        return (key, None)
    if key == "turn/diff/updated":
        return (key, None)

    return None


def extract_turn_error(turn_obj: dict[str, Any]) -> str | None:
    error_obj = turn_obj.get("error")
    if isinstance(error_obj, dict):
        message = error_obj.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(error_obj, str) and error_obj.strip():
        return error_obj.strip()
    return None


class AppServerRPC:
    def __init__(self, *, env: dict[str, str], cwd: str) -> None:
        self._proc = subprocess.Popen(
            ["codex", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=cwd,
            bufsize=1,
        )
        self._next_id = 1
        self._pending_responses: dict[int, dict[str, Any]] = {}
        self._stderr_chunks: list[str] = []
        self._stderr_reader = start_stream_drain_thread(self._proc.stderr, self._stderr_chunks)

    def close(self) -> str:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=1)
        return "".join(self._stderr_chunks)

    def send(self, payload: dict[str, Any]) -> None:
        stdin = self._proc.stdin
        if stdin is None:
            raise RuntimeError("app-server stdin is not available")
        stdin.write(json_dumps(payload) + "\n")
        stdin.flush()

    def read_message(self) -> dict[str, Any]:
        stdout = self._proc.stdout
        if stdout is None:
            raise RuntimeError("app-server stdout is not available")

        while True:
            line = stdout.readline()
            if not line:
                raise RuntimeError("app-server stream closed")
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

    def response_id(self, message: dict[str, Any]) -> int | None:
        return parse_int_maybe(message.get("id"))

    def cache_response(self, message: dict[str, Any]) -> None:
        rid = self.response_id(message)
        if rid is None:
            return
        self._pending_responses[rid] = message

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method, "params": params or {}}
        self.send(payload)

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_notification: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        rid = self._next_id
        self._next_id += 1
        self.send({"method": method, "id": rid, "params": params or {}})

        while True:
            cached = self._pending_responses.pop(rid, None)
            if isinstance(cached, dict):
                error = cached.get("error")
                if isinstance(error, dict):
                    message = error.get("message")
                    if isinstance(message, str) and message:
                        raise RuntimeError(f"{method}: {message}")
                    raise RuntimeError(f"{method}: {json_dumps(error)}")
                result = cached.get("result")
                return result if isinstance(result, dict) else as_dict(result)

            message = self.read_message()
            response_id = self.response_id(message)
            if response_id is not None:
                self.cache_response(message)
                continue
            if on_notification is not None:
                on_notification(message)

def build_codex_env(api_key: str | None) -> dict[str, str]:
    env = dict(os.environ)
    codex_home = env.setdefault("CODEX_HOME", "/tmp/codex-home")
    Path(codex_home).mkdir(parents=True, exist_ok=True)
    env.setdefault("RUST_LOG", "error")
    if api_key:
        env["CODEX_API_KEY"] = api_key
        env["OPENAI_API_KEY"] = api_key
    return env


def resolve_workspace_cwd() -> str:
    workspace = Path("/workspace")
    if workspace.is_dir():
        return str(workspace)
    return os.getcwd()


def request_with_fallback(
    client: AppServerRPC,
    *,
    method: str,
    params_candidates: list[dict[str, Any]],
    on_notification: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    last_error: Exception | None = None
    for params in params_candidates:
        try:
            return client.request(method, params, on_notification=on_notification)
        except Exception as error:  # noqa: BLE001
            last_error = error
    if last_error is None:
        raise RuntimeError(f"{method}: no parameter candidates provided")
    raise RuntimeError(f"{method} failed: {last_error}")


def run_codex_turn(
    *,
    session_id: str | None,
    text: str,
    model: str,
    api_key: str | None,
    max_parts: int = 0,
    part_offset: int = 0,
    part_total: int = 0,
    turn_number: int = 0,
    turn_total: int = 0,
) -> CodexTurnResult:
    turn_started_at = time.monotonic()
    log_progress(
        parts_seen=0,
        max_parts=max_parts,
        part_offset=part_offset,
        part_total=part_total,
        turn_number=turn_number,
        turn_total=turn_total,
        elapsed_seconds=0,
        description="launching codex app-server",
    )

    env = build_codex_env(api_key)
    execution_cwd = resolve_workspace_cwd()
    client = AppServerRPC(env=env, cwd=execution_cwd)

    events: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    meaningful_parts_seen = 0
    aborted_for_part_limit = False

    progress_stats: dict[str, int | float] = {
        "started_at": turn_started_at,
        "events": 0,
        "meaningful_parts": 0,
    }
    heartbeat_stop = threading.Event()
    heartbeat_thread = start_progress_heartbeat(
        progress_stats,
        heartbeat_stop,
        max_parts=max_parts,
        part_offset=part_offset,
        part_total=part_total,
        turn_number=turn_number,
        turn_total=turn_total,
    )

    resolved_thread_id: str | None = None
    turn_id: str | None = None
    turn_completed = False
    turn_status = "inProgress"
    turn_error: str | None = None
    latest_turn_diff: str | None = None
    usage_updates: dict[str, Any] = {}
    interrupt_sent = False

    agent_message_deltas: dict[str, list[str]] = {}
    reasoning_deltas: dict[str, list[str]] = {}
    command_output_deltas: dict[str, list[str]] = {}

    def append_delta(target: dict[str, list[str]], key: str | None, value: str) -> None:
        if not isinstance(key, str) or not key or not value:
            return
        target.setdefault(key, []).append(value)

    def on_notification(notification: dict[str, Any]) -> None:
        nonlocal meaningful_parts_seen
        nonlocal aborted_for_part_limit
        nonlocal resolved_thread_id
        nonlocal turn_id
        nonlocal turn_completed
        nonlocal turn_status
        nonlocal turn_error
        nonlocal latest_turn_diff
        nonlocal interrupt_sent

        events.append(notification)
        progress_stats["events"] = len(events)

        summary = summarize_notification(notification)
        if summary is not None:
            description, content = summary
            log_progress(
                parts_seen=meaningful_parts_seen,
                max_parts=max_parts,
                part_offset=part_offset,
                part_total=part_total,
                turn_number=turn_number,
                turn_total=turn_total,
                elapsed_seconds=int(
                    time.monotonic() - turn_started_at
                ),
                description=description,
                content=content,
                truncate_content="mcp_tool_call" not in description,
            )

        method = notification.get("method")
        if not isinstance(method, str):
            return
        key = method_key(method)
        params = as_dict(notification.get("params"))

        if key == "thread/started":
            thread_obj = as_dict(params.get("thread"))
            thread_started_id = thread_obj.get("id")
            if isinstance(thread_started_id, str) and thread_started_id:
                resolved_thread_id = thread_started_id
            return

        if key == "turn/started":
            turn_obj = as_dict(params.get("turn"))
            turn_started_id = turn_obj.get("id")
            if isinstance(turn_started_id, str) and turn_started_id:
                turn_id = turn_started_id
            return

        if key == "turn/completed":
            turn_obj = as_dict(params.get("turn"))
            status = turn_obj.get("status")
            if isinstance(status, str) and status:
                turn_status = status
            turn_error = extract_turn_error(turn_obj)
            turn_completed = True
            return

        if key == "turn/diff/updated":
            diff_value = params.get("diff")
            if isinstance(diff_value, str):
                latest_turn_diff = diff_value
            elif isinstance(diff_value, dict):
                latest_turn_diff = json_dumps(diff_value)
            return

        if key == "thread/token_usage/updated":
            collect_usage_candidates(params, [])
            for usage_key in ("usage", "token_usage", "tokenUsage", "tokens"):
                candidate = params.get(usage_key)
                if isinstance(candidate, dict) and candidate:
                    merge_usage_maps(usage_updates, candidate)
            return

        if key == "item/agent_message/delta":
            item_key = extract_delta_item_id(params)
            delta_text = extract_delta_text(params)
            append_delta(agent_message_deltas, item_key, delta_text)
            return

        if key in {"item/reasoning/text_delta", "item/reasoning/summary_text_delta"}:
            item_key = extract_delta_item_id(params)
            delta_text = extract_delta_text(params)
            append_delta(reasoning_deltas, item_key, delta_text)
            return

        if key == "item/command_execution/output_delta":
            item_key = extract_delta_item_id(params)
            delta_text = extract_delta_text(params)
            append_delta(command_output_deltas, item_key, delta_text)
            return

        if key != "item/completed":
            return

        item = as_dict(params.get("item"))
        if not item:
            return

        part = part_from_item(
            item,
            agent_message_deltas=agent_message_deltas,
            reasoning_deltas=reasoning_deltas,
            command_output_deltas=command_output_deltas,
        )
        if isinstance(part, dict):
            parts.append(part)

        trace_event = trace_event_from_item(
            notification=notification,
            item=item,
            agent_message_deltas=agent_message_deltas,
            reasoning_deltas=reasoning_deltas,
            command_output_deltas=command_output_deltas,
        )
        if trace_event is None:
            return

        emit_trace_event(trace_event)
        meaningful_parts_seen += 1
        progress_stats["meaningful_parts"] = meaningful_parts_seen

        if (
            max_parts > 0
            and meaningful_parts_seen >= max_parts
            and not interrupt_sent
            and isinstance(resolved_thread_id, str)
            and resolved_thread_id
            and isinstance(turn_id, str)
            and turn_id
        ):
            interrupt_sent = True
            aborted_for_part_limit = True
            log_progress(
                parts_seen=meaningful_parts_seen,
                max_parts=max_parts,
                part_offset=part_offset,
                part_total=part_total,
                turn_number=turn_number,
                turn_total=turn_total,
                elapsed_seconds=int(
                    time.monotonic() - turn_started_at
                ),
                description="part limit reached",
                content="sending turn/interrupt",
            )
            try:
                client.request(
                    "turn/interrupt",
                    {
                        "threadId": resolved_thread_id,
                        "turnId": turn_id,
                    },
                    on_notification=on_notification,
                )
            except Exception as error:  # noqa: BLE001
                log_progress(
                    parts_seen=meaningful_parts_seen,
                    max_parts=max_parts,
                    part_offset=part_offset,
                    part_total=part_total,
                    turn_number=turn_number,
                    turn_total=turn_total,
                    elapsed_seconds=int(
                        time.monotonic() - turn_started_at
                    ),
                    description="turn/interrupt warning",
                    content=str(error),
                )

    try:
        initialize_candidates: list[dict[str, Any]] = [
            {
                "clientInfo": {
                    "name": "envoi_trace_runner",
                    "title": "envoi-trace",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                },
            },
            {
                "clientInfo": {
                    "name": "envoi_trace_runner",
                    "title": "envoi-trace",
                    "version": "0.1.0",
                },
            },
        ]
        request_with_fallback(
            client,
            method="initialize",
            params_candidates=initialize_candidates,
            on_notification=on_notification,
        )
        client.notify("initialized", {})

        session_value = (session_id or "").strip()

        if session_value.startswith("fork:"):
            base_thread_id = session_value.split(":", 1)[1].strip()
            resume_result = request_with_fallback(
                client,
                method="thread/fork",
                params_candidates=[{"threadId": base_thread_id}],
                on_notification=on_notification,
            )
            thread_obj = as_dict(resume_result.get("thread"))
            thread_id_value = thread_obj.get("id")
            resolved_thread_id = (
                thread_id_value if isinstance(thread_id_value, str) else None
            )
        elif session_value and not session_value.startswith("pending-"):
            try:
                resume_result = request_with_fallback(
                    client,
                    method="thread/resume",
                    params_candidates=[{"threadId": session_value}],
                    on_notification=on_notification,
                )
                thread_obj = as_dict(resume_result.get("thread"))
                resolved_thread_id = (
                    thread_obj.get("id") if isinstance(thread_obj.get("id"), str) else session_value
                )
            except Exception:
                start_result = request_with_fallback(
                    client,
                    method="thread/start",
                    params_candidates=[{"model": model}, {}],
                    on_notification=on_notification,
                )
                thread_obj = as_dict(start_result.get("thread"))
                thread_id_value = thread_obj.get("id")
                resolved_thread_id = (
                    thread_id_value if isinstance(thread_id_value, str) else None
                )
        else:
            start_result = request_with_fallback(
                client,
                method="thread/start",
                params_candidates=[{"model": model}, {}],
                on_notification=on_notification,
            )
            thread_obj = as_dict(start_result.get("thread"))
            thread_id_value = thread_obj.get("id")
            resolved_thread_id = (
                thread_id_value if isinstance(thread_id_value, str) else None
            )

        if not isinstance(resolved_thread_id, str) or not resolved_thread_id:
            raise RuntimeError("thread id missing from app-server")

        input_items = [{"type": "text", "text": text}]
        turn_start_candidates: list[dict[str, Any]] = [
            {
                "threadId": resolved_thread_id,
                "input": input_items,
                "cwd": execution_cwd,
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "dangerFullAccess"},
                "model": model,
                "effort": "high",
            },
            {
                "threadId": resolved_thread_id,
                "input": input_items,
                "cwd": execution_cwd,
                "approvalPolicy": "never",
                "model": model,
            },
            {
                "threadId": resolved_thread_id,
                "input": input_items,
                "model": model,
            },
            {
                "threadId": resolved_thread_id,
                "input": input_items,
            },
        ]
        turn_start_result = request_with_fallback(
            client,
            method="turn/start",
            params_candidates=turn_start_candidates,
            on_notification=on_notification,
        )
        turn_obj = as_dict(turn_start_result.get("turn"))
        started_turn_id = turn_obj.get("id")
        if isinstance(started_turn_id, str) and started_turn_id:
            turn_id = started_turn_id
        started_status = turn_obj.get("status")
        if isinstance(started_status, str) and started_status in {
            "completed",
            "failed",
            "interrupted",
        }:
            turn_status = started_status
            turn_error = extract_turn_error(turn_obj)
            turn_completed = True

        while not turn_completed:
            message = client.read_message()
            response_id = parse_int_maybe(message.get("id"))
            if response_id is not None:
                client.cache_response(message)
                continue
            on_notification(message)

        usage = extract_usage_from_events(events) or {}
        if usage_updates:
            merge_usage_maps(usage, usage_updates)
        usage_obj = usage or None

        if meaningful_parts_seen == 0:
            meaningful_parts_seen = sum(
                1
                for part in parts
                if isinstance(part, dict) and part.get("type") in MEANINGFUL_PART_TYPES
            )

        now_ms = int(time.time() * 1000)
        mid = f"{resolved_thread_id}:{now_ms}" if resolved_thread_id else f"codex-message:{now_ms}"
        assistant_message: dict[str, Any] = {
            "info": {
                "id": mid,
                "role": "assistant",
                "sessionID": resolved_thread_id or "",
                "time": {"created": now_ms},
            },
            "parts": parts,
            "_events": events,
        }
        if usage_obj is not None:
            assistant_message["info"]["tokens"] = usage_obj

        body = {
            "info": {"id": mid},
            "parts": parts,
            "_events": events,
            "_message": assistant_message,
            "_session_id": resolved_thread_id,
            "_usage": usage_obj,
            "_stream": {
                "events_observed": len(events),
                "meaningful_parts_seen": meaningful_parts_seen,
                "aborted_for_part_limit": aborted_for_part_limit,
                "turn_status": turn_status,
                "latest_turn_diff": latest_turn_diff,
            },
        }

        ok = turn_status in {"completed", "interrupted"}
        if aborted_for_part_limit and turn_status in {"inProgress", "interrupted", "completed"}:
            ok = True

        error_text = turn_error or ""
        if not ok and not error_text:
            error_text = "codex app-server turn failed"

        elapsed = int(time.monotonic() - float(progress_stats["started_at"]))
        log_progress(
            parts_seen=meaningful_parts_seen,
            max_parts=max_parts,
            part_offset=part_offset,
            part_total=part_total,
            turn_number=turn_number,
            turn_total=turn_total,
            elapsed_seconds=elapsed,
            description="completed",
            content=(
                f"status={turn_status} elapsed={elapsed}s events={len(events)} "
                f"aborted_for_part_limit={aborted_for_part_limit}"
            ),
        )

        stderr_text = client.close()
        return CodexTurnResult(
            ok=ok,
            status_code=200 if ok else 500,
            body=body,
            error=None if ok else error_text,
            stderr=stderr_text,
        )
    except Exception as error:  # noqa: BLE001
        stderr_text = client.close()
        return CodexTurnResult(
            ok=False,
            status_code=500,
            body=None,
            error=str(error),
            stderr=stderr_text,
        )
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat_stream = subparsers.add_parser("chat-stream")
    chat_stream.add_argument("--session-id", default="")
    chat_stream.add_argument("--text-file", required=True)
    chat_stream.add_argument("--model", required=True)
    chat_stream.add_argument("--max-parts", type=int, default=0)
    chat_stream.add_argument("--part-offset", type=int, default=0)
    chat_stream.add_argument("--part-total", type=int, default=0)
    chat_stream.add_argument("--turn-number", type=int, default=0)
    chat_stream.add_argument("--turn-total", type=int, default=0)
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
            part_offset=max(0, args.part_offset),
            part_total=max(0, args.part_total),
            turn_number=max(0, args.turn_number),
            turn_total=max(0, args.turn_total),
        )
        print(result.model_dump_json())
        return


if __name__ == "__main__":
    main()


# -------------------------------------------------------------------
# CodexAgent: Agent implementation (runner-side only)
# -------------------------------------------------------------------
# The code below is only executed when imported by runner.py, never
# when this file runs as a standalone sandbox script.

try:
    import builtins

    from envoi_code.agents.base import (
        AgentCredentials,
        AgentSetupContext,
        AgentTurnOutcome,
        SandboxImageRequirements,
    )
    from envoi_code.agents.setup import run_workspace_init
    from envoi_code.sandbox.base import Sandbox
    from envoi_code.utils.helpers import (
        decode_b64_to_text,
        environment_upload_items,
        load_local_codex_auth_json_b64,
        parse_codex_auth_json,
        run_sandbox_client,
        tprint,
        truncate_text,
        upload_files_parallel,
    )
    from envoi_code.utils.parsing import agent_message_id, parse_trace_event_line

    CODEX_SCRIPT = "/sandbox/codex_client.py"
    CODEX_LABEL = "codex-cli"
    CODEX_HOME_DIR = "/tmp/codex-home"
    DEFAULT_CODEX_MODEL = "gpt-5.3-codex"

    CODEX_CONFIG_TOML_BASE = """\
model = "MODEL_PLACEHOLDER"
model_reasoning_effort = "high"
"""

    CODEX_CONFIG_TOML_MCP = """\
[mcp_servers.tests]
command = "python3"
args = ["/sandbox/mcp_server.py"]
enabled = true
required = false
tool_timeout_sec = 3600
"""

    CODEX_INSTALL_SCRIPT = """\
set -euo pipefail
export PATH="$HOME/.cargo/bin:$PATH"

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)
        TARGET_TRIPLE="x86_64-unknown-linux-musl"
        ;;
    aarch64|arm64)
        TARGET_TRIPLE="aarch64-unknown-linux-musl"
        ;;
    *)
        echo "[setup] ERROR: unsupported architecture for Codex binary: $ARCH"
        exit 1
        ;;
esac

CODEX_TARBALL_URL="https://github.com/openai/codex/releases/latest/download/codex-${TARGET_TRIPLE}.tar.gz"
tmpdir="$(mktemp -d)"
echo "[setup] downloading Codex CLI (${TARGET_TRIPLE})"
curl -fsSL "$CODEX_TARBALL_URL" -o "$tmpdir/codex.tar.gz"
echo "[setup] extracting Codex CLI archive"
tar -xzf "$tmpdir/codex.tar.gz" -C "$tmpdir"

CODEX_EXTRACTED_BIN="$tmpdir/codex-${TARGET_TRIPLE}"
if [ ! -f "$CODEX_EXTRACTED_BIN" ]; then
    echo "[setup] ERROR: expected Codex binary not found at ${CODEX_EXTRACTED_BIN}"
    ls -la "$tmpdir"
    exit 1
fi

echo "[setup] installing Codex CLI to /usr/local/bin"
install -m 0755 "$CODEX_EXTRACTED_BIN" /usr/local/bin/codex
if CODEX_VERSION="$(codex --version 2>/dev/null)"; then
    echo "[setup] codex version: ${CODEX_VERSION}"
fi
mkdir -p /tmp/codex-home
echo "[setup] codex install complete"
"""

    class CodexCredentials(AgentCredentials):
        """Codex-specific credentials with optional auth.json."""

        auth_json: str | None = None

    class CodexAgent:
        """Agent implementation for Codex."""

        @property
        def name(self) -> str:
            return "codex"

        @property
        def session_id(self) -> str | None:
            return self.current_session_id

        @property
        def log_files(self) -> list[str]:
            return ["/tmp/codex.log", "/tmp/envoi.log"]

        def __init__(self) -> None:
            self.sandbox: Sandbox | None = None
            self.agent_model: str = ""
            self.api_key: str = ""
            self.auth_json: str | None = None
            self.api_key_file: str | None = None
            self.current_session_id: str | None = None
            self.seen_message_ids: set[str] = set()

        # -- static methods -----------------------------------------

        @staticmethod
        def resolve_credentials(
            codex_auth_json_b64: str | None = None,
        ) -> CodexCredentials:
            """Resolve Codex credentials from env vars / b64 arg."""
            codex_auth_json: str | None = None
            env_b64 = os.environ.get(
                "CODEX_AUTH_JSON_B64", "",
            ).strip()
            env_raw = os.environ.get(
                "CODEX_AUTH_JSON", "",
            ).strip()

            if codex_auth_json_b64:
                decoded = decode_b64_to_text(
                    codex_auth_json_b64,
                    label="codex_auth_json_b64 arg",
                )
                codex_auth_json = parse_codex_auth_json(
                    decoded, label="codex_auth_json_b64 arg",
                )
            elif env_b64:
                decoded = decode_b64_to_text(
                    env_b64, label="CODEX_AUTH_JSON_B64",
                )
                codex_auth_json = parse_codex_auth_json(
                    decoded, label="CODEX_AUTH_JSON_B64",
                )
            elif env_raw:
                codex_auth_json = parse_codex_auth_json(
                    env_raw, label="CODEX_AUTH_JSON",
                )

            api_key = (
                os.environ.get("CODEX_API_KEY", "").strip()
                or os.environ.get("OPENAI_API_KEY", "").strip()
            )
            if not codex_auth_json and not api_key:
                raise RuntimeError(
                    "No Codex credentials found. Provide one of: "
                    "~/.codex/auth.json via --codex-auth-file, "
                    "CODEX_AUTH_JSON_B64/CODEX_AUTH_JSON, "
                    "or CODEX_API_KEY/OPENAI_API_KEY."
                )
            return CodexCredentials(
                api_key=api_key, auth_json=codex_auth_json,
            )

        @staticmethod
        def resolve_model(model: str | None) -> str:
            return model or DEFAULT_CODEX_MODEL

        @staticmethod
        def image_requirements() -> SandboxImageRequirements:
            return SandboxImageRequirements()

        @staticmethod
        def load_local_auth_b64(path: str) -> str | None:
            """Load a local auth.json and return as base64."""
            return load_local_codex_auth_json_b64(path)

        # -- instance methods ----------------------------------------

        def compute_turn_timeout(
            self,
            *,
            remaining_parts: int,
            remaining_run_seconds: float,
            message_timeout_seconds: int,
        ) -> int:
            return max(1, int(remaining_run_seconds))

        async def run_client(
            self,
            args: list[str],
            *,
            timeout: int = 60,
            quiet: bool = False,
            stream_output: bool = False,
            on_stderr_line=None,
        ) -> dict[str, Any] | None:
            assert self.sandbox is not None
            return await run_sandbox_client(
                self.sandbox,
                CODEX_SCRIPT,
                CODEX_LABEL,
                args,
                timeout=timeout,
                quiet=quiet,
                stream_output=stream_output,
                on_stderr_line=on_stderr_line,
            )

        # -- protocol methods ---------------------------------------

        async def setup(
            self,
            sandbox: Sandbox,
            ctx: AgentSetupContext,
        ) -> None:
            self.sandbox = sandbox
            self.agent_model = ctx.model
            credentials = ctx.credentials
            self.api_key = credentials.api_key

            auth_json: str | None = None
            if isinstance(credentials, CodexCredentials):
                auth_json = credentials.auth_json
            self.auth_json = auth_json

            builtins.print(
                f"[setup] agent=codex model={ctx.model}",
                flush=True,
            )
            codex_config = CODEX_CONFIG_TOML_BASE.replace(
                "MODEL_PLACEHOLDER", ctx.model,
            )
            if ctx.mcp_enabled and ctx.mcp_server_content.strip():
                codex_config = (
                    codex_config.rstrip()
                    + "\n\n"
                    + CODEX_CONFIG_TOML_MCP.strip()
                    + "\n"
                )
            setup_uploads: list[tuple[str, str]] = [
                ("/sandbox/codex_client.py", CODEX_CLIENT_CONTENT),
                (
                    f"{CODEX_HOME_DIR}/config.toml",
                    codex_config,
                ),
                ("/workspace/.gitignore", ctx.workspace_gitignore),
            ]
            if ctx.mcp_enabled and ctx.mcp_server_content.strip():
                setup_uploads.append(
                    ("/sandbox/mcp_server.py", ctx.mcp_server_content),
                )
            if self.api_key:
                setup_uploads.append(
                    ("/tmp/upload/codex_api_key.txt", self.api_key),
                )
                self.api_key_file = "/tmp/upload/codex_api_key.txt"
            if auth_json:
                setup_uploads.append(
                    (f"{CODEX_HOME_DIR}/auth.json", auth_json),
                )

            await upload_files_parallel(
                sandbox, setup_uploads, log_upload=True,
            )

            if ctx.env_files:
                py, c, txt = ctx.env_files
                await upload_files_parallel(
                    sandbox,
                    environment_upload_items(py, c, txt),
                    log_upload=True,
                )
                builtins.print(
                    f"[setup] uploaded {len(py)} py, "
                    f"{len(c)} c, {len(txt)} txt files",
                    flush=True,
                )

            await run_workspace_init(sandbox)

            # Install codex binary
            await sandbox.write_file(
                "/tmp/codex_install.sh",
                CODEX_INSTALL_SCRIPT,
                ensure_dir=False,
            )

            async def handle_line(line: str) -> None:
                stripped = line.strip()
                if stripped and stripped.startswith("[setup]"):
                    builtins.print(stripped, flush=True)

            result = await sandbox.run(
                "bash /tmp/codex_install.sh",
                timeout=300,
                on_stdout_line=handle_line,
                on_stderr_line=handle_line,
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Codex install failed (exit {result.exit_code})"
                )

        async def create_session(
            self,
            trajectory_id: str,
        ) -> str:
            self.current_session_id = f"pending-{trajectory_id}"
            return self.current_session_id

        async def run_turn(
            self,
            *,
            prompt_text: str,
            timeout: int,
            current_turn: int,
            remaining_parts_budget: int,
            global_part_count: int,
            global_max_parts: int,
            global_max_turns: int,
            on_stream_part=None,
        ) -> AgentTurnOutcome | None:
            assert self.sandbox is not None
            prompt_path = "/tmp/prompt.txt"
            await self.sandbox.write_file(
                prompt_path,
                prompt_text,
                ensure_dir=False,
            )
            args = [
                "chat-stream",
                "--session-id",
                self.current_session_id or "",
                "--text-file",
                prompt_path,
                "--model",
                self.agent_model,
                "--max-parts",
                str(remaining_parts_budget),
                "--part-offset",
                str(max(0, global_part_count)),
                "--part-total",
                str(max(0, global_max_parts)),
                "--turn-number",
                str(max(0, current_turn)),
                "--turn-total",
                str(max(0, global_max_turns)),
            ]
            if self.api_key_file:
                args.extend(
                    ["--api-key-file", self.api_key_file],
                )

            async def handle_stderr_line(line: str) -> None:
                handled = await parse_trace_event_line(
                    line, on_stream_part,
                )
                if handled:
                    return
                stripped = line.strip()
                if stripped:
                    tprint(
                        "[codex][stderr] "
                        + truncate_text(stripped, limit=500)
                    )

            response = await self.run_client(
                args,
                timeout=timeout,
                stream_output=False,
                on_stderr_line=handle_stderr_line,
            )
            if response is None:
                return None
            if not response.get("ok"):
                error_text = str(response.get("error"))
                if len(error_text) > 800:
                    error_text = (
                        error_text[:800] + "...[truncated]"
                    )
                builtins.print(
                    f"[codex] turn failed: {error_text}",
                    flush=True,
                )
                return None
            body = response.get("body")
            if not isinstance(body, dict):
                builtins.print(
                    "[codex] missing body in response",
                    flush=True,
                )
                return None

            updated_session_id = body.get("_session_id")
            effective_session_id = (
                updated_session_id
                if isinstance(updated_session_id, str)
                and updated_session_id
                else self.current_session_id or ""
            )

            message_obj = body.get("_message")
            new_messages: list[dict[str, Any]] = []
            if isinstance(message_obj, dict):
                mid = agent_message_id(message_obj)
                if mid and mid in self.seen_message_ids:
                    pass
                else:
                    if mid:
                        self.seen_message_ids.add(mid)
                    new_messages.append(message_obj)
            if not new_messages:
                fallback_msg = {
                    "info": {
                        "id": (
                            f"{effective_session_id}:"
                            f"{int(time.time() * 1000)}"
                        ),
                        "role": "assistant",
                        "sessionID": effective_session_id,
                        "time": {
                            "created": int(
                                time.time() * 1000,
                            ),
                        },
                    },
                    "parts": body.get("parts", []),
                }
                fallback_mid = agent_message_id(fallback_msg)
                if fallback_mid:
                    self.seen_message_ids.add(fallback_mid)
                new_messages.append(fallback_msg)

            session_obj = {
                "id": effective_session_id,
                "provider": "codex",
            }
            return AgentTurnOutcome(
                session_id=effective_session_id,
                response=body,
                session_objects=[session_obj],
                session_ids=[effective_session_id],
                new_messages=new_messages,
            )

        def on_turn_complete(
            self,
            outcome: AgentTurnOutcome,
        ) -> None:
            self.current_session_id = outcome.session_id

        def on_resume(
            self,
            existing_messages: list[dict[str, Any]],
        ) -> None:
            for msg in existing_messages:
                mid = agent_message_id(msg)
                if mid:
                    self.seen_message_ids.add(mid)

        async def recover_session(
            self,
            trajectory_id: str,
            attempt: int,
        ) -> str:
            sid = f"recovery-{trajectory_id}-{attempt}"
            self.current_session_id = sid
            return sid

        async def collect_crash_messages(
            self,
            session_id: str,
        ) -> list[dict[str, Any]] | None:
            return None

        async def stop(self) -> None:
            pass

    # The content of this file (agents/codex.py) for uploading into the
    # sandbox as the client script.
    CODEX_CLIENT_CONTENT = Path(__file__).read_text()

except ImportError:
    pass  # Running as standalone sandbox script
