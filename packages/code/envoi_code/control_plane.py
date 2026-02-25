from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import itertools
import json
import os
import random
import shlex
import signal
import sys
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import boto3
import pyarrow.parquet as pq
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from envoi_code.param_space import resolve_environment_param_space
from envoi_code.params_api import ParamSpace

RunStatus = Literal[
    "draft",
    "queued",
    "launching",
    "active_no_trace",
    "active_with_trace",
    "finishing",
    "completed",
    "failed",
    "timeout",
    "canceled",
]
BatchStatus = Literal[
    "draft",
    "queued",
    "running",
    "paused",
    "completed",
    "failed",
    "canceled",
]
ParamMode = Literal["manual", "grid", "random"]

TERMINAL_RUN_STATUSES: set[RunStatus] = {
    "completed",
    "failed",
    "timeout",
    "canceled",
}
ACTIVE_RUN_STATUSES: set[RunStatus] = {
    "launching",
    "active_no_trace",
    "active_with_trace",
    "finishing",
}


class EnvironmentLaunchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    task_dir: str
    environment_dir: str
    mode: ParamMode = "random"
    run_count: int | None = None
    manual_params: list[dict[str, str]] = Field(default_factory=list)
    grid_params: dict[str, list[str]] = Field(default_factory=dict)
    random_params: dict[str, list[str]] = Field(default_factory=dict)


class BatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    total_runs: int = Field(ge=1, le=10000)
    environments: list[EnvironmentLaunchConfig] = Field(min_length=1)
    agent: Literal["codex", "opencode"] = "codex"
    model: str | None = None
    max_parts: int | None = Field(default=None, ge=1)
    max_turns: int | None = Field(default=None, ge=1)
    timeout_seconds: int = Field(default=7200, ge=30)
    test_paths: list[str] = Field(default_factory=list)


class BatchActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    status: BatchStatus


class RunLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    timestamp: str
    channel: Literal["stdout", "stderr", "structured", "system"]
    message: str
    fields: dict[str, Any] | None = None


class StructuredLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    timestamp: str
    component: str | None = None
    event: str | None = None
    level: str | None = None
    message: str | None = None
    turn: int | None = None
    part: int | None = None
    git_commit: str | None = None
    session_id: str | None = None
    source: str | None = None
    fields: dict[str, Any] | None = None


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal[
        "run_created",
        "run_status",
        "run_trajectory",
        "run_metric",
        "run_finished",
        "run_canceled",
    ]
    run_id: str
    timestamp: str
    status: RunStatus | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BatchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal["batch_created", "batch_status", "batch_finished"]
    batch_id: str
    timestamp: str
    status: BatchStatus
    details: dict[str, Any] = Field(default_factory=dict)


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    batch_id: str
    name: str
    task_dir: str
    environment_dir: str
    environment_name: str
    params: dict[str, str] = Field(default_factory=dict)
    status: RunStatus = "draft"
    agent: Literal["codex", "opencode"] = "codex"
    model: str | None = None
    max_parts: int | None = None
    max_turns: int | None = None
    timeout_seconds: int = 7200
    test_paths: list[str] = Field(default_factory=list)
    attempt_count: int = 0
    trajectory_id: str | None = None
    trace_s3_uri: str | None = None
    bundle_s3_uri: str | None = None
    logs_s3_uri: str | None = None
    latest_end_reason: str | None = None
    created_at: str
    queued_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    canceled_at: str | None = None
    command: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    has_trace_data: bool = False
    raw_logs: list[RunLogEntry] = Field(default_factory=list)
    structured_logs: list[StructuredLogEntry] = Field(default_factory=list)
    latest_raw_sequence: int = 0
    latest_structured_sequence: int = 0


class BatchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    name: str
    status: BatchStatus = "draft"
    created_at: str
    launched_at: str | None = None
    paused_at: str | None = None
    finished_at: str | None = None
    total_runs: int
    run_ids: list[str] = Field(default_factory=list)


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    batch_id: str
    name: str
    environment_name: str
    status: RunStatus
    attempt_count: int
    params: dict[str, str] = Field(default_factory=dict)
    trajectory_id: str | None = None
    trace_s3_uri: str | None = None
    bundle_s3_uri: str | None = None
    logs_s3_uri: str | None = None
    latest_end_reason: str | None = None
    created_at: str
    queued_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    has_trace_data: bool = False


class BatchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    name: str
    status: BatchStatus
    created_at: str
    launched_at: str | None = None
    paused_at: str | None = None
    finished_at: str | None = None
    total_runs: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    run_ids: list[str] = Field(default_factory=list)


class BatchDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch: BatchSummary
    runs: list[RunSummary] = Field(default_factory=list)


class RunDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RunSummary


class RunLogsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    raw_logs: list[RunLogEntry] = Field(default_factory=list)
    structured_logs: list[StructuredLogEntry] = Field(default_factory=list)


class ParamSpaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_dir: str
    environment_dir: str


class ParamSpaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_dir: str
    task_dir: str
    param_space: ParamSpace


state_lock = asyncio.Lock()
batch_records: dict[str, BatchRecord] = {}
run_records: dict[str, RunRecord] = {}

run_event_subscribers: dict[str, list[asyncio.Queue[RunEvent]]] = {}
run_log_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
batch_event_subscribers: dict[str, list[asyncio.Queue[BatchEvent]]] = {}

run_tasks: dict[str, asyncio.Task[None]] = {}
run_processes: dict[str, asyncio.subprocess.Process] = {}
structured_log_tasks: dict[str, asyncio.Task[None]] = {}
cancel_requested: set[str] = set()

scheduler_wakeup = asyncio.Event()
scheduler_stop = asyncio.Event()
scheduler_task: asyncio.Task[None] | None = None

control_plane_workdir = Path(
    os.environ.get(
        "ENVOI_CONTROL_PLANE_WORKDIR",
        str(Path.cwd()),
    )
).expanduser().resolve()
max_global_active = int(os.environ.get("ENVOI_CONTROL_PLANE_MAX_ACTIVE", "20"))
max_environment_active = int(
    os.environ.get("ENVOI_CONTROL_PLANE_MAX_ACTIVE_PER_ENVIRONMENT", "5")
)
structured_log_poll_seconds = float(
    os.environ.get("ENVOI_CONTROL_PLANE_STRUCTURED_LOG_POLL_SECONDS", "5")
)


def now_iso():
    return datetime.now(UTC).isoformat()


def normalize_param_key(raw_key: str):
    return raw_key.replace("-", "_").strip().lower()


def normalized_environment_name(environment_dir: str):
    return Path(environment_dir).name


def parse_json_or_empty(raw_value: str):
    stripped_value = raw_value.strip()
    if not stripped_value:
        return {}
    try:
        parsed_value = json.loads(stripped_value)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed_value, dict):
        return parsed_value
    return {}


def status_counts_for_batch(batch_record: BatchRecord):
    counts: dict[str, int] = {}
    for run_id in batch_record.run_ids:
        run_record = run_records.get(run_id)
        if run_record is None:
            continue
        counts[run_record.status] = counts.get(run_record.status, 0) + 1
    return counts


def run_summary_from_record(run_record: RunRecord):
    return RunSummary(
        run_id=run_record.run_id,
        batch_id=run_record.batch_id,
        name=run_record.name,
        environment_name=run_record.environment_name,
        status=run_record.status,
        attempt_count=run_record.attempt_count,
        params=dict(run_record.params),
        trajectory_id=run_record.trajectory_id,
        trace_s3_uri=run_record.trace_s3_uri,
        bundle_s3_uri=run_record.bundle_s3_uri,
        logs_s3_uri=run_record.logs_s3_uri,
        latest_end_reason=run_record.latest_end_reason,
        created_at=run_record.created_at,
        queued_at=run_record.queued_at,
        started_at=run_record.started_at,
        finished_at=run_record.finished_at,
        has_trace_data=run_record.has_trace_data,
    )


def batch_summary_from_record(batch_record: BatchRecord):
    return BatchSummary(
        batch_id=batch_record.batch_id,
        name=batch_record.name,
        status=batch_record.status,
        created_at=batch_record.created_at,
        launched_at=batch_record.launched_at,
        paused_at=batch_record.paused_at,
        finished_at=batch_record.finished_at,
        total_runs=batch_record.total_runs,
        status_counts=status_counts_for_batch(batch_record),
        run_ids=list(batch_record.run_ids),
    )


def json_sse_payload(payload: BaseModel):
    return f"data: {payload.model_dump_json(exclude_none=True)}\n\n"


async def append_run_event(run_id: str, run_event: RunEvent):
    subscriber_queues = run_event_subscribers.get(run_id)
    if not subscriber_queues:
        return
    for subscriber_queue in list(subscriber_queues):
        with contextlib.suppress(asyncio.QueueFull):
            subscriber_queue.put_nowait(run_event)


async def append_batch_event(batch_id: str, batch_event: BatchEvent):
    subscriber_queues = batch_event_subscribers.get(batch_id)
    if not subscriber_queues:
        return
    for subscriber_queue in list(subscriber_queues):
        with contextlib.suppress(asyncio.QueueFull):
            subscriber_queue.put_nowait(batch_event)


async def append_run_log(run_id: str, payload: dict[str, Any]):
    subscriber_queues = run_log_subscribers.get(run_id)
    if not subscriber_queues:
        return
    for subscriber_queue in list(subscriber_queues):
        with contextlib.suppress(asyncio.QueueFull):
            subscriber_queue.put_nowait(payload)


