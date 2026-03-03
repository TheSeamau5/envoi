from __future__ import annotations

import json
import re
import sys
from typing import Any

USAGE_KEYS_DEFAULT = ("usage", "token_usage", "tokenUsage", "tokens")


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


def normalize_run_tests_payload(
    value: Any,
    *,
    nested_keys: tuple[str, ...] = (
        "result",
        "data",
        "output",
        "structured_content",
        "structuredContent",
    ),
) -> dict[str, Any] | None:
    parsed = parse_json_maybe(value)
    if not isinstance(parsed, dict):
        return None
    if "path" in parsed and "timestamp" in parsed:
        return parsed
    for nested_key in nested_keys:
        if nested_key not in parsed:
            continue
        nested = normalize_run_tests_payload(
            parsed.get(nested_key),
            nested_keys=nested_keys,
        )
        if isinstance(nested, dict):
            return nested
    return None


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def merge_usage_maps(base: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if key not in base:
            base[key] = value
            continue
        existing = base[key]
        if isinstance(existing, dict) and isinstance(value, dict):
            merge_usage_maps(existing, value)
        elif is_number(existing) and is_number(value):
            base[key] = existing + value
        else:
            base[key] = value


def event_token_usage(
    event_obj: dict[str, Any],
    part: dict[str, Any],
    *,
    event_container_keys: tuple[str, ...] = ("properties", "params"),
    part_container_keys: tuple[str, ...] = ("metadata", "state"),
    usage_keys: tuple[str, ...] = USAGE_KEYS_DEFAULT,
) -> dict[str, Any] | None:
    for container_key in event_container_keys:
        container = event_obj.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in usage_keys:
            value = container.get(key)
            if isinstance(value, dict) and value:
                return value

    for key in usage_keys:
        value = event_obj.get(key)
        if isinstance(value, dict) and value:
            return value

    for container_key in part_container_keys:
        container = part.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in usage_keys:
            value = container.get(key)
            if isinstance(value, dict) and value:
                return value

    for key in usage_keys:
        value = part.get(key)
        if isinstance(value, dict) and value:
            return value
    return None


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


def extract_usage_from_events(
    events: list[dict[str, Any]],
    *,
    top_level_container_keys: tuple[str, ...] = ("properties", "params"),
    usage_keys: tuple[str, ...] = USAGE_KEYS_DEFAULT,
    deep: bool = False,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for event_obj in events:
        if not isinstance(event_obj, dict):
            continue
        for container_key in top_level_container_keys:
            container = event_obj.get(container_key)
            if not isinstance(container, dict):
                continue
            for key in usage_keys:
                value = container.get(key)
                if isinstance(value, dict):
                    candidates.append(value)
            if deep:
                collect_usage_candidates(container, candidates)
        if deep:
            collect_usage_candidates(event_obj, candidates)

    if not candidates:
        return None

    merged: dict[str, Any] = {}
    for candidate in candidates:
        merge_usage_maps(merged, candidate)
    return merged or None


def format_elapsed(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours} h {minutes} m {secs} s"
    if minutes > 0:
        return f"{minutes} m {secs} s"
    return f"{secs} s"


def emit_trace_event(
    payload: dict[str, Any],
    *,
    prefix: str = "TRACE_EVENT ",
) -> None:
    print(
        f"{prefix}{json.dumps(payload, separators=(',', ':'), ensure_ascii=False)}",
        file=sys.stderr,
        flush=True,
    )


def canonical_token(value: str) -> str:
    converted = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    converted = converted.replace("-", "_").replace(".", "_")
    converted = re.sub(r"[^A-Za-z0-9_/]", "_", converted)
    converted = re.sub(r"_+", "_", converted)
    return converted.strip("_").lower()
