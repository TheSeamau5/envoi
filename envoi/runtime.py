from __future__ import annotations

import argparse
import asyncio
import importlib.util
import re
import shutil
import socket
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

from . import environment
from .constants import DEFAULT_SESSION_TIMEOUT_SECONDS
from .utils import Documents, parse_params, serialize_object, working_dir

sessions: dict[str, dict[str, Any]] = {}


def load_environment(module_file: str) -> None:
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
        tests_module_path = getattr(tests_module, "__file__", None)
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


async def extract_upload(upload: UploadFile, destination: Path) -> None:
    archive_path = destination / "_upload.tar.gz"
    with archive_path.open("wb") as output_file:
        while chunk := await upload.read(1024 * 1024):
            output_file.write(chunk)

    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(destination, filter="data")

    archive_path.unlink(missing_ok=True)


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


def find_free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def spawn_worker(
    module_file: str,
    session_dir: str,
    port: int,
) -> asyncio.subprocess.Process:
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
            raise RuntimeError(
                "Session worker exited before startup: "
                f"{stderr_text or f'exit code {process.returncode}'}"
            )

        try:
            async with httpx.AsyncClient() as client:
                await client.get(f"{worker_url}/docs", timeout=1.0)
            return process
        except Exception:
            await asyncio.sleep(0.1)

    try:
        process.terminate()
        await process.wait()
    except Exception:
        pass

    raise RuntimeError("Timed out waiting for session worker startup")


def extract_error_message(response: httpx.Response, payload: Any | None) -> str:
    if isinstance(payload, dict):
        error_value = payload.get("error")
        if isinstance(error_value, str):
            return error_value
        if error_value is not None:
            return str(error_value)

    text = response.text.strip()
    if text:
        return text
    return response.reason_phrase or "unknown error"


def try_parse_json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except ValueError:
        return None


async def session_timeout(session_id: str, timeout_seconds: int) -> None:
    await asyncio.sleep(timeout_seconds)
    if session_id in sessions:
        await cleanup_session(session_id)


def reset_session_timeout(session_id: str) -> None:
    if session_id not in sessions:
        return

    session_state = sessions[session_id]
    timeout_task: asyncio.Task[None] = session_state["timeout_task"]
    timeout_task.cancel()

    timeout_seconds: int = session_state["timeout_seconds"]
    session_state["timeout_task"] = asyncio.create_task(
        session_timeout(session_id, timeout_seconds)
    )


async def cleanup_session(session_id: str) -> None:
    session_state = sessions.pop(session_id, None)
    if session_state is None:
        return

    timeout_task: asyncio.Task[None] = session_state["timeout_task"]
    timeout_task.cancel()

    try:
        async with httpx.AsyncClient() as client:
            await client.delete(f"{session_state['url']}/teardown", timeout=30.0)
    except Exception:
        pass

    process: asyncio.subprocess.Process | None = session_state.get("proc")
    if process is not None:
        try:
            process.terminate()
            await process.wait()
        except Exception:
            pass

    shutil.rmtree(session_state["dir"], ignore_errors=True)


