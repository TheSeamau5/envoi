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

    impl_lang: Literal["rust", "zig", "c", "typescript"] = "rust"
    lang: Literal["en", "es", "fr", "zh"] = "en"
    milestone: Literal["M0", "M1", "M2"] = "M0"


def params() -> dict[str, object]:
    return {
        "advisor_model": "@anthropic/claude-opus-4.6",
        "advisor_model_thinking_level": "high",
        "advisor_max_output_tokens": 128000,
        "failed_tests_feedback_limit": 50,
    }


async def resolve_params(context: ParamsResolveContext) -> ResolvedParams:
    variant = VariantParams.model_validate(context.raw_params)

    if variant.impl_lang != "rust":
        raise ValueError(
            "gameboy_emulator currently supports impl_lang=rust only; "
            f"got {variant.impl_lang}"
        )

    return ResolvedParams(
        resolved_param_values=variant.model_dump(mode="json"),
        docker=DockerPlan(
            dockerfile_path="Dockerfile",
            build_args={
                "IMPL_LANG": variant.impl_lang,
                "TASK_LANG": variant.lang,
                "MILESTONE": variant.milestone,
            },
            features={
                "impl_lang_rust": True,
            },
            cache_key_inputs={
                "impl_lang": variant.impl_lang,
                "milestone": variant.milestone,
            },
        ),
        sandbox_requirements=SandboxRequirements(
            min_cpu=4.0,
            min_memory_mb=12288,
            requires_network=False,
            notes=["Large fixture/reference image; prefer image cache reuse"],
        ),
        runtime_env={
            "ENVOI_IMPL_LANG": variant.impl_lang,
            "ENVOI_TASK_LANG": variant.lang,
            "ENVOI_MILESTONE": variant.milestone,
        },
        metadata={
            "environment_family": "gameboy_emulator",
            "resolver_version": 1,
        },
    )


async def resolve_param_space(
    context: ParamSpaceResolveContext,
) -> ParamSpace:
    return ParamSpace(
        dimensions=[
            ParamSpaceDimension(
                key="impl_lang",
                label="Implementation Language",
                description="Gameboy emulator implementation language",
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
                description="Challenge milestone for the task",
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
