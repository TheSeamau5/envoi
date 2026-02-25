from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SandboxRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_cpu: float | None = None
    min_memory_mb: int | None = None
    min_disk_gb: int | None = None
    requires_network: bool | None = None
    notes: list[str] = Field(default_factory=list)


class DockerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dockerfile_path: str = "Dockerfile"
    build_args: dict[str, str] = Field(default_factory=dict)
    features: dict[str, bool] = Field(default_factory=dict)
    cache_key_inputs: dict[str, str] = Field(default_factory=dict)


class ParamsResolveContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_dir: str
    task_dir: str | None = None
    raw_params: dict[str, Any] = Field(default_factory=dict)
    selected_test_paths: list[str] = Field(default_factory=list)
    sandbox_provider: str
    user_limits: dict[str, Any] = Field(default_factory=dict)


class ResolvedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_param_values: dict[str, Any] = Field(default_factory=dict)
    docker: DockerPlan | None = None
    sandbox_requirements: SandboxRequirements = Field(
        default_factory=SandboxRequirements,
    )
    task_overrides: dict[str, Any] = Field(default_factory=dict)
    runtime_env: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