def build_app(module_file: str) -> FastAPI:
    module_path = str(Path(module_file).resolve())
    load_environment(module_path)

    app = FastAPI(title="envoi runtime")

    @app.get("/schema")
    async def get_schema() -> dict[str, Any]:
        return environment.schema()

    async def run_local_tests(
        path: str,
        file: UploadFile | None,
        params: str,
    ) -> Any:
        if environment.setup_fn is not None:
            return JSONResponse(
                status_code=400,
                content={"error": "This environment requires a session."},
            )

        matched = matched_tests(path)
        if not matched:
            return JSONResponse(status_code=404, content={"error": f"No tests match: {path}"})

        temp_dir = Path(tempfile.mkdtemp(prefix="envoi-test-"))
        try:
            if file is not None and file.filename:
                await extract_upload(file, temp_dir)

            parsed_params = parse_params(params)
            documents = Documents._from_dir(temp_dir)

            async def run_one(
                test_path: str,
                function: Callable[..., Any],
                path_params: dict[str, Any],
            ) -> tuple[str, Any]:
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

            if len(results) == 1 and (results[0][0] == path or "{" in results[0][0]):
                return results[0][1]
            return dict(results)
        except Exception as error:
            return JSONResponse(status_code=500, content={"error": str(error)})
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @app.post("/test")
    async def run_all_tests(
        file: UploadFile | None = File(default=None),
        params: str = Form(default="{}"),
    ) -> Any:
        return await run_local_tests("", file, params)

    @app.post("/test/{path:path}")
    async def run_test(
        path: str,
        file: UploadFile | None = File(default=None),
        params: str = Form(default="{}"),
    ) -> Any:
        return await run_local_tests(path, file, params)

    @app.post("/session")
    async def create_session(
        file: UploadFile | None = File(default=None),
        params: str = Form(default="{}"),
        timeout: int = Form(default=DEFAULT_SESSION_TIMEOUT_SECONDS),
    ) -> Any:
        session_id = str(uuid.uuid4())
        session_dir = Path(tempfile.mkdtemp(prefix=f"envoi-session-{session_id[:8]}-"))
        process: asyncio.subprocess.Process | None = None

        try:
            if file is not None and file.filename:
                await extract_upload(file, session_dir)

            port = find_free_port()
            process = await spawn_worker(module_path, str(session_dir), port)
            worker_url = f"http://127.0.0.1:{port}"

            async with httpx.AsyncClient() as client:
                setup_response = await client.post(
                    f"{worker_url}/setup",
                    data={"params": params},
                    timeout=300.0,
                )
            setup_payload = try_parse_json(setup_response)
            if setup_response.is_error or (
                isinstance(setup_payload, dict) and "error" in setup_payload
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
            return {"session_id": session_id, "timeout": timeout}
        except Exception as error:
            if process is not None:
                try:
                    process.terminate()
                    await process.wait()
                except Exception:
                    pass
            shutil.rmtree(session_dir, ignore_errors=True)
            return JSONResponse(status_code=500, content={"error": str(error)})

    async def proxy_session_tests(session_id: str, path: str, params: str) -> Any:
        if session_id not in sessions:
            return JSONResponse(status_code=404, content={"error": "Unknown session"})

        session_state = sessions[session_id]
        reset_session_timeout(session_id)

        request_url = (
            f"{session_state['url']}/test" if not path else f"{session_state['url']}/test/{path}"
        )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    request_url,
                    data={"params": params},
                    timeout=300.0,
                )
        except Exception as error:
            return JSONResponse(
                status_code=502,
                content={"error": f"Session worker unavailable: {error}"},
            )

        payload = try_parse_json(response)
        if response.is_error:
            if isinstance(payload, dict):
                return JSONResponse(status_code=response.status_code, content=payload)
            return JSONResponse(
                status_code=response.status_code,
                content={"error": extract_error_message(response, payload)},
            )

        if payload is None:
            return response.text
        return payload

    @app.post("/session/{session_id}/test")
    async def run_all_session_tests(
        session_id: str,
        params: str = Form(default="{}"),
    ) -> Any:
        return await proxy_session_tests(session_id, "", params)

    @app.post("/session/{session_id}/test/{path:path}")
    async def run_session_test(
        session_id: str,
        path: str,
        params: str = Form(default="{}"),
    ) -> Any:
        return await proxy_session_tests(session_id, path, params)

    @app.delete("/session/{session_id}")
    async def close_session(session_id: str) -> Any:
        if session_id not in sessions:
            return JSONResponse(status_code=404, content={"error": "Unknown session"})

        await cleanup_session(session_id)
        return {"status": "closed"}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m envoi.runtime")
    parser.add_argument("--file", required=True, help="Path to the environment Python file")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app = build_app(args.file)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
