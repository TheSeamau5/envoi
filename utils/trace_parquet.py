"""Parquet serialization for AgentTrace.

Converts between the in-memory AgentTrace model and the flat one-row-per-part
parquet format. Trajectory-level fields are denormalized into every row so that
any single row is self-contained. Nested objects (envoi_calls, testing_state,
repo_checkpoint, token_usage) are stored as JSON strings.

Key functions:
  agent_trace_to_rows() -- AgentTrace -> list of flat row dicts
  parquet_to_trace_dict() -- parquet bytes -> reconstructed trace dict
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.parquet as pq

if TYPE_CHECKING:
    from models import AgentTrace

TRACE_SCHEMA = pa.schema([
    ("trajectory_id", pa.string()),
    ("session_id", pa.string()),
    ("agent", pa.string()),
    ("agent_model", pa.string()),
    ("started_at", pa.string()),
    ("environment", pa.string()),
    ("task_params", pa.string()),
    ("part", pa.int32()),
    ("timestamp", pa.string()),
    ("role", pa.string()),
    ("part_type", pa.string()),
    ("item_type", pa.string()),
    ("summary", pa.string()),
    ("duration_ms", pa.int64()),
    ("git_commit", pa.string()),
    ("files", pa.string()),
    ("content", pa.string()),
    ("summary_word_count", pa.int32()),
    ("content_word_count", pa.int32()),
    ("summary_token_estimate", pa.int32()),
    ("content_token_estimate", pa.int32()),
    ("tool_name", pa.string()),
    ("tool_status", pa.string()),
    ("tool_input", pa.string()),
    ("tool_output", pa.string()),
    ("tool_error", pa.string()),
    ("tool_exit_code", pa.int32()),
    ("token_usage", pa.string()),
    ("patch", pa.string()),
    ("envoi_calls", pa.string()),
    ("testing_state", pa.string()),
    ("repo_checkpoint", pa.string()),
    ("turn", pa.int32()),
    ("session_end_reason", pa.string()),
    ("session_end_total_parts", pa.int32()),
    ("session_end_total_turns", pa.int32()),
    ("session_end_final_commit", pa.string()),
    ("evaluations", pa.string()),
    ("suites", pa.string()),
    ("artifacts", pa.string()),
    ("bundle_uri", pa.string()),
])

_SCALAR_PART_KEYS = (
    "trajectory_id", "session_id", "agent", "agent_model",
    "part", "timestamp", "role", "part_type", "item_type",
    "summary", "duration_ms", "git_commit", "content",
    "summary_word_count", "content_word_count",
    "summary_token_estimate", "content_token_estimate",
    "tool_name", "tool_status", "tool_exit_code", "patch",
)

_JSON_PART_KEYS = (
    "files", "tool_input", "tool_output", "tool_error",
    "token_usage", "envoi_calls", "testing_state", "repo_checkpoint",
)


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _build_turn_map(trace: AgentTrace) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for turn_rec in trace.turns:
        if turn_rec.part_start is not None and turn_rec.part_end is not None:
            for p in range(turn_rec.part_start, turn_rec.part_end + 1):
                mapping[p] = turn_rec.turn
    return mapping


def agent_trace_to_rows(
    trace: AgentTrace,
    *,
    environment: str,
    task_params: dict[str, Any],
    suites: dict[str, Any],
    bundle_uri: str | None,
) -> list[dict[str, Any]]:
    """Convert an AgentTrace to flat row dicts for parquet serialization.

    Produces one dict per part. Trajectory-level fields (session_end, evaluations,
    artifacts, suites) are denormalized into every row. Nested objects are serialized
    to JSON strings via _json_or_none().
    """
    turn_map = _build_turn_map(trace)

    se = trace.session_end
    se_reason = se.reason if se else None
    se_total_parts = se.total_parts if se else None
    se_total_turns = se.total_turns if se else None
    se_final_commit = se.final_git_commit if se else None

    evals_json = _json_or_none(
        {k: v.model_dump(mode="json") for k, v in trace.evaluations.items()}
    )
    suites_json = _json_or_none(suites)
    artifacts_json = _json_or_none(trace.artifacts)
    task_params_json = _json_or_none(task_params)

    rows: list[dict[str, Any]] = []
    for part_rec in trace.parts:
        rows.append({
            "trajectory_id": trace.trajectory_id,
            "session_id": part_rec.session_id,
            "agent": trace.agent,
            "agent_model": part_rec.agent_model,
            "started_at": trace.started_at,
            "environment": environment,
            "task_params": task_params_json,
            "part": part_rec.part,
            "timestamp": part_rec.timestamp,
            "role": part_rec.role,
            "part_type": part_rec.part_type,
            "item_type": part_rec.item_type,
            "summary": part_rec.summary,
            "duration_ms": part_rec.duration_ms,
            "git_commit": part_rec.git_commit,
            "files": _json_or_none(part_rec.files) if part_rec.files else None,
            "content": part_rec.content,
            "summary_word_count": part_rec.summary_word_count,
            "content_word_count": part_rec.content_word_count,
            "summary_token_estimate": part_rec.summary_token_estimate,
            "content_token_estimate": part_rec.content_token_estimate,
            "tool_name": part_rec.tool_name,
            "tool_status": part_rec.tool_status,
            "tool_input": _json_or_none(part_rec.tool_input),
            "tool_output": _json_or_none(part_rec.tool_output),
            "tool_error": _json_or_none(part_rec.tool_error),
            "tool_exit_code": part_rec.tool_exit_code,
            "token_usage": _json_or_none(part_rec.token_usage),
            "patch": part_rec.patch,
            "envoi_calls": _json_or_none(
                [c.model_dump(mode="json") for c in part_rec.envoi_calls]
            ) if part_rec.envoi_calls else None,
            "testing_state": _json_or_none(part_rec.testing_state),
            "repo_checkpoint": _json_or_none(part_rec.repo_checkpoint),
            "turn": turn_map.get(part_rec.part) if part_rec.part is not None else None,
            "session_end_reason": se_reason,
            "session_end_total_parts": se_total_parts,
            "session_end_total_turns": se_total_turns,
            "session_end_final_commit": se_final_commit,
            "evaluations": evals_json,
            "suites": suites_json,
            "artifacts": artifacts_json,
            "bundle_uri": bundle_uri,
        })
    return rows


def write_trace_parquet(rows: list[dict[str, Any]], dest: str | io.BytesIO) -> None:
    table = pa.Table.from_pylist(rows, schema=TRACE_SCHEMA)
    pq.write_table(table, dest)


def read_trace_parquet(source: str | io.BytesIO) -> list[dict[str, Any]]:
    table = pq.read_table(source, schema=TRACE_SCHEMA)
    return table.to_pylist()


def _parse_json_field(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def _rows_to_trace_dict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconstruct the trace dict from flat parquet rows.

    Returns a dict with: trajectory_id, session_id, agent, agent_model,
    started_at, parts[], turns[], evaluations{}, artifacts{}, session_end{}.
    """
    if not rows:
        return {
            "parts": [],
            "turns": [],
            "evaluations": {},
            "artifacts": {},
            "session_end": None,
        }

    first = rows[0]

    parts: list[dict[str, Any]] = []
    for row in rows:
        part: dict[str, Any] = {}
        for key in _SCALAR_PART_KEYS:
            part[key] = row.get(key)
        for key in _JSON_PART_KEYS:
            part[key] = _parse_json_field(row.get(key))
        parts.append(part)

    evaluations = _parse_json_field(first.get("evaluations")) or {}
    artifacts = _parse_json_field(first.get("artifacts")) or {}

    session_end = None
    se_reason = first.get("session_end_reason")
    if se_reason is not None:
        session_end = {
            "reason": se_reason,
            "total_parts": first.get("session_end_total_parts"),
            "total_turns": first.get("session_end_total_turns"),
            "final_git_commit": first.get("session_end_final_commit"),
        }

    return {
        "trajectory_id": first.get("trajectory_id"),
        "session_id": first.get("session_id"),
        "agent": first.get("agent"),
        "agent_model": first.get("agent_model"),
        "started_at": first.get("started_at"),
        "parts": parts,
        "turns": [],
        "evaluations": evaluations,
        "artifacts": artifacts,
        "session_end": session_end,
    }


def parquet_to_trace_dict(source: str | io.BytesIO) -> dict[str, Any]:
    """Read a parquet file and reconstruct the trace dict."""
    return _rows_to_trace_dict(read_trace_parquet(source))