async def transition_run_status(
    run_record: RunRecord,
    next_status: RunStatus,
    details: dict[str, Any] | None = None,
):
    run_record.status = next_status
    if next_status == "queued":
        run_record.queued_at = now_iso()
    if next_status in ACTIVE_RUN_STATUSES and run_record.started_at is None:
        run_record.started_at = now_iso()
    if next_status in TERMINAL_RUN_STATUSES:
        run_record.finished_at = now_iso()
    event_payload = RunEvent(
        event_type="run_status",
        run_id=run_record.run_id,
        timestamp=now_iso(),
        status=next_status,
        details=details or {},
    )
    await append_run_event(run_record.run_id, event_payload)


async def refresh_batch_status(batch_id: str):
    batch_record = batch_records.get(batch_id)
    if batch_record is None:
        return

    run_statuses = [
        run_records[run_id].status
        for run_id in batch_record.run_ids
        if run_id in run_records
    ]
    if not run_statuses:
        return

    if all(status in TERMINAL_RUN_STATUSES for status in run_statuses):
        if all(status == "canceled" for status in run_statuses):
            batch_record.status = "canceled"
        elif any(status in {"failed", "timeout"} for status in run_statuses):
            batch_record.status = "failed"
        else:
            batch_record.status = "completed"
        batch_record.finished_at = now_iso()
        await append_batch_event(
            batch_record.batch_id,
            BatchEvent(
                event_type="batch_finished",
                batch_id=batch_record.batch_id,
                timestamp=now_iso(),
                status=batch_record.status,
                details={
                    "status_counts": status_counts_for_batch(batch_record),
                },
            ),
        )
        return

    if batch_record.status == "paused":
        return

    if any(status in ACTIVE_RUN_STATUSES for status in run_statuses):
        batch_record.status = "running"
    elif any(status == "queued" for status in run_statuses):
        batch_record.status = "queued"
    else:
        batch_record.status = "running"

    await append_batch_event(
        batch_record.batch_id,
        BatchEvent(
            event_type="batch_status",
            batch_id=batch_record.batch_id,
            timestamp=now_iso(),
            status=batch_record.status,
            details={
                "status_counts": status_counts_for_batch(batch_record),
            },
        ),
    )


def param_sets_from_grid(
    grid_params: dict[str, list[str]],
):
    normalized_pairs = [
        (normalize_param_key(param_key), [str(value) for value in values if str(value)])
        for param_key, values in grid_params.items()
    ]
    normalized_pairs = [pair for pair in normalized_pairs if pair[1]]
    if not normalized_pairs:
        return [{}]

    axis_names = [name for name, _values in normalized_pairs]
    axis_values = [values for _name, values in normalized_pairs]
    run_params: list[dict[str, str]] = []
    for combination in itertools.product(*axis_values):
        param_values = {
            axis_names[index]: str(combination[index])
            for index in range(len(axis_names))
        }
        run_params.append(param_values)
    return run_params


def param_sets_from_random(
    random_params: dict[str, list[str]],
    run_count: int,
):
    normalized_pairs = [
        (normalize_param_key(param_key), [str(value) for value in values if str(value)])
        for param_key, values in random_params.items()
    ]
    normalized_pairs = [pair for pair in normalized_pairs if pair[1]]
    if run_count <= 0:
        return []
    if not normalized_pairs:
        return [{} for _ in range(run_count)]

    all_combinations = param_sets_from_grid(
        {
            param_key: param_values
            for param_key, param_values in normalized_pairs
        },
    )
    if len(all_combinations) <= run_count:
        shuffled_combinations = list(all_combinations)
        random.shuffle(shuffled_combinations)
        return shuffled_combinations
    sampled_combinations = random.sample(all_combinations, run_count)
    return sampled_combinations


def options_from_param_space(param_space: ParamSpace):
    option_map: dict[str, list[str]] = {}
    for dimension in param_space.dimensions:
        if dimension.kind != "enum":
            continue
        if not dimension.allow_random:
            continue
        values = [option.value for option in dimension.options if option.value]
        if not values:
            continue
        option_map[normalize_param_key(dimension.key)] = values
    return option_map


async def generated_param_sets_for_environment(
    launch_config: EnvironmentLaunchConfig,
    run_count: int,
):
    if launch_config.mode == "manual":
        if launch_config.manual_params:
            return [
                {
                    normalize_param_key(param_key): str(param_value)
                    for param_key, param_value in params.items()
                    if str(param_value)
                }
                for params in launch_config.manual_params
            ]
        return [{} for _ in range(run_count)]

    if launch_config.mode == "grid":
        generated_params = param_sets_from_grid(launch_config.grid_params)
        return generated_params[:run_count]

    random_axes = dict(launch_config.random_params)
    if not random_axes:
        resolved_param_space = await resolve_environment_param_space(
            environment_dir=Path(launch_config.environment_dir).expanduser().resolve(),
            task_dir=Path(launch_config.task_dir).expanduser().resolve(),
            selected_test_paths=[],
        )
        random_axes = options_from_param_space(resolved_param_space)
    return param_sets_from_random(random_axes, run_count)


def even_split_counts(total_runs: int, environment_count: int):
    if environment_count <= 0:
        return []
    base_count = total_runs // environment_count
    remainder_count = total_runs % environment_count
    return [
        base_count + (1 if index < remainder_count else 0)
        for index in range(environment_count)
    ]


