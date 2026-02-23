"""
OpenCode agent backend -- wraps the OpenCode Python SDK.

This module has two roles:
1. As a script running inside the sandbox: starts the OpenCode server, uses the
   AsyncOpencode client to create sessions and send messages, and emits
   TRACE_EVENT lines on stderr for each part.
2. As the OpenCodeAgent class implementing Agent: uploads itself into the
   sandbox, manages the OpenCode config, and translates turn results for runner.py.

Also exports OPENCODE_CONFIG_TEMPLATE, the JSON config that gets written to
.opencode.jsonc in the sandbox workspace. This config sets up the model, MCP
server, and tool permissions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import httpx
    from opencode_ai import APIConnectionError, APIStatusError, AsyncOpencode
except Exception:  # pragma: no cover - runner imports this module for config only
    httpx = None
    APIConnectionError = Exception
    APIStatusError = Exception
    AsyncOpencode = None

# Suppress noisy forward-compat serializer warnings from SDK model unions.
warnings.filterwarnings(
    "ignore",
    message=r"Pydantic serializer warnings:.*",
    category=UserWarning,
)


MEANINGFUL_PART_TYPES: set[str] = {
    "reasoning",
    "text",
    "tool",
    "tool_use",
    "tool_result",
    "patch",
}
TRACE_EVENT_PREFIX = "TRACE_EVENT "

OPENCODE_CONFIG_TEMPLATE = """{
  "$schema": "https://opencode.ai/config.json",
  "model": "MODEL_PLACEHOLDER",
  "small_model": "MODEL_PLACEHOLDER",
  "provider": {
    "opencode": {
      "options": {
        "apiKey": "{env:OPENCODE_API_KEY}"
      }
    }
  },
  "server": {
    "port": 4096,
    "hostname": "0.0.0.0"
  },
  "mcp": {
    "tests": {
      "type": "local",
      "command": ["python3", "/sandbox/mcp_server.py"],
      "enabled": true
    }
  },
  "permission": {
    "edit": "allow",
    "bash": "allow"
  },
  "tools": {
    "write": true,
    "bash": true,
    "edit": true
  }
}
"""


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        # OpenCode can emit newer union variants (e.g. reasoning parts) than the
        # pinned SDK models know about. Keep serialization resilient and quiet.
        try:
            return value.model_dump(mode="json", warnings=False)
        except TypeError:
            return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    return value


async def raw_request_with_client(
    *,
    client: Any,
    method: str,
    path: str,
    request_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if httpx is None:
        return {
            "ok": False,
            "status_code": None,
            "body": None,
            "error": "OpenCode SDK dependencies are not available in this environment",
        }

    if method == "GET":
        response = await client.get(path, cast_to=httpx.Response)
    elif method == "POST":
        response = await client.post(path, cast_to=httpx.Response, body=request_body or {})
    elif method == "PUT":
        response = await client.put(path, cast_to=httpx.Response, body=request_body or {})
    else:
        return {
            "ok": False,
            "status_code": None,
            "body": None,
            "error": f"Unsupported method: {method}",
        }

    try:
        parsed_body = response.json()
    except ValueError:
        parsed_body = response.text

    return {
        "ok": 200 <= response.status_code < 400,
        "status_code": response.status_code,
        "body": to_jsonable(parsed_body),
        "error": None,
    }


async def raw_request(
    *,
    method: str,
    path: str,
    request_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if AsyncOpencode is None:
        return {
            "ok": False,
            "status_code": None,
            "body": None,
            "error": "OpenCode SDK dependencies are not available in this environment",
        }

    base_url = os.environ.get("OPENCODE_BASE_URL", "http://localhost:4096")
    timeout = float(os.environ.get("OPENCODE_TIMEOUT_SECONDS", "600"))

    try:
        async with AsyncOpencode(
            base_url=base_url,
            timeout=timeout,
            max_retries=2,
        ) as client:
            return await raw_request_with_client(
                client=client,
                method=method,
                path=path,
                request_body=request_body,
            )
    except APIStatusError as error:
        status_code = getattr(error, "status_code", None)
        response = getattr(error, "response", None)
        status_error_body: Any = None
        if response is not None:
            try:
                status_error_body = response.json()
            except Exception:
                status_error_body = response.text
        return {
            "ok": False,
            "status_code": status_code,
            "body": to_jsonable(status_error_body),
            "error": str(error),
        }
    except APIConnectionError as error:
        return {
            "ok": False,
            "status_code": None,
            "body": None,
            "error": f"API connection error: {error}",
        }
    except Exception as error:
        return {
            "ok": False,
            "status_code": None,
            "body": None,
            "error": str(error),
        }


def event_session_id(event: dict[str, Any]) -> str | None:
    properties = event.get("properties")
    if not isinstance(properties, dict):
        return None

    for key in ("session_id", "sessionID"):
        value = properties.get(key)
        if isinstance(value, str) and value:
            return value

    info = properties.get("info")
    if isinstance(info, dict):
        for key in ("session_id", "sessionID"):
            value = info.get(key)
            if isinstance(value, str) and value:
                return value

    part = properties.get("part")
    if isinstance(part, dict):
        for key in ("session_id", "sessionID"):
            value = part.get(key)
            if isinstance(value, str) and value:
                return value

    return None


def tool_detail(tool: str, state: Any) -> str:
    """Extract a short detail string from tool input."""
    if not isinstance(state, dict):
        return ""
    inp = state.get("input", {})
    if not isinstance(inp, dict):
        return ""
    if tool == "bash":
        cmd = inp.get("command", "")
        return cmd[:80] if cmd else ""
    if tool == "read":
        return str(inp.get("filePath") or inp.get("path") or "")
    if tool in ("write", "edit"):
        path = inp.get("filePath") or inp.get("path") or ""
        return str(path)
    if tool == "run_tests":
        return inp.get("test_path", "")
    return ""


def summarize_event(event: dict[str, Any]) -> str | None:
    """Return a short human-readable summary, or None to suppress."""
    event_type = event.get("type")
    properties = event.get("properties")
    if not isinstance(properties, dict):
        return None

    if event_type == "message.part.updated":
        part = properties.get("part")
        if not isinstance(part, dict):
            return None
        part_type = part.get("type")
        if part_type in {"reasoning", "text", "snapshot"}:
            return None
        if isinstance(part_type, str) and (
            part_type.endswith("-start") or part_type.endswith("-finish")
        ):
            return None
        if part_type == "tool":
            tool = part.get("tool", "?")
            state = part.get("state")
            status = state.get("status", "?") if isinstance(state, dict) else "?"
            # Only show completed (skip pending/running noise)
            if status != "completed":
                return None
            detail = tool_detail(tool, state)
            if detail:
                return f"[tool] {tool}: {detail}"
            return f"[tool] {tool}"
        if part_type == "patch":
            files = part.get("files")
            if not isinstance(files, list):
                return "[patch] (0 files)"
            names: list[str] = []
            for f in files:
                if isinstance(f, str):
                    names.append(f)
                elif isinstance(f, dict):
                    name = (
                        f.get("path") or f.get("filename")
                        or f.get("name") or "?"
                    )
                    names.append(str(name))
            if len(names) <= 5:
                return f"[patch] {' '.join(names)} ({len(names)} files)"
            shown = " ".join(names[:5])
            return f"[patch] {shown} ... ({len(names)} files)"
        return None

    if event_type == "session.idle":
        return "session-idle"
    if event_type == "session.error":
        error = properties.get("error")
        return f"session-error {error}" if error is not None else "session-error"

    return None


def part_identifier(part: dict[str, Any], fallback: str) -> str:
    for key in ("id", "callID", "hash", "snapshot"):
        value = part.get(key)
        if isinstance(value, str) and value:
            return value
    metadata = part.get("metadata")
    if isinstance(metadata, dict):
        opencode_meta = metadata.get("opencode")
        if isinstance(opencode_meta, dict):
            item_id = opencode_meta.get("itemId")
            if isinstance(item_id, str) and item_id:
                return item_id
    return fallback


def truncate_for_trace(value: str, limit: int = 240) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


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


def normalize_run_tests_payload(value: Any) -> dict[str, Any] | None:
    parsed = parse_json_maybe(value)
    if not isinstance(parsed, dict):
        return None
    if "path" in parsed and "timestamp" in parsed:
        return parsed
    for nested_key in ("result", "data", "output", "structured_content"):
        if nested_key in parsed:
            nested = normalize_run_tests_payload(parsed.get(nested_key))
            if isinstance(nested, dict):
                return nested
    return None


def extract_run_tests_call_from_part(part: dict[str, Any]) -> dict[str, Any] | None:
    if str(part.get("tool") or "") != "run_tests":
        return None
    state = part.get("state")
    if not isinstance(state, dict):
        return None
    metadata = state.get("metadata")
    metadata_obj = metadata if isinstance(metadata, dict) else {}
    raw_output = state.get("output") or metadata_obj.get("output")
    parsed = normalize_run_tests_payload(raw_output)
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


def stream_part_summary(part: dict[str, Any]) -> str | None:
    part_type = str(part.get("type") or "")
    if part_type in {"reasoning", "text"}:
        text = str(part.get("text") or "").strip()
        return truncate_for_trace(text) if text else None
    if part_type == "tool":
        tool_name = str(part.get("tool") or "").strip()
        state = part.get("state")
        if not isinstance(state, dict):
            return tool_name or None
        input_obj = state.get("input")
        if tool_name == "bash" and isinstance(input_obj, dict):
            cmd = str(input_obj.get("command") or "").strip()
            if cmd:
                return truncate_for_trace(cmd)
        if tool_name == "run_tests" and isinstance(input_obj, dict):
            test_path = str(input_obj.get("test_path") or "").strip()
            if test_path:
                return f"run_tests {test_path}"
        return tool_name or None
    return None


def event_token_usage(event_obj: dict[str, Any], part: dict[str, Any]) -> dict[str, Any] | None:
    properties = event_obj.get("properties")
    if isinstance(properties, dict):
        for key in ("usage", "token_usage", "tokens"):
            value = properties.get(key)
            if isinstance(value, dict) and value:
                return value
    metadata = part.get("metadata")
    if isinstance(metadata, dict):
        for key in ("usage", "token_usage", "tokens"):
            value = metadata.get(key)
            if isinstance(value, dict) and value:
                return value
    state = part.get("state")
    if isinstance(state, dict):
        for key in ("usage", "token_usage", "tokens"):
            value = state.get(key)
            if isinstance(value, dict) and value:
                return value
    return None


def tool_fields_from_part(
    part: dict[str, Any],
) -> tuple[str | None, str | None, Any, Any, Any, int | None]:
    if str(part.get("type") or "") != "tool":
        return None, None, None, None, None, None

    tool_name = str(part.get("tool") or "").strip() or None
    state = part.get("state")
    if not isinstance(state, dict):
        return tool_name, None, None, None, None, None

    status_raw = state.get("status")
    status = str(status_raw) if status_raw is not None else None
    tool_input = state.get("input")
    tool_output = state.get("output")
    tool_error = state.get("error")
    exit_code = parse_int_maybe(state.get("exit_code"))
    return tool_name, status, tool_input, tool_output, tool_error, exit_code


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


def extract_usage_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    usage: dict[str, Any] = {}
    for event_obj in events:
        if not isinstance(event_obj, dict):
            continue
        properties = event_obj.get("properties")
        if not isinstance(properties, dict):
            continue
        for key in ("usage", "token_usage", "tokens"):
            candidate = properties.get(key)
            if isinstance(candidate, dict) and candidate:
                merge_usage_maps(usage, candidate)
    return usage or None


async def stream_session_events(
    *,
    client: Any,
    session_id: str,
    done_event: asyncio.Event,
    max_parts: int = 0,
) -> tuple[list[dict[str, Any]], int, bool]:
    events: list[dict[str, Any]] = []
    meaningful_parts_seen = 0
    meaningful_part_ids_seen: set[str] = set()
    aborted_for_part_limit = False
    try:
        stream = await client.event.list()
        async with stream:
            async for event in stream:
                event_obj = to_jsonable(event)
                if not isinstance(event_obj, dict):
                    continue

                sid = event_session_id(event_obj)
                if sid and sid != session_id:
                    if done_event.is_set():
                        break
                    continue

                events.append(event_obj)
                summary = summarize_event(event_obj)
                if summary:
                    ts = datetime.now(UTC).strftime("%H:%M:%S")
                    budget = (
                        f"[{meaningful_parts_seen}/{max_parts}]"
                        if max_parts > 0
                        else f"[{meaningful_parts_seen}]"
                    )
                    print(
                        f"[{ts}] {budget} {summary}",
                        file=sys.stderr,
                        flush=True,
                    )

                if event_obj.get("type") == "message.part.updated":
                    properties = event_obj.get("properties")
                    part = properties.get("part") if isinstance(properties, dict) else None
                    if isinstance(part, dict):
                        part_type = part.get("type")
                        if part_type in MEANINGFUL_PART_TYPES:
                            part_id = part_identifier(
                                part,
                                fallback=f"{len(events)}:{part_type}",
                            )
                            if part_id not in meaningful_part_ids_seen:
                                meaningful_part_ids_seen.add(part_id)
                                meaningful_parts_seen += 1
                                files: list[str] = []
                                if part_type == "patch":
                                    raw_files = part.get("files")
                                    if isinstance(raw_files, list):
                                        for entry in raw_files:
                                            if isinstance(entry, str) and entry:
                                                files.append(entry)
                                            elif isinstance(entry, dict):
                                                name = (
                                                    entry.get("path")
                                                    or entry.get("filename")
                                                    or entry.get("name")
                                                )
                                                if isinstance(name, str) and name:
                                                    files.append(name)
                                content: str | None = None
                                if part_type in {"reasoning", "text"}:
                                    text = part.get("text")
                                    if isinstance(text, str) and text:
                                        content = text
                                (
                                    tool_name,
                                    tool_status,
                                    tool_input,
                                    tool_output,
                                    tool_error,
                                    tool_exit_code,
                                ) = tool_fields_from_part(part)
                                trace_event = {
                                    "event": "part.completed",
                                    "role": "assistant",
                                    "part_type": part_type,
                                    "item_type": part_type,
                                    "summary": stream_part_summary(part),
                                    "content": content,
                                    "has_file_change": part_type == "patch",
                                    "files": files,
                                    "tool_name": tool_name,
                                    "tool_status": tool_status,
                                    "tool_input": tool_input,
                                    "tool_output": tool_output,
                                    "tool_error": tool_error,
                                    "tool_exit_code": tool_exit_code,
                                    "token_usage": event_token_usage(event_obj, part),
                                    "provider_part": part,
                                    "provider_event": event_obj,
                                    "test_call": (
                                        extract_run_tests_call_from_part(part)
                                        if part_type == "tool"
                                        else None
                                    ),
                                    "timestamp_ms": int(time.time() * 1000),
                                }
                                print(
                                    (
                                        f"{TRACE_EVENT_PREFIX}"
                                        f"{json.dumps(trace_event, separators=(',', ':'))}"
                                    ),
                                    file=sys.stderr,
                                    flush=True,
                                )
                        if (
                            max_parts > 0
                            and meaningful_parts_seen >= max_parts
                            and not aborted_for_part_limit
                        ):
                            aborted_for_part_limit = True
                            ts = datetime.now(UTC).strftime("%H:%M:%S")
                            print(
                                f"[{ts}] [{meaningful_parts_seen}/{max_parts}]"
                                " part budget reached, aborting session",
                                file=sys.stderr,
                                flush=True,
                            )
                            try:
                                await client.session.abort(session_id)
                            except Exception as error:  # noqa: BLE001
                                print(
                                    f"abort warning: {error}",
                                    file=sys.stderr,
                                    flush=True,
                                )

                if done_event.is_set() and event_obj.get("type") == "session.idle":
                    break
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        print(f"stream warning: {error}", file=sys.stderr, flush=True)
    return events, meaningful_parts_seen, aborted_for_part_limit


async def chat_with_stream(
    *,
    session_id: str,
    text: str,
    max_parts: int = 0,
) -> dict[str, Any]:
    if AsyncOpencode is None:
        return {
            "ok": False,
            "status_code": None,
            "body": None,
            "error": "OpenCode SDK dependencies are not available in this environment",
        }

    base_url = os.environ.get("OPENCODE_BASE_URL", "http://localhost:4096")
    timeout = float(os.environ.get("OPENCODE_TIMEOUT_SECONDS", "600"))
    payload = {"parts": [{"type": "text", "text": text}]}

    try:
        async with AsyncOpencode(
            base_url=base_url,
            timeout=timeout,
            max_retries=2,
        ) as client:
            done_event = asyncio.Event()
            stream_task = asyncio.create_task(
                stream_session_events(
                    client=client,
                    session_id=session_id,
                    done_event=done_event,
                    max_parts=max_parts,
                )
            )
            try:
                try:
                    result = await raw_request_with_client(
                        client=client,
                        method="POST",
                        path=f"/session/{session_id}/message",
                        request_body=payload,
                    )
                except APIStatusError as error:
                    status_code = getattr(error, "status_code", None)
                    response = getattr(error, "response", None)
                    status_error_body: Any = None
                    if response is not None:
                        try:
                            status_error_body = response.json()
                        except Exception:
                            status_error_body = response.text
                    result = {
                        "ok": False,
                        "status_code": status_code,
                        "body": to_jsonable(status_error_body),
                        "error": str(error),
                    }
                except APIConnectionError as error:
                    result = {
                        "ok": False,
                        "status_code": None,
                        "body": None,
                        "error": f"API connection error: {error}",
                    }
                except Exception as error:
                    result = {
                        "ok": False,
                        "status_code": None,
                        "body": None,
                        "error": str(error),
                    }
            finally:
                done_event.set()

            events: list[dict[str, Any]] = []
            meaningful_parts_seen = 0
            aborted_for_part_limit = False
            try:
                events, meaningful_parts_seen, aborted_for_part_limit = await asyncio.wait_for(
                    stream_task,
                    timeout=2.0,
                )
            except TimeoutError:
                stream_task.cancel()
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass
            except Exception:
                pass

            body = result.get("body")
            usage = extract_usage_from_events(events)
            meta = {
                "events_observed": len(events),
                "meaningful_parts_seen": meaningful_parts_seen,
                "aborted_for_part_limit": aborted_for_part_limit,
            }
            if usage is not None:
                meta["token_usage"] = usage
            result["meta"] = meta
            if isinstance(body, dict):
                stream_stats = (
                    dict(body.get("_stream", {}))
                    if isinstance(body.get("_stream"), dict)
                    else {}
                )
                stream_stats.update(meta)
                body["_stream"] = stream_stats
                if usage is not None:
                    body["_usage"] = usage
                result["body"] = body
            elif meta["aborted_for_part_limit"]:
                result["body"] = {"_stream": dict(meta)}
            return result
    except APIStatusError as error:
        status_code = getattr(error, "status_code", None)
        response = getattr(error, "response", None)
        api_status_body: Any = None
        if response is not None:
            try:
                api_status_body = response.json()
            except Exception:
                api_status_body = response.text
        return {
            "ok": False,
            "status_code": status_code,
            "body": to_jsonable(api_status_body),
            "error": str(error),
        }
    except APIConnectionError as error:
        return {
            "ok": False,
            "status_code": None,
            "body": None,
            "error": f"API connection error: {error}",
        }
    except Exception as error:
        return {
            "ok": False,
            "status_code": None,
            "body": None,
            "error": str(error),
        }


async def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create-session")
    create_parser.add_argument("--title", default="")

    chat_parser = subparsers.add_parser("chat")
    chat_parser.add_argument("--session-id", required=True)
    chat_parser.add_argument("--text-file", required=True)

    chat_stream_parser = subparsers.add_parser("chat-stream")
    chat_stream_parser.add_argument("--session-id", required=True)
    chat_stream_parser.add_argument("--text-file", required=True)
    chat_stream_parser.add_argument("--max-parts", type=int, default=0)

    list_messages_parser = subparsers.add_parser("list-messages")
    list_messages_parser.add_argument("--session-id", required=True)

    subparsers.add_parser("list-sessions")
    subparsers.add_parser("provider-status")

    auth_parser = subparsers.add_parser("provider-auth")
    auth_parser.add_argument("--api-key-file", required=True)

    args = parser.parse_args()

    if args.command == "create-session":
        request_body = {"title": args.title} if args.title else {}
        result = await raw_request(method="POST", path="/session", request_body=request_body)
        print(json.dumps(result))
        return

    if args.command == "chat":
        text = Path(args.text_file).read_text()
        payload = {"parts": [{"type": "text", "text": text}]}
        result = await raw_request(
            method="POST",
            path=f"/session/{args.session_id}/message",
            request_body=payload,
        )
        print(json.dumps(result))
        return

    if args.command == "chat-stream":
        text = Path(args.text_file).read_text()
        result = await chat_with_stream(
            session_id=args.session_id,
            text=text,
            max_parts=max(0, args.max_parts),
        )
        print(json.dumps(result))
        return

    if args.command == "list-messages":
        result = await raw_request(
            method="GET",
            path=f"/session/{args.session_id}/message",
        )
        print(json.dumps(result))
        return

    if args.command == "list-sessions":
        result = await raw_request(method="GET", path="/session")
        print(json.dumps(result))
        return

    if args.command == "provider-status":
        result = await raw_request(method="GET", path="/provider")
        print(json.dumps(result))
        return

    if args.command == "provider-auth":
        api_key = Path(args.api_key_file).read_text().strip()
        result = await raw_request(
            method="PUT",
            path="/auth/opencode",
            request_body={"apiKey": api_key},
        )
        print(json.dumps(result))
        return


if __name__ == "__main__":
    asyncio.run(main())


# -------------------------------------------------------------------
# OpenCodeAgent: Agent implementation (runner-side only)
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
    from envoi_code.agents.setup import run_task_setup, run_workspace_init
    from envoi_code.sandbox.base import Sandbox
    from envoi_code.utils.helpers import (
        compute_turn_timeout_seconds,
        environment_upload_items,
        run_sandbox_client,
        upload_files_parallel,
    )
    from envoi_code.utils.parsing import agent_message_id, parse_trace_event_line

    OPENCODE_SCRIPT = "/sandbox/opencode_client.py"
    OPENCODE_LABEL = "opencode-sdk"
    DEFAULT_OPENCODE_MODEL = "opencode/gpt-5-nano"

    OPENCODE_INSTALL_SCRIPT = """\
