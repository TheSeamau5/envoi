"""Modal deployment wrapper for envoi-trace.

This module exists solely to deploy runner.py as a Modal remote function.
It owns the Modal app, the function image (which packages runner's own code
for remote execution), and the @app.function / @app.local_entrypoint
decorators. runner.py itself has ZERO knowledge of Modal.

Usage:
    modal run sandbox/modal/deploy.py
    modal run sandbox/modal/deploy.py --agent codex --max-parts 500
"""

from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path
from typing import Any

import modal

from envoi_code.orchestrator import (
    AGENT_BACKENDS,
    DEFAULT_AGENT,
    MESSAGE_TIMEOUT_SECONDS,
    RESUME_FROM_S3,
    run_trajectory,
)
from envoi_code.sandbox.modal.backend import ModalSandbox
from envoi_code.utils.helpers import tprint

print = tprint

ROOT = Path(__file__).parent.parent.parent
WORKSPACE_ROOT = ROOT.parent.parent.parent

app = modal.App("envoi-trace")

function_image = (
    modal.Image.debian_slim()
    .pip_install(
        "boto3",
        "pydantic",
        "pyarrow",
        "python-dotenv",
        "anthropic[aiohttp]",
    )
    .add_local_dir(
        ROOT, remote_path="/root/envoi_code",
    )
    .add_local_dir(
        WORKSPACE_ROOT / "packages" / "envoi" / "envoi",
        remote_path="/root/envoi",
    )
    .add_local_dir(
        WORKSPACE_ROOT / "examples", remote_path="/root/examples",
    )
)


def parse_raw_params_json(raw_params_json: str | None) -> dict[str, Any] | None:
    if raw_params_json is None or not raw_params_json.strip():
        return None
    value = json.loads(raw_params_json)
    if not isinstance(value, dict):
        raise ValueError("--raw-params-json must decode to a JSON object")
    return value


@app.function(
    timeout=14400,
    secrets=[modal.Secret.from_dotenv()],
    image=function_image,
)
async def modal_run_trajectory(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int | None = None,
    max_turns: int | None = None,
    test: Any = None,
    test_timeout_seconds: int | None = None,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 7200,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
    raw_params_json: str | None = None,
    sandbox_cpu: float | None = None,
    sandbox_memory_mb: int | None = None,
) -> str:
    # Share the function's ephemeral app so sandboxes are cleaned up with it.
    ModalSandbox.app = app
    raw_params = parse_raw_params_json(raw_params_json)
    return await run_trajectory(
        agent=agent,
        model=model,
        max_parts=max_parts,
        max_turns=max_turns,
        test=test,
        test_timeout_seconds=test_timeout_seconds,
        message_timeout_seconds=message_timeout_seconds,
        timeout_seconds=timeout_seconds,
        trajectory_id=trajectory_id,
        codex_auth_json_b64=codex_auth_json_b64,
        resume=resume,
        sandbox_provider=sandbox_provider,
        task_dir=task_dir,
        environment_dir=environment_dir,
        raw_params=raw_params,
        sandbox_cpu=sandbox_cpu,
        sandbox_memory_mb=sandbox_memory_mb,
    )


@app.function(
    timeout=14400,
    secrets=[modal.Secret.from_dotenv()],
    image=function_image,
    nonpreemptible=True,
    name="run_trajectory_non_preemptible",
)
async def modal_run_trajectory_non_preemptible(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int | None = None,
    max_turns: int | None = None,
    test: Any = None,
    test_timeout_seconds: int | None = None,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 7200,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
    raw_params_json: str | None = None,
    sandbox_cpu: float | None = None,
    sandbox_memory_mb: int | None = None,
) -> str:
    ModalSandbox.app = app
    raw_params = parse_raw_params_json(raw_params_json)
    return await run_trajectory(
        agent=agent,
        model=model,
        max_parts=max_parts,
        max_turns=max_turns,
        test=test,
        test_timeout_seconds=test_timeout_seconds,
        message_timeout_seconds=message_timeout_seconds,
        timeout_seconds=timeout_seconds,
        trajectory_id=trajectory_id,
        codex_auth_json_b64=codex_auth_json_b64,
        resume=resume,
        sandbox_provider=sandbox_provider,
        task_dir=task_dir,
        environment_dir=environment_dir,
        raw_params=raw_params,
        sandbox_cpu=sandbox_cpu,
        sandbox_memory_mb=sandbox_memory_mb,
    )


