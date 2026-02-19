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
