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

from pathlib import Path
from typing import Any

import modal

ROOT = Path(__file__).parent.parent.parent
WORKSPACE_ROOT = ROOT.parent.parent.parent

app = modal.App("envoi-trace")

function_image = (
    modal.Image.debian_slim()
    .pip_install("boto3", "pydantic", "pyarrow", "python-dotenv")
    .add_local_dir(
        ROOT, remote_path="/root/envoi_code",
    )
    .add_local_dir(
        WORKSPACE_ROOT / "examples", remote_path="/root/examples",
    )
)

# Import these after defining image — they get serialized into the image.
from envoi_code.orchestrator import (  # noqa: E402
    AGENT_BACKENDS,
    DEFAULT_AGENT,
    MESSAGE_TIMEOUT_SECONDS,
    RESUME_FROM_S3,
    run_trajectory,
)
from envoi_code.utils.helpers import tprint  # noqa: E402

print = tprint


@app.function(
    timeout=14400,
    secrets=[modal.Secret.from_dotenv()],
    image=function_image,
)
async def modal_run_trajectory(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
    max_turns: int | None = None,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
) -> str:
    return await run_trajectory(
        agent=agent,
        model=model,
        max_parts=max_parts,
        max_turns=max_turns,
        message_timeout_seconds=message_timeout_seconds,
        timeout_seconds=timeout_seconds,
        trajectory_id=trajectory_id,
        codex_auth_json_b64=codex_auth_json_b64,
        resume=resume,
        sandbox_provider=sandbox_provider,
        task_dir=task_dir,
        environment_dir=environment_dir,
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
    max_parts: int = 1000,
    max_turns: int | None = None,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
) -> str:
    return await run_trajectory(
        agent=agent,
        model=model,
        max_parts=max_parts,
        max_turns=max_turns,
        message_timeout_seconds=message_timeout_seconds,
        timeout_seconds=timeout_seconds,
        trajectory_id=trajectory_id,
        codex_auth_json_b64=codex_auth_json_b64,
        resume=resume,
        sandbox_provider=sandbox_provider,
        task_dir=task_dir,
        environment_dir=environment_dir,
    )


def get_non_preemptible_runner() -> Any:
    return modal_run_trajectory_non_preemptible


@app.local_entrypoint()
async def main(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
    max_turns: int | None = None,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    non_preemptible: bool = True,
    trajectory_id: str | None = None,
    codex_auth_file: str = "~/.codex/auth.json",
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
) -> None:
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
    try:
        result = await runner.remote.aio(
            agent=normalized_agent,
            model=model,
            max_parts=max_parts,
            max_turns=max_turns,
            message_timeout_seconds=message_timeout_seconds,
            trajectory_id=trajectory_id,
            codex_auth_json_b64=codex_auth_json_b64,
            resume=resume,
            sandbox_provider=sandbox_provider,
            task_dir=task_dir,
            environment_dir=environment_dir,
        )
        print(f"Completed trajectory: {result}")
    except Exception as e:
        message = str(e).strip()
        if not message:
            message = "remote run stopped or failed"
        print(f"[error] {message}")
