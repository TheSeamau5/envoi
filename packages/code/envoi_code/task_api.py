from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskResolveContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_dir: str
    environment_dir: str
    raw_params: dict[str, Any] = Field(default_factory=dict)
    selected_test_paths: list[str] = Field(default_factory=list)
    agent: str
    model: str | None = None


class ResolvedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    task_params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

