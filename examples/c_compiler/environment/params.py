"""Environment-level runner configuration for envoi-code."""

from __future__ import annotations

from typing import Literal

from envoi_code.params_api import (
    DockerPlan,
    ParamSpace,
    ParamSpaceDimension,
    ParamSpaceOption,
    ParamSpaceResolveContext,
    ParamsResolveContext,
    ResolvedParams,
    SandboxRequirements,
)
from pydantic import BaseModel, ConfigDict


class VariantParams(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target: Literal["x86_64-linux", "aarch64-linux", "wasm32-wasi"] = "x86_64-linux"
    impl_lang: Literal["rust", "zig", "c", "typescript"] = "rust"
    lang: Literal["en", "es", "fr", "zh"] = "en"
    milestone: Literal["M0", "M1", "M2"] = "M0"


def params() -> dict[str, object]:
    return {
        "advisor_model": "@anthropic/claude-opus-4.6",
        "advisor_model_thinking_level": "high",
        "diagnostics_suite_priority": [
            "basics",
            "c_testsuite",
            "wacct",
            "torture",
        ],
        "failed_tests_feedback_limit": 50,
    }


async def resolve_params(context: ParamsResolveContext) -> ResolvedParams:
    variant = VariantParams.model_validate(context.raw_params)

    if variant.target != "x86_64-linux":
        raise ValueError(
            "c_compiler currently supports target=x86_64-linux only; "
            f"got {variant.target}"
        )
    if variant.impl_lang != "rust":
        raise ValueError(
            "c_compiler currently supports impl_lang=rust only; "
            f"got {variant.impl_lang}"
        )

    return ResolvedParams(
        resolved_param_values=variant.model_dump(mode="json"),
        docker=DockerPlan(
            dockerfile_path="Dockerfile",
            build_args={
                "TARGET": variant.target,
                "IMPL_LANG": variant.impl_lang,
                "TASK_LANG": variant.lang,
                "MILESTONE": variant.milestone,
            },
            features={
                "target_x86_64_linux": True,
                "impl_lang_rust": True,
            },
            cache_key_inputs={
                "target": variant.target,
                "impl_lang": variant.impl_lang,
                "milestone": variant.milestone,
            },
        ),
        sandbox_requirements=SandboxRequirements(
            min_cpu=2.0,
            min_memory_mb=8192,
            requires_network=False,
            notes=["Heavy fixture image build; prefer image cache reuse"],
        ),
        task_overrides={},
        runtime_env={
            "ENVOI_TARGET": variant.target,
            "ENVOI_IMPL_LANG": variant.impl_lang,
            "ENVOI_TASK_LANG": variant.lang,
            "ENVOI_MILESTONE": variant.milestone,
        },
        metadata={
            "environment_family": "c_compiler",
            "resolver_version": 1,
        },
    )


async def resolve_param_space(
    context: ParamSpaceResolveContext,
) -> ParamSpace:
    return ParamSpace(
        dimensions=[
            ParamSpaceDimension(
                key="target",
                label="Compilation Target",
                description="Target architecture for generated assembly/binary",
                kind="enum",
                required=True,
                default_value="x86_64-linux",
                options=[
                    ParamSpaceOption(value="x86_64-linux", label="x86_64 Linux"),
                    ParamSpaceOption(value="aarch64-linux", label="ARM64 Linux"),
                    ParamSpaceOption(value="wasm32-wasi", label="WASM32 WASI"),
                ],
            ),
            ParamSpaceDimension(
                key="impl_lang",
                label="Implementation Language",
                description="Compiler implementation language",
                kind="enum",
                required=True,
                default_value="rust",
                options=[
                    ParamSpaceOption(value="rust", label="Rust"),
                    ParamSpaceOption(value="zig", label="Zig"),
                    ParamSpaceOption(value="c", label="C"),
                    ParamSpaceOption(value="typescript", label="TypeScript"),
                ],
            ),
            ParamSpaceDimension(
                key="lang",
                label="Prompt Language",
                description="Task prompt language",
                kind="enum",
                required=True,
                default_value="en",
                options=[
                    ParamSpaceOption(value="en", label="English"),
                    ParamSpaceOption(value="es", label="Spanish"),
                    ParamSpaceOption(value="fr", label="French"),
                    ParamSpaceOption(value="zh", label="Chinese"),
                ],
            ),
            ParamSpaceDimension(
                key="milestone",
                label="Milestone",
                description="Difficulty slice for the task",
                kind="enum",
                required=True,
                default_value="M0",
                options=[
                    ParamSpaceOption(value="M0"),
                    ParamSpaceOption(value="M1"),
                    ParamSpaceOption(value="M2"),
                ],
            ),
        ],
        notes=[
            f"Environment dir: {context.environment_dir}",
            "Use resolve_params for final validation before execution",
        ],
    )
