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
    from envoi_code.models import AgentTrace

TRACE_SCHEMA_VERSION = "envoi.trace.v2"

TRACE_SCHEMA = pa.schema([
    ("trajectory_id", pa.string()),
    ("session_id", pa.string()),
    ("agent", pa.string()),
    ("agent_model", pa.string()),
    ("started_at", pa.string()),
    ("trace_schema_version", pa.string()),
    ("environment", pa.string()),
    ("task_params", pa.string()),
    ("run_metadata", pa.string()),
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
    ("turn_user_message", pa.string()),
    ("turn_feedback_eval_id", pa.string()),
    ("eval_events_delta", pa.string()),
    ("session_end_reason", pa.string()),
    ("session_end_total_parts", pa.int32()),
    ("session_end_total_turns", pa.int32()),
    ("session_end_final_commit", pa.string()),
    ("suites", pa.string()),
    ("artifacts", pa.string()),
    ("bundle_uri", pa.string()),
])

SCALAR_PART_KEYS = (
    "trajectory_id", "session_id", "agent", "agent_model",
    "part", "timestamp", "role", "part_type", "item_type",
    "summary", "duration_ms", "git_commit", "content",
    "summary_word_count", "content_word_count",
    "summary_token_estimate", "content_token_estimate",
    "tool_name", "tool_status", "tool_exit_code", "patch",
    "turn",
    "turn_user_message",
    "turn_feedback_eval_id",
)

JSON_PART_KEYS = (
    "files", "tool_input", "tool_output", "tool_error",
    "token_usage", "envoi_calls", "testing_state", "repo_checkpoint",
    "eval_events_delta",
)


def json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def build_turn_map(trace: AgentTrace) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for turn_rec in trace.turns:
        if turn_rec.part_start is not None and turn_rec.part_end is not None:
            for p in range(turn_rec.part_start, turn_rec.part_end + 1):
                mapping[p] = turn_rec.turn
    return mapping


def build_turn_user_message_map(
    trace: AgentTrace,
) -> dict[int, str | None]:
    mapping: dict[int, str | None] = {}
    for turn_rec in trace.turns:
        if turn_rec.part_start is None:
            continue
        mapping[turn_rec.part_start] = turn_rec.prompt
    return mapping


