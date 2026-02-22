"""
Main orchestrator for envoi-trace.

This module runs inside a Modal function. It creates a sandbox, provisions an
agent (Codex or OpenCode), and runs a turn loop with a bounded part budget.
After every part, it persists trace.parquet to S3. After every file change, it
creates a git checkpoint. At end-of-run, it uploads a repo.bundle with full
git history.

The two core abstractions are AgentBackend (how to talk to an agent) and
SandboxBackend (where the agent runs). This file wires them together and
manages the turn loop, resume logic, and artifact persistence.

Usage (via CLI):
    envoi-trace --task examples/tasks/c_compiler --env examples/environments/c_compiler
    envoi-trace --agent codex --max-parts 1000 --task <path> --env <path>
"""

from __future__ import annotations

import asyncio
import base64
import builtins
import inspect
import json
import os
import shlex
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import modal

from agents.base import AgentBackend
from agents.codex import CodexAgent
from agents.opencode import OPENCODE_CONFIG_TEMPLATE, OpenCodeAgent
from models import (
    AgentTrace,
    EnvoiCall,
    EvaluationRecord,
    SessionEnd,
    TurnRecord,
)
from sandbox.base import SandboxBackend
from sandbox.modal import ModalSandbox
from utils.evaluation import (
    EVALUATION_CONCURRENCY,
    _extract_leaf_paths,
    _extract_suite_roots,
    run_commit_evaluation,
)
from utils.git import get_git_commit
from utils.helpers import (
    compute_turn_timeout_seconds,
    decode_b64_to_text,
    environment_upload_items,
    load_environment_files,
    load_local_codex_auth_json_b64,
    parse_codex_auth_json,
    resolve_model,
    tprint,
    truncate_text,
    upload_files_parallel,
)
from utils.parsing import (
    count_meaningful_parts,
    extract_envoi_calls,
    extract_turn_token_usage,
    log_message_parts,
)
from utils.solve import SolveTracker
from utils.storage import (
    artifact_uri,
    get_bucket,
    get_s3_client,
    load_trace_snapshot,
    save_trace_parquet,
    upload_file,
)
from utils.stream import make_stream_part_callback

app = modal.App("envoi-trace")

DEFAULT_AGENT = "codex"
CODEX_HOME_DIR = "/tmp/codex-home"
MESSAGE_TIMEOUT_SECONDS = int(
    os.environ.get("MESSAGE_TIMEOUT_SECONDS", "600")
)  # hard cap per message turn
RESUME_FROM_S3 = (
    os.environ.get("RESUME_FROM_S3", "1").strip().lower()
    not in {"0", "false", "no"}
)
TURN_RECOVERY_RETRIES = max(
    0, int(os.environ.get("TURN_RECOVERY_RETRIES", "3"))
)


print = tprint

AGENT_BACKENDS: dict[str, type] = {
    "opencode": OpenCodeAgent,
    "codex": CodexAgent,
}

# ---------------------------------------------------------------------------
# Load files at import time (Modal serializes these into the function image)
# ---------------------------------------------------------------------------

SETUP_SH = (Path(__file__).parent / "sandbox" / "modal" / "setup.sh").read_text()
MCP_SERVER = (Path(__file__).parent / "sandbox" / "modal" / "mcp_server.py").read_text()
OPENCODE_CLIENT = (Path(__file__).parent / "agents" / "opencode.py").read_text()
CODEX_CLIENT = (Path(__file__).parent / "agents" / "codex.py").read_text()
OPENCODE_CONFIG = OPENCODE_CONFIG_TEMPLATE

_EXAMPLES_DIR = Path(__file__).parent / "examples"
_DEFAULT_ENVIRONMENT_DIR = _EXAMPLES_DIR / "environments" / "c_compiler"


async def load_task(
    task_dir: Path, *, lang: str = "en",
) -> tuple[str, dict[str, Any]]:
    """Load a task prompt from a directory path.

    Three tiers, checked in order:
      Tier 3: task_dir/task.py with a generate() function -> (prompt, params)
      Tier 2: prompt file (en.md or prompt.md) + params.py -> template substitution
      Tier 1: prompt file only -> static prompt text

    Task directories don't need to be Python packages. Uses
    importlib.util.spec_from_file_location for file-based module loading.

    Returns (prompt_text, params_dict).
    """
    import importlib.util

    # Tier 3: full dynamic generation
    if (task_dir / "task.py").exists():
        spec = importlib.util.spec_from_file_location("_task", task_dir / "task.py")
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            gen = getattr(mod, "generate", None)
            if gen is not None:
                return await gen() if inspect.iscoroutinefunction(gen) else gen()

    # Tier 1/2: load prompt file
    prompt_file = task_dir / f"{lang}.md"
    if not prompt_file.exists():
        prompt_file = task_dir / "prompt.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"No prompt found in {task_dir}")

    prompt = prompt_file.read_text().strip()

    # Tier 2: apply params if params.py exists
    params: dict[str, Any] = {}
    if (task_dir / "params.py").exists():
        spec = importlib.util.spec_from_file_location("_params", task_dir / "params.py")
        if spec and spec.loader:
            params_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(params_mod)
            params_fn = params_mod.params
            params = (
                await params_fn()
                if inspect.iscoroutinefunction(params_fn)
                else params_fn()
            )
            prompt = prompt.format(**params)

    return prompt, params
_ENV_PY, _ENV_C, _ENV_TXT = load_environment_files(
    _DEFAULT_ENVIRONMENT_DIR,
)

WORKSPACE_GITIGNORE = """\
target/
cc
debug_artifacts/
test_*
*.o
*.out
*.s
opencode.jsonc
.opencode/
.codex/
"""