def active_count_for_environment(environment_name: str):
    return sum(
        1
        for run_record in run_records.values()
        if run_record.environment_name == environment_name
        and run_record.status in ACTIVE_RUN_STATUSES
    )


def active_run_count():
    return sum(
        1
        for run_record in run_records.values()
        if run_record.status in ACTIVE_RUN_STATUSES
    )


def next_queued_run_id():
    batch_candidates = sorted(
        batch_records.values(),
        key=lambda batch_record: batch_record.created_at,
    )
    for batch_record in batch_candidates:
        if batch_record.status not in {"queued", "running"}:
            continue
        for run_id in batch_record.run_ids:
            run_record = run_records.get(run_id)
            if run_record is None:
                continue
            if run_record.status != "queued":
                continue
            if active_run_count() >= max_global_active:
                return None
            if (
                active_count_for_environment(run_record.environment_name)
                >= max_environment_active
            ):
                continue
            return run_id
    return None


def command_for_run(run_record: RunRecord):
    command = [
        "envoi",
        "code",
        "--task",
        run_record.task_dir,
        "--env",
        run_record.environment_dir,
        "--agent",
        run_record.agent,
        "--trajectory-id",
        run_record.trajectory_id or run_record.run_id,
        "--timeout-seconds",
        str(run_record.timeout_seconds),
    ]
    if run_record.model:
        command.extend(["--model", run_record.model])
    if run_record.max_parts is not None:
        command.extend(["--max-parts", str(run_record.max_parts)])
    if run_record.max_turns is not None:
        command.extend(["--max-turns", str(run_record.max_turns)])
    for test_path in run_record.test_paths:
        command.extend(["--test", test_path])
    for param_key, param_value in run_record.params.items():
        command.extend([
            f"--param-{param_key.replace('_', '-')}",
            str(param_value),
        ])
    return command


def parse_s3_uri(uri: str):
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URI: {uri}")
    bucket_name = parsed.netloc
    object_key = parsed.path.lstrip("/")
    return bucket_name, object_key


async def add_raw_log(
    run_record: RunRecord,
    channel: Literal["stdout", "stderr", "system"],
    message: str,
):
    run_record.latest_raw_sequence += 1
    raw_entry = RunLogEntry(
        sequence=run_record.latest_raw_sequence,
        timestamp=now_iso(),
        channel=channel,
        message=message,
    )
    run_record.raw_logs.append(raw_entry)
    await append_run_log(
        run_record.run_id,
        {
            "kind": "raw",
            "entry": raw_entry.model_dump(mode="json", exclude_none=True),
        },
    )


async def add_structured_log(
    run_record: RunRecord,
    row: dict[str, Any],
):
    sequence_value = row.get("seq")
    if not isinstance(sequence_value, int):
        return
    if sequence_value <= run_record.latest_structured_sequence:
        return

    fields_value = row.get("fields")
    fields = (
        parse_json_or_empty(fields_value)
        if isinstance(fields_value, str)
        else fields_value
        if isinstance(fields_value, dict)
        else {}
    )
    structured_entry = StructuredLogEntry(
        sequence=sequence_value,
        timestamp=str(row.get("ts") or now_iso()),
        component=row.get("component") if isinstance(row.get("component"), str) else None,
        event=row.get("event") if isinstance(row.get("event"), str) else None,
        level=row.get("level") if isinstance(row.get("level"), str) else None,
        message=row.get("message") if isinstance(row.get("message"), str) else None,
        turn=row.get("turn") if isinstance(row.get("turn"), int) else None,
        part=row.get("part") if isinstance(row.get("part"), int) else None,
        git_commit=row.get("git_commit") if isinstance(row.get("git_commit"), str) else None,
        session_id=row.get("session_id") if isinstance(row.get("session_id"), str) else None,
        source=row.get("source") if isinstance(row.get("source"), str) else None,
        fields=fields or None,
    )
    run_record.latest_structured_sequence = sequence_value
    run_record.structured_logs.append(structured_entry)
    await append_run_log(
        run_record.run_id,
        {
            "kind": "structured",
            "entry": structured_entry.model_dump(mode="json", exclude_none=True),
        },
    )


def maybe_update_artifact_uri(run_record: RunRecord, line: str):
    if line.startswith("TRACE_S3_URI:"):
        run_record.trace_s3_uri = line.split(":", 1)[1].strip()
        return True
    if line.startswith("BUNDLE_S3_URI:"):
        run_record.bundle_s3_uri = line.split(":", 1)[1].strip()
        return True
    if line.startswith("LOGS_S3_URI:"):
        run_record.logs_s3_uri = line.split(":", 1)[1].strip()
        return True
    if line.startswith("TRAJECTORY_ID:"):
        run_record.trajectory_id = line.split(":", 1)[1].strip()
        return True
    return False


