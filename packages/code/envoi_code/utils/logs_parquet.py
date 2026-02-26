"""Parquet serialization helpers for structured runtime logs."""

from __future__ import annotations

import io
import json
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

LOG_SCHEMA = pa.schema([
    ("trajectory_id", pa.string()),
    ("seq", pa.int64()),
    ("ts", pa.string()),
    ("component", pa.string()),
    ("event", pa.string()),
    ("level", pa.string()),
    ("message", pa.string()),
    ("turn", pa.int32()),
    ("part", pa.int32()),
    ("git_commit", pa.string()),
    ("session_id", pa.string()),
    ("source", pa.string()),
    ("fields", pa.string()),
])

_CORE_KEYS: set[str] = {
    "ts",
    "component",
    "event",
    "level",
    "message",
    "turn",
    "part",
    "git_commit",
    "session_id",
    "source",
}


def json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def int_or_none(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def str_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return None


def log_records_to_rows(
    trajectory_id: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        extras = {
            key: value
            for key, value in record.items()
            if key not in _CORE_KEYS
        }
        rows.append(
            {
                "trajectory_id": trajectory_id,
                "seq": idx,
                "ts": str_or_none(record.get("ts")),
                "component": str_or_none(record.get("component")),
                "event": str_or_none(record.get("event")),
                "level": str_or_none(record.get("level")),
                "message": str_or_none(record.get("message")),
                "turn": int_or_none(record.get("turn")),
                "part": int_or_none(record.get("part")),
                "git_commit": str_or_none(record.get("git_commit")),
                "session_id": str_or_none(record.get("session_id")),
                "source": str_or_none(record.get("source")),
                "fields": json_or_none(extras) if extras else None,
            }
        )
    return rows


def write_logs_parquet(rows: list[dict[str, Any]], dest: str | io.BytesIO) -> None:
    table = pa.Table.from_pylist(rows, schema=LOG_SCHEMA)
    pq.write_table(table, dest)


def read_logs_parquet(source: str | io.BytesIO) -> list[dict[str, Any]]:
    table = pq.read_table(source, schema=LOG_SCHEMA)
    return table.to_pylist()
