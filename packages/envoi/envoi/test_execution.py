from __future__ import annotations

import asyncio
import time
import traceback
from pathlib import Path

from . import environment
from .test_selection import matched_tests
from .utils import Documents, emit_environment_log, serialize_object, working_dir


def fold_test_results(path: str, results: list[tuple[str, object]]) -> object:
    if len(results) == 1:
        first_path, first_result = results[0]
        if first_path == path or "{" in first_path:
            return first_result
    return dict(results)


async def execute_matched_tests(
    *,
    path: str,
    registry_items: list[tuple[str, environment.TestFunction]],
    params: dict[str, object],
    workdir: str | Path,
    documents: Documents | None,
) -> tuple[int, object] | None:
    matched = matched_tests(path, registry_items)
    if not matched:
        return None

    workdir_value = str(workdir)
    started = time.monotonic()
    emit_environment_log(
        "matched_tests.start",
        path=path or "/",
        matched=len(matched),
        matched_paths=sorted(matched.keys())[:100],
        workdir=workdir_value,
    )

    async def run_case(
        test_path: str,
        function: environment.TestFunction,
        path_params: dict[str, object],
    ) -> tuple[str, object]:
        token = working_dir.set(workdir_value)
        test_started = time.monotonic()
        try:
            emit_environment_log(
                "matched_test.start",
                path=test_path,
                params=path_params,
            )
            kwargs_input = {**params, **path_params}
            kwargs = environment.resolve_kwargs(function, documents, kwargs_input)
            result = await function(**kwargs)
            emit_environment_log(
                "matched_test.complete",
                path=test_path,
                duration_ms=int((time.monotonic() - test_started) * 1000),
                result_type=type(result).__name__,
            )
            return test_path, serialize_object(result)
        except Exception as error:
            emit_environment_log(
                "matched_test.failed",
                level="error",
                path=test_path,
                duration_ms=int((time.monotonic() - test_started) * 1000),
                error=str(error),
                traceback=traceback.format_exc(),
            )
            return test_path, {"error": str(error)}
        finally:
            working_dir.reset(token)

    results = await asyncio.gather(
        *[
            run_case(test_path, function, path_params)
            for test_path, (function, path_params) in matched.items()
        ]
    )

    emit_environment_log(
        "matched_tests.complete",
        path=path or "/",
        matched=len(matched),
        duration_ms=int((time.monotonic() - started) * 1000),
    )

    return len(matched), fold_test_results(path, results)