def maybe_update_end_reason(run_record: RunRecord, line: str):
    if not line.startswith("[end] reason="):
        return
    raw_reason = line.removeprefix("[end] reason=")
    reason_value = raw_reason.split(" ", 1)[0].strip()
    if reason_value:
        run_record.latest_end_reason = reason_value


async def drain_process_stream(
    run_record: RunRecord,
    stream_reader: asyncio.StreamReader,
    channel: Literal["stdout", "stderr"],
):
    while True:
        line_bytes = await stream_reader.readline()
        if not line_bytes:
            break
        text_line = line_bytes.decode(errors="replace").rstrip("\n")
        async with state_lock:
            await add_raw_log(run_record, channel, text_line)
            changed = maybe_update_artifact_uri(run_record, text_line)
            maybe_update_end_reason(run_record, text_line)
            if "saved trace.parquet" in text_line and not run_record.has_trace_data:
                run_record.has_trace_data = True
                await transition_run_status(
                    run_record,
                    "active_with_trace",
                    details={
                        "reason": "trace_detected",
                    },
                )
            if changed:
                await append_run_event(
                    run_record.run_id,
                    RunEvent(
                        event_type="run_trajectory",
                        run_id=run_record.run_id,
                        timestamp=now_iso(),
                        status=run_record.status,
                        details={
                            "trajectory_id": run_record.trajectory_id,
                            "trace_s3_uri": run_record.trace_s3_uri,
                            "bundle_s3_uri": run_record.bundle_s3_uri,
                            "logs_s3_uri": run_record.logs_s3_uri,
                        },
                    ),
                )


async def ingest_structured_logs_periodically(run_id: str):
    while True:
        await asyncio.sleep(max(1.0, structured_log_poll_seconds))
        async with state_lock:
            run_record = run_records.get(run_id)
            if run_record is None:
                return
            if run_record.status in TERMINAL_RUN_STATUSES and run_record.logs_s3_uri is None:
                return
            logs_uri = run_record.logs_s3_uri
        if logs_uri is None:
            continue
        try:
            bucket_name, object_key = parse_s3_uri(logs_uri)
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                region_name=os.environ.get("AWS_REGION", "us-east-1"),
            )
            response = await asyncio.to_thread(
                s3_client.get_object,
                Bucket=bucket_name,
                Key=object_key,
            )
            body = response.get("Body")
            if body is None:
                continue
            raw_bytes = await asyncio.to_thread(body.read)
            table = await asyncio.to_thread(
                pq.read_table,
                io.BytesIO(raw_bytes),
            )
            rows = table.to_pylist()
        except Exception:
            continue

        async with state_lock:
            run_record = run_records.get(run_id)
            if run_record is None:
                return
            for row in rows:
                if isinstance(row, dict):
                    await add_structured_log(run_record, row)
            if run_record.status in TERMINAL_RUN_STATUSES:
                return


async def execute_run(run_id: str):
    async with state_lock:
        run_record = run_records.get(run_id)
        if run_record is None:
            return
        run_record.attempt_count += 1
        run_record.trajectory_id = run_record.trajectory_id or run_record.run_id
        run_record.command = command_for_run(run_record)
        await transition_run_status(
            run_record,
            "launching",
            details={
                "attempt": run_record.attempt_count,
                "command": " ".join(shlex.quote(part) for part in run_record.command),
            },
        )

    command = run_record.command
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(control_plane_workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as process_error:
        async with state_lock:
            run_record = run_records.get(run_id)
            if run_record is None:
                return
            await add_raw_log(
                run_record,
                "system",
                f"launch_failed: {process_error}",
            )
            run_record.exit_code = 1
            await transition_run_status(
                run_record,
                "failed",
                details={
                    "error": str(process_error),
                },
            )
            await refresh_batch_status(run_record.batch_id)
            scheduler_wakeup.set()
        return

    async with state_lock:
        run_processes[run_id] = process
        run_record = run_records.get(run_id)
        if run_record is None:
            return
        await transition_run_status(run_record, "active_no_trace")
        await add_raw_log(
            run_record,
            "system",
            f"process_started pid={process.pid}",
        )
        if run_id not in structured_log_tasks:
            structured_log_tasks[run_id] = asyncio.create_task(
                ingest_structured_logs_periodically(run_id)
            )

    stdout_reader = process.stdout
    stderr_reader = process.stderr
    stream_tasks: list[asyncio.Task[Any]] = []
    if stdout_reader is not None:
        stream_tasks.append(
            asyncio.create_task(
                drain_process_stream(run_record, stdout_reader, "stdout")
            )
        )
    if stderr_reader is not None:
        stream_tasks.append(
            asyncio.create_task(
                drain_process_stream(run_record, stderr_reader, "stderr")
            )
        )

    await asyncio.gather(*stream_tasks, return_exceptions=True)
    exit_code = await process.wait()

    async with state_lock:
        run_processes.pop(run_id, None)
        run_record = run_records.get(run_id)
        if run_record is None:
            scheduler_wakeup.set()
            return

        run_record.exit_code = exit_code
        await transition_run_status(
            run_record,
            "finishing",
            details={"exit_code": exit_code},
        )

        final_status: RunStatus
        if run_id in cancel_requested:
            final_status = "canceled"
            run_record.canceled_at = now_iso()
            cancel_requested.discard(run_id)
        elif run_record.latest_end_reason == "timeout":
            final_status = "timeout"
        elif exit_code == 0:
            final_status = "completed"
        else:
            final_status = "failed"

        await transition_run_status(
            run_record,
            final_status,
            details={
                "exit_code": exit_code,
                "end_reason": run_record.latest_end_reason,
            },
        )
        await append_run_event(
            run_record.run_id,
            RunEvent(
                event_type="run_finished",
                run_id=run_record.run_id,
                timestamp=now_iso(),
                status=final_status,
                details={
                    "exit_code": exit_code,
                    "end_reason": run_record.latest_end_reason,
                    "has_trace_data": run_record.has_trace_data,
                },
            ),
        )

        structured_task = structured_log_tasks.pop(run_id, None)
        if structured_task is not None:
            structured_task.cancel()

        await refresh_batch_status(run_record.batch_id)
        scheduler_wakeup.set()


async def scheduler_loop():
    while not scheduler_stop.is_set():
        await scheduler_wakeup.wait()
        scheduler_wakeup.clear()

        while not scheduler_stop.is_set():
            async with state_lock:
                run_id = next_queued_run_id()
                if run_id is None:
                    break
                if run_id in run_tasks:
                    break
                task = asyncio.create_task(execute_run(run_id))
                run_tasks[run_id] = task
                attach_task_cleanup(run_id, task)
            await asyncio.sleep(0)


async def cleanup_task(run_id: str):
    async with state_lock:
        run_tasks.pop(run_id, None)


def attach_task_cleanup(run_id: str, task: asyncio.Task[None]):
    def done_callback(_: asyncio.Task[None]):
        asyncio.create_task(cleanup_task(run_id))

    task.add_done_callback(done_callback)


async def register_run_event_subscriber(run_id: str):
    subscriber_queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=500)
    async with state_lock:
        run_event_subscribers.setdefault(run_id, []).append(subscriber_queue)
    return subscriber_queue


