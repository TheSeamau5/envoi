"""S3 persistence for trace artifacts.

Handles saving trace.parquet after every part (save_trace_parquet), saving
structured logs.parquet snapshots (save_logs_parquet), loading a prior trace
for resume (load_trace_snapshot), uploading raw files like repo.bundle
(upload_file), and constructing S3 URIs (artifact_uri).
"""

from __future__ import annotations

import builtins
import io
import os
from typing import Any

import boto3

from envoi_code.models import AgentTrace
from envoi_code.scripts.materialize_summaries import (
    EVALUATION_SUMMARY_FILENAME,
    EVALUATION_SUMMARY_SCHEMA,
    SUMMARY_PUBLISH_FILENAMES,
    TRAJECTORY_SUMMARY_FILENAME,
    TRAJECTORY_SUMMARY_SCHEMA,
    build_summary_outputs,
    process_trace_rows,
    read_table_rows,
    replace_summary_rows_for_trajectory,
    summary_artifact_key,
)
from envoi_code.utils.helpers import tprint, ts
from envoi_code.utils.logs_parquet import (
    log_records_to_rows,
    write_logs_parquet,
)
from envoi_code.utils.trace_parquet import (
    agent_trace_to_rows,
    parquet_to_trace_dict,
    write_trace_parquet,
)

print = tprint

_s3_client = None
_last_saved_trace_log_key: dict[str, tuple[int, int, str]] = {}
_last_saved_logs_count: dict[str, int] = {}
_did_warn_bucket_deprecation = False
TRACE_SAVE_LOG_EVERY_PARTS = max(
    1, int(os.environ.get("TRACE_SAVE_LOG_EVERY_PARTS", "25"))
)
LOGS_SAVE_LOG_EVERY_ROWS = max(
    1, int(os.environ.get("LOGS_SAVE_LOG_EVERY_ROWS", "200"))
)


def normalize_prefix(raw_value: str) -> str:
    value = raw_value.strip()
    if value.startswith("s3://"):
        value = value[len("s3://") :]
    value = value.strip("/")
    if not value:
        return ""
    if "/" in value:
        raise RuntimeError(
            "AWS_S3_PREFIX must be a bucket name or s3://<bucket> (without path)"
        )
    return value


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
    return _s3_client


def get_prefix() -> str:
    global _did_warn_bucket_deprecation
    prefix = normalize_prefix(os.environ.get("AWS_S3_PREFIX") or "")
    if prefix:
        return prefix

    bucket = normalize_prefix(os.environ.get("AWS_S3_BUCKET") or "")
    if bucket:
        if not _did_warn_bucket_deprecation:
            builtins.print(
                f"[{ts()}] [s3] AWS_S3_BUCKET is deprecated; use AWS_S3_PREFIX",
                flush=True,
            )
            _did_warn_bucket_deprecation = True
        return bucket

    raise RuntimeError("AWS_S3_PREFIX environment variable is required")


def get_project() -> str:
    project = (os.environ.get("ENVOI_PROJECT") or "").strip()
    if project:
        return project
    return "default"


def trajectory_artifact_key(
    trajectory_id: str,
    filename: str,
    *,
    project: str | None = None,
) -> str:
    active_project = (project or get_project()).strip() or "default"
    return f"project/{active_project}/trajectories/{trajectory_id}/{filename}"


def build_trace_suites(trace: AgentTrace) -> dict[str, Any]:
    # Merge suite_results across all evaluations to build the most complete
    # picture. Individual evals may have partial results when suites timeout,
    # so taking the union ensures the suite definition reflects the full
    # environment rather than one eval's partial snapshot.
    suites: dict[str, Any] = {}
    for eval_rec in trace.evaluations.values():
        if not eval_rec.suite_results:
            continue
        for key, val in eval_rec.suite_results.items():
            if key not in suites:
                suites[key] = val
    return suites


def save_trace_parquet(
    trajectory_id: str,
    trace: AgentTrace,
    *,
    environment: str,
    task_params: dict[str, Any] | None = None,
    allow_empty: bool = False,
    project: str | None = None,
) -> None:
    """Serialize the current AgentTrace to parquet and upload to S3.

    Called after every part to ensure the trace is always persisted. Skips
    upload if the trace has no parts/turns (unless allow_empty=True).
    """
    part_count = len(trace.parts)
    turn_count = len(trace.turns)
    if not allow_empty and turn_count == 0 and part_count == 0:
        return

    rows = agent_trace_to_rows(
        trace,
        environment=environment,
        task_params=task_params or {},
        suites=build_trace_suites(trace),
        bundle_uri=artifact_uri(trajectory_id, "repo.bundle", project=project),
    )
    buf = io.BytesIO()
    write_trace_parquet(rows, buf)
    upload_file(trajectory_id, "trace.parquet", buf.getvalue(), project=project)
    session_reason = (
        trace.session_end.reason
        if trace.session_end is not None
        and isinstance(trace.session_end.reason, str)
        else ""
    )
    log_key = (part_count, turn_count, session_reason)
    previous_log_key = _last_saved_trace_log_key.get(trajectory_id)
    if previous_log_key != log_key:
        should_log = False
        if previous_log_key is None:
            should_log = True
        elif session_reason:
            should_log = True
        elif part_count <= 3:
            should_log = True
        elif part_count % TRACE_SAVE_LOG_EVERY_PARTS == 0:
            should_log = True
        if should_log:
            print(f"[s3] saved trace.parquet (parts={part_count})")
        _last_saved_trace_log_key[trajectory_id] = log_key


