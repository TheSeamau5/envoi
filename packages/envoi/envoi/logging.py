from __future__ import annotations

import json
import os
import threading
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOG_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "envoi_log_context",
    default=None,
)
_LOG_CALLBACK: ContextVar[Any] = ContextVar(
    "envoi_log_callback",
    default=None,
)
_FILE_LOCK = threading.Lock()


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def json_default(value: Any) -> str:
    return str(value)


def default_component() -> str:
    component = os.environ.get("ENVOI_LOG_COMPONENT", "").strip()
    if component:
        return component
    return "envoi"


def component_name(default_name: str) -> str:
    component = os.environ.get("ENVOI_LOG_COMPONENT", "").strip()
    if component:
        return component
    return default_name


def log_component_event(
    default_name: str,
    event: str,
    *,
    message: str = "",
    level: str = "info",
    **fields: Any,
) -> dict[str, Any]:
    return log_event(
        component=component_name(default_name),
        event=event,
        message=message,
        level=level,
        **fields,
    )


def set_log_callback(callback: Any) -> Token[Any]:
    return _LOG_CALLBACK.set(callback)


def reset_log_callback(token: Token[Any]) -> None:
    _LOG_CALLBACK.reset(token)


def bind_log_context(**fields: Any) -> Token[dict[str, Any] | None]:
    current = dict(_LOG_CONTEXT.get() or {})
    for key, value in fields.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    return _LOG_CONTEXT.set(current)


def reset_log_context(token: Token[dict[str, Any] | None]) -> None:
    _LOG_CONTEXT.reset(token)


def update_log_context(**fields: Any) -> None:
    current = dict(_LOG_CONTEXT.get() or {})
    for key, value in fields.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    _LOG_CONTEXT.set(current)


def get_log_context() -> dict[str, Any]:
    return dict(_LOG_CONTEXT.get() or {})


def write_log_file(record: dict[str, Any]) -> None:
    log_path = (os.environ.get("ENVOI_LOG_PATH") or "").strip()
    if not log_path:
        return
    target = Path(log_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=json_default)
    with _FILE_LOCK:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def log_event(
    *,
    component: str | None = None,
    event: str = "log",
    message: str = "",
    level: str = "info",
    **fields: Any,
) -> dict[str, Any]:
    base = get_log_context()
    resolved_component = component or default_component()
    record: dict[str, Any] = {
        "ts": iso_now(),
        "component": resolved_component,
        "event": event,
        "level": level,
        "message": message,
    }
    record.update(base)
    for key, value in fields.items():
        if value is None:
            continue
        record[key] = value

    callback = _LOG_CALLBACK.get()
    if callable(callback):
        try:
            callback(record)
        except Exception:
            pass

    write_log_file(record)
    return record
