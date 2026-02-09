from __future__ import annotations

import argparse
import asyncio
import importlib.util
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from . import environment
from .utils import Documents, parse_params, serialize_object, working_dir

DEFAULT_SESSION_TIMEOUT_SECONDS = 300

sessions: dict[str, dict[str, Any]] = {}


def load_environment(module_file: str) -> None:
    environment.clear_environment()

    module_path = Path(module_file).resolve()
    if not module_path.exists():
        raise FileNotFoundError(f"Environment file not found: {module_path}")

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

async def run_teardown(session_dir: str) -> None:
    if environment.state.teardown_fn is None:
        return

    token = working_dir.set(session_dir)
    try:
        await environment.state.teardown_fn()
    finally:
        working_dir.reset(token)


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

    session_dir = session_state["dir"]
    try:
        await run_teardown(session_dir)
    finally:
        shutil.rmtree(session_dir, ignore_errors=True)


def build_app(module_file: str) -> FastAPI:
    load_environment(module_file)
    env = environment.state

    app = FastAPI(title="envoi runtime")

    @app.get("/schema")
    async def get_schema() -> dict[str, Any]:
        return env.schema()

    @app.post("/test/{test_name}")
    async def run_test(
        test_name: str,
        file: UploadFile | None = File(default=None),
        params: str = Form(default="{}"),
    ) -> Any:
        if env.setup_fn is not None:
            return JSONResponse(
                status_code=400,
                content={"error": "This environment requires a session."},
            )

        if test_name not in env.tests:
            return JSONResponse(
                status_code=404,
                content={"error": f"Unknown test: {test_name}"},
            )

        temp_dir = Path(tempfile.mkdtemp(prefix="envoi-test-"))
        try:
            if file is not None and file.filename:
                await extract_upload(file, temp_dir)

            documents = Documents._from_dir(temp_dir)
            parsed_params = parse_params(params)

            token = working_dir.set(str(temp_dir))
            try:
                test_function = env.tests[test_name]
                function_kwargs = environment.resolve_kwargs(
                    test_function, documents, parsed_params
                )
                result = await test_function(**function_kwargs)
            finally:
                working_dir.reset(token)

            return serialize_object(result)
        except Exception as error:
            return JSONResponse(status_code=500, content={"error": str(error)})
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @app.post("/session")
    async def create_session(
        file: UploadFile | None = File(default=None),
        params: str = Form(default="{}"),
        timeout: int = Form(default=DEFAULT_SESSION_TIMEOUT_SECONDS),
    ) -> Any:
        session_id = str(uuid.uuid4())
        session_dir = Path(tempfile.mkdtemp(prefix=f"envoi-session-{session_id[:8]}-"))

        try:
            if file is not None and file.filename:
                await extract_upload(file, session_dir)

            documents = Documents._from_dir(session_dir)
            parsed_params = parse_params(params)

            if env.setup_fn is not None:
                token = working_dir.set(str(session_dir))
                try:
                    setup_kwargs = environment.resolve_kwargs(
                        env.setup_fn, documents, parsed_params
                    )
                    await env.setup_fn(**setup_kwargs)
                finally:
                    working_dir.reset(token)

            timeout_task = asyncio.create_task(session_timeout(session_id, timeout))
            sessions[session_id] = {
                "dir": str(session_dir),
                "timeout_seconds": timeout,
                "timeout_task": timeout_task,
            }

            return {"session_id": session_id, "timeout": timeout}
        except Exception as error:
            shutil.rmtree(session_dir, ignore_errors=True)
            return JSONResponse(status_code=500, content={"error": str(error)})

    @app.post("/session/{session_id}/test/{test_name}")
    async def run_session_test(
        session_id: str,
        test_name: str,
        params: str = Form(default="{}"),
    ) -> Any:
        if session_id not in sessions:
            return JSONResponse(status_code=404, content={"error": "Unknown session"})

        if test_name not in env.tests:
            return JSONResponse(
                status_code=404,
                content={"error": f"Unknown test: {test_name}"},
            )

        session_state = sessions[session_id]
        reset_session_timeout(session_id)

        token = working_dir.set(session_state["dir"])
        try:
            test_function = env.tests[test_name]
            parsed_params = parse_params(params)
            function_kwargs = environment.resolve_kwargs(test_function, None, parsed_params)
            result = await test_function(**function_kwargs)
            return serialize_object(result)
        except Exception as error:
            return JSONResponse(status_code=500, content={"error": str(error)})
        finally:
            working_dir.reset(token)

    @app.delete("/session/{session_id}")
    async def close_session(session_id: str) -> Any:
        if session_id not in sessions:
            return JSONResponse(status_code=404, content={"error": "Unknown session"})

        await cleanup_session(session_id)
        return {"status": "closed"}

    @app.websocket("/session/{session_id}/stream")
    async def stream_session(websocket: WebSocket, session_id: str) -> None:
        if session_id not in sessions:
            await websocket.close(code=4004)
            return

        await websocket.accept()

        try:
            await asyncio.wait_for(websocket.receive_json(), timeout=10)
        except (asyncio.TimeoutError, WebSocketDisconnect):
            await websocket.close()
            return

        session_state = sessions[session_id]
        reset_session_timeout(session_id)

        stop_stream = asyncio.Event()

        async def observe_loop() -> None:
            if not env.observables:
                await stop_stream.wait()
                return

            while not stop_stream.is_set():
                for observe_name, observe_function in env.observables.items():
                    token = working_dir.set(session_state["dir"])
                    try:
                        observe_result = await observe_function()
                        await websocket.send_json(
                            {
                                "type": "observe",
                                "name": observe_name,
                                "data": serialize_object(observe_result),
                            }
                        )
                    except WebSocketDisconnect:
                        stop_stream.set()
                        return
                    except Exception as error:
                        try:
                            await websocket.send_json(
                                {"type": "error", "message": str(error)}
                            )
                        except WebSocketDisconnect:
                            pass
                        stop_stream.set()
                        return
                    finally:
                        working_dir.reset(token)

                await asyncio.sleep(0.1)

        async def action_loop() -> None:
            while not stop_stream.is_set():
                try:
                    message = await websocket.receive_json()
                except WebSocketDisconnect:
                    stop_stream.set()
                    return

                if message.get("type") != "action":
                    continue

                action_name = message.get("name")
                if (
                    not isinstance(action_name, str)
                    or action_name not in env.actions
                ):
                    await websocket.send_json(
                        {"type": "error", "message": "Unknown action"}
                    )
                    continue

                reset_session_timeout(session_id)

                token = working_dir.set(session_state["dir"])
                try:
                    action_payload = message.get("data", {})
                    if not isinstance(action_payload, dict):
                        action_payload = {}

                    action_kwargs = environment.resolve_action_kwargs(
                        action_name,
                        action_payload,
                    )
                    action_result = await env.actions[action_name](**action_kwargs)
                    if action_result is not None:
                        await websocket.send_json(
                            {
                                "type": "action_result",
                                "name": action_name,
                                "data": serialize_object(action_result),
                            }
                        )
                except Exception as error:
                    await websocket.send_json(
                        {"type": "error", "message": str(error)}
                    )
                finally:
                    working_dir.reset(token)

        try:
            await asyncio.gather(observe_loop(), action_loop())
        finally:
            stop_stream.set()
            try:
                await websocket.close()
            except Exception:
                pass

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