async def unregister_run_event_subscriber(
    run_id: str,
    subscriber_queue: asyncio.Queue[RunEvent],
):
    async with state_lock:
        queues = run_event_subscribers.get(run_id)
        if not queues:
            return
        if subscriber_queue in queues:
            queues.remove(subscriber_queue)
        if not queues:
            run_event_subscribers.pop(run_id, None)


async def register_run_log_subscriber(run_id: str):
    subscriber_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
    async with state_lock:
        run_log_subscribers.setdefault(run_id, []).append(subscriber_queue)
    return subscriber_queue


async def unregister_run_log_subscriber(
    run_id: str,
    subscriber_queue: asyncio.Queue[dict[str, Any]],
):
    async with state_lock:
        queues = run_log_subscribers.get(run_id)
        if not queues:
            return
        if subscriber_queue in queues:
            queues.remove(subscriber_queue)
        if not queues:
            run_log_subscribers.pop(run_id, None)


async def register_batch_event_subscriber(batch_id: str):
    subscriber_queue: asyncio.Queue[BatchEvent] = asyncio.Queue(maxsize=500)
    async with state_lock:
        batch_event_subscribers.setdefault(batch_id, []).append(subscriber_queue)
    return subscriber_queue


async def unregister_batch_event_subscriber(
    batch_id: str,
    subscriber_queue: asyncio.Queue[BatchEvent],
):
    async with state_lock:
        queues = batch_event_subscribers.get(batch_id)
        if not queues:
            return
        if subscriber_queue in queues:
            queues.remove(subscriber_queue)
        if not queues:
            batch_event_subscribers.pop(batch_id, None)


app = FastAPI(title="envoi-control-plane")


@app.on_event("startup")
async def startup_event():
    global scheduler_task
    scheduler_stop.clear()
    scheduler_wakeup.set()
    scheduler_task = asyncio.create_task(scheduler_loop())


@app.on_event("shutdown")
async def shutdown_event():
    scheduler_stop.set()
    scheduler_wakeup.set()
    if scheduler_task is not None:
        scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler_task

    async with state_lock:
        running_processes = list(run_processes.values())
        running_tasks = list(run_tasks.values())
        polling_tasks = list(structured_log_tasks.values())
        run_processes.clear()
        run_tasks.clear()
        structured_log_tasks.clear()

    for process in running_processes:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
    for task in running_tasks + polling_tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@app.get("/health")
async def health_check():
    return {
        "ok": True,
        "timestamp": now_iso(),
    }


