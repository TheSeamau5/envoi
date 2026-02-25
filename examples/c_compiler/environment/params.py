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
