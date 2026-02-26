from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path

type LogRecord = dict[str, object]
type LogCallback = Callable[[LogRecord], object]


_LOG_CONTEXT: ContextVar[LogRecord | None] = ContextVar(
    "envoi_log_context",
    default=None,
)
_LOG_CALLBACK: ContextVar[LogCallback | None] = ContextVar(
    "envoi_log_callback",
    default=None,
)
_FILE_LOCK = threading.Lock()
DEFAULT_COMPONENT = "envoi"


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def json_default(value: object) -> str:
    return str(value)


def set_log_callback(callback: LogCallback | None) -> Token[LogCallback | None]:
    return _LOG_CALLBACK.set(callback)


def reset_log_callback(token: Token[LogCallback | None]) -> None:
    _LOG_CALLBACK.reset(token)


def bind_log_context(**fields: object) -> Token[LogRecord | None]:
    current = dict(_LOG_CONTEXT.get() or {})
    for key, value in fields.items():
        if value is None:
            _ = current.pop(key, None)
        else:
            current[key] = value
    return _LOG_CONTEXT.set(current)


def reset_log_context(token: Token[LogRecord | None]) -> None:
    _LOG_CONTEXT.reset(token)


def update_log_context(**fields: object) -> None:
    current = dict(_LOG_CONTEXT.get() or {})
    for key, value in fields.items():
        if value is None:
            _ = current.pop(key, None)
        else:
            current[key] = value
    _ = _LOG_CONTEXT.set(current)


def get_log_context() -> LogRecord:
    return dict(_LOG_CONTEXT.get() or {})


def write_log_file(record: LogRecord) -> None:
    log_path = (os.environ.get("ENVOI_LOG_PATH") or "").strip()
    if not log_path:
        return
    target = Path(log_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=json_default)
    with _FILE_LOCK:
        with target.open("a", encoding="utf-8") as handle:
            _ = handle.write(line + "\n")


def log_event(
    *,
    component: str | None = None,
    event: str = "log",
    message: str = "",
    level: str = "info",
    **fields: object,
) -> LogRecord:
    base = get_log_context()
    resolved_component = (
        component.strip()
        if isinstance(component, str) and component.strip()
        else DEFAULT_COMPONENT
    )
    record: LogRecord = {
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
    if callback is not None:
        try:
            _ = callback(record)
        except Exception:
            pass

    write_log_file(record)
    return record