set -euo pipefail
export PATH="$HOME/.cargo/bin:$PATH"

echo "[setup] installing NodeSource repository setup"
bash -lc "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -"
echo "[setup] installing nodejs"
apt-get install -y -qq nodejs
echo "[setup] installing OpenCode CLI"
bash -lc "curl -fsSL https://opencode.ai/install | bash"

OPENCODE_BIN="$HOME/.opencode/bin/opencode"
if [ ! -f "$OPENCODE_BIN" ]; then
    echo "[setup] ERROR: expected OpenCode binary missing at ${OPENCODE_BIN}"
    find / -name opencode -type f 2>/dev/null || true
    exit 1
fi
echo "[setup] opencode install complete"
"""

    OPENCODE_START_SERVER_SCRIPT = """\
set -euo pipefail

if [ ! -f /tmp/upload/opencode_api_key.txt ]; then
    echo "[setup] ERROR: /tmp/upload/opencode_api_key.txt not found"
    exit 1
fi
OPENCODE_API_KEY="$(cat /tmp/upload/opencode_api_key.txt)"

OPENCODE_BIN="$HOME/.opencode/bin/opencode"
echo "[setup] starting OpenCode server on :4096"
cd /workspace
OPENCODE_API_KEY="$OPENCODE_API_KEY" \
OPENCODE_CONFIG="/workspace/opencode.jsonc" \
    "$OPENCODE_BIN" serve --port 4096 --hostname 0.0.0.0 > /tmp/opencode.log 2>&1 &
