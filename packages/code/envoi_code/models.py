"""Pydantic models shared across envoi-trace.

PartRecord is the fundamental unit — one row in trace.parquet. TurnRecord groups
parts from a single agent request/response. AgentTrace is the top-level container
that holds all parts, turns, evaluations, and session metadata for one trajectory.

Supporting models: EnvoiCall (a test invocation), TestingState (solve progress
snapshot), RepoCheckpoint (git state before/after a part), SessionEnd (why the
run stopped), EvaluationRecord (offline commit evaluation), EvalEvent (lifecycle
evaluation event persisted in trace rows).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TestResult(BaseModel):
    passed: int
    failed: int
    total: int
    tests: list[dict[str, Any]] = Field(default_factory=list)


class EnvoiCall(BaseModel):
    path: str
    timestamp: str
    duration_ms: int
    status_code: int
    error: str | None = None
    result: TestResult | None = None


class TestingState(BaseModel):
    solved_paths: int
    total_paths: int
    latest_path: str | None = None
    latest_passed: int | None = None
    latest_total: int | None = None
    latest_status_code: int | None = None
    latest_error: str | None = None


class SessionEnd(BaseModel):
    reason: Literal[
        "solved", "part_limit", "timeout", "agent_error", "envoi_error"
    ]
    total_parts: int
    total_turns: int | None = None
    final_git_commit: str | None = None


class RepoCheckpoint(BaseModel):
    commit_before: str | None = None
    commit_after: str | None = None
    committed: bool = False
    changed_files: list[str] = Field(default_factory=list)
    patch: str | None = None
    stats: str | None = None
    numstat: list[dict[str, Any]] = Field(default_factory=list)


class EvalTestResult(BaseModel):
    suite: str
    test_id: str
    status: Literal[
        "passed",
        "failed",
        "error",
        "timeout",
        "skipped",
    ]
    duration_ms: int | None = None
    failure_type: str | None = None
    message: str | None = None
    source: str | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None
    truncated: bool = False


class EvalEvent(BaseModel):
    eval_id: str
    kind: Literal["commit_async", "turn_end_blocking"]
    trigger_part: int
    trigger_turn: int
    target_commit: str | None = None
    queued_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    status: Literal["queued", "running", "completed", "failed"]
    passed: int = 0
    failed: int = 0
    total: int = 0
    suite_results: dict[str, Any] = Field(default_factory=dict)
    tests: list[EvalTestResult] = Field(default_factory=list)
    error: str | None = None


class EvaluationRecord(BaseModel):
    eval_id: str
    commit: str
    part: int
    trigger_turn: int | None = None
    kind: Literal["commit_async"] = "commit_async"
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    queued_at: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    passed: int = 0
    failed: int = 0
    total: int = 0
    suite_results: dict[str, Any] = Field(default_factory=dict)
    tests: list[EvalTestResult] = Field(default_factory=list)
    error: str | None = None
    command: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None


class PartRecord(BaseModel):
    trajectory_id: str
    session_id: str
    agent: str = "codex"
    part: int | None = None
    timestamp: str
    role: Literal["assistant", "user"] = "assistant"
    part_type: str | None = None
    item_type: str | None = None
    summary: str | None = None
    duration_ms: int | None = None
    agent_model: str
    git_commit: str | None = None
    files: list[str] = Field(default_factory=list)
    content: str | None = None
    summary_word_count: int | None = None
    summary_token_estimate: int | None = None
    content_word_count: int | None = None
    content_token_estimate: int | None = None
    tool_name: str | None = None
    tool_status: str | None = None
    tool_input: Any = None
    tool_output: Any = None
    tool_error: Any = None
    tool_exit_code: int | None = None
    token_usage: dict[str, Any] | None = None
    provider_part: dict[str, Any] | None = None
    provider_item: dict[str, Any] | None = None
    provider_event: dict[str, Any] | None = None
    patch: str | None = None
    envoi_calls: list[EnvoiCall] = Field(default_factory=list)
    testing_state: TestingState | None = None
    repo_checkpoint: RepoCheckpoint | None = None
    eval_events_delta: list[EvalEvent] = Field(default_factory=list)


class TurnRecord(BaseModel):
    trajectory_id: str
    session_id: str
    agent: str = "codex"
    turn: int
    part_start: int | None = None
    part_end: int | None = None
    timestamp: str
    agent_model: str
    prompt: str | None = None
    git_commit: str | None = None
    repo_checkpoint: RepoCheckpoint | None = None
    session_ids: list[str] = Field(default_factory=list)
    session_objects: list[dict[str, Any]] = Field(default_factory=list)
    new_messages: list[dict[str, Any]] = Field(default_factory=list)
    token_usage: dict[str, Any] | None = None
    feedback_eval_id: str | None = None
    parts: list[PartRecord] = Field(default_factory=list)
    session_end: SessionEnd | None = None


class AgentTrace(BaseModel):
    trajectory_id: str
    session_id: str
    agent: str = "codex"
    agent_model: str
    started_at: str
    parts: list[PartRecord] = Field(default_factory=list)
    turns: list[TurnRecord] = Field(default_factory=list)
    evaluations: dict[str, EvaluationRecord] = Field(default_factory=dict)
    artifacts: dict[str, str | None] = Field(default_factory=dict)
    session_end: SessionEnd | None = None