@app.post("/api/v1/param-spaces", response_model=ParamSpaceResponse)
async def resolve_param_space(request: ParamSpaceRequest):
    task_dir = Path(request.task_dir).expanduser().resolve()
    environment_dir = Path(request.environment_dir).expanduser().resolve()
    if not task_dir.exists():
        raise HTTPException(status_code=400, detail="task_dir does not exist")
    if not environment_dir.exists():
        raise HTTPException(status_code=400, detail="environment_dir does not exist")
    try:
        param_space = await resolve_environment_param_space(
            environment_dir=environment_dir,
            task_dir=task_dir,
            selected_test_paths=[],
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"failed to resolve param space: {error}",
        ) from error
    return ParamSpaceResponse(
        environment_dir=str(environment_dir),
        task_dir=str(task_dir),
        param_space=param_space,
    )


@app.post("/api/v1/batches", response_model=BatchDetailResponse)
async def create_batch(request: BatchCreateRequest):
    run_counts = even_split_counts(request.total_runs, len(request.environments))

    batch_id = uuid.uuid4().hex
    created_at = now_iso()
    batch_record = BatchRecord(
        batch_id=batch_id,
        name=request.name,
        created_at=created_at,
        total_runs=request.total_runs,
    )

    generated_run_ids: list[str] = []

    for index, environment in enumerate(request.environments):
        allocated_count = environment.run_count
        if allocated_count is None:
            allocated_count = run_counts[index]
        if allocated_count <= 0:
            continue
        generated_params = await generated_param_sets_for_environment(
            environment,
            allocated_count,
        )
        if not generated_params:
            generated_params = [{} for _ in range(allocated_count)]

        for param_index, params in enumerate(generated_params):
            run_id = uuid.uuid4().hex
            run_name = f"{environment.name}-{param_index + 1}"
            run_record = RunRecord(
                run_id=run_id,
                batch_id=batch_id,
                name=run_name,
                task_dir=str(Path(environment.task_dir).expanduser().resolve()),
                environment_dir=str(Path(environment.environment_dir).expanduser().resolve()),
                environment_name=environment.name,
                params=params,
                status="draft",
                agent=request.agent,
                model=request.model,
                max_parts=request.max_parts,
                max_turns=request.max_turns,
                timeout_seconds=request.timeout_seconds,
                test_paths=list(request.test_paths),
                created_at=created_at,
            )
            run_records[run_id] = run_record
            generated_run_ids.append(run_id)

    if not generated_run_ids:
        raise HTTPException(status_code=400, detail="No runs generated from request")

    batch_record.run_ids = generated_run_ids
    batch_record.total_runs = len(generated_run_ids)
    batch_records[batch_id] = batch_record

    async with state_lock:
        await append_batch_event(
            batch_id,
            BatchEvent(
                event_type="batch_created",
                batch_id=batch_id,
                timestamp=now_iso(),
                status=batch_record.status,
                details={
                    "total_runs": batch_record.total_runs,
                },
            ),
        )
        for run_id in generated_run_ids:
            run_record = run_records[run_id]
            await append_run_event(
                run_id,
                RunEvent(
                    event_type="run_created",
                    run_id=run_id,
                    timestamp=now_iso(),
                    status=run_record.status,
                    details={
                        "batch_id": batch_id,
                        "environment_name": run_record.environment_name,
                    },
                ),
            )

    batch_summary = batch_summary_from_record(batch_record)
    run_summaries = [
        run_summary_from_record(run_records[run_id])
        for run_id in batch_record.run_ids
        if run_id in run_records
    ]
    return BatchDetailResponse(
        batch=batch_summary,
        runs=run_summaries,
    )


@app.get("/api/v1/batches", response_model=list[BatchSummary])
async def list_batches():
    sorted_batches = sorted(
        batch_records.values(),
        key=lambda batch_record: batch_record.created_at,
        reverse=True,
    )
    return [batch_summary_from_record(batch_record) for batch_record in sorted_batches]


@app.post("/api/v1/batches/{batch_id}/launch", response_model=BatchActionResponse)
async def launch_batch(batch_id: str):
    async with state_lock:
        batch_record = batch_records.get(batch_id)
        if batch_record is None:
            raise HTTPException(status_code=404, detail="batch not found")

        for run_id in batch_record.run_ids:
            run_record = run_records.get(run_id)
            if run_record is None:
                continue
            if run_record.status == "draft":
                await transition_run_status(run_record, "queued")

        batch_record.status = "queued"
        batch_record.launched_at = now_iso()
        batch_record.paused_at = None

        await append_batch_event(
            batch_id,
            BatchEvent(
                event_type="batch_status",
                batch_id=batch_id,
                timestamp=now_iso(),
                status=batch_record.status,
                details={
                    "status_counts": status_counts_for_batch(batch_record),
                },
            ),
        )
        scheduler_wakeup.set()

    return BatchActionResponse(
        batch_id=batch_id,
        status="queued",
    )


