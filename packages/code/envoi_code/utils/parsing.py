"""Parse agent responses into parts and envoi calls.

Extracts structured data from raw agent turn responses: counts meaningful parts,
extracts envoi test calls from tool outputs, aggregates token usage, and logs
part summaries. Used by runner.py after each turn to update the trace.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from envoi_code.models import EnvoiCall
from envoi_code.utils.helpers import merge_usage_maps, redact_secrets, truncate_text

TRACE_EVENT_PREFIX = "TRACE_EVENT "

MEANINGFUL_PART_TYPES: set[str] = {
    "reasoning",
    "text",
    "tool",
    "tool_use",
    "tool_result",
    "patch",
}


def extract_turn_token_usage(
    response: dict[str, Any],
    new_messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    usage: dict[str, Any] = {}

    for key in ("_usage", "usage", "token_usage", "tokens"):
        candidate = response.get(key)
        if isinstance(candidate, dict):
            merge_usage_maps(usage, redact_secrets(candidate))

    response_info = response.get("info")
    if isinstance(response_info, dict):
        info_tokens = response_info.get("tokens")
        if isinstance(info_tokens, dict):
            merge_usage_maps(usage, redact_secrets(info_tokens))

    for message in new_messages:
        if not isinstance(message, dict):
            continue
        info = message.get("info")
        if not isinstance(info, dict):
            continue
        tokens = info.get("tokens")
        if isinstance(tokens, dict):
            merge_usage_maps(usage, redact_secrets(tokens))

    return usage or None


def summarize_tool_input(name: str, input_data: Any) -> str:
    if not isinstance(input_data, dict):
        return truncate_text(str(input_data), limit=200)
    if name == "bash":
        return truncate_text(input_data.get("command", ""), limit=200)
    if name == "read":
        return str(
            input_data.get("filePath") or input_data.get("path") or "?"
        )
    if name in {"write", "edit"}:
        path = (
            input_data.get("filePath") or input_data.get("path") or "?"
        )
        content = (
            input_data.get("content")
            or input_data.get("newString")
            or ""
        )
        return f"{path} ({len(content)} bytes)"
    if name == "run_tests":
        return input_data.get(
            "test_path",
            truncate_text(json.dumps(input_data), limit=200),
        )
    return truncate_text(json.dumps(input_data), limit=200)


def log_message_parts(
    message: dict[str, Any],
    *,
    _print: Callable[..., Any] | None = None,
) -> None:
    """Print a human-readable summary of every part in a message."""
    from envoi_code.utils.helpers import tprint

    out = _print or tprint
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
                out(f"  [{role}] {truncate_text(text, limit=300)}")
        elif ptype == "reasoning":
            text = part.get("text", "").strip()
            if text:
                out(
                    f"  [{role}] reasoning: "
                    f"{truncate_text(text, limit=220)}"
                )
        elif ptype == "tool":
            name = part.get("tool", "?")
            state = part.get("state", {})
            status = state.get("status", "?")
            summary = summarize_tool_input(name, state.get("input", {}))
            output = (
                state.get("output")
                or state.get("metadata", {}).get("output")
                or ""
            )
            output_str = (
                truncate_text(str(output), limit=200) if output else ""
            )
            out(f"  [{role}] {name} ({status})")
            if summary:
                out(f"         input: {summary}")
            if output_str and status == "completed":
                out(f"         -> {output_str}")
        elif ptype == "tool_use":
            name = part.get("name", "?")
            status = part.get("status", "?")
            summary = summarize_tool_input(name, part.get("input", {}))
            out(f"  [{role}] {name} ({status})")
            if summary:
                out(f"         input: {summary}")
        elif ptype == "tool_result":
            content = str(part.get("content", ""))
            if content:
                out(f"         -> {truncate_text(content, limit=200)}")
        elif ptype == "patch":
            files = part.get("files", [])
            names: list[str] = []
            for f in files:
                if isinstance(f, str):
                    names.append(f)
                elif isinstance(f, dict):
                    name = (
                        f.get("path")
                        or f.get("filename")
                        or f.get("name")
                        or "?"
                    )
                    names.append(str(name))
            out(
                f"  [{role}] patch: "
                f"{', '.join(names)} ({len(names)} files)"
            )
        elif isinstance(ptype, str) and (
            ptype.endswith("-start") or ptype.endswith("-finish")
        ):
            pass  # skip noise
        else:
            out(f"  [{role}] {ptype}")


def extract_envoi_calls(
    message_parts: list[dict[str, Any]],
) -> list[EnvoiCall]:
    """Extract envoi test calls from message parts."""
    calls: list[EnvoiCall] = []

    tool_results: dict[str, dict[str, Any]] = {}
    for part in message_parts:
        if part.get("type") == "tool_result":
            tool_results[part.get("tool_use_id", "")] = part
    for part in message_parts:
        if (
            part.get("type") == "tool_use"
            and part.get("name") == "run_tests"
        ):
            tool_result = tool_results.get(part.get("id", ""))
            if tool_result:
                content = tool_result.get("content", "")
                parsed_call = parse_envoi_call_payload(content)
                if parsed_call is not None:
                    calls.append(parsed_call)
        if (
            part.get("type") == "tool"
            and part.get("tool") == "run_tests"
        ):
            state = part.get("state", {})
            if state.get("status") == "completed":
                output = (
                    state.get("output")
                    or state.get("metadata", {}).get("output")
                    or ""
                )
                parsed_call = parse_envoi_call_payload(output)
                if parsed_call is not None:
                    calls.append(parsed_call)
    return calls


def parse_envoi_call_payload(value: Any) -> EnvoiCall | None:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parse_envoi_call_payload(parsed)

    if isinstance(value, dict):
        if "path" in value and "timestamp" in value:
            try:
                return EnvoiCall(**value)
            except Exception:
                pass
        for nested_key in (
            "result",
            "data",
            "output",
            "structured_content",
        ):
            if nested_key in value:
                parsed_nested = parse_envoi_call_payload(
                    value.get(nested_key),
                )
                if parsed_nested is not None:
                    return parsed_nested
    return None


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
            if (
                isinstance(part, dict)
                and part.get("type") in MEANINGFUL_PART_TYPES
            ):
                count += 1
    return count


async def parse_trace_event_line(
    line: str,
    on_stream_part: (
        Callable[[dict[str, Any]], Awaitable[None]] | None
    ),
) -> None:
    """Parse a TRACE_EVENT from a stderr line and forward to callback."""
    if on_stream_part is None:
        return
    stripped = line.strip()
    if not stripped.startswith(TRACE_EVENT_PREFIX):
        return
    payload = stripped[len(TRACE_EVENT_PREFIX) :].strip()
    if not payload:
        return
    try:
        event_obj = json.loads(payload)
    except json.JSONDecodeError:
        return
    if isinstance(event_obj, dict):
        await on_stream_part(event_obj)


def agent_message_id(message: dict[str, Any]) -> str | None:
    """Extract the message id from a message dict."""
    info = message.get("info", {})
    message_id_value = info.get("id")
    if isinstance(message_id_value, str) and message_id_value:
        return message_id_value
    return None
