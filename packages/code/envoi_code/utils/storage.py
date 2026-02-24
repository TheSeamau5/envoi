"""S3 persistence for trace artifacts.

Handles saving trace.parquet after every part (save_trace_parquet), loading a
prior trace for resume (load_trace_snapshot), uploading raw files like
repo.bundle (upload_file), and constructing S3 URIs (artifact_uri).
"""

from __future__ import annotations

import io
import os
from typing import Any

import boto3

from envoi_code.models import AgentTrace
from envoi_code.utils.helpers import tprint
from envoi_code.utils.trace_parquet import (
    agent_trace_to_rows,
    parquet_to_trace_dict,
    write_trace_parquet,
)

print = tprint

_s3_client = None
_last_saved_trace_log_key: dict[str, tuple[int, int, str]] = {}


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


def get_bucket() -> str:
    bucket = os.environ.get("AWS_S3_BUCKET")
    if not bucket:
        raise RuntimeError("AWS_S3_BUCKET environment variable is required")
    return bucket


def save_trace_parquet(
    trajectory_id: str,
    trace: AgentTrace,
    *,
    environment: str,
    task_params: dict[str, Any] | None = None,
    allow_empty: bool = False,
) -> None:
    """Serialize the current AgentTrace to parquet and upload to S3.

    Called after every part to ensure the trace is always persisted. Skips
    upload if the trace has no parts/turns (unless allow_empty=True).
    """
    part_count = len(trace.parts)
    turn_count = len(trace.turns)
    if not allow_empty and turn_count == 0 and part_count == 0:
        return

    suites: dict[str, Any] = {}
    for eval_rec in trace.evaluations.values():
        if eval_rec.suite_results:
            suites = eval_rec.suite_results

    rows = agent_trace_to_rows(
        trace,
        environment=environment,
        task_params=task_params or {},
        suites=suites,
        bundle_uri=artifact_uri(trajectory_id, "repo.bundle"),
    )
    buf = io.BytesIO()
    write_trace_parquet(rows, buf)
    upload_file(trajectory_id, "trace.parquet", buf.getvalue())
    session_reason = (
        trace.session_end.reason
        if trace.session_end is not None
        and isinstance(trace.session_end.reason, str)
        else ""
    )
    log_key = (part_count, turn_count, session_reason)
    previous_log_key = _last_saved_trace_log_key.get(trajectory_id)
    if previous_log_key != log_key:
        print(f"[s3] saved trace.parquet (parts={part_count})")
        _last_saved_trace_log_key[trajectory_id] = log_key


def upload_file(trajectory_id: str, filename: str, data: bytes) -> str:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/{filename}"
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    return f"s3://{bucket}/{key}"


def artifact_uri(trajectory_id: str, filename: str) -> str:
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/{filename}"
    return f"s3://{bucket}/{key}"


def load_trace_snapshot(trajectory_id: str) -> AgentTrace | None:
    """Download and parse a prior trace from S3 for resume.

    Returns None if the trace doesn't exist or can't be parsed. Used at the
    start of a run to restore state from a previous (possibly crashed) session.
    """
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/trace.parquet"
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
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