def save_logs_parquet(
    trajectory_id: str,
    records: list[dict[str, Any]],
    *,
    project: str | None = None,
) -> None:
    """Serialize structured logs to parquet and upload to S3."""
    if not records:
        return

    rows = log_records_to_rows(trajectory_id, records)
    if not rows:
        return

    buf = io.BytesIO()
    write_logs_parquet(rows, buf)
    upload_file(trajectory_id, "logs.parquet", buf.getvalue(), project=project)

    count = len(rows)
    previous_count = _last_saved_logs_count.get(trajectory_id)
    should_log = False
    if previous_count is None:
        should_log = True
    elif count <= 50:
        should_log = True
    elif count - previous_count >= LOGS_SAVE_LOG_EVERY_ROWS:
        should_log = True
    if should_log:
        builtins.print(f"[{ts()}] [s3] saved logs.parquet (rows={count})", flush=True)
        _last_saved_logs_count[trajectory_id] = count


def upload_file(
    trajectory_id: str,
    filename: str,
    data: bytes,
    *,
    project: str | None = None,
) -> str:
    s3 = get_s3_client()
    prefix = get_prefix()
    key = trajectory_artifact_key(trajectory_id, filename, project=project)
    s3.put_object(Bucket=prefix, Key=key, Body=data)
    return f"s3://{prefix}/{key}"


def artifact_uri(
    trajectory_id: str,
    filename: str,
    *,
    project: str | None = None,
) -> str:
    prefix = get_prefix()
    key = trajectory_artifact_key(trajectory_id, filename, project=project)
    return f"s3://{prefix}/{key}"


def download_optional_file_bytes(key: str) -> bytes | None:
    s3 = get_s3_client()
    prefix = get_prefix()
    try:
        response = s3.get_object(Bucket=prefix, Key=key)
    except Exception as error:
        code = str(
            getattr(error, "response", {}).get("Error", {}).get("Code", ""),
        ).strip()
        if code in {"NoSuchKey", "404", "NotFound"}:
            return None
        print(f"[s3] failed to download {key}: {error}")
        return None

    raw_body = response.get("Body")
    if raw_body is None:
        return None
    return raw_body.read()


def publish_completed_trajectory_summary(
    trace: AgentTrace,
    *,
    environment: str,
    task_params: dict[str, Any] | None = None,
    project: str | None = None,
) -> None:
    active_project = (project or get_project()).strip() or "default"
    if active_project != "c-compiler":
        return
    if not trace.parts:
        return

    trajectory_id = trace.trajectory_id
    trajectory_rows = agent_trace_to_rows(
        trace,
        environment=environment,
        task_params=task_params or {},
        suites=build_trace_suites(trace),
        bundle_uri=artifact_uri(trajectory_id, "repo.bundle", project=active_project),
    )
    trajectory_row, evaluation_rows = process_trace_rows(trajectory_rows)

    trajectory_key = summary_artifact_key(
        active_project,
        TRAJECTORY_SUMMARY_FILENAME,
    )
    evaluation_key = summary_artifact_key(
        active_project,
        EVALUATION_SUMMARY_FILENAME,
    )
    existing_trajectory_rows = read_table_rows(
        download_optional_file_bytes(trajectory_key),
        TRAJECTORY_SUMMARY_SCHEMA,
    )
    existing_evaluation_rows = read_table_rows(
        download_optional_file_bytes(evaluation_key),
        EVALUATION_SUMMARY_SCHEMA,
    )
    merged_trajectory_rows, merged_evaluation_rows = (
        replace_summary_rows_for_trajectory(
            existing_trajectory_rows,
            existing_evaluation_rows,
            trajectory_row,
            evaluation_rows,
        )
    )
    outputs = build_summary_outputs(
        merged_trajectory_rows,
        merged_evaluation_rows,
    )

    s3 = get_s3_client()
    prefix = get_prefix()
    for filename in SUMMARY_PUBLISH_FILENAMES:
        content_type = (
            "application/json"
            if filename.endswith(".json")
            else "application/octet-stream"
        )
        key = summary_artifact_key(active_project, filename)
        s3.put_object(
            Bucket=prefix,
            Key=key,
            Body=outputs[filename],
            ContentType=content_type,
        )
    print(
        f"[s3] published trajectory summaries for project={active_project} "
        f"trajectory_id={trajectory_id}",
    )


def load_trace_snapshot(
    trajectory_id: str,
    *,
    project: str | None = None,
) -> AgentTrace | None:
    """Download and parse a prior trace from S3 for resume.

    Returns None if the trace doesn't exist or can't be parsed. Used at the
    start of a run to restore state from a previous (possibly crashed) session.
    """
    s3 = get_s3_client()
    prefix = get_prefix()
    key = trajectory_artifact_key(trajectory_id, "trace.parquet", project=project)
    try:
        response = s3.get_object(Bucket=prefix, Key=key)
    except Exception as error:  # noqa: BLE001
        code = str(
            getattr(error, "response", {}).get("Error", {}).get("Code", "")
        ).strip()
        if code in {"NoSuchKey", "404", "NotFound"}:
            return None
        print(f"[resume] failed to load prior trace: {error}")
        return None

    raw_body = response.get("Body")
    if raw_body is None:
        return None
    try:
        buf = io.BytesIO(raw_body.read())
        trace_dict = parquet_to_trace_dict(buf)
    except Exception as error:  # noqa: BLE001
        print(f"[resume] failed to read parquet trace: {error}")
        return None

    try:
        return AgentTrace.model_validate(trace_dict)
    except Exception as error:  # noqa: BLE001
        print(f"[resume] failed to parse trace schema: {error}")
        return None
