from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType

from envoi_code.params_api import ParamSpace, ParamSpaceResolveContext


def load_python_module(
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


async def resolve_environment_param_space(
    *,
    environment_dir: Path,
    task_dir: Path | None = None,
    selected_test_paths: list[str] | None = None,
) -> ParamSpace:
    params_module = load_python_module(
        "envoi_environment_param_space",
        environment_dir / "params.py",
    )
    if params_module is None:
        return ParamSpace()

    resolver = getattr(params_module, "resolve_param_space", None)
    if resolver is None:
        static_value = getattr(params_module, "PARAM_SPACE", None)
        if static_value is None:
            return ParamSpace()
        return ParamSpace.model_validate(static_value)
    if not inspect.iscoroutinefunction(resolver):
        raise TypeError("environment resolve_param_space(context) must be async")

    context = ParamSpaceResolveContext(
        environment_dir=str(environment_dir),
        task_dir=str(task_dir) if task_dir is not None else None,
        selected_test_paths=selected_test_paths or [],
    )
    resolved_value = await resolver(context)
    return ParamSpace.model_validate(resolved_value)
