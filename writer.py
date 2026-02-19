from __future__ import annotations

import json
from typing import Any

from models import EnvoiCall, TestResult


def extract_envoi_calls(message_parts: list[dict[str, Any]]) -> list[EnvoiCall]:
    calls: list[EnvoiCall] = []

    tool_results: dict[str, dict[str, Any]] = {}
    for part in message_parts:
        if part.get("type") == "tool_result":
            tool_results[part.get("tool_use_id", "")] = part

    for part in message_parts:
        if part.get("type") == "tool_use" and part.get("name") == "run_tests":
            tool_result = tool_results.get(part.get("id", ""))
            if tool_result:
                content = tool_result.get("content", "")
                if isinstance(content, str):
                    try:
                        data = json.loads(content)
                        calls.append(EnvoiCall(**data))
                    except json.JSONDecodeError:
                        pass

    return calls


def has_tool_calls(message_parts: list[dict[str, Any]]) -> bool:
    for part in message_parts:
        if part.get("type") == "tool_use":
            return True
    return False


def parse_envoi_result(raw_result: dict[str, Any]) -> TestResult | None:
    if not raw_result:
        return None
    try:
        return TestResult(**raw_result)
    except Exception:
        return None