def get_non_preemptible_runner() -> Any:
    return modal_run_trajectory_non_preemptible


@app.local_entrypoint()
async def main(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int | None = None,
    max_turns: int | None = None,
    test: str | None = None,
    test_json: str | None = None,
    test_timeout_seconds: int | None = None,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 7200,
    non_preemptible: bool = True,
    trajectory_id: str | None = None,
    codex_auth_file: str = "~/.codex/auth.json",
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
    raw_params_json: str | None = None,
    sandbox_cpu: float | None = None,
    sandbox_memory_mb: int | None = None,
) -> None:
    selected_tests: list[str] | None = None
    if test_json and test_json.strip():
        raw = json.loads(test_json)
        if not isinstance(raw, list):
            raise ValueError("--test-json must decode to a JSON list of strings")
        cleaned = [
            path.strip()
            for path in raw
            if isinstance(path, str) and path.strip()
        ]
        selected_tests = cleaned or None
    elif test and test.strip():
        selected_tests = [test.strip()]

    normalized_agent = (agent or DEFAULT_AGENT).strip().lower()
    codex_auth_json_b64: str | None = None
    agent_cls = AGENT_BACKENDS.get(normalized_agent)
    if (
        agent_cls is not None
        and hasattr(agent_cls, "load_local_auth_b64")
        and codex_auth_file.strip()
    ):
        codex_auth_json_b64 = agent_cls.load_local_auth_b64(
            codex_auth_file.strip(),
        )
        if codex_auth_json_b64:
            print(
                f"[auth] loaded auth from "
                f"{Path(codex_auth_file).expanduser()}"
            )
        else:
            print(
                f"[auth] no auth file found at "
                f"{Path(codex_auth_file).expanduser()}"
            )

    runner = (
        get_non_preemptible_runner()
        if non_preemptible
        else modal_run_trajectory
    )
    call = None
    try:
        # Use spawn + get instead of remote so we hold a handle we can cancel
        # when the user presses Ctrl+C.  Without this, the remote function
        # (and its sandbox) keeps running on Modal even after the local
        # process exits.
        with modal.enable_output():
            call = await runner.spawn.aio(
                agent=normalized_agent,
                model=model,
                max_parts=max_parts,
                max_turns=max_turns,
                test=selected_tests,
                test_timeout_seconds=test_timeout_seconds,
                message_timeout_seconds=message_timeout_seconds,
                timeout_seconds=timeout_seconds,
                trajectory_id=trajectory_id,
                codex_auth_json_b64=codex_auth_json_b64,
                resume=resume,
                sandbox_provider=sandbox_provider,
                task_dir=task_dir,
                environment_dir=environment_dir,
                raw_params_json=raw_params_json,
                sandbox_cpu=sandbox_cpu,
                sandbox_memory_mb=sandbox_memory_mb,
            )
            result = await call.get.aio()
        print(f"Completed trajectory: {result}")
    except KeyboardInterrupt:
        print("[modal] interrupt received; cancelling remote function", flush=True)
        if call is not None:
            try:
                await call.cancel.aio()
                print("[modal] remote function cancelled", flush=True)
            except Exception:
                pass
        raise
    except asyncio.CancelledError:
        print("[modal] cancelled; stopping remote function", flush=True)
        if call is not None:
            try:
                await call.cancel.aio()
                print("[modal] remote function cancelled", flush=True)
            except Exception:
                pass
        raise
    except Exception as e:
        message = str(e).strip()
        if not message:
            message = "remote run stopped or failed"
        print(f"[error] {type(e).__name__}: {message}")
        print(traceback.format_exc())
