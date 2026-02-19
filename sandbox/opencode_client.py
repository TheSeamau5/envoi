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
from pathlib import Path
from typing import Any

import httpx
from opencode_ai import APIConnectionError, APIStatusError, AsyncOpencode


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    return value


async def raw_request_with_client(
    *,
    client: AsyncOpencode,
    method: str,
    path: str,
    request_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        error_body: Any = None
        if response is not None:
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text
        return {
            "ok": False,
            "status_code": status_code,
            "body": to_jsonable(error_body),
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


def summarize_event(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    properties = event.get("properties")
    if not isinstance(properties, dict):
        return None

    if event_type == "message.part.updated":
        part = properties.get("part")
        if not isinstance(part, dict):
            return None
        part_type = part.get("type")
        if part_type == "step-start":
            snapshot = str(part.get("snapshot", ""))
            return f"step-start {snapshot[:12]}"
        if part_type == "step-finish":
            reason = part.get("reason", "?")
            return f"step-finish reason={reason}"
        if part_type == "tool":
            tool = part.get("tool", "?")
            state = part.get("state")
            status = state.get("status", "?") if isinstance(state, dict) else "?"
            return f"tool {tool} status={status}"
        if part_type == "patch":
            files = part.get("files")
            file_count = len(files) if isinstance(files, list) else 0
            return f"patch files={file_count}"
        return None

    if event_type == "session.idle":
        return "session-idle"
    if event_type == "session.error":
        error = properties.get("error")
        return f"session-error {error}" if error is not None else "session-error"

    return None


async def stream_session_events(
    *,
    client: AsyncOpencode,
    session_id: str,
    done_event: asyncio.Event,
    max_steps: int = 0,
) -> tuple[list[dict[str, Any]], int, bool]:
    events: list[dict[str, Any]] = []
    step_finishes_seen = 0
    aborted_for_step_limit = False
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
                    print(f"[stream] {summary}", file=sys.stderr, flush=True)

                if event_obj.get("type") == "message.part.updated":
                    properties = event_obj.get("properties")
                    part = properties.get("part") if isinstance(properties, dict) else None
                    if isinstance(part, dict) and part.get("type") == "step-finish":
                        step_finishes_seen += 1
                        if (
                            max_steps > 0
                            and step_finishes_seen >= max_steps
                            and not aborted_for_step_limit
                        ):
                            aborted_for_step_limit = True
                            print(
                                (
                                    "[stream] step budget reached "
                                    f"({step_finishes_seen}/{max_steps}), aborting session"
                                ),
                                file=sys.stderr,
                                flush=True,
                            )
                            try:
                                await client.session.abort(session_id)
                            except Exception as error:  # noqa: BLE001
                                print(
                                    f"[stream] abort warning: {error}",
                                    file=sys.stderr,
                                    flush=True,
                                )

                if done_event.is_set() and event_obj.get("type") == "session.idle":
                    break
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        print(f"[stream] warning: {error}", file=sys.stderr, flush=True)
    return events, step_finishes_seen, aborted_for_step_limit


async def chat_with_stream(
    *,
    session_id: str,
    text: str,
    max_steps: int = 0,
) -> dict[str, Any]:
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
                    max_steps=max_steps,
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
            step_finishes_seen = 0
            aborted_for_step_limit = False
            try:
                events, step_finishes_seen, aborted_for_step_limit = await asyncio.wait_for(
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
                "step_finishes_seen": step_finishes_seen,
                "aborted_for_step_limit": aborted_for_step_limit,
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
            elif meta["aborted_for_step_limit"]:
                result["body"] = {"_stream": dict(meta)}
            return result
    except APIStatusError as error:
        status_code = getattr(error, "status_code", None)
        response = getattr(error, "response", None)
        error_body: Any = None
        if response is not None:
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text
        return {
            "ok": False,
            "status_code": status_code,
            "body": to_jsonable(error_body),
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
    chat_stream_parser.add_argument("--max-steps", type=int, default=0)

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
            max_steps=max(0, args.max_steps),
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
