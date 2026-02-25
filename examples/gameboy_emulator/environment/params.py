"""Environment-level runner configuration for envoi-code."""

from __future__ import annotations

from typing import Literal

from envoi_code.params_api import (
    DockerPlan,
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
