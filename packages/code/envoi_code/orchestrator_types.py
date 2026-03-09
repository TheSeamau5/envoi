from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from envoi_code.models import AgentTrace
from envoi_code.params_api import ResolvedParams

RunStopReason = Literal[
    "solved",
    "part_limit",
    "timeout",
    "agent_error",
    "envoi_error",
]

EnvironmentFiles = (
    tuple[
        dict[str, str],
        dict[str, str],
        dict[str, str],
        dict[str, str],
    ]
    | None
)


class TurnEndEvaluationOutcome(BaseModel):
    feedback: str
    payload: dict[str, Any] | None
    passed: int | None
    total: int | None
    has_error: bool
    no_tests_detected: bool


class TurnLoopResult(BaseModel):
    session_id: str
    prompt_text: str
    turn_count: int
    part_count: int
    latest_git_commit: str | None
    end_reason: RunStopReason
    evaluator: Any

    model_config = {"arbitrary_types_allowed": True}


class TrajectoryExecutionResult(BaseModel):
    sandbox: Any
    eval_sandbox: Any
    agent_trace: AgentTrace
    agent_backend: Any
    evaluator: Any
    session_id: str
    prompt_text: str
    turn_count: int
    part_count: int
    latest_git_commit: str | None
    end_reason: RunStopReason

    model_config = {"arbitrary_types_allowed": True}


class TrajectoryPreparedContext(BaseModel):
    project: str
    max_parts: int | None
    max_turns: int | None
    selected_test_paths: list[str]
    agent_name: str
    task_path: Path
    env_path: Path
    environment: str
    agent_cls: type
    resolved_model: str
    credentials: Any
    prompt: str
    task_params_loaded: dict[str, Any]
    effective_resolved_env_params: ResolvedParams
    normalized_advisor_model: str | None
    normalized_advisor_thinking_level: str
    advisor_max_output_tokens: int | None
    failed_tests_feedback_limit: int
    advisor_system_prompt_override: str | None
    advisor_user_prompt_prefix_override: str | None
    dockerfile_rel_path: str
    docker_build_args: dict[str, str]
    sandbox_cpu_request: float | None
    sandbox_memory_mb_request: int | None
    sandbox_min_cpu: float | None
    sandbox_min_memory_mb: int | None
    run_metadata: dict[str, Any]
    env_files: EnvironmentFiles
    existing_trace: AgentTrace | None
    trace_s3_uri: str
    bundle_s3_uri: str
    logs_s3_uri: str

    model_config = {"arbitrary_types_allowed": True}


class LogsRuntime(BaseModel):
    records: list[dict[str, Any]]
    flush: Callable[..., Awaitable[None]]
    capture: Callable[[dict[str, Any]], None]
    task: asyncio.Task[None]
    wakeup: asyncio.Event
    stop: asyncio.Event

    model_config = {"arbitrary_types_allowed": True}