CODEX_CONFIG_TOML = """\
model = "MODEL_PLACEHOLDER"
model_reasoning_effort = "high"

[mcp_servers.tests]
command = "python3"
args = ["/sandbox/mcp_server.py"]
enabled = true
required = false
tool_timeout_sec = 3600
"""


# ---------------------------------------------------------------------------
# Modal images
# ---------------------------------------------------------------------------

function_image = (
    modal.Image.debian_slim()
    .pip_install("boto3", "pydantic", "pyarrow")
    .add_local_file(
        Path(__file__).parent / "models.py",
        remote_path="/root/models.py",
    )
    .add_local_dir(Path(__file__).parent / "utils", remote_path="/root/utils")
    .add_local_dir(_EXAMPLES_DIR / "tasks", remote_path="/root/examples/tasks")
    .add_local_dir(Path(__file__).parent / "agents", remote_path="/root/agents")
    .add_local_dir(Path(__file__).parent / "sandbox", remote_path="/root/sandbox")
    .add_local_dir(
        _EXAMPLES_DIR / "environments",
        remote_path="/root/examples/environments",
    )
)

sandbox_image = (
    modal.Image.from_registry("ubuntu:24.04", add_python="3.12")
    .apt_install(
        "build-essential",
        "gcc",
        "g++",
        "clang",
        "git",
        "curl",
        "wget",
        "pkg-config",
        "libssl-dev",
    )
    .pip_install(
        "envoi @ git+https://github.com/TheSeamau5/envoi.git",
        "httpx>=0.27.0",
        "opencode-ai>=0.1.0a36",
        "pydantic>=2.0.0",
        "mcp>=1.0.0",
    )
    .run_commands("curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y")
)

# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------


