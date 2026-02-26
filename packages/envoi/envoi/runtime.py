from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import shutil
import socket
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, TypedDict, cast

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

from . import environment
from .constants import DEFAULT_SESSION_TIMEOUT_SECONDS
from .logging import bind_log_context, log_event
from .test_selection import matched_tests
from .utils import (
    Documents,
    mapping_from_object,
    parse_params,
    serialize_object,
    working_dir,
)

RUNTIME_COMPONENT_DEFAULT = "runtime"

TestHandler = Callable[..., Awaitable[object]]


class RuntimeArgs(argparse.Namespace):
    file: str = ""
    host: str = "0.0.0.0"
    port: int = 8000


class SessionState(TypedDict):
    url: str
    proc: asyncio.subprocess.Process
    dir: str
    timeout_seconds: int
    timeout_task: asyncio.Task[None]


sessions: dict[str, SessionState] = {}


def emit_runtime_log(
    event: str,
    *,
    message: str = "",
    level: str = "info",
    **fields: object,
) -> None:
    _ = log_event(
        component=RUNTIME_COMPONENT_DEFAULT,
        event=event,
        message=message,
        level=level,
        **fields,
    )


def object_dict(value: object | None) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return mapping_from_object(cast(object, value))


def load_environment(module_file: str) -> None:
    emit_runtime_log(
        "environment.load.start",
        module_file=module_file,
    )
    environment.clear_environment()

    module_path = Path(module_file).resolve()
    if not module_path.exists():
        raise FileNotFoundError(f"Environment file not found: {module_path}")

    module_dir = str(module_path.parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)

    tests_dir = module_path.parent / "tests"
    if tests_dir.is_dir():
        tests_module = sys.modules.get("tests")
        tests_module_path_obj = getattr(tests_module, "__file__", None)
        tests_module_path = (
            tests_module_path_obj
            if isinstance(tests_module_path_obj, str)
            else None
        )
        is_local_tests_module = (
            tests_module_path is not None
            and Path(tests_module_path).resolve().parent == tests_dir
        )
        if tests_module is not None and not is_local_tests_module:
            for module_name in list(sys.modules):
                if module_name == "tests" or module_name.startswith("tests."):
                    del sys.modules[module_name]

    spec = importlib.util.spec_from_file_location("_envoi_environment", str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load environment from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    emit_runtime_log(
        "environment.load.complete",
        module_file=str(module_path),
        has_setup=environment.setup_fn is not None,
        test_count=len(environment.test_registry_items()),
    )


async def extract_upload(upload: UploadFile, destination: Path) -> None:
    archive_path = destination / "_upload.tar.gz"
    with archive_path.open("wb") as output_file:
        while chunk := await upload.read(1024 * 1024):
            _ = output_file.write(chunk)

    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(destination, filter="data")

    archive_path.unlink(missing_ok=True)


def find_free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        address = cast(tuple[str, int], listener.getsockname())
        return address[1]


async def spawn_worker(
    module_file: str,
    session_dir: str,
    port: int,
    session_id: str,
) -> asyncio.subprocess.Process:
    worker_log_path = f"/tmp/envoi_worker_{session_id[:8]}_{port}.jsonl"
    worker_env = dict(os.environ)
    worker_env["ENVOI_LOG_PATH"] = worker_log_path
    worker_env["ENVOI_LOG_SESSION_ID"] = session_id
    emit_runtime_log(
        "worker.spawn.start",
        session_id=session_id,
        port=port,
        log_path=worker_log_path,
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "envoi.backend_local",
        "--file",
        module_file,
        "--session-dir",
        session_dir,
        "--port",
        str(port),
        env=worker_env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    worker_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if process.returncode is not None:
            stderr_text = ""
            if process.stderr is not None:
                stderr_text = (await process.stderr.read()).decode(
                    errors="replace"
                ).strip()
            emit_runtime_log(
                "worker.spawn.failed",
                level="error",
                session_id=session_id,
                port=port,
                return_code=process.returncode,
                stderr=stderr_text,
            )
            exit_reason = stderr_text or f"exit code {process.returncode}"
            raise RuntimeError(
                f"Session worker exited before startup: {exit_reason}"
            )

        try:
            async with httpx.AsyncClient() as client:
                _ = await client.get(f"{worker_url}/docs", timeout=1.0)
            emit_runtime_log(
                "worker.spawn.ready",
                session_id=session_id,
                port=port,
                url=worker_url,
                pid=process.pid,
                log_path=worker_log_path,
            )
            return process
        except Exception:
            await asyncio.sleep(0.1)

    try:
        process.terminate()
        _ = await process.wait()
    except Exception:
        pass

    emit_runtime_log(
        "worker.spawn.timeout",
        level="error",
        session_id=session_id,
        port=port,
    )
    raise RuntimeError("Timed out waiting for session worker startup")


def extract_error_message(response: httpx.Response, payload: object | None) -> str:
    payload_dict = object_dict(payload)
    if payload_dict is not None:
        error_value = payload_dict.get("error")
        if isinstance(error_value, str):
            return error_value
        if error_value is not None:
            return str(error_value)

    text = response.text.strip()
    if text:
        return text
    return response.reason_phrase or "unknown error"


def try_parse_json(response: httpx.Response) -> object | None:
    try:
        return cast(object, response.json())
    except ValueError:
        return None


async def session_timeout(session_id: str, timeout_seconds: int) -> None:
    await asyncio.sleep(timeout_seconds)
    if session_id in sessions:
        emit_runtime_log(
            "session.timeout",
            session_id=session_id,
            timeout_seconds=timeout_seconds,
        )
        await cleanup_session(session_id)


def reset_session_timeout(session_id: str) -> None:
    if session_id not in sessions:
        return

    session_state = sessions[session_id]
    timeout_task = session_state["timeout_task"]
    _ = timeout_task.cancel()

    timeout_seconds = session_state["timeout_seconds"]
    session_state["timeout_task"] = asyncio.create_task(
        session_timeout(session_id, timeout_seconds)
    )


async def cleanup_session(session_id: str) -> None:
    session_state = sessions.pop(session_id, None)
    if session_state is None:
        return
    emit_runtime_log(
        "session.cleanup.start",
        session_id=session_id,
        worker_url=session_state["url"],
    )

    timeout_task = session_state["timeout_task"]
    _ = timeout_task.cancel()

    try:
        async with httpx.AsyncClient() as client:
            _ = await client.delete(f"{session_state['url']}/teardown", timeout=30.0)
    except Exception:
        pass

    process = session_state["proc"]
    try:
        process.terminate()
        _ = await process.wait()
    except Exception:
        pass

    shutil.rmtree(session_state["dir"], ignore_errors=True)
    emit_runtime_log(
        "session.cleanup.complete",
        session_id=session_id,
    )


def build_app(module_file: str) -> FastAPI:
    module_path = str(Path(module_file).resolve())
    _ = bind_log_context(
        component=RUNTIME_COMPONENT_DEFAULT,
        module_file=module_path,
    )
    load_environment(module_path)

    app = FastAPI(title="envoi runtime")

    async def get_schema_handler() -> dict[str, object]:
        return environment.schema()

    async def run_local_tests(
        path: str,
        file: UploadFile | None,
        params: str,
    ) -> object:
        started = time.monotonic()
        emit_runtime_log("test.local.start", path=path or "/")
        if environment.setup_fn is not None:
            return JSONResponse(
                status_code=400,
                content={"error": "This environment requires a session."},
            )

        matched = matched_tests(path, environment.test_registry_items())
        if not matched:
            return JSONResponse(
                status_code=404,
                content={"error": f"No tests match: {path}"},
            )

        temp_dir = Path(tempfile.mkdtemp(prefix="envoi-test-"))
        try:
            if file is not None and file.filename:
                await extract_upload(file, temp_dir)

            parsed_params = parse_params(params)
            documents = Documents.from_dir(temp_dir)

            async def run_one(
                test_path: str,
                function: TestHandler,
                path_params: dict[str, object],
            ) -> tuple[str, object]:
                token = working_dir.set(str(temp_dir))
                try:
                    kwargs_input = {**parsed_params, **path_params}
                    kwargs = environment.resolve_kwargs(function, documents, kwargs_input)
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
            emit_runtime_log(
                "test.local.complete",
                path=path or "/",
                matched=len(matched),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

            if len(results) == 1 and (results[0][0] == path or "{" in results[0][0]):
                return results[0][1]
            return dict(results)
        except Exception as error:
            emit_runtime_log(
                "test.local.failed",
                level="error",
                path=path or "/",
                error=str(error),
            )
            return JSONResponse(status_code=500, content={"error": str(error)})
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def run_all_tests_handler(
        file: Annotated[UploadFile | None, File()] = None,
        params: Annotated[str, Form()] = "{}",
    ) -> object:
        return await run_local_tests("", file, params)

    async def run_test_handler(
        path: str,
        file: Annotated[UploadFile | None, File()] = None,
        params: Annotated[str, Form()] = "{}",
    ) -> object:
        return await run_local_tests(path, file, params)

    async def create_session_handler(
        file: Annotated[UploadFile | None, File()] = None,
        params: Annotated[str, Form()] = "{}",
        timeout: Annotated[int, Form()] = DEFAULT_SESSION_TIMEOUT_SECONDS,
    ) -> object:
        session_id = str(uuid.uuid4())
        session_dir = Path(tempfile.mkdtemp(prefix=f"envoi-session-{session_id[:8]}-"))
        process: asyncio.subprocess.Process | None = None

        try:
            emit_runtime_log(
                "session.create.start",
                session_id=session_id,
                timeout_seconds=timeout,
            )
            if file is not None and file.filename:
                await extract_upload(file, session_dir)

            port = find_free_port()
            process = await spawn_worker(
                module_path,
                str(session_dir),
                port,
                session_id,
            )
            worker_url = f"http://127.0.0.1:{port}"
            setup_timeout = max(300.0, float(timeout) + 30.0)
            started = time.monotonic()

            async with httpx.AsyncClient() as client:
                setup_response = await client.post(
                    f"{worker_url}/setup",
                    data={"params": params},
                    timeout=setup_timeout,
                )
            setup_payload = try_parse_json(setup_response)
            setup_payload_dict = object_dict(setup_payload)
            if setup_response.is_error or (
                setup_payload_dict is not None and "error" in setup_payload_dict
            ):
                raise RuntimeError(extract_error_message(setup_response, setup_payload))

            timeout_task = asyncio.create_task(session_timeout(session_id, timeout))
            sessions[session_id] = {
                "url": worker_url,
                "proc": process,
                "dir": str(session_dir),
                "timeout_seconds": timeout,
                "timeout_task": timeout_task,
            }
            emit_runtime_log(
                "session.create.complete",
                session_id=session_id,
                timeout_seconds=timeout,
                setup_duration_ms=int((time.monotonic() - started) * 1000),
                worker_url=worker_url,
                worker_pid=process.pid,
            )
            return {"session_id": session_id, "timeout": timeout}
        except Exception as error:
            emit_runtime_log(
                "session.create.failed",
                level="error",
                session_id=session_id,
                error=str(error),
            )
            if process is not None:
                try:
                    process.terminate()
                    _ = await process.wait()
                except Exception:
                    pass
            shutil.rmtree(session_dir, ignore_errors=True)
            return JSONResponse(status_code=500, content={"error": str(error)})

    async def proxy_session_tests(session_id: str, path: str, params: str) -> object:
        if session_id not in sessions:
            return JSONResponse(status_code=404, content={"error": "Unknown session"})

        session_state = sessions[session_id]
        reset_session_timeout(session_id)

        request_url = (
            f"{session_state['url']}/test"
            if not path
            else f"{session_state['url']}/test/{path}"
        )
        request_timeout = max(
            30.0,
            float(session_state.get("timeout_seconds") or DEFAULT_SESSION_TIMEOUT_SECONDS)
            + 30.0,
        )
        started = time.monotonic()
        emit_runtime_log(
            "session.test.start",
            session_id=session_id,
            path=path or "/",
            timeout_seconds=request_timeout,
            request_url=request_url,
        )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    request_url,
                    data={"params": params},
                    timeout=request_timeout,
                )
        except Exception as error:
            emit_runtime_log(
                "session.test.unavailable",
                level="error",
                session_id=session_id,
                path=path or "/",
                error=str(error),
            )
            return JSONResponse(
                status_code=502,
                content={"error": f"Session worker unavailable: {error}"},
            )

        payload = try_parse_json(response)
        emit_runtime_log(
            "session.test.response",
            session_id=session_id,
            path=path or "/",
            status_code=response.status_code,
            duration_ms=int((time.monotonic() - started) * 1000),
            has_payload=isinstance(payload, dict | list),
        )
        if response.is_error:
            payload_dict = object_dict(payload)
            if payload_dict is not None:
                return JSONResponse(status_code=response.status_code, content=payload_dict)
            return JSONResponse(
                status_code=response.status_code,
                content={"error": extract_error_message(response, payload)},
            )

        if payload is None:
            return response.text
        return payload

    async def run_all_session_tests_handler(
        session_id: str,
        params: Annotated[str, Form()] = "{}",
    ) -> object:
        return await proxy_session_tests(session_id, "", params)

    async def run_session_test_handler(
        session_id: str,
        path: str,
        params: Annotated[str, Form()] = "{}",
    ) -> object:
        return await proxy_session_tests(session_id, path, params)

    async def close_session_handler(session_id: str) -> object:
        if session_id not in sessions:
            return JSONResponse(status_code=404, content={"error": "Unknown session"})

        await cleanup_session(session_id)
        return {"status": "closed"}

    _ = app.get("/schema")(get_schema_handler)
    _ = app.post("/test")(run_all_tests_handler)
    _ = app.post("/test/{path:path}")(run_test_handler)
    _ = app.post("/session")(create_session_handler)
    _ = app.post("/session/{session_id}/test")(run_all_session_tests_handler)
    _ = app.post("/session/{session_id}/test/{path:path}")(run_session_test_handler)
    _ = app.delete("/session/{session_id}")(close_session_handler)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m envoi.runtime")
    _ = parser.add_argument("--file", required=True, help="Path to the environment Python file")
    _ = parser.add_argument("--host", default="0.0.0.0")
    _ = parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(namespace=RuntimeArgs())

    emit_runtime_log(
        "runtime.start",
        file=args.file,
        host=args.host,
        port=args.port,
    )
    app = build_app(args.file)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
