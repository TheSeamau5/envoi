"""
Local backend per-session worker process.

Spawned by runtime.py once per session. Loads the environment fresh.
Module globals are isolated from all other sessions by the OS.
Exits after /teardown is called.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse

from . import environment
from .logging import bind_log_context, log_event
from .runtime import load_environment
from .test_selection import matched_tests
from .utils import Documents, parse_params, serialize_object, working_dir

WORKER_COMPONENT_DEFAULT = "session_worker"

TestHandler = Callable[..., Awaitable[object]]


class BackendLocalArgs(argparse.Namespace):
    file: str = ""
    session_dir: str = ""
    port: int = 0


def emit_worker_log(
    event: str,
    *,
    message: str = "",
    level: str = "info",
    **fields: object,
) -> None:
    _ = log_event(
        component=WORKER_COMPONENT_DEFAULT,
        event=event,
        message=message,
        level=level,
        **fields,
    )


def bind_worker_context(session_id: str | None) -> None:
    _ = bind_log_context(
        component=WORKER_COMPONENT_DEFAULT,
        session_id=session_id,
    )


def build_worker_app(module_file: str, session_dir: str) -> FastAPI:
    bind_worker_context(os.environ.get("ENVOI_LOG_SESSION_ID"))
    emit_worker_log(
        "worker.app.start",
        module_file=module_file,
        session_dir=session_dir,
    )
    load_environment(module_file)
    app = FastAPI(title="envoi session worker")

    async def setup_handler(
        params: Annotated[str, Form()] = "{}",
    ) -> object:
        started = time.monotonic()
        emit_worker_log("worker.setup.start")
        if environment.setup_fn is None:
            emit_worker_log("worker.setup.skip", message="no setup fn")
            return {"ok": True}

        token = working_dir.set(session_dir)
        try:
            documents = Documents.from_dir(Path(session_dir))
            kwargs = environment.resolve_kwargs(
                environment.setup_fn,
                documents,
                parse_params(params),
            )
            _ = await environment.setup_fn(**kwargs)
            emit_worker_log(
                "worker.setup.complete",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return {"ok": True}
        except Exception as error:
            emit_worker_log(
                "worker.setup.failed",
                level="error",
                error=str(error),
            )
            return JSONResponse(status_code=500, content={"error": str(error)})
        finally:
            working_dir.reset(token)

    async def run_tests(path: str, params: str) -> object:
        started = time.monotonic()
        emit_worker_log("worker.test.start", path=path or "/")
        matched = matched_tests(path, environment.test_registry_items())
        if not matched:
            return JSONResponse(
                status_code=404,
                content={"error": f"No tests match: {path}"},
            )

        parsed_params = parse_params(params)

        async def run_one(
            test_path: str,
            function: TestHandler,
            path_params: dict[str, object],
        ) -> tuple[str, object]:
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
        emit_worker_log(
            "worker.test.complete",
            path=path or "/",
            matched=len(matched),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

        if len(results) == 1 and (results[0][0] == path or "{" in results[0][0]):
            return results[0][1]
        return dict(results)

    async def run_all_tests_handler(
        params: Annotated[str, Form()] = "{}",
    ) -> object:
        return await run_tests("", params)

    async def run_test_handler(
        path: str,
        params: Annotated[str, Form()] = "{}",
    ) -> object:
        return await run_tests(path, params)

    async def teardown_handler() -> object:
        emit_worker_log("worker.teardown.start")
        if environment.teardown_fn is not None:
            token = working_dir.set(session_dir)
            try:
                _ = await environment.teardown_fn()
            finally:
                working_dir.reset(token)

        emit_worker_log("worker.teardown.complete")
        _ = asyncio.get_event_loop().call_later(0.1, sys.exit, 0)
        return {"ok": True}

    _ = app.post("/setup")(setup_handler)
    _ = app.post("/test")(run_all_tests_handler)
    _ = app.post("/test/{path:path}")(run_test_handler)
    _ = app.delete("/teardown")(teardown_handler)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m envoi.backend_local")
    _ = parser.add_argument("--file", required=True)
    _ = parser.add_argument("--session-dir", required=True)
    _ = parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(namespace=BackendLocalArgs())

    emit_worker_log(
        "worker.start",
        file=args.file,
        session_dir=args.session_dir,
        port=args.port,
    )
    app = build_worker_app(args.file, args.session_dir)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error")


if __name__ == "__main__":
    main()