def build_turn_feedback_eval_map(
    trace: AgentTrace,
) -> dict[int, str | None]:
    mapping: dict[int, str | None] = {}
    for turn_rec in trace.turns:
        if turn_rec.part_start is None:
            continue
        mapping[turn_rec.part_start] = turn_rec.feedback_eval_id
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

    Produces one dict per part. Trajectory-level fields (session_end, artifacts,
    suites) are denormalized into every row. Nested objects are serialized to JSON
    strings via json_or_none().
    """
    turn_map = build_turn_map(trace)
    turn_user_message_map = build_turn_user_message_map(trace)
    turn_feedback_eval_map = build_turn_feedback_eval_map(trace)

    se = trace.session_end
    se_reason = se.reason if se else None
    se_total_parts = se.total_parts if se else None
    se_total_turns = se.total_turns if se else None
    se_final_commit = se.final_git_commit if se else None

    suites_json = json_or_none(suites)
    artifacts_json = json_or_none(trace.artifacts)
    task_params_json = json_or_none(task_params)
    run_metadata_json = json_or_none(trace.run_metadata)

    rows: list[dict[str, Any]] = []
    for part_rec in trace.parts:
        rows.append({
            "trajectory_id": trace.trajectory_id,
            "session_id": part_rec.session_id,
            "agent": trace.agent,
            "agent_model": part_rec.agent_model,
            "started_at": trace.started_at,
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "environment": environment,
            "task_params": task_params_json,
            "run_metadata": run_metadata_json,
            "part": part_rec.part,
            "timestamp": part_rec.timestamp,
            "role": part_rec.role,
            "part_type": part_rec.part_type,
            "item_type": part_rec.item_type,
            "summary": part_rec.summary,
            "duration_ms": part_rec.duration_ms,
            "git_commit": part_rec.git_commit,
            "files": json_or_none(part_rec.files) if part_rec.files else None,
            "content": part_rec.content,
            "summary_word_count": part_rec.summary_word_count,
            "content_word_count": part_rec.content_word_count,
            "summary_token_estimate": part_rec.summary_token_estimate,
            "content_token_estimate": part_rec.content_token_estimate,
            "tool_name": part_rec.tool_name,
            "tool_status": part_rec.tool_status,
            "tool_input": json_or_none(part_rec.tool_input),
            "tool_output": json_or_none(part_rec.tool_output),
            "tool_error": json_or_none(part_rec.tool_error),
            "tool_exit_code": part_rec.tool_exit_code,
            "token_usage": json_or_none(part_rec.token_usage),
            "patch": part_rec.patch,
            "envoi_calls": json_or_none(
                [c.model_dump(mode="json") for c in part_rec.envoi_calls]
            ) if part_rec.envoi_calls else None,
            "testing_state": json_or_none(part_rec.testing_state),
            "repo_checkpoint": json_or_none(part_rec.repo_checkpoint),
            "turn": turn_map.get(part_rec.part) if part_rec.part is not None else None,
            "turn_user_message": (
                turn_user_message_map.get(part_rec.part)
                if part_rec.part is not None
                else None
            ),
            "turn_feedback_eval_id": (
                turn_feedback_eval_map.get(part_rec.part)
                if part_rec.part is not None
                else None
            ),
            "eval_events_delta": json_or_none(
                [e.model_dump(mode="json") for e in part_rec.eval_events_delta]
            ) if part_rec.eval_events_delta else None,
            "session_end_reason": se_reason,
            "session_end_total_parts": se_total_parts,
            "session_end_total_turns": se_total_turns,
            "session_end_final_commit": se_final_commit,
            "suites": suites_json,
            "artifacts": artifacts_json,
            "bundle_uri": bundle_uri,
        })
    return rows


def write_trace_parquet(rows: list[dict[str, Any]], dest: str | io.BytesIO) -> None:
    table = pa.Table.from_pylist(rows, schema=TRACE_SCHEMA)
    pq.write_table(table, dest)


def read_trace_parquet(source: str | io.BytesIO) -> list[dict[str, Any]]:
    table = pq.read_table(source)
    for field in TRACE_SCHEMA:
        if field.name in table.schema.names:
            continue
        nulls = pa.nulls(table.num_rows, type=field.type)
        table = table.append_column(field.name, nulls)
    table = table.select(TRACE_SCHEMA.names)
    table = table.cast(TRACE_SCHEMA, safe=False)
    return table.to_pylist()


def parse_json_field(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def build_evaluations_from_parts(
    parts: list[dict[str, Any]],
) -> dict[str, Any]:
    evaluations: dict[str, dict[str, Any]] = {}
    for part in parts:
        events = part.get("eval_events_delta")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("kind") != "commit_async":
                continue
            commit = event.get("target_commit")
            if not isinstance(commit, str) or not commit:
                continue
            trigger_part = event.get("trigger_part")
            part_value = (
                int(trigger_part)
                if isinstance(trigger_part, int)
                else part.get("part")
                if isinstance(part.get("part"), int)
                else 0
            )
            row = evaluations.setdefault(
                commit,
                {
                    "eval_id": (
                        event.get("eval_id")
                        if isinstance(event.get("eval_id"), str)
                        and event.get("eval_id")
                        else f"recovered-{commit[:12]}-{part_value}"
                    ),
                    "commit": commit,
                    "part": part_value,
                    "trigger_turn": event.get("trigger_turn"),
                    "kind": "commit_async",
                    "status": "queued",
                    "queued_at": event.get("queued_at")
                    or part.get("timestamp")
                    or "",
                    "started_at": None,
                    "completed_at": None,
                    "duration_ms": None,
                    "passed": 0,
                    "failed": 0,
                    "total": 0,
                    "payload": {},
                    "suite_results": {},
                    "tests": [],
                    "error": None,
                    "command": None,
                    "exit_code": None,
                    "stdout": None,
                    "stderr": None,
                },
            )
            status = event.get("status")
            if isinstance(status, str) and status:
                row["status"] = status
            if isinstance(event.get("eval_id"), str):
                row["eval_id"] = event.get("eval_id")
            if isinstance(event.get("trigger_turn"), int):
                row["trigger_turn"] = event.get("trigger_turn")
            if isinstance(event.get("queued_at"), str):
                row["queued_at"] = event.get("queued_at")
            if isinstance(event.get("started_at"), str):
                row["started_at"] = event.get("started_at")
            if isinstance(event.get("finished_at"), str):
                row["completed_at"] = event.get("finished_at")
            if isinstance(event.get("passed"), int):
                row["passed"] = event.get("passed")
            if isinstance(event.get("failed"), int):
                row["failed"] = event.get("failed")
            if isinstance(event.get("total"), int):
                row["total"] = event.get("total")
            event_payload = event.get("payload")
            if isinstance(event_payload, dict):
                row["payload"] = event_payload
            suite_results = event.get("suite_results")
            if isinstance(suite_results, dict):
                row["suite_results"] = suite_results
            tests = event.get("tests")
            if isinstance(tests, list):
                row["tests"] = tests
            error = event.get("error")
            if isinstance(error, str):
                row["error"] = error
    return evaluations


def build_turns_from_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        turn_value = row.get("turn")
        if not isinstance(turn_value, int):
            continue
        info = grouped.get(turn_value)
        if info is None:
            info = {
                "trajectory_id": row.get("trajectory_id"),
                "session_id": row.get("session_id"),
                "agent": row.get("agent"),
                "turn": turn_value,
                "part_start": row.get("part"),
                "part_end": row.get("part"),
                "timestamp": row.get("timestamp"),
                "agent_model": row.get("agent_model"),
                "prompt": row.get("turn_user_message"),
                "git_commit": row.get("git_commit"),
                "repo_checkpoint": parse_json_field(
                    row.get("repo_checkpoint"),
                ),
                "session_ids": [],
                "session_objects": [],
                "new_messages": [],
                "token_usage": None,
                "feedback_eval_id": row.get("turn_feedback_eval_id"),
                "parts": [],
                "session_end": None,
            }
            grouped[turn_value] = info
        part_number = row.get("part")
        if isinstance(part_number, int):
            start = info.get("part_start")
            end = info.get("part_end")
            if not isinstance(start, int) or part_number < start:
                info["part_start"] = part_number
            if not isinstance(end, int) or part_number > end:
                info["part_end"] = part_number
        if not isinstance(info.get("prompt"), str):
            prompt_value = row.get("turn_user_message")
            if isinstance(prompt_value, str):
                info["prompt"] = prompt_value
        if not isinstance(info.get("feedback_eval_id"), str):
            eval_id = row.get("turn_feedback_eval_id")
            if isinstance(eval_id, str):
                info["feedback_eval_id"] = eval_id
        if not isinstance(info.get("git_commit"), str):
            git_commit = row.get("git_commit")
            if isinstance(git_commit, str):
                info["git_commit"] = git_commit
    turns = list(grouped.values())
    turns.sort(key=turn_sort_key)
    return turns


def turn_sort_key(value: dict[str, Any]) -> int:
    turn_value: object = value.get("turn")
    return turn_value if isinstance(turn_value, int) else 10**9


def rows_to_trace_dict(rows: list[dict[str, Any]]) -> dict[str, Any]:
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
            "run_metadata": {},
            "session_end": None,
        }

    first = rows[0]

    parts: list[dict[str, Any]] = []
    for row in rows:
        part: dict[str, Any] = {}
        for key in SCALAR_PART_KEYS:
            part[key] = row.get(key)
        for key in JSON_PART_KEYS:
            value = parse_json_field(row.get(key))
            if key in {"files", "envoi_calls", "eval_events_delta"}:
                part[key] = value if isinstance(value, list) else []
            else:
                part[key] = value
        parts.append(part)

    evaluations = build_evaluations_from_parts(parts)
    turns = build_turns_from_rows(rows)
    artifacts = parse_json_field(first.get("artifacts")) or {}
    run_metadata = parse_json_field(first.get("run_metadata")) or {}

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
        "turns": turns,
        "evaluations": evaluations,
        "artifacts": artifacts,
        "run_metadata": run_metadata,
        "session_end": session_end,
    }


def parquet_to_trace_dict(source: str | io.BytesIO) -> dict[str, Any]:
    """Read a parquet file and reconstruct the trace dict."""
    return rows_to_trace_dict(read_trace_parquet(source))
