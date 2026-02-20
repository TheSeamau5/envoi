"""
Minimal Codex CLI wrapper for non-interactive turns.

This script runs inside the Modal sandbox and executes `codex exec --json`,
then normalizes JSONL events into a message-like structure used by runner.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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

TRACE_EVENT_PREFIX = "TRACE_EVENT "


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


def truncate_for_trace(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def extract_run_tests_call(item: dict[str, Any]) -> dict[str, Any] | None:
    if str(item.get("tool") or "") != "run_tests":
        return None

    result_obj = _as_dict(item.get("result"))
    payload: Any = result_obj.get("structured_content")
    if payload is None:
        payload = mcp_output_payload(item.get("result"), item.get("error"))
    parsed = parse_json_maybe(payload)
    if not isinstance(parsed, dict):
        return None

    path = parsed.get("path")
    timestamp = parsed.get("timestamp")
    duration_ms = parsed.get("duration_ms")
    status_code = parsed.get("status_code")
    if not isinstance(path, str) or not path:
        return None
    if not isinstance(timestamp, str) or not timestamp:
        return None
    if not isinstance(duration_ms, int):
        return None
    if not isinstance(status_code, int):
        return None

    normalized: dict[str, Any] = {
        "path": path,
        "timestamp": timestamp,
        "duration_ms": duration_ms,
        "status_code": status_code,
        "error": parsed.get("error") if isinstance(parsed.get("error"), str) else None,
        "result": parsed.get("result"),
    }
    return normalized


def format_generic_structured(value: Any) -> str:
    parsed = parse_json_maybe(value)
    if isinstance(parsed, (dict, list)):
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
    if isinstance(duration_ms, (int, float)):
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
        # Normalize through mcp_output_payload first so wrapper shapes like
        # {"content":[...], "structured_content": {...}} collapse consistently.
        normalized = mcp_output_payload(result, error)
        return format_run_tests_output(normalized)
    return format_generic_structured(mcp_output_payload(result, error))


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
    env.setdefault("CODEX_HOME", "/tmp/codex-home")
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


def progress_timestamp() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def clean_progress_content(
    value: Any,
    *,
    truncate_content: bool = True,
    limit: int = 240,
) -> str:
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
    description: str,
    content: str | None = None,
    truncate_content: bool = True,
) -> None:
    total_label = str(max_parts) if max_parts > 0 else "?"
    print(
        f"[{progress_timestamp()}] [{parts_seen} / {total_label} parts] {description}",
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
    interval_sec: int = 15,
) -> threading.Thread:
    def _heartbeat() -> None:
        while not stop_event.wait(interval_sec):
            log_progress(
                parts_seen=int(stats["meaningful_parts"]),
                max_parts=max_parts,
                description="heartbeat",
            )

    thread = threading.Thread(target=_heartbeat, daemon=True)
    thread.start()
    return thread


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


def files_from_item(item: dict[str, Any], part: dict[str, Any]) -> list[str]:
    files: list[str] = []
    changes = item.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict):
                path = change.get("path")
                if isinstance(path, str) and path:
                    files.append(path)
    if files:
        return files

    part_files = part.get("files")
    if isinstance(part_files, list):
        for entry in part_files:
            if isinstance(entry, str) and entry:
                files.append(entry)
    return files


def workspace_changed_files() -> list[str]:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", "/workspace", "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    files: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        entry = line[3:].strip() if len(line) >= 4 else line.strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if entry:
            files.append(entry)
    return files


def emit_trace_event(payload: dict[str, Any]) -> None:
    print(f"{TRACE_EVENT_PREFIX}{_json_dumps(payload)}", file=sys.stderr, flush=True)


def trace_event_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("type") != "item.completed":
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    part = part_from_item(item)
    if not isinstance(part, dict):
        return None
    part_type = part.get("type")
    if part_type not in MEANINGFUL_PART_TYPES:
        return None
    item_type = item.get("type")
    if not isinstance(item_type, str):
        item_type = "unknown"
    has_file_change = item_type == "file_change" or part_type == "patch"
    files = files_from_item(item, part) if has_file_change else []
    if item_type == "command_execution":
        files = workspace_changed_files()
        has_file_change = bool(files)
    summary: str | None = None
    if part_type in {"reasoning", "text"}:
        text = str(part.get("text") or item.get("text") or "").strip()
        summary = truncate_for_trace(text) if text else None
    elif item_type == "command_execution":
        command = str(item.get("command") or "").strip()
        summary = truncate_for_trace(command) if command else None
    elif item_type == "mcp_tool_call":
        tool_name = str(item.get("tool") or "mcp_tool_call")
        args = item.get("arguments")
        test_path = ""
        if isinstance(args, dict):
            test_path = str(args.get("test_path") or "")
        suffix = f" {test_path}" if test_path else ""
        summary = f"{tool_name}{suffix}".strip()
    test_call = extract_run_tests_call(item) if item_type == "mcp_tool_call" else None
    return {
        "event": "part.completed",
        "role": "assistant",
        "part_type": part_type,
        "item_type": item_type,
        "summary": summary,
        "has_file_change": has_file_change,
        "files": files,
        "test_call": test_call,
        "timestamp_ms": int(time.time() * 1000),
    }


def summarize_event(event: dict[str, Any]) -> tuple[str, str | None] | None:
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None

    if event_type == "thread.started":
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return (event_type, f"thread_id={thread_id}")
        return (event_type, None)

    if event_type in {"turn.started", "turn.completed", "turn.failed", "error"}:
        err = event.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, str) and msg:
                return (event_type, msg)
        msg = event.get("message")
        if isinstance(msg, str) and msg:
            return (event_type, msg)
        return (event_type, None)

    if event_type in {"item.started", "item.completed"}:
        item = event.get("item")
        if not isinstance(item, dict):
            return (event_type, None)
        item_type = item.get("type")
        if not isinstance(item_type, str):
            return (event_type, None)
        description = f"{event_type} {item_type}"
        if item_type == "command_execution":
            command = str(item.get("command") or "").strip()
            if command:
                return (description, command)
            return (description, None)
        if item_type == "mcp_tool_call":
            tool_name = str(item.get("tool") or "mcp_tool_call")
            status = str(item.get("status") or "")
            details_lines: list[str] = [f"tool={tool_name}"]
            if status:
                details_lines.append(f"status={status}")
            if event_type == "item.completed":
                output_preview = format_mcp_output_for_log(
                    tool_name,
                    item.get("result"),
                    item.get("error"),
                )
                if output_preview:
                    details_lines.append("output:")
                    details_lines.append(output_preview)
            return (description, "\n".join(details_lines))
        if item_type == "agent_message":
            text = str(item.get("text") or "").strip()
            return (description, text if text else None)
        return (description, None)

    return None


def log_event_stream_progress(
    parsed_events: list[dict[str, Any]],
    meaningful_parts_seen: int,
    max_parts: int,
) -> None:
    for event in parsed_events:
        summary = summarize_event(event)
        if not summary:
            continue
        description, content = summary
        log_progress(
            parts_seen=meaningful_parts_seen,
            max_parts=max_parts,
            description=description,
            content=content,
            truncate_content="mcp_tool_call" not in description,
        )


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
    log_progress(
        parts_seen=0,
        max_parts=max_parts,
        description="launching codex exec",
        content=" ".join(command),
    )

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

    progress_stats: dict[str, int | float] = {
        "started_at": time.monotonic(),
        "events": 0,
        "meaningful_parts": 0,
    }
    heartbeat_stop = threading.Event()
    heartbeat_thread = start_progress_heartbeat(
        progress_stats,
        heartbeat_stop,
        max_parts=max_parts,
    )

    stderr_reader = start_stream_drain_thread(proc.stderr, stderr_lines)

    if proc.stdout is not None:
        for line in proc.stdout:
            stdout_lines.append(line)
            parsed_events, meaningful_from_line = parse_line_events_and_meaningful_count(line)
            if parsed_events:
                events.extend(parsed_events)
                progress_stats["events"] = len(events)
                for event in parsed_events:
                    trace_event = trace_event_from_event(event)
                    if trace_event is not None:
                        emit_trace_event(trace_event)
            meaningful_parts_seen += meaningful_from_line
            progress_stats["meaningful_parts"] = meaningful_parts_seen
            if parsed_events:
                log_event_stream_progress(
                    parsed_events,
                    meaningful_parts_seen,
                    max_parts=max_parts,
                )
            if max_parts > 0 and meaningful_parts_seen >= max_parts:
                aborted_for_part_limit = True
                log_progress(
                    parts_seen=meaningful_parts_seen,
                    max_parts=max_parts,
                    description="part limit reached",
                    content="terminating codex process",
                )
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
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)

    if stderr_reader is not None:
        stderr_reader.join(timeout=2)

    stderr_text = "".join(stderr_lines)
    elapsed = int(time.monotonic() - float(progress_stats["started_at"]))
    log_progress(
        parts_seen=meaningful_parts_seen,
        max_parts=max_parts,
        description="completed",
        content=(
            f"exit={return_code} elapsed={elapsed}s events={len(events)} "
            f"aborted_for_part_limit={aborted_for_part_limit}"
        ),
    )

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