async def dump_sandbox_logs(
    sb: SandboxBackend,
    *,
    agent: str,
    tail: int = 50,
) -> None:
    """Print the tail of agent + envoi logs from the sandbox."""
    log_files = ["/tmp/envoi.log"]
    if agent == "opencode":
        log_files.insert(0, "/tmp/opencode.log")
    elif agent == "codex":
        log_files.insert(0, "/tmp/codex.log")

    for log_file in log_files:
        try:
            _, stdout, _ = (await sb.run(
                f"[ -f {shlex.quote(log_file)} ] && tail -n {tail} {shlex.quote(log_file)} || true",
                timeout=10,
                quiet=True,
            )).unpack()
            if stdout.strip():
                label = log_file.split("/")[-1]
                print(f"[logs] === {label} (last {tail} lines) ===")
                for line in stdout.strip().splitlines():
                    builtins.print(f"  {line}", flush=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Agent backends + sb setup
# ---------------------------------------------------------------------------


async def upload_environment_files(
    sb: SandboxBackend,
    py_files: dict[str, str] | None = None,
    c_files: dict[str, str] | None = None,
    txt_files: dict[str, str] | None = None,
) -> None:
    env_py = py_files if py_files is not None else _ENV_PY
    env_c = c_files if c_files is not None else _ENV_C
    env_txt = txt_files if txt_files is not None else _ENV_TXT
    await upload_files_parallel(
        sb,
        environment_upload_items(env_py, env_c, env_txt),
        log_upload=True,
    )

    print(
        f"[setup] uploaded {len(env_py)} py, "
        f"{len(env_c)} c, {len(env_txt)} txt files"
    )


async def run_setup_script(sb: SandboxBackend, agent: str) -> None:
    print(f"[setup] running setup.sh (agent={agent})...")

    async def handle_setup_line(line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        if stripped.startswith("[setup]") or stripped.startswith("[fixtures]"):
            print(stripped)
            return
        if stripped.startswith("ERROR:"):
            print(f"[setup] {stripped}")

    exit_code, stdout, stderr = (await sb.run(
        f"AGENT_KIND={shlex.quote(agent)} bash /tmp/upload/setup.sh",
        timeout=1800,
        on_stdout_line=handle_setup_line,
        on_stderr_line=handle_setup_line,
    )).unpack()
    if exit_code != 0:
        print(f"[setup] FAILED:\n{stdout}\n{stderr}")
        raise RuntimeError(f"Setup failed (exit {exit_code})")
    print("[setup] done")


def get_trace_last_part(trace: AgentTrace) -> int:
    return max((part.part or 0) for part in trace.parts) if trace.parts else 0


def get_trace_last_turn(trace: AgentTrace) -> int:
    if not trace.turns:
        return 0
    turn_values = [turn.turn for turn in trace.turns if isinstance(turn.turn, int)]
    if turn_values:
        return max(turn_values)
    return len(trace.turns)


def get_trace_latest_commit(trace: AgentTrace) -> str | None:
    if trace.session_end and isinstance(trace.session_end.final_git_commit, str):
        final_commit = trace.session_end.final_git_commit.strip()
        if final_commit:
            return final_commit

    for part in reversed(trace.parts):
        if isinstance(part.git_commit, str) and part.git_commit:
            return part.git_commit
        checkpoint = part.repo_checkpoint
        if checkpoint is None:
            continue
        if isinstance(checkpoint.commit_after, str) and checkpoint.commit_after:
            return checkpoint.commit_after
        if isinstance(checkpoint.commit_before, str) and checkpoint.commit_before:
            return checkpoint.commit_before
    return None


def build_unsolved_status_lines(tracker: SolveTracker) -> list[str]:
    details: list[str] = []
    for path in tracker.get_unsolved_paths()[:10]:
        call = tracker.get_latest_call_for_path(path)
        if call and call.result:
            details.append(f"  - {path}: {call.result.passed}/{call.result.total}")
        else:
            details.append(f"  - {path}: not run")
    return details


async def restore_workspace_from_bundle(
    *,
    sb: SandboxBackend,
    trajectory_id: str,
    commit: str,
) -> bool:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/repo.bundle"
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except Exception as error:  # noqa: BLE001
        code = str(getattr(error, "response", {}).get("Error", {}).get("Code", "")).strip()
        if code in {"NoSuchKey", "404", "NotFound"}:
            print("[resume] repo.bundle not found; continuing without workspace restore")
            return False
        print(f"[resume] failed to read repo.bundle: {error}")
        return False

    body = response.get("Body")
    if body is None:
        print("[resume] repo.bundle body missing; continuing without workspace restore")
        return False

    bundle_bytes = body.read()
    if not bundle_bytes:
        print("[resume] repo.bundle empty; continuing without workspace restore")
        return False

    encoded = base64.b64encode(bundle_bytes).decode("ascii")
    await sb.write_file(
        "/tmp/resume.bundle.b64",
        encoded,
        ensure_dir=False,
    )

    quoted_commit = shlex.quote(commit)
    restore_cmd = (
        "set -euo pipefail\n"
        "base64 -d /tmp/resume.bundle.b64 > /tmp/resume.bundle\n"
        "rm -rf /tmp/resume_repo\n"
        "git clone -q /tmp/resume.bundle /tmp/resume_repo\n"
        "cd /tmp/resume_repo\n"
        f"git checkout -q {quoted_commit}\n"
        "rm -rf /workspace\n"
        "mkdir -p /workspace\n"
        "cp -a /tmp/resume_repo/. /workspace/\n"
        "cd /workspace\n"
        "git config user.email 'agent@example.com'\n"
        "git config user.name 'Agent'\n"
    )
    exit_code, _, stderr = (await sb.run(
        restore_cmd,
        timeout=300,
        quiet=True,
    )).unpack()
    if exit_code != 0:
        stderr_summary = truncate_text(stderr or "(no stderr)", limit=600)
        print(
            f"[resume] workspace restore failed: {stderr_summary}"
        )
        return False
    print(f"[resume] restored workspace from bundle at commit {commit}")
    return True


async def setup_sandbox_opencode(
    sb: SandboxBackend,
    model: str,
    api_key: str,
    setup_script: str = "",
    env_files: tuple[
        dict[str, str], dict[str, str], dict[str, str]
    ] | None = None,
) -> None:
    print(f"[setup] agent=opencode model={model}")
    config = OPENCODE_CONFIG.replace("MODEL_PLACEHOLDER", model)
    await upload_files_parallel(
        sb,
        [
            ("/tmp/upload/setup.sh", SETUP_SH),
            ("/tmp/upload/task_setup.sh", setup_script),
            ("/sandbox/mcp_server.py", MCP_SERVER),
            ("/sandbox/opencode_client.py", OPENCODE_CLIENT),
            ("/tmp/upload/opencode_api_key.txt", api_key),
            ("/workspace/opencode.jsonc", config),
            ("/workspace/.gitignore", WORKSPACE_GITIGNORE),
        ],
        log_upload=True,
    )
    py, c, txt = env_files if env_files else (_ENV_PY, _ENV_C, _ENV_TXT)
    await upload_environment_files(sb, py, c, txt)
    await run_setup_script(sb, "opencode")


async def setup_sandbox_codex(
    sb: SandboxBackend,
    model: str,
    api_key: str,
    auth_json: str | None = None,
    setup_script: str = "",
    env_files: tuple[
        dict[str, str], dict[str, str], dict[str, str]
    ] | None = None,
) -> None:
    print(f"[setup] agent=codex model={model}")
    codex_config = CODEX_CONFIG_TOML.replace("MODEL_PLACEHOLDER", model)
    setup_uploads: list[tuple[str, str]] = [
        ("/tmp/upload/setup.sh", SETUP_SH),
        ("/tmp/upload/task_setup.sh", setup_script),
        ("/sandbox/mcp_server.py", MCP_SERVER),
        ("/sandbox/codex_client.py", CODEX_CLIENT),
        (f"{CODEX_HOME_DIR}/config.toml", codex_config),
        ("/workspace/.gitignore", WORKSPACE_GITIGNORE),
    ]
    if api_key:
        setup_uploads.append(("/tmp/upload/codex_api_key.txt", api_key))
    if auth_json:
        setup_uploads.append((f"{CODEX_HOME_DIR}/auth.json", auth_json))

    await upload_files_parallel(
        sb,
        setup_uploads,
        log_upload=True,
    )
    py, c, txt = env_files if env_files else (_ENV_PY, _ENV_C, _ENV_TXT)
    await upload_environment_files(sb, py, c, txt)
    await run_setup_script(sb, "codex")


# ---------------------------------------------------------------------------
# End session
# ---------------------------------------------------------------------------


async def end_session(
    sb: SandboxBackend,
    agent_trace: AgentTrace,
    part_count: int,
    turn_count: int,
    reason: Literal["solved", "part_limit", "timeout", "agent_error", "envoi_error"],
    *,
    environment: str = "",
    task_params: dict[str, Any] | None = None,
) -> None:
    print(f"[end] reason={reason} parts={part_count}")

    if part_count == 0 and turn_count == 0:
        print("[end] nothing to save (0 parts), skipping S3 upload")
        return

    final_commit = await get_git_commit(sb)
    bundle_s3_uri: str | None = None

    agent_trace.session_end = SessionEnd(
        reason=reason,
        total_parts=part_count,
        total_turns=turn_count,
        final_git_commit=final_commit,
    )

    # Upload git bundle
    try:
        exit_code, _, _ = (await sb.run(
            "git bundle create /tmp/repo.bundle --all",
            quiet=True,
            cwd="/workspace",
        )).unpack()
        _, size_out, _ = (await sb.run(
            "stat -c %s /tmp/repo.bundle 2>/dev/null || echo 0",
            quiet=True,
        )).unpack()
        bundle_size = int(size_out.strip() or "0")
        print(f"[bundle] size={bundle_size} bytes")

        if bundle_size > 0:
            _, b64, _ = (await sb.run("base64 /tmp/repo.bundle", quiet=True)).unpack()
            data = base64.b64decode(b64.strip())
            bundle_s3_uri = upload_file(agent_trace.trajectory_id, "repo.bundle", data)
            print(f"[bundle] uploaded ({len(data)} bytes)")
    except Exception as e:
        print(f"[bundle] failed: {e}")

    trace_parquet_uri = artifact_uri(agent_trace.trajectory_id, "trace.parquet")
    agent_trace.artifacts = {
        "trace_parquet": trace_parquet_uri,
        "repo_bundle": bundle_s3_uri,
    }
    if environment:
        save_trace_parquet(
            agent_trace.trajectory_id, agent_trace,
            environment=environment, task_params=task_params,
        )

    print(
        f"[end] session ended: {reason}, {part_count} parts, commit={final_commit}"
    )


# ---------------------------------------------------------------------------
# Modal images
# ---------------------------------------------------------------------------


async def _run_trajectory_impl(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
    task_lang: str = "en",
    task_params: dict[str, str] | None = None,
) -> str:
    if trajectory_id is None:
        trajectory_id = str(uuid.uuid4())
    agent = (agent or DEFAULT_AGENT).strip().lower()

    task_path = Path(task_dir)
    env_path = Path(environment_dir)
    environment = env_path.name
    prompt, task_params_loaded = await load_task(task_path, lang=task_lang)
    if task_params:
        task_params_loaded.update(task_params)
    env_files = load_environment_files(env_path)
    setup_script_file = env_path / "setup.sh"
    setup_script = (
        setup_script_file.read_text() if setup_script_file.exists() else ""
    )

    effective_max_parts = max_parts
    resolved_model = resolve_model(agent, model)
    existing_trace = load_trace_snapshot(trajectory_id) if resume else None
    if existing_trace is not None and existing_trace.agent != agent:
        print(
            f"[resume] existing trajectory agent={existing_trace.agent} differs from "
            f"requested agent={agent}; starting new trace object"
        )
        existing_trace = None
    trace_s3_uri = artifact_uri(trajectory_id, "trace.parquet")
    bundle_s3_uri = artifact_uri(trajectory_id, "repo.bundle")
    banner = "=" * 72
    print(banner)
    print(f"TRAJECTORY_ID: {trajectory_id}")
    print(f"TRACE_S3_URI: {trace_s3_uri}")
    print(f"BUNDLE_S3_URI: {bundle_s3_uri}")
    print(
        f"agent={agent} model={resolved_model} max_parts={effective_max_parts} "
        f"timeout={timeout_seconds}s message_timeout={message_timeout_seconds}s"
    )
    if existing_trace is not None:
        print(
            f"[resume] found existing trace: parts={len(existing_trace.parts)} "
            f"turns={len(existing_trace.turns)}"
        )
    print(banner)

    agent_api_key = ""
    codex_auth_json: str | None = None
    if agent == "opencode":
        agent_api_key = os.environ.get("OPENCODE_API_KEY", "").strip()
        if not agent_api_key:
            raise RuntimeError("OPENCODE_API_KEY not set")
    else:
        env_codex_auth_b64 = os.environ.get("CODEX_AUTH_JSON_B64", "").strip()
        env_codex_auth_raw = os.environ.get("CODEX_AUTH_JSON", "").strip()
        if codex_auth_json_b64:
            decoded = decode_b64_to_text(codex_auth_json_b64, label="codex_auth_json_b64 arg")
            codex_auth_json = parse_codex_auth_json(decoded, label="codex_auth_json_b64 arg")
        elif env_codex_auth_b64:
            decoded = decode_b64_to_text(env_codex_auth_b64, label="CODEX_AUTH_JSON_B64")
            codex_auth_json = parse_codex_auth_json(decoded, label="CODEX_AUTH_JSON_B64")
        elif env_codex_auth_raw:
            codex_auth_json = parse_codex_auth_json(env_codex_auth_raw, label="CODEX_AUTH_JSON")

        agent_api_key = (
            os.environ.get("CODEX_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )
        if not codex_auth_json and not agent_api_key:
            raise RuntimeError(
                "No Codex credentials found. Provide one of: "
                "~/.codex/auth.json via --codex-auth-file, "
                "CODEX_AUTH_JSON_B64/CODEX_AUTH_JSON, or CODEX_API_KEY/OPENAI_API_KEY."
            )

    sb: SandboxBackend | None = None
    agent_trace: AgentTrace | None = None
    turn_count = 0
    part_count = 0
    end_reason: str | None = None
    wait_for_evaluations_fn: Callable[[], Awaitable[None]] | None = None

    try:
        if sandbox_provider == "modal":
            sb = await ModalSandbox.create(
                image=sandbox_image,
                timeout=timeout_seconds,
                app=app,
            )
        elif sandbox_provider == "e2b":
            from sandbox.e2b import E2BSandbox

            sb = await E2BSandbox.create(timeout=timeout_seconds)
        else:
            raise ValueError(f"Unknown sandbox provider: {sandbox_provider}")
        start_time = time.monotonic()

        # --- Setup ---
        if agent == "opencode":
            await setup_sandbox_opencode(
                sb, resolved_model, agent_api_key,
                setup_script=setup_script, env_files=env_files,
            )
        else:
            await setup_sandbox_codex(
                sb, resolved_model, agent_api_key, codex_auth_json,
                setup_script=setup_script, env_files=env_files,
            )

        resume_commit = (
            get_trace_latest_commit(existing_trace)
            if existing_trace
            else None
        )
        if (
            existing_trace is not None
            and isinstance(resume_commit, str)
            and resume_commit
        ):
            await restore_workspace_from_bundle(
                sb=sb,
                trajectory_id=trajectory_id,
                commit=resume_commit,
            )

        # --- Create agent backend and session ---
        agent_cls = AGENT_BACKENDS.get(agent)
        if agent_cls is None:
            raise ValueError(f"Unknown agent: {agent}")
        agent_backend: AgentBackend = agent_cls()
        codex_api_key_file = (
            "/tmp/upload/codex_api_key.txt"
            if agent == "codex" and agent_api_key
            else None
        )
        await agent_backend.start(
            sb=sb,
            model=resolved_model,
            api_key=agent_api_key,
            auth_json=codex_auth_json,
            api_key_file=codex_api_key_file,
        )
        session_id = await agent_backend.create_session(
            trajectory_id,
        )
        if not session_id:
            raise RuntimeError(
                f"Failed to create session for agent={agent}",
            )

        if existing_trace is not None:
            agent_trace = existing_trace
            agent_trace.session_id = session_id
            agent_trace.agent = agent
            agent_trace.agent_model = resolved_model
            agent_trace.session_end = None
            part_count = get_trace_last_part(agent_trace)
            turn_count = get_trace_last_turn(agent_trace)
            print(f"[resume] continuing from part={part_count} turn={turn_count}")
        else:
            agent_trace = AgentTrace(
                trajectory_id=trajectory_id,
                session_id=session_id,
                agent=agent,
                agent_model=resolved_model,
                started_at=datetime.now(UTC).isoformat(),
            )
        save_trace_parquet(
            trajectory_id, agent_trace,
            environment=environment,
            task_params=task_params_loaded,
        )

        evaluation_tasks: set[asyncio.Task[None]] = set()
        evaluation_commits: set[str] = set(agent_trace.evaluations.keys())
        evaluation_semaphore = asyncio.Semaphore(EVALUATION_CONCURRENCY)
        for evaluation in agent_trace.evaluations.values():
            if evaluation.status in {"queued", "running"}:
                evaluation.status = "failed"
                evaluation.error = "Interrupted before evaluation completed"
                evaluation.completed_at = datetime.now(UTC).isoformat()

        def schedule_commit_evaluation(commit: str, part: int) -> None:
            if commit in evaluation_commits:
                return
            evaluation_commits.add(commit)
            queued_at = datetime.now(UTC).isoformat()
            print(f"[eval] queued commit {commit[:10]} from part {part}")
            agent_trace.evaluations[commit] = EvaluationRecord(
                commit=commit,
                part=part,
                status="queued",
                queued_at=queued_at,
            )
            save_trace_parquet(
                trajectory_id, agent_trace,
                environment=environment,
                task_params=task_params_loaded,
            )

            async def _runner() -> None:
                started_at = datetime.now(UTC).isoformat()
                evaluation = agent_trace.evaluations.get(commit)
                if evaluation is None:
                    evaluation = EvaluationRecord(
                        commit=commit,
                        part=part,
                        status="queued",
                        queued_at=queued_at,
                    )
                    agent_trace.evaluations[commit] = evaluation
                evaluation.status = "running"
                evaluation.started_at = started_at
                save_trace_parquet(
                    trajectory_id, agent_trace,
                    environment=environment,
                    task_params=task_params_loaded,
                )

                async with evaluation_semaphore:
                    run_payload: dict[str, Any] | None = None
                    started_mono = time.monotonic()
                    try:
                        run_payload = await run_commit_evaluation(
                            sb=sb,
                            commit=commit,
                            suite_paths=suite_paths or None,
                        )
                        payload = run_payload.get("payload")
                        exit_code_value = run_payload.get("exit_code")
                        stdout_value = run_payload.get("stdout")
                        stderr_value = run_payload.get("stderr")
                        command_value = run_payload.get("command")

                        evaluation.command = (
                            command_value if isinstance(command_value, str) else None
                        )
                        evaluation.exit_code = (
                            exit_code_value if isinstance(exit_code_value, int) else None
                        )
                        raw_stdout = stdout_value if isinstance(stdout_value, str) else None
                        raw_stderr = stderr_value if isinstance(stderr_value, str) else None

                        if (
                            isinstance(evaluation.exit_code, int)
                            and evaluation.exit_code != 0
                        ):
                            evaluation.status = "failed"
                            evaluation.error = (
                                f"Evaluation command failed with exit code {evaluation.exit_code}"
                            )
                            evaluation.stdout = raw_stdout
                            evaluation.stderr = raw_stderr
                            evaluation.passed = 0
                            evaluation.failed = 0
                            evaluation.total = 0
                            evaluation.suite_results = {}
                        elif not isinstance(payload, dict):
                            evaluation.status = "failed"
                            evaluation.error = "Missing evaluation payload in command output"
                            evaluation.stdout = raw_stdout
                            evaluation.stderr = raw_stderr
                            evaluation.passed = 0
                            evaluation.failed = 0
                            evaluation.total = 0
                            evaluation.suite_results = {}
                        else:
                            evaluation.status = "completed"
                            evaluation.error = (
                                payload.get("error")
                                if isinstance(payload.get("error"), str)
                                else None
                            )
                            evaluation.stdout = raw_stdout
                            evaluation.stderr = raw_stderr
                            evaluation.duration_ms = int(
                                payload.get("duration_ms", 0) or 0
                            )
                            evaluation.passed = int(payload.get("passed", 0) or 0)
                            evaluation.failed = int(payload.get("failed", 0) or 0)
                            evaluation.total = int(payload.get("total", 0) or 0)
                            suite_results = payload.get("suite_results")
                            if isinstance(suite_results, dict):
                                evaluation.suite_results = suite_results
                            else:
                                evaluation.suite_results = {}
                    except Exception as eval_error:
                        evaluation.status = "failed"
                        evaluation.error = str(eval_error)
                        evaluation.passed = 0
                        evaluation.failed = 0
                        evaluation.total = 0
                        evaluation.suite_results = {}
                        if run_payload is not None:
                            exit_code_value = run_payload.get("exit_code")
                            stdout_value = run_payload.get("stdout")
                            stderr_value = run_payload.get("stderr")
                            command_value = run_payload.get("command")
                            evaluation.command = (
                                command_value if isinstance(command_value, str) else None
                            )
                            evaluation.exit_code = (
                                exit_code_value if isinstance(exit_code_value, int) else None
                            )
                            evaluation.stdout = (
                                stdout_value if isinstance(stdout_value, str) else None
                            )
                            evaluation.stderr = (
                                stderr_value if isinstance(stderr_value, str) else None
                            )
                    finally:
                        if evaluation.duration_ms is None:
                            evaluation.duration_ms = int(
                                (time.monotonic() - started_mono) * 1000
                            )
                        evaluation.completed_at = datetime.now(UTC).isoformat()
                        print(
                            f"[eval] commit {commit[:10]} status={evaluation.status} "
                            f"passed={evaluation.passed}/{evaluation.total}"
                        )
                        save_trace_parquet(
                            trajectory_id, agent_trace,
                            environment=environment,
                            task_params=task_params_loaded,
                        )

            task = asyncio.create_task(_runner())
            evaluation_tasks.add(task)

            def _on_done(done_task: asyncio.Task[None]) -> None:
                evaluation_tasks.discard(done_task)
                try:
                    done_task.result()
                except Exception as task_error:
                    print(
                        f"[eval] unexpected task error for commit {commit}: {task_error}"
                    )

            task.add_done_callback(_on_done)

        async def _wait_for_evaluations() -> None:
            while evaluation_tasks:
                pending = list(evaluation_tasks)
                if not pending:
                    break
                await asyncio.gather(*pending, return_exceptions=True)

        wait_for_evaluations_fn = _wait_for_evaluations

        # --- Discover test paths from envoi /schema ---
        required_test_paths: list[str] = []
        suite_paths: list[str] = []
        schema_result = await sb.run(
            "curl -sf http://localhost:8000/schema",
            quiet=True, timeout=30,
        )
        if schema_result.exit_code == 0 and schema_result.stdout.strip():
            try:
                schema = json.loads(schema_result.stdout)
                required_test_paths = _extract_leaf_paths(schema)
                suite_paths = _extract_suite_roots(schema)
                print(
                    f"[schema] discovered "
                    f"{len(required_test_paths)} test paths, "
                    f"{len(suite_paths)} suites"
                )
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[schema] parse error: {e}")
        else:
            print("[schema] /schema not available, running without completion tracking")

        # --- Main loop: blocking message calls with part budget ---
        required = set(required_test_paths)
        passing: set[str] = set()
        tracker = SolveTracker(required_test_paths)
        for part_record in agent_trace.parts:
            tracker.update(list(part_record.envoi_calls))
        for path in tracker.solved:
            passing.add(path)

        continue_prompt = "Continue."

        def _followup() -> str:
            status = build_unsolved_status_lines(tracker)
            if not status:
                return continue_prompt
            return (
                continue_prompt
                + "\n\nCurrent test status:\n"
                + "\n".join(status)
            )

        prompt_text = prompt if part_count == 0 else _followup()
        consecutive_turn_failures = 0

        try:
            while part_count < effective_max_parts:
                elapsed = time.monotonic() - start_time
                if elapsed > timeout_seconds:
                    end_reason = "timeout"
                    break
                remaining_run_seconds = timeout_seconds - elapsed
                remaining_parts = max(1, effective_max_parts - part_count)
                if agent == "codex":
                    # For Codex, don't short-circuit long productive turns with the
                    # per-turn message timeout; only the overall run timeout applies.
                    turn_timeout_seconds = max(1, int(remaining_run_seconds))
                else:
                    turn_timeout_seconds = compute_turn_timeout_seconds(
                        remaining_parts=remaining_parts,
                        remaining_run_seconds=remaining_run_seconds,
                        message_timeout_seconds=message_timeout_seconds,
                    )

                banner = "=" * 60
                builtins.print(f"\n{banner}", flush=True)
                builtins.print(
                    f" TURN {turn_count + 1}  "
                    f"(part_count {part_count}/{effective_max_parts}, "
                    f"timeout {turn_timeout_seconds}s)",
                    flush=True,
                )
                builtins.print(banner, flush=True)

                turn_started_at = datetime.now(UTC).isoformat()
                per_call_parts_budget = remaining_parts
                previous_part_count = part_count
                streamed_parts = 0
                observed_parts = 0
                part_limit_abort = False
                git_commit = await get_git_commit(sb)
                turn_record: TurnRecord | None = None

                turn_count += 1
                turn_record = TurnRecord(
                    trajectory_id=trajectory_id,
                    session_id=session_id,
                    agent=agent,
                    turn=turn_count,
                    part_start=None,
                    part_end=None,
                    timestamp=turn_started_at,
                    agent_model=resolved_model,
                    prompt=prompt_text,
                    git_commit=git_commit,
                    repo_checkpoint=None,
                    parts=[],
                )
                agent_trace.turns.append(turn_record)

                stream_part_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None
                stream_part_counter: list[int] = [part_count]
                stream_git_commit_ref: list[str | None] = [git_commit]
                stream_last_part_ts_ref: list[int | None] = [None]
                stream_part_cb = make_stream_part_callback(
                    sb=sb,
                    trajectory_id=trajectory_id,
                    agent_trace=agent_trace,
                    tracker=tracker,
                    environment=environment,
                    task_params=task_params_loaded,
                    agent_name=agent,
                    resolved_model=resolved_model,
                    effective_max_parts=effective_max_parts,
                    part_counter=stream_part_counter,
                    git_commit_ref=stream_git_commit_ref,
                    last_part_timestamp_ms_ref=stream_last_part_ts_ref,
                    turn_record=turn_record,
                    session_id=session_id,
                    schedule_commit_evaluation=schedule_commit_evaluation,
                )

                # Send message and BLOCK until agent finishes
                turn_outcome = await agent_backend.run_turn(
                    prompt_text=prompt_text,
                    timeout=turn_timeout_seconds,
                    remaining_parts_budget=per_call_parts_budget,
                    on_stream_part=stream_part_cb,
                )
                part_count = stream_part_counter[0]
                git_commit = stream_git_commit_ref[0]

                if turn_outcome is None:
                    if turn_record is not None and not turn_record.parts:
                        agent_trace.turns.pop()
                    consecutive_turn_failures += 1
                    print(
                        "[progress] no response from agent "
                        f"(recovery {consecutive_turn_failures}/{TURN_RECOVERY_RETRIES})"
                    )
                    await dump_sandbox_logs(sb, agent=agent)
                    if consecutive_turn_failures <= TURN_RECOVERY_RETRIES:
                        recovered_session_id = (
                            await agent_backend.recover_session(
                                trajectory_id,
                                consecutive_turn_failures,
                            )
                        )
                        if recovered_session_id:
                            session_id = recovered_session_id
                            agent_trace.session_id = recovered_session_id
                            save_trace_parquet(
                                trajectory_id, agent_trace,
                                environment=environment,
                                task_params=task_params_loaded,
                            )
                            prompt_text = _followup()
                            continue
                    end_reason = "agent_error"
                    break
                consecutive_turn_failures = 0
                agent_backend.on_turn_complete(turn_outcome)

                response = turn_outcome.response
                session_id = turn_outcome.session_id
                if agent_trace.session_id != session_id:
                    agent_trace.session_id = session_id

                # Log what the agent did
                info = response.get("info", {})
                parts = response.get("parts", [])
                response_message_id = info.get("id")
                print(f"[progress] response id={response_message_id} parts={len(parts)}")
                log_message_parts(response)

                session_ids = turn_outcome.session_ids
                session_objects = turn_outcome.session_objects
                new_messages = turn_outcome.new_messages
                print(f"[progress] new_messages={len(new_messages)} sessions={len(session_ids)}")
                if turn_record is not None:
                    turn_record.session_ids = session_ids
                    turn_record.session_objects = session_objects
                    turn_record.new_messages = new_messages
                    turn_record.token_usage = extract_turn_token_usage(response, new_messages)

                # Extract envoi calls from newly observed messages only.
                new_envoi_calls: list[EnvoiCall] = []
                for msg in new_messages:
                    msg_parts = msg.get("parts", [])
                    if isinstance(msg_parts, list):
                        new_envoi_calls.extend(extract_envoi_calls(msg_parts))

                tracker.update(new_envoi_calls)
                for call in new_envoi_calls:
                    if (
                        call.result
                        and call.result.total > 0
                        and call.result.passed == call.result.total
                    ):
                        passing.add(call.path)

                stream_meta = response.get("_stream", {}) if isinstance(response, dict) else {}
                stream_meta_obj = stream_meta if isinstance(stream_meta, dict) else {}
                streamed_parts = int(stream_meta_obj.get("meaningful_parts_seen", 0) or 0)
                part_limit_abort = bool(stream_meta_obj.get("aborted_for_part_limit"))
                observed_parts = count_meaningful_parts(new_messages)

                new_parts = part_count - previous_part_count
                if turn_record is not None:
                    turn_record.session_id = session_id
                    for record in turn_record.parts:
                        record.session_id = session_id
                    if turn_record.parts:
                        last_part_record = turn_record.parts[-1]
                        existing_keys = {
                            tracker._call_key(call) for call in last_part_record.envoi_calls
                        }
                        for call in new_envoi_calls:
                            key = tracker._call_key(call)
                            if key not in existing_keys:
                                last_part_record.envoi_calls.append(call)
                                existing_keys.add(key)
                        last_part_record.testing_state = tracker.snapshot()
                        if turn_record.git_commit is None:
                            turn_record.git_commit = last_part_record.git_commit
                    else:
                        turn_record.git_commit = git_commit
                save_trace_parquet(
                    trajectory_id, agent_trace,
                    environment=environment,
                    task_params=task_params_loaded,
                )

                if evaluation_tasks:
                    print("[eval] waiting for pending commit evaluations before next turn")
                    await _wait_for_evaluations()

                solved_count = len(passing)
                total_count = len(required)
                print(
                    f"[progress] turn={turn_count} commit={git_commit} "
                    f"parts=+{new_parts} total={part_count}/{effective_max_parts} "
                    f"(observed_parts={observed_parts} streamed_parts={streamed_parts}) "
                    f"envoi_calls={len(new_envoi_calls)} "
                    f"solved={solved_count}/{total_count} "
                    f"started={turn_started_at}"
                )

                if required and passing >= required:
                    end_reason = "solved"
                    break

                if part_limit_abort and part_count >= effective_max_parts:
                    end_reason = "part_limit"
                    break

                if part_count >= effective_max_parts:
                    end_reason = "part_limit"
                    break

                # Build re-injection prompt for next turn
                prompt_text = _followup()

            if end_reason is None:
                end_reason = "part_limit"

        except Exception as loop_err:
            print(f"[error] crash during main loop: {loop_err}")
            await dump_sandbox_logs(sb, agent=agent)
            end_reason = "agent_error"
            # Save whatever messages we have
            try:
                if (
                    agent_backend.name == "opencode"
                    and hasattr(
                        agent_backend,
                        "_collect_turn_messages",
                    )
                ):
                    _, _, crash_new_messages = (
                        await agent_backend._collect_turn_messages(
                            session_id,
                        )
                    )
                else:
                    crash_new_messages = []
                if crash_new_messages:
                    crash_record = TurnRecord(
                        trajectory_id=trajectory_id,
                        session_id=session_id,
                        agent=agent,
                        turn=turn_count + 1,
                        part_start=part_count + 1,
                        part_end=part_count,
                        timestamp=datetime.now(UTC).isoformat(),
                        agent_model=resolved_model,
                        prompt=prompt_text,
                        git_commit=await get_git_commit(sb),
                        parts=[],
                    )
                    agent_trace.turns.append(crash_record)
                    save_trace_parquet(
                        trajectory_id, agent_trace,
                        environment=environment,
                        task_params=task_params_loaded,
                    )
                    print(f"[error] saved {len(crash_new_messages)} new messages before crash")
            except Exception:
                print("[error] could not save crash messages")

        # Always end the session and save final state
        if wait_for_evaluations_fn is not None:
            await wait_for_evaluations_fn()
        await end_session(
            sb,
            agent_trace,
            part_count,
            turn_count,
            end_reason,
            environment=environment,
            task_params=task_params_loaded,
        )
        return trajectory_id

    except Exception as e:
        print(f"[error] {e}")
        if sb is not None and agent_trace is not None:
            try:
                if wait_for_evaluations_fn is not None:
                    await wait_for_evaluations_fn()
                await end_session(
                    sb,
                    agent_trace,
                    part_count,
                    turn_count,
                    "agent_error",
                    environment=environment,
                    task_params=task_params_loaded,
                )
            except Exception as end_err:
                print(f"[error] failed to finalize session after exception: {end_err}")
        return trajectory_id
    finally:
        if sb is not None:
            try:
                await sb.terminate()
            except Exception:
                pass

    return trajectory_id


@app.function(
    timeout=14400,
    secrets=[modal.Secret.from_dotenv()],
    image=function_image,
)
async def run_trajectory(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
) -> str:
    return await _run_trajectory_impl(
        agent=agent,
        model=model,
        max_parts=max_parts,
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
async def run_trajectory_non_preemptible(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 14400,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
) -> str:
    return await _run_trajectory_impl(
        agent=agent,
        model=model,
        max_parts=max_parts,
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
    return run_trajectory_non_preemptible


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


@app.local_entrypoint()
async def main(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int = 1000,
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
    if normalized_agent == "codex" and codex_auth_file.strip():
        codex_auth_json_b64 = load_local_codex_auth_json_b64(codex_auth_file.strip())
        if codex_auth_json_b64:
            print(f"[auth] loaded codex auth from {Path(codex_auth_file).expanduser()}")
        else:
            print(f"[auth] no codex auth file found at {Path(codex_auth_file).expanduser()}")

    runner = get_non_preemptible_runner() if non_preemptible else run_trajectory
    try:
        result = await runner.remote.aio(
            agent=normalized_agent,
            model=model,
            max_parts=max_parts,
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


# ---------------------------------------------------------------------------
# Direct execution entry point (for non-Modal sandbox providers)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse as _ap

    from dotenv import load_dotenv

    load_dotenv()

    _parser = _ap.ArgumentParser(description="Run trajectory directly (non-Modal).")
    _parser.add_argument("--agent", default=DEFAULT_AGENT)
    _parser.add_argument("--model", default=None)
    _parser.add_argument("--max-parts", type=int, default=1000)
    _parser.add_argument("--sandbox-provider", default="modal")
    _parser.add_argument("--trajectory-id", default=None)
    _parser.add_argument(
        "--message-timeout-seconds", type=int, default=MESSAGE_TIMEOUT_SECONDS
    )
    _parser.add_argument("--codex-auth-file", default="~/.codex/auth.json")
    _parser.add_argument("--task-dir", required=True)
    _parser.add_argument("--environment-dir", required=True)
    _args = _parser.parse_args()

    _codex_auth_json_b64: str | None = None
    if (_args.agent or DEFAULT_AGENT).strip().lower() == "codex" and _args.codex_auth_file:
        _codex_auth_json_b64 = load_local_codex_auth_json_b64(_args.codex_auth_file.strip())

    asyncio.run(
        _run_trajectory_impl(
            agent=_args.agent,
            model=_args.model,
            max_parts=_args.max_parts,
            message_timeout_seconds=_args.message_timeout_seconds,
            trajectory_id=_args.trajectory_id,
            codex_auth_json_b64=_codex_auth_json_b64,
            sandbox_provider=_args.sandbox_provider,
            task_dir=_args.task_dir,
            environment_dir=_args.environment_dir,
        )
    )
