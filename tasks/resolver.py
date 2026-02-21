"""Dynamic task resolution."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EnvConfig(BaseModel):
    """Resolved configuration for a task + environment pair."""

    task_name: str
    environment: str
    prompt: str
    continue_prompt: str
    setup_script: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    environment_dir: Path
    required_test_paths: list[str] = Field(default_factory=list)
    suite_paths: list[str] = Field(default_factory=list)
    heavy_test_roots: dict[str, str] = Field(default_factory=dict)


def resolve_task(
    task_name: str,
    *,
    lang: str = "en",
    params_overrides: dict[str, str] | None = None,
) -> EnvConfig:
    """Resolve a task by name into a fully populated ``EnvConfig``.

    Imports ``tasks.<task_name>.task`` and reads its exports.
    """
    mod = importlib.import_module(f"tasks.{task_name}.task")

    # Prompt: prefer load_prompt(), fall back to PROMPT constant.
    if hasattr(mod, "load_prompt"):
        prompt = mod.load_prompt(lang=lang)
    elif hasattr(mod, "PROMPT"):
        prompt = mod.PROMPT
    else:
        raise AttributeError(
            f"tasks.{task_name}.task must export load_prompt() or PROMPT"
        )

    environment = getattr(mod, "ENVIRONMENT", task_name)
    continue_prompt = getattr(mod, "CONTINUE_PROMPT", "Continue.")
    setup_script = getattr(mod, "SETUP_SH", "")
    heavy_test_roots = getattr(mod, "HEAVY_TEST_ROOTS", {})
    required_test_paths = list(
        getattr(mod, "REQUIRED_TEST_PATHS", ())
    )
    suite_paths = list(getattr(mod, "SUITE_PATHS", ()))
    environment_dir = (
        Path(__file__).resolve().parent.parent
        / "environments"
        / environment
    )

    params: dict[str, Any] = {"lang": lang}
    if params_overrides:
        params.update(params_overrides)

    return EnvConfig(
        task_name=task_name,
        environment=environment,
        prompt=prompt,
        continue_prompt=continue_prompt,
        setup_script=setup_script,
        params=params,
        environment_dir=environment_dir,
        heavy_test_roots=heavy_test_roots,
        required_test_paths=required_test_paths,
        suite_paths=suite_paths,
    )