OPENCODE_PID=$!
echo "$OPENCODE_PID" > /tmp/opencode.pid
echo "[setup] OpenCode process started (pid=${OPENCODE_PID})"

# Wait for OpenCode server
for i in $(seq 1 120); do
    if curl -sf http://localhost:4096/global/health >/dev/null 2>&1; then
        echo "[setup] opencode ready"
        break
    fi
    if [ $((i % 5)) -eq 0 ]; then
        echo "[setup] still waiting for opencode (${i}s)"
    fi
    sleep 1
done
curl -sf http://localhost:4096/global/health >/dev/null 2>&1 || {
    echo "[setup] ERROR: timeout waiting for opencode"
    exit 1
}
echo "[setup] setup complete: envoi=:8000 opencode=:4096"
"""

    # Module-level helpers for session tree walking

    def session_object_id(
        session: dict[str, Any],
    ) -> str | None:
        for key in ("id", "sessionID", "session_id"):
            value = session.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def session_object_parent_id(
        session: dict[str, Any],
    ) -> str | None:
        for key in (
            "parentID",
            "parent_id",
            "parentId",
        ):
            value = session.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def get_session_family(
        root_session_id: str,
        sessions: list[dict[str, Any]],
    ) -> list[str]:
        known_ids = {
            sid
            for s in sessions
            if isinstance(s, dict)
            if (sid := session_object_id(s))
        }
        if root_session_id not in known_ids:
            return [root_session_id]

        children_by_parent: dict[str, list[str]] = {}
        for s in sessions:
            if not isinstance(s, dict):
                continue
            sid = session_object_id(s)
            parent_id = session_object_parent_id(s)
            if sid and parent_id:
                children_by_parent.setdefault(
                    parent_id, [],
                ).append(sid)

        family: list[str] = []
        queue = [root_session_id]
        seen: set[str] = set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            family.append(current)
            queue.extend(
                children_by_parent.get(current, []),
            )
        return family

    def message_created_ms(
        message: dict[str, Any],
    ) -> int:
        info = message.get("info", {})
        time_info = info.get("time", {})
        created = time_info.get("created")
        return (
            int(created)
            if isinstance(created, int)
            else 0
        )

    class OpenCodeAgent:
        """Agent implementation for OpenCode."""

        @property
        def name(self) -> str:
            return "opencode"

        @property
        def session_id(self) -> str | None:
            return self.current_session_id

        @property
        def log_files(self) -> list[str]:
            return ["/tmp/opencode.log", "/tmp/envoi.log"]

        def __init__(self) -> None:
            self.sandbox: Sandbox | None = None
            self.agent_model: str = ""
            self.api_key: str = ""
            self.current_session_id: str | None = None
            self.seen_message_ids: set[str] = set()

        # -- static methods -----------------------------------------

        @staticmethod
        def resolve_credentials(
            codex_auth_json_b64: str | None = None,
        ) -> AgentCredentials:
            """Resolve OpenCode credentials from env vars."""
            api_key = os.environ.get(
                "OPENCODE_API_KEY", "",
            ).strip()
            if not api_key:
                raise RuntimeError("OPENCODE_API_KEY not set")
            return AgentCredentials(api_key=api_key)

        @staticmethod
        def resolve_model(model: str | None) -> str:
            raw = model or DEFAULT_OPENCODE_MODEL
            if "/" in raw:
                return raw
            return f"opencode/{raw}"

        @staticmethod
        def image_requirements() -> SandboxImageRequirements:
            return SandboxImageRequirements(
                pip_packages=["opencode-ai>=0.1.0a36"],
            )

        # -- instance methods ----------------------------------------

        def compute_turn_timeout(
            self,
            *,
            remaining_parts: int,
            remaining_run_seconds: float,
            message_timeout_seconds: int,
        ) -> int:
            return compute_turn_timeout_seconds(
                remaining_parts=remaining_parts,
                remaining_run_seconds=remaining_run_seconds,
                message_timeout_seconds=message_timeout_seconds,
            )

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
                OPENCODE_SCRIPT,
                OPENCODE_LABEL,
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
            self.api_key = ctx.credentials.api_key

            builtins.print(
                f"[setup] agent=opencode model={ctx.model}",
                flush=True,
            )
            config = OPENCODE_CONFIG_TEMPLATE.replace(
                "MODEL_PLACEHOLDER", ctx.model,
            )
            setup_uploads: list[tuple[str, str]] = [
                ("/tmp/upload/task_setup.sh", ctx.setup_script),
                ("/sandbox/mcp_server.py", ctx.mcp_server_content),
                (
                    "/sandbox/opencode_client.py",
                    OPENCODE_CLIENT_CONTENT,
                ),
                (
                    "/tmp/upload/opencode_api_key.txt",
                    self.api_key,
                ),
                ("/workspace/opencode.jsonc", config),
                (
                    "/workspace/.gitignore",
                    ctx.workspace_gitignore,
                ),
            ]

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

            await run_task_setup(sandbox)
            await run_workspace_init(sandbox)

            # Install opencode binary
            await sandbox.write_file(
                "/tmp/opencode_install.sh",
                OPENCODE_INSTALL_SCRIPT,
                ensure_dir=False,
            )

            async def handle_line(line: str) -> None:
                stripped = line.strip()
                if stripped and stripped.startswith("[setup]"):
                    builtins.print(stripped, flush=True)

            result = await sandbox.run(
                "bash /tmp/opencode_install.sh",
                timeout=300,
                on_stdout_line=handle_line,
                on_stderr_line=handle_line,
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    "OpenCode install failed "
                    f"(exit {result.exit_code})"
                )

            # Start opencode server
            await sandbox.write_file(
                "/tmp/opencode_start.sh",
                OPENCODE_START_SERVER_SCRIPT,
                ensure_dir=False,
            )
            result = await sandbox.run(
                "bash /tmp/opencode_start.sh",
                timeout=300,
                on_stdout_line=handle_line,
                on_stderr_line=handle_line,
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    "OpenCode server start failed "
                    f"(exit {result.exit_code})"
                )

            # Ensure provider is connected
            await self.ensure_provider_connected()

        async def ensure_provider_connected(self) -> None:
            assert self.sandbox is not None
            response = await self.run_client(
                ["provider-status"],
                timeout=30,
                quiet=True,
            )
            if (
                response
                and response.get("ok")
                and isinstance(response.get("body"), dict)
            ):
                connected = response["body"].get(
                    "connected", [],
                )
                builtins.print(
                    f"[provider] connected={connected}",
                    flush=True,
                )
                if (
                    isinstance(connected, list)
                    and "opencode" in connected
                ):
                    return

            builtins.print(
                "[provider] opencode not connected, "
                "setting auth...",
                flush=True,
            )
            api_key_path = "/tmp/auth_opencode_api_key.txt"
            await self.sandbox.write_file(
                api_key_path,
                self.api_key,
                ensure_dir=False,
            )
            auth_response = await self.run_client(
                [
                    "provider-auth",
                    "--api-key-file",
                    api_key_path,
                ],
                timeout=30,
            )
            if (
                auth_response is None
                or not auth_response.get("ok")
            ):
                raise RuntimeError(
                    "Failed to authenticate provider: "
                    f"{auth_response}",
                )

        async def create_session(
            self,
            trajectory_id: str,
        ) -> str:
            assert self.sandbox is not None
            title = f"trajectory-{trajectory_id}"
            response = await self.run_client(
                ["create-session", "--title", title],
                timeout=60,
            )
            if response is None:
                raise RuntimeError(
                    "Failed to create session: no response",
                )
            if not response.get("ok"):
                raise RuntimeError(
                    "Failed to create session: "
                    f"{response.get('error')}",
                )
            body = response.get("body", {})
            sid = (
                body.get("id")
                if isinstance(body, dict)
                else None
            )
            builtins.print(
                f"[session] created id={sid}", flush=True,
            )
            self.current_session_id = sid or ""
            return self.current_session_id

        async def get_all_messages(
            self,
            session_id: str,
        ) -> list[dict[str, Any]]:
            assert self.sandbox is not None
            response = await self.run_client(
                [
                    "list-messages",
                    "--session-id",
                    session_id,
                ],
                timeout=60,
                quiet=True,
            )
            if response is None or not response.get("ok"):
                return []
            body = response.get("body")
            if isinstance(body, list):
                return body
            if isinstance(body, dict):
                for key in (
                    "items",
                    "messages",
                    "data",
                    "results",
                ):
                    value = body.get(key)
                    if isinstance(value, list):
                        return value
            return []

        async def get_all_sessions(
            self,
        ) -> list[dict[str, Any]]:
            assert self.sandbox is not None
            response = await self.run_client(
                ["list-sessions"], timeout=60, quiet=True,
            )
            if response is None or not response.get("ok"):
                return []
            body = response.get("body")
            if isinstance(body, list):
                return body
            if isinstance(body, dict):
                for key in (
                    "items",
                    "sessions",
                    "data",
                    "results",
                ):
                    value = body.get(key)
                    if isinstance(value, list):
                        return value
            return []

        async def collect_turn_messages(
            self,
            root_session_id: str,
        ) -> tuple[
            list[dict[str, Any]],
            list[str],
            list[dict[str, Any]],
        ]:
            sessions = await self.get_all_sessions()
            session_ids = get_session_family(
                root_session_id, sessions,
            )
            session_map: dict[str, dict[str, Any]] = {}
            for s in sessions:
                if not isinstance(s, dict):
                    continue
                sid = session_object_id(s)
                if sid and sid in session_ids:
                    session_map[sid] = s

            message_results = await asyncio.gather(
                *[
                    self.get_all_messages(sid)
                    for sid in session_ids
                ],
            )

            all_messages: list[dict[str, Any]] = []
            for sid, messages in zip(
                session_ids,
                message_results,
                strict=False,
            ):
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    info = message.setdefault("info", {})
                    if "sessionID" not in info:
                        info["sessionID"] = sid
                    all_messages.append(message)

            all_messages.sort(key=message_created_ms)

            new_messages: list[dict[str, Any]] = []
            for message in all_messages:
                mid = agent_message_id(message)
                if mid and mid in self.seen_message_ids:
                    continue
                if mid:
                    self.seen_message_ids.add(mid)
                new_messages.append(message)

            session_objects = [
                session_map[sid]
                for sid in session_ids
                if sid in session_map
            ]
            return session_objects, session_ids, new_messages

        async def send_message_blocking(
            self,
            text: str,
            timeout: int,
            remaining_parts_budget: int,
            on_stream_part=None,
        ) -> dict[str, Any] | None:
            assert self.sandbox is not None
            prompt_path = "/tmp/prompt.txt"
            await self.sandbox.write_file(
                prompt_path, text, ensure_dir=False,
            )
            builtins.print(
                f"[prompt] sending message ({len(text)} "
                f"chars), waiting up to {timeout}s...",
                flush=True,
            )

            async def handle_stderr_line(
                line: str,
            ) -> None:
                await parse_trace_event_line(
                    line, on_stream_part,
                )

            response = await self.run_client(
                [
                    "chat-stream",
                    "--session-id",
                    self.current_session_id or "",
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
            meta_obj = (
                meta if isinstance(meta, dict) else {}
            )
            aborted_for_part_limit = bool(
                meta_obj.get("aborted_for_part_limit"),
            )
            builtins.print(
                f"[prompt] done http={status_code} ok={ok}",
                flush=True,
            )

            if not ok:
                if aborted_for_part_limit:
                    builtins.print(
                        "[prompt] part limit reached during "
                        "stream; ending current turn",
                        flush=True,
                    )
                    if isinstance(body, dict):
                        stream_meta = body.get("_stream")
                        stream_obj = (
                            stream_meta
                            if isinstance(stream_meta, dict)
                            else {}
                        )
                        stream_obj.update(meta_obj)
                        body["_stream"] = stream_obj
                        return body
                    return {"_stream": dict(meta_obj)}
                error_text = str(
                    response.get("error") or body,
                )
                if len(error_text) > 1000:
                    error_text = (
                        error_text[:1000]
                        + "...[truncated]"
                    )
                builtins.print(
                    f"[prompt] ERROR: {error_text}",
                    flush=True,
                )
                return None

            if not isinstance(body, dict):
                if aborted_for_part_limit:
                    return {"_stream": dict(meta_obj)}
                builtins.print(
                    "[prompt] unexpected response type: "
                    f"{type(body).__name__}",
                    flush=True,
                )
                return None

            stream_meta = body.get("_stream")
            stream_obj = (
                stream_meta
                if isinstance(stream_meta, dict)
                else {}
            )
            stream_obj.update(meta_obj)
            body["_stream"] = stream_obj
            return body

        async def run_turn(
            self,
            *,
            prompt_text: str,
            timeout: int,
            remaining_parts_budget: int,
            on_stream_part=None,
        ) -> AgentTurnOutcome | None:
            response = await self.send_message_blocking(
                prompt_text,
                timeout=timeout,
                remaining_parts_budget=remaining_parts_budget,
                on_stream_part=on_stream_part,
            )
            if response is None:
                return None
            (
                session_objects,
                session_ids,
                new_messages,
            ) = await self.collect_turn_messages(
                self.current_session_id or "",
            )
            return AgentTurnOutcome(
                session_id=self.current_session_id or "",
                response=response,
                session_objects=session_objects,
                session_ids=session_ids,
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
            return await self.create_session(trajectory_id)

        async def collect_crash_messages(
            self,
            session_id: str,
        ) -> list[dict[str, Any]] | None:
            """Attempt to recover messages after a crash."""
            try:
                (
                    _session_objects,
                    _session_ids,
                    new_messages,
                ) = await self.collect_turn_messages(
                    session_id,
                )
                if new_messages:
                    return new_messages
            except Exception:
                pass
            return None

        async def stop(self) -> None:
            pass

    # The content of this file (agents/opencode.py) for uploading into
    # the sandbox as the client script.
    OPENCODE_CLIENT_CONTENT = Path(__file__).read_text()

except ImportError:
    pass  # Running as standalone sandbox script
