from __future__ import annotations

from typing import Any, Literal

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


class ParamSpaceOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    label: str | None = None
    weight: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParamSpaceDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str | None = None
    description: str | None = None
    kind: Literal["enum", "int", "float", "bool", "string"] = "enum"
    required: bool = True
    default_value: Any = None
    options: list[ParamSpaceOption] = Field(default_factory=list)
    min_int: int | None = None
    max_int: int | None = None
    min_float: float | None = None
    max_float: float | None = None
    string_pattern: str | None = None
    allow_manual: bool = True
    allow_grid: bool = True
    allow_random: bool = True


class ParamSpace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimensions: list[ParamSpaceDimension] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ParamSpaceResolveContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_dir: str
    task_dir: str | None = None
    selected_test_paths: list[str] = Field(default_factory=list)


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
    param_space: ParamSpace | None = None
