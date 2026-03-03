from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from envoi_code.params_api import ParamsResolveContext, ResolvedParams
from envoi_code.task_api import ResolvedTask, TaskResolveContext


def load_python_file_module(
    module_name: str,
    file_path: Path,
) -> ModuleType | None:
    if not file_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if previous_module is not None:
            sys.modules[module_name] = previous_module
        else:
            sys.modules.pop(module_name, None)
        raise
    return module


async def load_task(
    task_dir: Path,
    *,
    environment_dir: Path,
    raw_params: dict[str, Any],
    selected_test_paths: list[str],
    agent: str,
    model: str | None,
) -> ResolvedTask:
    """Load a task definition.

    Canonical path: task_dir/task.py with async resolve_task(context).
    Fallback: task_dir/prompt.md (static prompt only).
    """
    task_module = load_python_file_module("envoi_task", task_dir / "task.py")
    if task_module is not None:
        resolve_task = getattr(task_module, "resolve_task", None)
        if resolve_task is None:
            raise TypeError("task.py must define async resolve_task(context)")
        if not inspect.iscoroutinefunction(resolve_task):
            raise TypeError("task.py resolve_task(context) must be async")
        context = TaskResolveContext(
            task_dir=str(task_dir),
            environment_dir=str(environment_dir),
            raw_params=raw_params,
            selected_test_paths=selected_test_paths,
            agent=agent,
            model=model,
        )
        value = await resolve_task(context)
        return ResolvedTask.model_validate(value)

    prompt_file = task_dir / "prompt.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            "No task.py resolver or prompt.md found in "
            f"{task_dir}"
        )
    return ResolvedTask(
        prompt=prompt_file.read_text().strip(),
        task_params={},
        metadata={},
    )


def load_environment_params_module(environment_dir: Path) -> ModuleType | None:
    return load_python_file_module("envoi_environment_params", environment_dir / "params.py")


async def load_environment_params_from_module(
    module: ModuleType | None,
) -> dict[str, Any]:
    """Load optional environment runner config from environment/params.py."""
    if module is None:
        return {}

    params_fn = getattr(module, "params", None)
    if params_fn is not None:
        value = (
            await params_fn()
            if inspect.iscoroutinefunction(params_fn)
            else params_fn()
        )
        if isinstance(value, dict):
            return value
        raise TypeError("environment params() must return a dict")

    params_const = getattr(module, "PARAMS", None)
    if isinstance(params_const, dict):
        return params_const

    return {}


async def load_environment_resolved_params(
    module: ModuleType | None,
    *,
    context: ParamsResolveContext,
) -> ResolvedParams | None:
    if module is None:
        return None
    resolve_params = getattr(module, "resolve_params", None)
    if resolve_params is None:
        return None
    if not inspect.iscoroutinefunction(resolve_params):
        raise TypeError("environment resolve_params(context) must be async")
    value = await resolve_params(context)
    return ResolvedParams.model_validate(value)


def merge_resource_request(
    *,
    resource_name: str,
    requested: float | int | None,
    minimum: float | int | None,
) -> float | int | None:
    if minimum is None:
        return requested
    if requested is None:
        return minimum
    if requested < minimum:
        raise ValueError(
            f"Requested {resource_name} ({requested}) is below "
            f"environment minimum ({minimum})"
        )
    return requested
