from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
