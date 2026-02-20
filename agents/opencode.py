"""
Minimal OpenCode API wrapper using the Python SDK.

This script runs inside the Modal sandbox and talks to the local OpenCode server.
It prints one JSON object to stdout with the shape:
{
  "ok": bool,
  "status_code": int | null,
  "body": any,
  "error": str | null
}
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


def _tool_detail(tool: str, state: Any) -> str:
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
            detail = _tool_detail(tool, state)
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


def extract_run_tests_call_from_part(part: dict[str, Any]) -> dict[str, Any] | None:
    if str(part.get("tool") or "") != "run_tests":
        return None
    state = part.get("state")
    if not isinstance(state, dict):
        return None
    metadata = state.get("metadata")
    metadata_obj = metadata if isinstance(metadata, dict) else {}
    raw_output = state.get("output") or metadata_obj.get("output")
    parsed = parse_json_maybe(raw_output)
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
                                trace_event = {
                                    "event": "part.completed",
                                    "role": "assistant",
                                    "part_type": part_type,
                                    "item_type": part_type,
                                    "summary": stream_part_summary(part),
                                    "has_file_change": part_type == "patch",
                                    "files": files,
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
            meta = {
                "events_observed": len(events),
                "meaningful_parts_seen": meaningful_parts_seen,
                "aborted_for_part_limit": aborted_for_part_limit,
            }
            result["meta"] = meta
            if isinstance(body, dict):
                stream_stats = (
                    dict(body.get("_stream", {}))
                    if isinstance(body.get("_stream"), dict)
                    else {}
                )
                stream_stats.update(meta)
                body["_stream"] = stream_stats
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
