"""
Local backend per-session worker process.

Spawned by runtime.py once per session. Loads the environment fresh.
Module globals are isolated from all other sessions by the OS.
Exits after /teardown is called.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse

from . import environment
from .runtime import load_environment
from .utils import Documents, parse_params, serialize_object, working_dir


def coerce_path_value(value: str) -> Any:
    try:
        return int(value)
    except ValueError:
        return value


def extract_template_params(template_path: str, request_path: str) -> dict[str, Any] | None:
    if "{" not in template_path or "}" not in template_path:
        return None
    if "{" in request_path or "}" in request_path:
        return None

    pattern_parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template_path):
        pattern_parts.append(re.escape(template_path[cursor:match.start()]))
        parameter_name = match.group(1)
        pattern_parts.append(f"(?P<{parameter_name}>[^/]+)")
        cursor = match.end()
    pattern_parts.append(re.escape(template_path[cursor:]))

    pattern = "^" + "".join(pattern_parts) + "$"
    path_match = re.match(pattern, request_path)
    if path_match is None:
        return None

    return {key: coerce_path_value(value) for key, value in path_match.groupdict().items()}


def matched_tests(path: str) -> dict[str, tuple[Callable[..., Any], dict[str, Any]]]:
    matched: dict[str, tuple[Callable[..., Any], dict[str, Any]]] = {}
    for test_path, function in environment._test_registry.items():
        is_template = "{" in test_path and "}" in test_path

        if is_template:
            if not path:
                continue
            template_params = extract_template_params(test_path, path)
            if template_params is not None:
                matched[test_path] = (function, template_params)
            continue

        if not path:
            matched[test_path] = (function, {})
            continue

        if test_path == path or test_path.startswith(path + "/"):
            matched[test_path] = (function, {})

    return matched


def build_worker_app(module_file: str, session_dir: str) -> FastAPI:
    load_environment(module_file)
    app = FastAPI(title="envoi session worker")

    @app.post("/setup")
    async def setup(params: str = Form(default="{}")) -> Any:
        if environment.setup_fn is None:
            return {"ok": True}

        token = working_dir.set(session_dir)
        try:
            documents = Documents._from_dir(Path(session_dir))
            kwargs = environment.resolve_kwargs(
                environment.setup_fn,
                documents,
                parse_params(params),
            )
            await environment.setup_fn(**kwargs)
            return {"ok": True}
        except Exception as error:
            return JSONResponse(status_code=500, content={"error": str(error)})
        finally:
            working_dir.reset(token)

    async def run_tests(path: str, params: str) -> Any:
        matched = matched_tests(path)
        if not matched:
            return JSONResponse(status_code=404, content={"error": f"No tests match: {path}"})

        parsed_params = parse_params(params)

        async def run_one(
            test_path: str,
            function: Callable[..., Any],
            path_params: dict[str, Any],
        ) -> tuple[str, Any]:
            token = working_dir.set(session_dir)
            try:
                kwargs_input = {**parsed_params, **path_params}
                kwargs = environment.resolve_kwargs(function, None, kwargs_input)
                result = await function(**kwargs)
                return test_path, serialize_object(result)
            except Exception as error:
                return test_path, {"error": str(error)}
            finally:
                working_dir.reset(token)

        results = await asyncio.gather(
            *[
                run_one(test_path, function, path_params)
                for test_path, (function, path_params) in matched.items()
            ]
        )

        if len(results) == 1 and (results[0][0] == path or "{" in results[0][0]):
            return results[0][1]
        return dict(results)

    @app.post("/test")
    async def run_all_tests(params: str = Form(default="{}")) -> Any:
        return await run_tests("", params)

    @app.post("/test/{path:path}")
    async def run_test(path: str, params: str = Form(default="{}")) -> Any:
        return await run_tests(path, params)

    @app.delete("/teardown")
    async def teardown() -> Any:
        if environment.teardown_fn is not None:
            token = working_dir.set(session_dir)
            try:
                await environment.teardown_fn()
            finally:
                working_dir.reset(token)

        asyncio.get_event_loop().call_later(0.1, sys.exit, 0)
        return {"ok": True}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m envoi.backend_local")
    parser.add_argument("--file", required=True)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    app = build_worker_app(args.file, args.session_dir)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error")


if __name__ == "__main__":
    main()