@app.post("/api/v1/batches/{batch_id}/pause", response_model=BatchActionResponse)
async def pause_batch(batch_id: str):
    async with state_lock:
        batch_record = batch_records.get(batch_id)
        if batch_record is None:
            raise HTTPException(status_code=404, detail="batch not found")

        batch_record.status = "paused"
        batch_record.paused_at = now_iso()
        await append_batch_event(
            batch_id,
            BatchEvent(
                event_type="batch_status",
                batch_id=batch_id,
                timestamp=now_iso(),
                status=batch_record.status,
                details={
                    "status_counts": status_counts_for_batch(batch_record),
                },
            ),
        )

    return BatchActionResponse(batch_id=batch_id, status="paused")


@app.post("/api/v1/batches/{batch_id}/resume", response_model=BatchActionResponse)
async def resume_batch(batch_id: str):
    async with state_lock:
        batch_record = batch_records.get(batch_id)
        if batch_record is None:
            raise HTTPException(status_code=404, detail="batch not found")

        if batch_record.status == "paused":
            has_active = any(
                run_records[run_id].status in ACTIVE_RUN_STATUSES
                for run_id in batch_record.run_ids
                if run_id in run_records
            )
            batch_record.status = "running" if has_active else "queued"
            batch_record.paused_at = None

        await append_batch_event(
            batch_id,
            BatchEvent(
                event_type="batch_status",
                batch_id=batch_id,
                timestamp=now_iso(),
                status=batch_record.status,
                details={
                    "status_counts": status_counts_for_batch(batch_record),
                },
            ),
        )
        scheduler_wakeup.set()

    return BatchActionResponse(batch_id=batch_id, status=batch_record.status)


@app.get("/api/v1/batches/{batch_id}", response_model=BatchDetailResponse)
async def get_batch(batch_id: str):
    batch_record = batch_records.get(batch_id)
    if batch_record is None:
        raise HTTPException(status_code=404, detail="batch not found")

    run_summaries = [
        run_summary_from_record(run_records[run_id])
        for run_id in batch_record.run_ids
        if run_id in run_records
    ]
    return BatchDetailResponse(
        batch=batch_summary_from_record(batch_record),
        runs=run_summaries,
    )


@app.get("/api/v1/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(run_id: str):
    run_record = run_records.get(run_id)
    if run_record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunDetailResponse(run=run_summary_from_record(run_record))


@app.post("/api/v1/runs/{run_id}/cancel", response_model=RunDetailResponse)
async def cancel_run(run_id: str):
    async with state_lock:
        run_record = run_records.get(run_id)
        if run_record is None:
            raise HTTPException(status_code=404, detail="run not found")

        if run_record.status in TERMINAL_RUN_STATUSES:
            return RunDetailResponse(run=run_summary_from_record(run_record))

        cancel_requested.add(run_id)
        process = run_processes.get(run_id)
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                process.send_signal(signal.SIGTERM)

        await append_run_event(
            run_id,
            RunEvent(
                event_type="run_canceled",
                run_id=run_id,
                timestamp=now_iso(),
                status=run_record.status,
                details={
                    "cancel_requested": True,
                },
            ),
        )
        await refresh_batch_status(run_record.batch_id)
        scheduler_wakeup.set()

        return RunDetailResponse(run=run_summary_from_record(run_record))


@app.get("/api/v1/runs/{run_id}/logs", response_model=RunLogsResponse)
async def get_run_logs(
    run_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
):
    run_record = run_records.get(run_id)
    if run_record is None:
        raise HTTPException(status_code=404, detail="run not found")

    raw_logs = [
        entry
        for entry in run_record.raw_logs
        if entry.sequence > after_sequence
    ]
    structured_logs = [
        entry
        for entry in run_record.structured_logs
        if entry.sequence > after_sequence
    ]
    return RunLogsResponse(
        run_id=run_id,
        raw_logs=raw_logs[:limit],
        structured_logs=structured_logs[:limit],
    )


@app.get("/api/v1/runs/{run_id}/events/stream")
async def stream_run_events(run_id: str):
    if run_id not in run_records:
        raise HTTPException(status_code=404, detail="run not found")
    queue = await register_run_event_subscriber(run_id)

    async def event_generator():
        try:
            while True:
                try:
                    run_event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield json_sse_payload(run_event)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await unregister_run_event_subscriber(run_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/v1/runs/{run_id}/logs/stream")
async def stream_run_logs(run_id: str):
    if run_id not in run_records:
        raise HTTPException(status_code=404, detail="run not found")
    queue = await register_run_log_subscriber(run_id)

    async def event_generator():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    serialized_payload = json.dumps(
                        payload,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                    yield f"data: {serialized_payload}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await unregister_run_log_subscriber(run_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/v1/batches/{batch_id}/events/stream")
async def stream_batch_events(batch_id: str):
    if batch_id not in batch_records:
        raise HTTPException(status_code=404, detail="batch not found")
    queue = await register_batch_event_subscriber(batch_id)

    async def event_generator():
        try:
            while True:
                try:
                    batch_event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield json_sse_payload(batch_event)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await unregister_batch_event_subscriber(batch_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


def main():
    parser = argparse.ArgumentParser(prog="envoi-control-plane")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    try:
        uvicorn.run(
            "envoi_code.control_plane:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            factory=False,
        )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
