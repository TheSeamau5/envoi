from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TestCase(BaseModel):
    name: str
    passed: bool
    duration_ms: int
    stderr: str | None = None


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


class SessionEnd(BaseModel):
    reason: Literal["solved", "turn_limit", "timeout", "agent_error", "envoi_error"]
    total_turns: int
    final_git_commit: str | None = None


class TurnRecord(BaseModel):
    trajectory_id: str
    session_id: str
    turn: int | None
    timestamp: str
    agent_model: str
    git_commit: str | None = None
    message_id: str | None = None
    envoi_calls: list[EnvoiCall] = Field(default_factory=list)
    session_end: SessionEnd | None = None


REQUIRED_PATHS: list[str] = [
    "basics",
    *[f"wacct/chapter_{i}" for i in range(1, 21)],
    *[f"c_testsuite/part_{i}" for i in range(1, 6)],
    *[f"torture/part_{i}" for i in range(1, 11)],
]
