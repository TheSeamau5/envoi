"""
Main orchestrator for envoi-trace.

Creates a sandbox, provisions an agent (Codex or OpenCode), and runs a turn
loop. After every part, it persists trace.parquet
to S3. After every file change, it creates a git checkpoint. At end-of-run,
it uploads a repo.bundle for the final export commit and logs.parquet for
structured orchestrator/runtime diagnostics.

The two core abstractions are Agent (how to talk to an agent) and
Sandbox (where the agent runs). This file wires them together and
manages the turn loop, resume logic, and artifact persistence.
It has zero knowledge of specific sandbox providers.

Usage (via CLI):
    envoi code --task examples/c_compiler/task --env examples/c_compiler/environment
    envoi code --example examples/c_compiler --max-parts 1000
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import builtins
import copy
import importlib.util
import inspect
import json
import os
import shlex
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from envoi.logging import (
    bind_log_context,
    reset_log_callback,
    reset_log_context,
    set_log_callback,
    update_log_context,
)

from envoi_code.agents.base import Agent, AgentSetupContext
from envoi_code.agents.codex import CodexAgent
from envoi_code.agents.opencode import OpenCodeAgent
from envoi_code.models import (
    AgentTrace,
    EnvoiCall,
    EvalEvent,
    EvalTestResult,
    EvaluationRecord,
    PartRecord,
    SessionEnd,
    TurnRecord,
)
from envoi_code.sandbox import SandboxConfig, create_sandbox
from envoi_code.sandbox.base import Sandbox
from envoi_code.utils.advisor import (
    normalize_advisor_model,
    normalize_thinking_level,
    request_anthropic_advisor,
)
from envoi_code.utils.diagnostics import enrich_evaluation_payload
from envoi_code.utils.evaluation import (
    EVALUATION_CONCURRENCY,
    extract_leaf_paths,
    normalize_test_paths,
    run_commit_evaluation,
    run_workspace_evaluation,
)
from envoi_code.utils.git import get_git_commit
from envoi_code.utils.helpers import (
    load_environment_files,
    tprint,
    truncate_text,
)
from envoi_code.utils.parsing import (
    count_meaningful_parts,
    extract_envoi_calls,
    extract_turn_token_usage,
)
from envoi_code.utils.solve import SolveTracker
from envoi_code.utils.storage import (
    artifact_uri,
    get_bucket,
    get_s3_client,
    load_trace_snapshot,
    save_logs_parquet,
    save_trace_parquet,
    upload_file,
)
from envoi_code.utils.stream import make_stream_part_callback

DEFAULT_AGENT = "codex"
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
MAX_INLINE_TEST_MESSAGE_CHARS = max(
    80, int(os.environ.get("MAX_INLINE_TEST_MESSAGE_CHARS", "220"))
)
FAILED_TEST_FEEDBACK_LIMIT = max(
    1, int(os.environ.get("FAILED_TEST_FEEDBACK_LIMIT", "50"))
)
ADVISOR_TIMEOUT_SECONDS = max(
    30, int(os.environ.get("ADVISOR_TIMEOUT_SECONDS", "180"))
)
LOGS_FLUSH_INTERVAL_SECONDS = max(
    1, int(os.environ.get("LOGS_FLUSH_INTERVAL_SECONDS", "5"))
)
LOGS_FLUSH_BATCH_SIZE = max(
    1, int(os.environ.get("LOGS_FLUSH_BATCH_SIZE", "50"))
)
SHUTDOWN_GRACE_SECONDS = max(
    0, int(os.environ.get("SHUTDOWN_GRACE_SECONDS", "300"))
)
EVALUATOR_DRAIN_TIMEOUT_SECONDS = max(
    0, int(os.environ.get("EVALUATOR_DRAIN_TIMEOUT_SECONDS", "30"))
)


print = tprint

AGENT_BACKENDS: dict[str, type] = {
    "opencode": OpenCodeAgent,
    "codex": CodexAgent,
}

EXAMPLES_DIR = Path(__file__).parent / "examples"
DEFAULT_ENVIRONMENT_DIR = EXAMPLES_DIR / "environments" / "c_compiler"


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


async def load_environment_params(
    environment_dir: Path,
) -> dict[str, Any]:
    """Load optional environment params from environment/params.py."""
    params_file = environment_dir / "params.py"
    if not params_file.exists():
        return {}

    spec = importlib.util.spec_from_file_location(
        "_env_params",
        params_file,
    )
    if spec is None or spec.loader is None:
        return {}

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    params_fn = getattr(mod, "params", None)
    if params_fn is not None:
        value = (
            await params_fn()
            if inspect.iscoroutinefunction(params_fn)
            else params_fn()
        )
        if isinstance(value, dict):
            return value
        raise TypeError("environment params() must return a dict")

    params_const = getattr(mod, "PARAMS", None)
    if isinstance(params_const, dict):
        return params_const

    return {}


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


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------


async def dump_sandbox_logs(
    sandbox: Sandbox,
    *,
    agent: Agent,
    tail: int = 50,
) -> None:
    """Print the tail of agent + envoi logs from the sandbox."""
    for log_file in agent.log_files:
        try:
            _, stdout, _ = (await sandbox.run(
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


def get_trace_last_part(trace: AgentTrace) -> int:
    return max((part.part or 0) for part in trace.parts) if trace.parts else 0


def get_trace_last_turn(trace: AgentTrace) -> int:
    if not trace.turns:
        return 0
    turn_values = [turn.turn for turn in trace.turns if isinstance(turn.turn, int)]
    if turn_values:
        return max(turn_values)
    return len(trace.turns)


def normalize_positive_limit(value: int | None) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def resolve_failed_tests_feedback_limit(value: Any) -> int:
    if isinstance(value, bool):
        return FAILED_TEST_FEEDBACK_LIMIT
    if isinstance(value, int):
        return max(1, value)
    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit():
            return max(1, int(raw))
    return FAILED_TEST_FEEDBACK_LIMIT


def resolve_suite_feedback_priority(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            path = item.strip().lower()
            if not path or path in seen:
                continue
            normalized.append(path)
            seen.add(path)
        if normalized:
            return tuple(normalized)
    return SUITE_FEEDBACK_PRIORITY


def format_progress_counter(
    *,
    name: str,
    current: int,
    limit: int | None,
) -> str:
    if isinstance(limit, int) and limit > 0:
        return f"{name}={current}/{limit}"
    return f"{name}={current}"


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


def is_winning_evaluation(evaluation: EvaluationRecord) -> bool:
    return (
        evaluation.status == "completed"
        and evaluation.total > 0
        and evaluation.passed == evaluation.total
        and not (
            isinstance(evaluation.error, str)
            and evaluation.error.strip()
        )
    )


def first_winning_commit(
    evaluations: dict[str, EvaluationRecord],
) -> tuple[str, EvaluationRecord] | None:
    winner: tuple[str, EvaluationRecord] | None = None
    for commit, evaluation in evaluations.items():
        if not is_winning_evaluation(evaluation):
            continue
        if winner is None:
            winner = (commit, evaluation)
            continue
        _, best = winner
        best_part = best.part if isinstance(best.part, int) else 10**9
        candidate_part = (
            evaluation.part if isinstance(evaluation.part, int) else 10**9
        )
        if candidate_part < best_part:
            winner = (commit, evaluation)
    return winner


def winner_part_number(
    evaluation: EvaluationRecord,
) -> int | None:
    part = evaluation.part
    if isinstance(part, int) and part > 0:
        return part
    return None


def trim_trace_after_part(
    trace: AgentTrace,
    *,
    max_part_inclusive: int,
) -> None:
    if max_part_inclusive <= 0:
        trace.parts = []
        trace.turns = []
        trace.evaluations = {}
        return

    trace.parts = [
        record
        for record in trace.parts
        if isinstance(record.part, int)
        and record.part <= max_part_inclusive
    ]
    kept_parts = {
        record.part
        for record in trace.parts
        if isinstance(record.part, int)
    }

    trimmed_turns: list[TurnRecord] = []
    for turn in trace.turns:
        filtered_turn_parts = [
            record
            for record in turn.parts
            if isinstance(record.part, int)
            and record.part in kept_parts
        ]
        if filtered_turn_parts:
            turn.parts = filtered_turn_parts
            turn.part_start = filtered_turn_parts[0].part
            turn.part_end = filtered_turn_parts[-1].part
            last_commit = filtered_turn_parts[-1].git_commit
            if isinstance(last_commit, str) and last_commit:
                turn.git_commit = last_commit
            trimmed_turns.append(turn)
            continue

        turn_start = turn.part_start if isinstance(turn.part_start, int) else None
        turn_end = turn.part_end if isinstance(turn.part_end, int) else None
        if turn_start is None and turn_end is None:
            continue
        if turn_start is not None and turn_start > max_part_inclusive:
            continue
        if turn_end is not None and turn_end > max_part_inclusive:
            turn.part_end = max_part_inclusive
        trimmed_turns.append(turn)
    trace.turns = trimmed_turns

    trace.evaluations = {
        commit: evaluation
        for commit, evaluation in trace.evaluations.items()
        if (
            not isinstance(evaluation.part, int)
            or evaluation.part <= max_part_inclusive
        )
    }


def apply_winning_projection(
    trace: AgentTrace,
    *,
    winner_commit: str,
    winner_eval: EvaluationRecord,
) -> int | None:
    winner_part = winner_part_number(winner_eval)
    if winner_part is None:
        return None

    trim_trace_after_part(
        trace,
        max_part_inclusive=winner_part,
    )
    if trace.session_end is not None:
        trace.session_end.final_git_commit = winner_commit
        trace.session_end.total_parts = winner_part
    return winner_part


async def checkout_workspace_commit(
    sandbox: Sandbox,
    commit: str,
) -> bool:
    result = await sandbox.run(
        f"cd /workspace && git checkout -q -f {shlex.quote(commit)}",
        quiet=True,
        timeout=60,
    )
    if result.exit_code != 0:
        print(
            "[git] failed to checkout winning commit "
            f"{commit[:10]}: {truncate_text(result.stderr, 400)}"
        )
        return False
    print(f"[git] checked out winning commit {commit[:10]}")
    return True


def find_part_record_by_number(
    trace: AgentTrace,
    part_number: int,
) -> PartRecord | None:
    for record in reversed(trace.parts):
        if record.part == part_number:
            return record
    return None


def append_eval_event_delta(
    trace: AgentTrace,
    event: EvalEvent,
) -> None:
    target = find_part_record_by_number(trace, event.trigger_part)
    if target is None and trace.parts:
        target = trace.parts[-1]
    if target is None:
        return
    target.eval_events_delta.append(event)


def find_latest_completed_turn_end_tests(
    trace: AgentTrace,
) -> list[EvalTestResult] | None:
    for part_record in reversed(trace.parts):
        if not part_record.eval_events_delta:
            continue
        for event in reversed(part_record.eval_events_delta):
            if event.kind != "turn_end_blocking":
                continue
            if event.status != "completed":
                continue
            if not event.tests:
                continue
            return list(event.tests)
    return None


def load_optional_mcp_server(
    task_dir: Path,
    environment_dir: Path,
) -> tuple[str, str | None]:
    """Load an opt-in MCP server script from task/environment folders.

    Convention:
    - task_dir/mcp_server.py
    - environment_dir/mcp_server.py
    """
    candidates = [
        task_dir / "mcp_server.py",
        environment_dir / "mcp_server.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(), str(candidate)
    return "", None


def parse_jsonl_log_records(
    raw_text: str,
    *,
    source: str,
    log_path: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            records.append(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "component": source,
                    "event": "log.parse_error",
                    "level": "error",
                    "message": "Invalid JSON log line",
                    "source": source,
                    "log_path": log_path,
                    "line_no": line_no,
                    "raw": truncate_text(stripped, 500),
                }
            )
            continue
        if isinstance(parsed, dict):
            parsed.setdefault("source", source)
            parsed.setdefault("log_path", log_path)
            parsed.setdefault("line_no", line_no)
            records.append(parsed)
    return records


async def collect_sandbox_structured_logs(
    sandbox: Sandbox,
) -> list[dict[str, Any]]:
    """Collect structured runtime/worker logs emitted inside sandbox /tmp."""
    listing = await sandbox.run(
        "ls -1 /tmp/envoi_*.jsonl 2>/dev/null || true",
        quiet=True,
        timeout=30,
    )
    if listing.exit_code != 0 or not listing.stdout.strip():
        return []

    records: list[dict[str, Any]] = []
    for path in sorted(
        line.strip()
        for line in listing.stdout.splitlines()
        if line.strip()
    ):
        content_result = await sandbox.run(
            f"cat {shlex.quote(path)}",
            quiet=True,
            timeout=60,
        )
        if content_result.exit_code != 0:
            records.append(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "component": "sandbox",
                    "event": "log.read_error",
                    "level": "error",
                    "message": "Failed reading sandbox log file",
                    "source": "sandbox",
                    "log_path": path,
                    "stderr": truncate_text(
                        content_result.stderr or "",
                        600,
                    ),
                }
            )
            continue
        source_name = (
            "runtime" if "runtime" in Path(path).name else "session_worker"
        )
        records.extend(
            parse_jsonl_log_records(
                content_result.stdout,
                source=source_name,
                log_path=path,
            )
        )
    return records


async def restore_workspace_from_bundle(
    *,
    sandbox: Sandbox,
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
    await sandbox.write_file(
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
    exit_code, _, stderr = (await sandbox.run(
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


# ---------------------------------------------------------------------------
# End session
# ---------------------------------------------------------------------------


async def end_session(
    sandbox: Sandbox,
    agent_trace: AgentTrace,
    part_count: int,
    turn_count: int,
    reason: Literal["solved", "part_limit", "timeout", "agent_error", "envoi_error"],
    *,
    environment: str = "",
    task_params: dict[str, Any] | None = None,
    logs_parquet_uri: str | None = None,
    final_commit_hint: str | None = None,
) -> None:
    print(f"[end] reason={reason} parts={part_count}")

    if part_count == 0 and turn_count == 0:
        print("[end] nothing to save (0 parts), skipping S3 upload")
        return

    final_commit = final_commit_hint
    try:
        commit_from_sandbox = await get_git_commit(sandbox)
        if isinstance(commit_from_sandbox, str) and commit_from_sandbox:
            final_commit = commit_from_sandbox
    except Exception as commit_error:
        print(
            "[end] failed to read final git commit from sandbox: "
            f"{commit_error}"
        )
    winner = first_winning_commit(agent_trace.evaluations)
    bundle_export_commit = final_commit
    if winner is not None:
        winner_commit, winner_eval = winner
        bundle_export_commit = winner_commit
        print(
            "[bundle] exporting first winning commit "
            f"{winner_commit[:10]} "
            f"(part={winner_eval.part}, "
            f"score={winner_eval.passed}/{winner_eval.total})"
        )
    bundle_s3_uri: str | None = None

    agent_trace.session_end = SessionEnd(
        reason=reason,
        total_parts=part_count,
        total_turns=turn_count,
        final_git_commit=final_commit,
    )
    trace_parquet_uri = artifact_uri(agent_trace.trajectory_id, "trace.parquet")
    agent_trace.artifacts = {
        "trace_parquet": trace_parquet_uri,
        "repo_bundle": None,
        "logs_parquet": logs_parquet_uri,
    }
    # Persist session_end before any sandbox-dependent export steps.
    if environment:
        save_trace_parquet(
            agent_trace.trajectory_id, agent_trace,
            environment=environment, task_params=task_params,
        )

    # Upload git bundle
    try:
        bundle_target = (
            bundle_export_commit.strip()
            if isinstance(bundle_export_commit, str)
            and bundle_export_commit.strip()
            else "HEAD"
        )
        bundle_ref = "__envoi_bundle_export__"
        bundle_cmd = (
            "set -euo pipefail\n"
            f"git branch -f {bundle_ref} "
            f"{shlex.quote(bundle_target)}\n"
            "cleanup() {\n"
            f"  git branch -D {bundle_ref} >/dev/null 2>&1 || true\n"
            "}\n"
            "trap cleanup EXIT\n"
            f"git bundle create /tmp/repo.bundle "
            f"refs/heads/{bundle_ref}\n"
        )
        exit_code, _, _ = (await sandbox.run(
            bundle_cmd,
            quiet=True,
            cwd="/workspace",
        )).unpack()
        _, size_out, _ = (await sandbox.run(
            "stat -c %s /tmp/repo.bundle 2>/dev/null || echo 0",
            quiet=True,
        )).unpack()
        bundle_size = int(size_out.strip() or "0")
        print(f"[bundle] size={bundle_size} bytes")

        if bundle_size > 0:
            _, b64, _ = (await sandbox.run("base64 /tmp/repo.bundle", quiet=True)).unpack()
            data = base64.b64decode(b64.strip())
            bundle_s3_uri = upload_file(agent_trace.trajectory_id, "repo.bundle", data)
            print(f"[bundle] uploaded ({len(data)} bytes)")
    except Exception as e:
        print(f"[bundle] failed: {e}")

    agent_trace.artifacts = {
        "trace_parquet": trace_parquet_uri,
        "repo_bundle": bundle_s3_uri,
        "logs_parquet": logs_parquet_uri,
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
# Evaluation scheduler
# ---------------------------------------------------------------------------


class EvaluationScheduler:
    """Manages async commit evaluations during a trajectory run."""

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        agent_trace: AgentTrace,
        trajectory_id: str,
        environment: str,
        task_params: dict[str, Any] | None,
        test_paths: list[str] | None = None,
        test_timeout_seconds: int | None = None,
        should_stop: Callable[[], bool] | None = None,
        on_winner: (
            Callable[[str, EvaluationRecord], Awaitable[None] | None]
            | None
        ) = None,
    ) -> None:
        self.sandbox = sandbox
        self.agent_trace = agent_trace
        self.trajectory_id = trajectory_id
        self.environment = environment
        self.task_params = task_params
        self.test_paths = normalize_test_paths(test_paths)
        self.test_timeout_seconds = test_timeout_seconds
        self.should_stop = should_stop
        self.on_winner = on_winner
        self.tasks: set[asyncio.Task[None]] = set()
        self.seen_commits: set[str] = set(agent_trace.evaluations.keys())
        self.semaphore = asyncio.Semaphore(EVALUATION_CONCURRENCY)

        for evaluation in agent_trace.evaluations.values():
            if evaluation.status in {"queued", "running"}:
                evaluation.status = "failed"
                evaluation.error = "Interrupted before evaluation completed"
                evaluation.completed_at = datetime.now(UTC).isoformat()
                self.emit_event(evaluation)

    @property
    def has_pending(self) -> bool:
        return bool(self.tasks)

    def save(self) -> None:
        save_trace_parquet(
            self.trajectory_id, self.agent_trace,
            environment=self.environment,
            task_params=self.task_params,
        )

    @staticmethod
    def str_or_none(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def int_or_none(value: Any) -> int | None:
        return value if isinstance(value, int) else None

    @staticmethod
    def normalize_tests(value: Any) -> list[EvalTestResult]:
        tests: list[EvalTestResult] = []
        if not isinstance(value, list):
            return tests
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                tests.append(EvalTestResult.model_validate(item))
            except Exception:
                continue
        return tests

    @staticmethod
    def to_event(evaluation: EvaluationRecord) -> EvalEvent:
        return EvalEvent(
            eval_id=evaluation.eval_id,
            kind="commit_async",
            trigger_part=evaluation.part,
            trigger_turn=evaluation.trigger_turn or 0,
            target_commit=evaluation.commit,
            queued_at=evaluation.queued_at,
            started_at=evaluation.started_at,
            finished_at=evaluation.completed_at,
            status=evaluation.status,
            passed=evaluation.passed,
            failed=evaluation.failed,
            total=evaluation.total,
            payload=dict(evaluation.payload),
            suite_results=evaluation.suite_results,
            tests=list(evaluation.tests),
            error=evaluation.error,
        )

    def emit_event(self, evaluation: EvaluationRecord) -> None:
        append_eval_event_delta(
            self.agent_trace,
            self.to_event(evaluation),
        )
        self.save()

    @staticmethod
    def apply_result(
        evaluation: EvaluationRecord,
        run_payload: dict[str, Any],
    ) -> None:
        """Apply a successful run_commit_evaluation result."""
        payload = run_payload.get("payload")
        evaluation.command = EvaluationScheduler.str_or_none(
            run_payload.get("command"),
        )
        evaluation.exit_code = EvaluationScheduler.int_or_none(
            run_payload.get("exit_code"),
        )
        evaluation.stdout = EvaluationScheduler.str_or_none(
            run_payload.get("stdout"),
        )
        evaluation.stderr = EvaluationScheduler.str_or_none(
            run_payload.get("stderr"),
        )

        if (
            isinstance(evaluation.exit_code, int)
            and evaluation.exit_code != 0
        ):
            evaluation.status = "failed"
            evaluation.error = (
                "Evaluation command failed with exit code "
                f"{evaluation.exit_code}"
            )
            evaluation.passed = 0
            evaluation.failed = 0
            evaluation.total = 0
            evaluation.payload = {}
            evaluation.suite_results = {}
            evaluation.tests = []
        elif not isinstance(payload, dict):
            evaluation.status = "failed"
            evaluation.error = (
                "Missing evaluation payload in command output"
            )
            evaluation.passed = 0
            evaluation.failed = 0
            evaluation.total = 0
            evaluation.payload = {}
            evaluation.suite_results = {}
            evaluation.tests = []
        else:
            evaluation.status = "completed"
            evaluation.error = (
                payload.get("error")
                if isinstance(payload.get("error"), str)
                else None
            )
            evaluation.duration_ms = int(
                payload.get("duration_ms", 0) or 0,
            )
            evaluation.passed = int(payload.get("passed", 0) or 0)
            evaluation.failed = int(payload.get("failed", 0) or 0)
            evaluation.total = int(payload.get("total", 0) or 0)
            evaluation.payload = payload
            suite_results = payload.get("suite_results")
            evaluation.suite_results = (
                suite_results if isinstance(suite_results, dict) else {}
            )
            evaluation.tests = EvaluationScheduler.normalize_tests(
                payload.get("tests"),
            )

    @staticmethod
    def apply_failure(
        evaluation: EvaluationRecord,
        error: Exception,
        run_payload: dict[str, Any] | None,
    ) -> None:
        """Apply an exception result, salvaging partial output."""
        evaluation.status = "failed"
        evaluation.error = str(error)
        evaluation.passed = 0
        evaluation.failed = 0
        evaluation.total = 0
        evaluation.payload = {}
        evaluation.suite_results = {}
        evaluation.tests = []
        if run_payload is not None:
            evaluation.command = EvaluationScheduler.str_or_none(
                run_payload.get("command"),
            )
            evaluation.exit_code = EvaluationScheduler.int_or_none(
                run_payload.get("exit_code"),
            )
            evaluation.stdout = EvaluationScheduler.str_or_none(
                run_payload.get("stdout"),
            )
            evaluation.stderr = EvaluationScheduler.str_or_none(
                run_payload.get("stderr"),
            )

    def schedule(
        self,
        commit: str,
        part: int,
        turn: int,
    ) -> None:
        if self.should_stop is not None and self.should_stop():
            return
        if commit in self.seen_commits:
            return
        self.seen_commits.add(commit)
        queued_at = datetime.now(UTC).isoformat()
        print(f"[eval] queued commit {commit[:10]} from part {part}")
        evaluation = EvaluationRecord(
            eval_id=uuid.uuid4().hex,
            commit=commit,
            part=part,
            trigger_turn=turn,
            status="queued",
            queued_at=queued_at,
        )
        self.agent_trace.evaluations[commit] = evaluation
        self.emit_event(evaluation)
        task = asyncio.create_task(
            self.run_one(commit, part, turn, queued_at),
        )
        self.tasks.add(task)
        task.add_done_callback(self.on_done)

    async def run_one(
        self,
        commit: str,
        part: int,
        turn: int,
        queued_at: str,
    ) -> None:
        evaluation = self.agent_trace.evaluations.get(commit)
        if evaluation is None:
            evaluation = EvaluationRecord(
                eval_id=uuid.uuid4().hex,
                commit=commit,
                part=part,
                trigger_turn=turn,
                status="queued",
                queued_at=queued_at,
            )
            self.agent_trace.evaluations[commit] = evaluation
        evaluation.status = "running"
        evaluation.started_at = datetime.now(UTC).isoformat()
        self.emit_event(evaluation)

        async with self.semaphore:
            run_payload: dict[str, Any] | None = None
            started_mono = time.monotonic()
            try:
                run_payload = await run_commit_evaluation(
                    sandbox=self.sandbox,
                    commit=commit,
                    test_paths=self.test_paths,
                    timeout_seconds=self.test_timeout_seconds,
                )
                self.apply_result(evaluation, run_payload)
            except Exception as eval_error:
                self.apply_failure(
                    evaluation, eval_error, run_payload,
                )
            finally:
                if evaluation.duration_ms is None:
                    evaluation.duration_ms = int(
                        (time.monotonic() - started_mono) * 1000,
                    )
                evaluation.completed_at = datetime.now(UTC).isoformat()
                if (
                    evaluation.status == "completed"
                    and evaluation.total == 0
                    and not evaluation.error
                ):
                    print(
                        f"[eval] commit {commit[:10]} status=no_tests"
                    )
                else:
                    print(
                        f"[eval] commit {commit[:10]} "
                        f"status={evaluation.status} "
                        f"passed={evaluation.passed}/{evaluation.total}"
                    )
                self.emit_event(evaluation)
                if (
                    is_winning_evaluation(evaluation)
                    and self.on_winner is not None
                ):
                    callback_result = self.on_winner(
                        commit, evaluation,
                    )
                    if inspect.isawaitable(callback_result):
                        await callback_result

    def on_done(self, done_task: asyncio.Task[None]) -> None:
        self.tasks.discard(done_task)
        try:
            done_task.result()
        except asyncio.CancelledError:
            return
        except Exception as task_error:
            print(f"[eval] unexpected task error: {task_error}")

    async def wait(self) -> None:
        while self.tasks:
            pending = list(self.tasks)
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)

    async def cancel_pending(self, *, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        for evaluation in self.agent_trace.evaluations.values():
            if evaluation.status == "queued":
                evaluation.status = "failed"
                if not evaluation.error:
                    evaluation.error = reason
                if evaluation.completed_at is None:
                    evaluation.completed_at = now
                if evaluation.passed is None:
                    evaluation.passed = 0
                if evaluation.failed is None:
                    evaluation.failed = 0
                if evaluation.total is None:
                    evaluation.total = 0
                self.emit_event(evaluation)
                continue
            if evaluation.status == "running":
                evaluation.status = "failed"
                if not evaluation.error:
                    evaluation.error = reason

        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# Main trajectory implementation
# ---------------------------------------------------------------------------


def build_followup_prompt(
    tracker: SolveTracker,
    evaluation_feedback: str | None = None,
    continue_prompt: str = "Continue.",
    include_mcp_status: bool = False,
) -> str:
    """Build the re-injection prompt with current test status."""
    sections: list[str] = [continue_prompt]
    if evaluation_feedback:
        sections.append(
            "End-of-turn evaluation feedback:\n"
            + evaluation_feedback
        )
    if include_mcp_status:
        status = build_unsolved_status_lines(tracker)
        if status:
            sections.append(
                "Current test status:\n" + "\n".join(status)
            )
    return "\n\n".join(section for section in sections if section)


SUITE_FEEDBACK_PRIORITY: tuple[str, ...] = ()
CURRENT_SUITE_FEEDBACK_PRIORITY: tuple[str, ...] = (
    SUITE_FEEDBACK_PRIORITY
)


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _normalize_suite_path(value: str | None) -> str:
    if not value:
        return ""
    parts = [part for part in value.split("/") if part]
    if not parts:
        return ""
    normalized = [parts[0]]
    for part in parts[1:]:
        if part == normalized[-1]:
            continue
        normalized.append(part)
    return "/".join(normalized)


def _suite_family(suite_path: str) -> str | None:
    normalized = _normalize_suite_path(suite_path)
    for family in CURRENT_SUITE_FEEDBACK_PRIORITY:
        if normalized == family:
            return family
        if normalized.startswith(f"{family}/"):
            return family
        if f"/{family}/" in f"/{normalized}/":
            return family
    return None


def _suite_rank(suite_path: str) -> tuple[int, int, str]:
    normalized = _normalize_suite_path(suite_path)
    family = _suite_family(normalized)
    if family in CURRENT_SUITE_FEEDBACK_PRIORITY:
        return (
            CURRENT_SUITE_FEEDBACK_PRIORITY.index(family),
            0,
            normalized,
        )
    if normalized.endswith("/run_all") or normalized == "all/run_all":
        return (
            len(CURRENT_SUITE_FEEDBACK_PRIORITY),
            1,
            normalized,
        )
    return (
        len(CURRENT_SUITE_FEEDBACK_PRIORITY),
        0,
        normalized,
    )


def format_suite_feedback_priority(priority: tuple[str, ...]) -> str:
    if not priority:
        return "none"
    return " -> ".join(priority)


def _test_sort_key(test: dict[str, Any]) -> tuple[int, int, str, str]:
    suite = _normalize_suite_path(_string_or_none(test.get("suite")))
    test_id = _string_or_none(test.get("test_id")) or ""
    family_rank, run_all_rank, suite_rank = _suite_rank(suite)
    return (family_rank, run_all_rank, suite_rank, test_id)


def select_failed_tests_for_feedback(
    payload: dict[str, Any],
    *,
    limit: int = FAILED_TEST_FEEDBACK_LIMIT,
) -> list[dict[str, Any]]:
    raw_tests = payload.get("tests")
    if not isinstance(raw_tests, list) or not raw_tests:
        return []

    failed_tests: list[dict[str, Any]] = []
    for item in raw_tests:
        if not isinstance(item, dict):
            continue
        status = _string_or_none(item.get("status")) or "failed"
        if status.lower() == "passed":
            continue
        failed_tests.append(item)

    if not failed_tests:
        return []

    failed_tests.sort(key=_test_sort_key)

    selected: list[dict[str, Any]] = []
    seen_family_keys: set[tuple[str, str]] = set()
    seen_suite_test_keys: set[tuple[str, str]] = set()
    for test in failed_tests:
        suite = _normalize_suite_path(_string_or_none(test.get("suite")))
        family = _suite_family(suite)
        test_id = _string_or_none(test.get("test_id")) or "unknown_test"

        if family is not None:
            family_key = (family, test_id)
            if family_key in seen_family_keys:
                continue
            seen_family_keys.add(family_key)
        else:
            suite_test_key = (suite, test_id)
            if suite_test_key in seen_suite_test_keys:
                continue
            seen_suite_test_keys.add(suite_test_key)

        selected.append(test)
        if len(selected) >= max(1, limit):
            break
    return selected


def _format_single_failed_test(
    index: int,
    test: dict[str, Any],
) -> str:
    suite = _normalize_suite_path(_string_or_none(test.get("suite"))) or "unknown_suite"
    test_id = _string_or_none(test.get("test_id")) or "unknown_test"
    status = (_string_or_none(test.get("status")) or "failed").lower()
    failure_type = _string_or_none(test.get("failure_type"))
    label = f"{status}/{failure_type}" if failure_type else status
    message = _string_or_none(test.get("message"))
    if message is None:
        message = _string_or_none(test.get("stderr_tail"))
    if message is None:
        message = _string_or_none(test.get("stdout_tail"))
    source = _string_or_none(test.get("source"))

    lines = [
        f"{index}. {suite}/{test_id}",
        f"status: {label}",
    ]
    if message is not None:
        lines.append("error:")
        lines.append(message)
    rendered_diagnostic = _string_or_none(
        test.get("rendered_diagnostic"),
    )
    if rendered_diagnostic is not None:
        lines.extend([
            "diagnostic:",
            "```text",
            rendered_diagnostic,
            "```",
        ])
    if source is not None:
        lines.extend([
            "source:",
            "```c",
            source,
            "```",
        ])
    else:
        lines.append("source: (missing)")
    return "\n".join(lines)


def build_cluster_summary_section(
    payload: dict[str, Any],
    *,
    limit: int = 8,
) -> str:
    raw_clusters = payload.get("diagnostic_clusters")
    if not isinstance(raw_clusters, list) or not raw_clusters:
        return "diagnostic_clusters: 0"

    lines = [f"diagnostic_clusters: {len(raw_clusters)} (top {max(1, limit)})"]
    for cluster in raw_clusters[: max(1, limit)]:
        if not isinstance(cluster, dict):
            continue
        key = _string_or_none(cluster.get("key")) or "unknown"
        kind = _string_or_none(cluster.get("kind")) or "unknown_kind"
        code = _string_or_none(cluster.get("code"))
        count_value = cluster.get("count")
        count = count_value if isinstance(count_value, int) else 0
        suffix = f" code={code}" if code else ""
        lines.append(
            f"- {kind}{suffix}: count={count} key={key}"
        )
        samples = cluster.get("sample_tests")
        if isinstance(samples, list) and samples:
            rendered_samples = ", ".join(
                sample for sample in samples
                if isinstance(sample, str) and sample
            )
            if rendered_samples:
                lines.append(f"  samples: {rendered_samples}")
    return "\n".join(lines)


def build_failed_tests_feedback_section(
    payload: dict[str, Any],
    *,
    limit: int = FAILED_TEST_FEEDBACK_LIMIT,
) -> tuple[str, list[dict[str, Any]]]:
    selected = select_failed_tests_for_feedback(payload, limit=limit)
    if not selected:
        return "top_failed_tests_with_source: 0", []

    lines = [
        "Top failed tests with source "
        f"(prioritized: {format_suite_feedback_priority(CURRENT_SUITE_FEEDBACK_PRIORITY)}):",
        f"count: {len(selected)} (limit={max(1, limit)})",
    ]
    for idx, test in enumerate(selected, start=1):
        lines.append("")
        lines.append(_format_single_failed_test(idx, test))
    return "\n".join(lines), selected


async def collect_commit_code_snapshot(
    sandbox: Sandbox,
    *,
    commit: str | None,
    max_files: int = 80,
    max_total_chars: int = 220_000,
    max_file_chars: int = 24_000,
) -> dict[str, Any]:
    commit_json = json.dumps(commit or "")
    script = (
        "import json\n"
        "import subprocess\n"
        "from pathlib import Path\n"
        f"commit = {commit_json}\n"
        f"max_files = {int(max_files)}\n"
        f"max_total_chars = {int(max_total_chars)}\n"
        f"max_file_chars = {int(max_file_chars)}\n"
        "allow_suffixes = {\n"
        "    '.rs', '.py', '.c', '.h', '.cpp', '.hpp', '.toml', '.json',\n"
        "    '.yaml', '.yml', '.sh', '.md', '.txt', '.mk'\n"
        "}\n"
        "allow_names = {\n"
        "    'Cargo.toml', 'Cargo.lock', 'Makefile', 'Dockerfile',\n"
        "    'build.sh', 'README.md'\n"
        "}\n"
        "exclude_prefixes = (\n"
        "    '.git/', 'target/', 'debug_artifacts/', '.codex/', '.opencode/'\n"
        ")\n"
        "result = {\n"
        "    'commit': commit or None,\n"
        "    'files': [],\n"
        "    'truncated': False,\n"
        "    'total_chars': 0,\n"
        "}\n"
        "cmd = ['git', 'ls-tree', '-r', '--name-only', commit] if commit else ['git', 'ls-files']\n"
        "proc = subprocess.run(cmd, check=False, capture_output=True, text=True)\n"
        "if proc.returncode != 0:\n"
        "    print(json.dumps(result, ensure_ascii=False))\n"
        "    raise SystemExit(0)\n"
        "paths = [line.strip() for line in proc.stdout.splitlines() if line.strip()]\n"
        "for path in paths:\n"
        "    if path.startswith(exclude_prefixes):\n"
        "        continue\n"
        "    p = Path(path)\n"
        "    if p.name not in allow_names and p.suffix.lower() not in allow_suffixes:\n"
        "        continue\n"
        "    source = None\n"
        "    if commit:\n"
        "        show = subprocess.run(\n"
        "            ['git', 'show', f'{commit}:{path}'],\n"
        "            check=False,\n"
        "            capture_output=True,\n"
        "            text=False,\n"
        "        )\n"
        "        if show.returncode != 0:\n"
        "            continue\n"
        "        raw = show.stdout\n"
        "    else:\n"
        "        try:\n"
        "            raw = p.read_bytes()\n"
        "        except OSError:\n"
        "            continue\n"
        "    if b'\\x00' in raw:\n"
        "        continue\n"
        "    source = raw.decode('utf-8', errors='replace')\n"
        "    if len(source) > max_file_chars:\n"
        "        source = source[:max_file_chars]\n"
        "        result['truncated'] = True\n"
        "    if result['total_chars'] + len(source) > max_total_chars:\n"
        "        remaining = max_total_chars - result['total_chars']\n"
        "        if remaining <= 0:\n"
        "            result['truncated'] = True\n"
        "            break\n"
        "        source = source[:remaining]\n"
        "        result['truncated'] = True\n"
        "    result['files'].append({'path': path, 'source': source})\n"
        "    result['total_chars'] += len(source)\n"
        "    if len(result['files']) >= max_files:\n"
        "        result['truncated'] = True\n"
        "        break\n"
        "print(json.dumps(result, ensure_ascii=False))\n"
    )
    cmd = "python3 - <<'PY'\n" + script + "PY\n"
    output = await sandbox.run(cmd, timeout=40, quiet=True)
    if output.exit_code != 0:
        return {
            "commit": commit,
            "files": [],
            "truncated": False,
            "total_chars": 0,
        }
    try:
        parsed = json.loads(output.stdout.strip() or "{}")
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        return {
            "commit": commit,
            "files": [],
            "truncated": False,
            "total_chars": 0,
        }
    files = parsed.get("files")
    if not isinstance(files, list):
        parsed["files"] = []
    return parsed


def build_advisor_user_prompt(
    *,
    task_prompt: str,
    commit: str | None,
    selected_failed_tests: list[dict[str, Any]],
    diagnostic_clusters: list[dict[str, Any]],
    code_snapshot: dict[str, Any],
) -> str:
    lines: list[str] = [
        "You are reviewing a Rust C-compiler implementation after an evaluation run.",
        "",
        "Goal task prompt:",
        task_prompt,
        "",
        f"Evaluated commit: {commit or 'unknown'}",
        "",
        "Top diagnostic clusters:",
    ]
    if diagnostic_clusters:
        for cluster in diagnostic_clusters[:10]:
            if not isinstance(cluster, dict):
                continue
            key = _string_or_none(cluster.get("key")) or "unknown"
            kind = _string_or_none(cluster.get("kind")) or "unknown_kind"
            code = _string_or_none(cluster.get("code"))
            count_value = cluster.get("count")
            count = count_value if isinstance(count_value, int) else 0
            code_suffix = f" code={code}" if code else ""
            lines.append(f"- {kind}{code_suffix}: count={count} key={key}")
    else:
        lines.append("- none")

    lines.extend([
        "",
        "Selected failing tests (with full source):",
    ])
    for idx, test in enumerate(selected_failed_tests, start=1):
        lines.append("")
        lines.append(_format_single_failed_test(idx, test))

    files = code_snapshot.get("files")
    if isinstance(files, list) and files:
        lines.extend(["", "Relevant commit code snapshot:"])
        for file_info in files:
            if not isinstance(file_info, dict):
                continue
            path = _string_or_none(file_info.get("path")) or "unknown"
            source = _string_or_none(file_info.get("source")) or ""
            lines.extend([
                "",
                f"file: {path}",
                "```",
                source,
                "```",
            ])
    return "\n".join(lines).strip()


async def build_advisor_assessment(
    *,
    sandbox: Sandbox,
    task_prompt: str,
    commit: str | None,
    payload: dict[str, Any],
    advisor_model: str,
    advisor_model_thinking_level: str,
    failed_tests_limit: int,
) -> str:
    payload_for_feedback = enrich_evaluation_payload(
        copy.deepcopy(payload),
    )
    selected_failed_tests = select_failed_tests_for_feedback(
        payload_for_feedback,
        limit=failed_tests_limit,
    )
    if not selected_failed_tests:
        return "Advisor assessment: no failing tests available."

    code_snapshot = await collect_commit_code_snapshot(
        sandbox,
        commit=commit,
    )
    clusters = payload_for_feedback.get("diagnostic_clusters")
    diagnostic_clusters = (
        clusters
        if isinstance(clusters, list)
        else []
    )
    snapshot_files = code_snapshot.get("files")
    snapshot_file_count = len(snapshot_files) if isinstance(snapshot_files, list) else 0
    snapshot_total_chars = code_snapshot.get("total_chars")
    print(
        "[advisor] assessment_prepare "
        f"commit={(commit or 'unknown')[:12]} "
        f"failed_tests={len(selected_failed_tests)} "
        f"clusters={len(diagnostic_clusters) if isinstance(diagnostic_clusters, list) else 0} "
        f"snapshot_files={snapshot_file_count} "
        f"snapshot_chars={snapshot_total_chars}"
    )
    user_prompt = build_advisor_user_prompt(
        task_prompt=task_prompt,
        commit=commit,
        selected_failed_tests=selected_failed_tests,
        diagnostic_clusters=diagnostic_clusters,
        code_snapshot=code_snapshot,
    )
    system_prompt = (
        "You are a strict compiler engineering reviewer. "
        "Given failed tests and current Rust code, identify the most likely "
        "root causes, cluster recurring error patterns, and propose a "
        "prioritized fix plan with concrete file-level changes."
    )
    assessment = await request_anthropic_advisor(
        model_spec=advisor_model,
        thinking_level=advisor_model_thinking_level,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout_seconds=ADVISOR_TIMEOUT_SECONDS,
    )
    print(
        "[advisor] assessment_ready "
        f"commit={(commit or 'unknown')[:12]} "
        f"chars={len(assessment)}"
    )
    return (
        "External assessment "
        f"({advisor_model}, thinking={advisor_model_thinking_level}):\n"
        + assessment.strip()
    )


def _eval_result_key(test: EvalTestResult) -> tuple[str, str]:
    suite = _normalize_suite_path(_string_or_none(test.suite) or "")
    test_id = _string_or_none(test.test_id) or "unknown_test"
    return suite, test_id


def _eval_result_sort_key(test: EvalTestResult) -> tuple[int, int, str, str]:
    suite = _normalize_suite_path(_string_or_none(test.suite) or "")
    test_id = _string_or_none(test.test_id) or ""
    family_rank, run_all_rank, suite_rank = _suite_rank(suite)
    return (family_rank, run_all_rank, suite_rank, test_id)


def _eval_result_is_passed(test: EvalTestResult) -> bool:
    return (test.status or "").strip().lower() == "passed"


def _eval_result_ref(test: EvalTestResult) -> str:
    suite = _normalize_suite_path(_string_or_none(test.suite) or "")
    test_id = _string_or_none(test.test_id) or "unknown_test"
    if suite:
        return f"{suite}/{test_id}"
    return test_id


def _eval_result_message(test: EvalTestResult) -> str | None:
    return (
        _string_or_none(test.message)
        or _string_or_none(test.stderr_tail)
        or _string_or_none(test.stdout_tail)
    )


def build_turn_regression_feedback_section(
    *,
    current_tests: list[EvalTestResult],
    previous_tests: list[EvalTestResult] | None,
    limit: int = 10,
) -> str:
    if previous_tests is None:
        return (
            "regressions_vs_previous_turn_end: unavailable "
            "(no previous turn-end snapshot)"
        )
    if not current_tests:
        return (
            "regressions_vs_previous_turn_end: unavailable "
            "(current turn-end test details missing)"
        )

    prev_by_key: dict[tuple[str, str], EvalTestResult] = {
        _eval_result_key(test): test for test in previous_tests
    }
    cur_by_key: dict[tuple[str, str], EvalTestResult] = {
        _eval_result_key(test): test for test in current_tests
    }

    prev_passed = sum(
        1 for test in prev_by_key.values()
        if _eval_result_is_passed(test)
    )
    cur_passed = sum(
        1 for test in cur_by_key.values()
        if _eval_result_is_passed(test)
    )

    newly_broken = [
        cur_by_key[key]
        for key, prev in prev_by_key.items()
        if key in cur_by_key
        and _eval_result_is_passed(prev)
        and not _eval_result_is_passed(cur_by_key[key])
    ]
    newly_fixed = [
        cur_by_key[key]
        for key, prev in prev_by_key.items()
        if key in cur_by_key
        and not _eval_result_is_passed(prev)
        and _eval_result_is_passed(cur_by_key[key])
    ]
    still_failing = sum(
        1 for test in cur_by_key.values()
        if not _eval_result_is_passed(test)
    )
    total_delta = cur_passed - prev_passed

    lines = [
        "regressions_vs_previous_turn_end:",
        f"- baseline_tests: {len(prev_by_key)}",
        f"- current_tests: {len(cur_by_key)}",
        f"- passed_delta: {total_delta} ({prev_passed}->{cur_passed})",
        f"- newly_broken: {len(newly_broken)}",
        f"- newly_fixed: {len(newly_fixed)}",
        f"- currently_failing: {still_failing}",
    ]

    if newly_broken:
        lines.append("- newly_broken_top:")
        sorted_broken = sorted(
            newly_broken,
            key=_eval_result_sort_key,
        )
        for index, test in enumerate(
            sorted_broken[: max(1, limit)],
            start=1,
        ):
            failure_type = _string_or_none(test.failure_type)
            status_suffix = (
                f"/{failure_type}" if failure_type else ""
            )
            lines.append(
                f"  {index}. {_eval_result_ref(test)}: "
                f"passed -> {test.status}{status_suffix}"
            )
            message = _eval_result_message(test)
            if message is not None:
                lines.append(
                    "     error: "
                    + truncate_text(
                        message,
                        limit=MAX_INLINE_TEST_MESSAGE_CHARS,
                    )
                )
    return "\n".join(lines)


def format_turn_end_evaluation_feedback(
    run_payload: dict[str, Any],
    *,
    failed_tests_limit: int = FAILED_TEST_FEEDBACK_LIMIT,
    advisor_assessment: str | None = None,
    previous_turn_end_tests: list[EvalTestResult] | None = None,
) -> str:
    """Render compact, actionable turn-end evaluation feedback."""
    payload = run_payload.get("payload")
    feedback_payload = (
        enrich_evaluation_payload(copy.deepcopy(payload))
        if isinstance(payload, dict)
        else None
    )
    exit_code = run_payload.get("exit_code")

    lines: list[str] = ["Turn-end full evaluation result:"]
    if isinstance(exit_code, int):
        lines.append(f"exit_code: {exit_code}")

    def _truncate(value: str | None, limit: int) -> str | None:
        if value is None:
            return None
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + " ..."

    if isinstance(payload, dict):
        passed = int(payload.get("passed", 0) or 0)
        failed = int(payload.get("failed", 0) or 0)
        total = int(payload.get("total", 0) or 0)
        duration_ms = int(payload.get("duration_ms", 0) or 0)
        current_tests = normalize_eval_tests(payload)
        lines.append(
            "summary: "
            f"passed={passed} failed={failed} total={total} "
            f"duration_ms={duration_ms}"
        )
        suite_results = payload.get("suite_results")
        if isinstance(suite_results, dict) and suite_results:
            lines.append("suites:")
            for suite_name in sorted(suite_results):
                suite_payload = suite_results.get(suite_name)
                if not isinstance(suite_payload, dict):
                    continue
                suite_passed = int(suite_payload.get("passed", 0) or 0)
                suite_total = int(suite_payload.get("total", 0) or 0)
                lines.append(
                    f"- {suite_name}: {suite_passed}/{suite_total}"
                )
        lines.append(
            build_turn_regression_feedback_section(
                current_tests=current_tests,
                previous_tests=previous_turn_end_tests,
            )
        )

        cluster_payload = (
            feedback_payload
            if isinstance(feedback_payload, dict)
            else payload
        )
        lines.append(build_cluster_summary_section(cluster_payload))

        failed_section, selected_failed_tests = build_failed_tests_feedback_section(
            cluster_payload,
            limit=failed_tests_limit,
        )
        lines.append(failed_section)
        lines.append(
            "failed_tests_selected: "
            f"{len(selected_failed_tests)}"
        )

        payload_error = _string_or_none(payload.get("error"))
        if payload_error is not None:
            lines.append(f"error: {payload_error}")

        if advisor_assessment:
            lines.extend([
                "",
                advisor_assessment.strip(),
            ])
    else:
        lines.append("payload: null")
        stdout = _string_or_none(run_payload.get("stdout"))
        stderr = _string_or_none(run_payload.get("stderr"))
        if stdout is not None:
            lines.append(
                "stdout: "
                + (_truncate(stdout, MAX_INLINE_TEST_MESSAGE_CHARS) or "")
            )
        if stderr is not None:
            lines.append(
                "stderr: "
                + (_truncate(stderr, MAX_INLINE_TEST_MESSAGE_CHARS) or "")
            )
        if advisor_assessment:
            lines.extend([
                "",
                advisor_assessment.strip(),
            ])

    return "\n".join(lines).strip()


def normalize_eval_tests(payload: dict[str, Any]) -> list[EvalTestResult]:
    tests: list[EvalTestResult] = []
    raw_tests = payload.get("tests")
    if not isinstance(raw_tests, list):
        return tests
    for item in raw_tests:
        if not isinstance(item, dict):
            continue
        try:
            tests.append(EvalTestResult.model_validate(item))
        except Exception:
            continue
    return tests


def build_turn_end_eval_event(
    *,
    turn: int,
    part: int,
    commit: str | None,
    run_payload: dict[str, Any] | None,
    error: str | None = None,
) -> EvalEvent:
    payload = (
        run_payload.get("payload")
        if isinstance(run_payload, dict)
        else None
    )
    exit_code = (
        run_payload.get("exit_code")
        if isinstance(run_payload, dict)
        else None
    )
    status = "failed"
    passed = 0
    failed = 0
    total = 0
    event_payload: dict[str, Any] = {}
    suite_results: dict[str, Any] = {}
    tests: list[EvalTestResult] = []
    event_error = error
    if isinstance(payload, dict):
        passed = int(payload.get("passed", 0) or 0)
        failed = int(payload.get("failed", 0) or 0)
        total = int(payload.get("total", 0) or 0)
        event_payload = payload
        suite_payload = payload.get("suite_results")
        if isinstance(suite_payload, dict):
            suite_results = suite_payload
        tests = normalize_eval_tests(payload)
        payload_error = payload.get("error")
        if (
            event_error is None
            and isinstance(payload_error, str)
            and payload_error.strip()
        ):
            event_error = payload_error.strip()
        status = "completed"
    if isinstance(exit_code, int) and exit_code != 0:
        if event_error is None:
            event_error = (
                "Turn-end evaluation command failed "
                f"with exit code {exit_code}"
            )
        status = "failed"
    if event_error is not None:
        status = "failed"
    now_iso = datetime.now(UTC).isoformat()
    return EvalEvent(
        eval_id=uuid.uuid4().hex,
        kind="turn_end_blocking",
        trigger_part=part,
        trigger_turn=turn,
        target_commit=commit,
        queued_at=now_iso,
        started_at=now_iso,
        finished_at=now_iso,
        status=status,
        passed=passed,
        failed=failed,
        total=total,
        payload=event_payload,
        suite_results=suite_results,
        tests=tests,
        error=event_error,
    )


async def run_trajectory(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_parts: int | None = None,
    max_turns: int | None = None,
    test: list[str] | None = None,
    test_timeout_seconds: int | None = None,
    message_timeout_seconds: int = MESSAGE_TIMEOUT_SECONDS,
    timeout_seconds: int = 7200,
    trajectory_id: str | None = None,
    codex_auth_json_b64: str | None = None,
    resume: bool = RESUME_FROM_S3,
    sandbox_provider: str = "modal",
    task_dir: str = "",
    environment_dir: str = "",
    task_lang: str = "en",
    task_params: dict[str, str] | None = None,
) -> str:
    global CURRENT_SUITE_FEEDBACK_PRIORITY
    if trajectory_id is None:
        trajectory_id = str(uuid.uuid4())
    structured_logs: list[dict[str, Any]] = []
    log_callback_token: Any = None
    log_context_token: Any = None
    logs_flush_task: asyncio.Task[None] | None = None
    logs_flush_wakeup: asyncio.Event | None = None
    logs_flush_stop: asyncio.Event | None = None
    logs_flush_lock = asyncio.Lock()
    last_logs_flush_count = 0
    last_logs_flush_mono = time.monotonic()

    async def flush_logs(
        *,
        force: bool = False,
    ) -> None:
        nonlocal last_logs_flush_count, last_logs_flush_mono
        if not structured_logs:
            return
        async with logs_flush_lock:
            total_count = len(structured_logs)
            if total_count == 0:
                return
            new_records = total_count - last_logs_flush_count
            elapsed = time.monotonic() - last_logs_flush_mono
            if (
                not force
                and (
                    new_records <= 0
                    or (
                        new_records < LOGS_FLUSH_BATCH_SIZE
                        and elapsed < LOGS_FLUSH_INTERVAL_SECONDS
                    )
                )
            ):
                return
            snapshot = list(structured_logs)
            await asyncio.to_thread(
                save_logs_parquet,
                trajectory_id,
                snapshot,
            )
            last_logs_flush_count = len(snapshot)
            last_logs_flush_mono = time.monotonic()

    def capture_structured_log(record: dict[str, Any]) -> None:
        if not isinstance(record, dict):
            return
        normalized = dict(record)
        normalized.setdefault("trajectory_id", trajectory_id)
        normalized.setdefault("source", "orchestrator")
        structured_logs.append(normalized)
        if logs_flush_wakeup is None:
            return
        level_value = normalized.get("level")
        level = (
            level_value.lower()
            if isinstance(level_value, str)
            else ""
        )
        new_records = len(structured_logs) - last_logs_flush_count
        if level in {"error", "warning"} or new_records >= LOGS_FLUSH_BATCH_SIZE:
            logs_flush_wakeup.set()
    max_parts = normalize_positive_limit(max_parts)
    max_turns = normalize_positive_limit(max_turns)
    if timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be > 0")
    if test_timeout_seconds is not None and test_timeout_seconds <= 0:
        raise ValueError("--test-timeout-seconds must be > 0")
    selected_test_paths = normalize_test_paths(test)
    agent_name = (agent or DEFAULT_AGENT).strip().lower()

    task_path = Path(task_dir)
    env_path = Path(environment_dir)
    environment = env_path.name
    prompt, task_params_loaded = await load_task(task_path, lang=task_lang)
    environment_params = await load_environment_params(env_path)
    advisor_model_from_env = _string_or_none(
        environment_params.get("advisor_model"),
    )
    normalized_advisor_model: str | None = None
    if advisor_model_from_env is not None:
        normalized_advisor_model = normalize_advisor_model(
            advisor_model_from_env,
        )
    normalized_advisor_thinking_level = normalize_thinking_level(
        _string_or_none(
            environment_params.get("advisor_model_thinking_level"),
        )
        or "high",
    )
    failed_tests_feedback_limit = resolve_failed_tests_feedback_limit(
        environment_params.get("failed_tests_feedback_limit"),
    )
    CURRENT_SUITE_FEEDBACK_PRIORITY = resolve_suite_feedback_priority(
        environment_params.get("diagnostics_suite_priority"),
    )
    if task_params:
        task_params_loaded.update(task_params)
    task_params_loaded["_eval_test_paths"] = selected_test_paths
    task_params_loaded["_eval_test_timeout_seconds"] = test_timeout_seconds
    task_params_loaded["_environment_params"] = environment_params
    env_files = load_environment_files(env_path)

    # Resolve agent class, model, and credentials via protocol
    agent_cls = AGENT_BACKENDS.get(agent_name)
    if agent_cls is None:
        raise ValueError(f"Unknown agent: {agent_name}")
    resolved_model = agent_cls.resolve_model(model)
    credentials = agent_cls.resolve_credentials(
        codex_auth_json_b64=codex_auth_json_b64,
    )

    existing_trace = load_trace_snapshot(trajectory_id) if resume else None
    if existing_trace is not None and existing_trace.agent != agent_name:
        print(
            f"[resume] existing trajectory agent={existing_trace.agent} "
            f"differs from requested agent={agent_name}; "
            "starting new trace object"
        )
        existing_trace = None
    trace_s3_uri = artifact_uri(trajectory_id, "trace.parquet")
    bundle_s3_uri = artifact_uri(trajectory_id, "repo.bundle")
    logs_s3_uri = artifact_uri(trajectory_id, "logs.parquet")
    log_callback_token = set_log_callback(capture_structured_log)
    log_context_token = bind_log_context(
        component="orchestrator",
        trajectory_id=trajectory_id,
        source="orchestrator",
    )

    logs_flush_wakeup = asyncio.Event()
    logs_flush_stop = asyncio.Event()

    async def periodic_logs_flush_loop() -> None:
        assert logs_flush_wakeup is not None
        assert logs_flush_stop is not None
        while not logs_flush_stop.is_set():
            try:
                await asyncio.wait_for(
                    logs_flush_wakeup.wait(),
                    timeout=LOGS_FLUSH_INTERVAL_SECONDS,
                )
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            finally:
                logs_flush_wakeup.clear()
            try:
                await flush_logs(force=False)
            except Exception as log_flush_error:
                print(
                    "[logs] periodic flush failed: "
                    f"{log_flush_error}"
                )
        try:
            await flush_logs(force=True)
        except Exception as log_flush_error:
            print(
                "[logs] final periodic flush failed: "
                f"{log_flush_error}"
            )

    logs_flush_task = asyncio.create_task(
        periodic_logs_flush_loop(),
    )

    banner = "=" * 72
    print(banner)
    print(f"TRAJECTORY_ID: {trajectory_id}")
    print(f"TRACE_S3_URI: {trace_s3_uri}")
    print(f"BUNDLE_S3_URI: {bundle_s3_uri}")
    print(f"LOGS_S3_URI: {logs_s3_uri}")
    part_limit_label = str(max_parts) if max_parts is not None else "none"
    turn_limit_label = str(max_turns) if max_turns is not None else "none"
    print(
        f"agent={agent_name} model={resolved_model} "
        f"part_limit={part_limit_label} turn_limit={turn_limit_label} "
        f"timeout={timeout_seconds}s "
        f"message_timeout={message_timeout_seconds}s"
    )
    test_selector = ",".join(selected_test_paths) if selected_test_paths else "all"
    test_timeout_label = (
        f"{test_timeout_seconds}s"
        if isinstance(test_timeout_seconds, int)
        else "default"
    )
    print(
        f"tests={test_selector} "
        f"test_timeout={test_timeout_label} "
        f"eval_concurrency={EVALUATION_CONCURRENCY} "
        f"advisor_model={normalized_advisor_model or 'none'} "
        f"advisor_thinking={normalized_advisor_thinking_level} "
        "suite_priority="
        f"{format_suite_feedback_priority(CURRENT_SUITE_FEEDBACK_PRIORITY)} "
        f"failed_tests_limit={failed_tests_feedback_limit}"
    )
    if existing_trace is not None:
        print(
            f"[resume] found existing trace: "
            f"parts={len(existing_trace.parts)} "
            f"turns={len(existing_trace.turns)}"
        )
    print(banner)

    sandbox: Sandbox | None = None
    agent_trace: AgentTrace | None = None
    agent_backend: Agent | None = None
    evaluator: EvaluationScheduler | None = None
    session_id: str = ""
    prompt_text: str = ""
    turn_count = 0
    part_count = 0
    latest_git_commit: str | None = None
    end_reason: str = "agent_error"

    try:
        # --- Create sandbox ---
        sandbox_timeout_seconds = timeout_seconds + SHUTDOWN_GRACE_SECONDS
        config = SandboxConfig(
            timeout=sandbox_timeout_seconds,
            image_requirements=agent_cls.image_requirements(),
            environment_dockerfile=str(env_path / "Dockerfile"),
        )
        sandbox = await create_sandbox(sandbox_provider, config)
        start_time = time.monotonic()

        # --- Agent setup (one call, no branching) ---
        agent_backend = agent_cls()
        assert agent_backend is not None
        mcp_server_content, mcp_source = load_optional_mcp_server(
            task_path,
            env_path,
        )
        mcp_enabled = bool(mcp_server_content.strip())
        if mcp_enabled:
            print(f"[mcp] enabled from {mcp_source}")
        else:
            print("[mcp] disabled (no mcp_server.py in task/env)")
        ctx = AgentSetupContext(
            model=resolved_model,
            credentials=credentials,
            env_files=env_files,
            mcp_server_content=mcp_server_content,
            mcp_enabled=mcp_enabled,
            workspace_gitignore=WORKSPACE_GITIGNORE,
        )
        await agent_backend.setup(sandbox, ctx)

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
                sandbox=sandbox,
                trajectory_id=trajectory_id,
                commit=resume_commit,
            )
            latest_git_commit = resume_commit

        # --- Create session ---
        session_id = await agent_backend.create_session(
            trajectory_id,
        )
        if not session_id:
            raise RuntimeError(
                f"Failed to create session for agent={agent_name}",
            )
        update_log_context(session_id=session_id)

        if existing_trace is not None:
            agent_trace = existing_trace
            agent_trace.session_id = session_id
            agent_trace.agent = agent_name
            agent_trace.agent_model = resolved_model
            agent_trace.session_end = None
            part_count = get_trace_last_part(agent_trace)
            turn_count = get_trace_last_turn(agent_trace)
            trace_latest_commit = get_trace_latest_commit(agent_trace)
            if isinstance(trace_latest_commit, str) and trace_latest_commit:
                latest_git_commit = trace_latest_commit
            print(
                f"[resume] continuing from part={part_count} "
                f"turn={turn_count}"
            )
        else:
            agent_trace = AgentTrace(
                trajectory_id=trajectory_id,
                session_id=session_id,
                agent=agent_name,
                agent_model=resolved_model,
                started_at=datetime.now(UTC).isoformat(),
            )
        save_trace_parquet(
            trajectory_id, agent_trace,
            environment=environment,
            task_params=task_params_loaded,
        )

        # --- Discover test paths from envoi /schema ---
        required_test_paths: list[str] = []
        schema_result = await sandbox.run(
            "curl -sf http://localhost:8000/schema",
            quiet=True, timeout=30,
        )
        if (
            schema_result.exit_code == 0
            and schema_result.stdout.strip()
        ):
            try:
                schema = json.loads(schema_result.stdout)
                required_test_paths = extract_leaf_paths(schema)
                print(
                    f"[schema] discovered "
                    f"{len(required_test_paths)} test paths"
                )
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[schema] parse error: {e}")
        else:
            print(
                "[schema] /schema not available, "
                "running without completion tracking"
            )

        if selected_test_paths and required_test_paths:
            discovered = set(required_test_paths)
            invalid_paths: list[str] = []
            for path in selected_test_paths:
                if path in discovered:
                    continue
                has_child = any(
                    candidate.startswith(path + "/")
                    for candidate in required_test_paths
                )
                if not has_child:
                    invalid_paths.append(path)
            if invalid_paths:
                available_preview = ", ".join(required_test_paths[:20])
                raise ValueError(
                    "Unknown --test path(s): "
                    + ", ".join(invalid_paths)
                    + ". Available paths include: "
                    + available_preview
                )

        # --- Main loop ---
        tracker = SolveTracker(required_test_paths)
        for part_record in agent_trace.parts:
            tracker.update(list(part_record.envoi_calls))
        previous_turn_end_tests = (
            find_latest_completed_turn_end_tests(agent_trace)
        )

        winner_stop_part_ref: list[int | None] = [None]

        def winner_latched() -> bool:
            return isinstance(winner_stop_part_ref[0], int)

        def latch_winner(
            commit: str,
            evaluation: EvaluationRecord,
            *,
            source: str,
        ) -> bool:
            winner_part = winner_part_number(evaluation)
            if winner_part is None:
                return False
            current = winner_stop_part_ref[0]
            if isinstance(current, int) and current <= winner_part:
                return False
            winner_stop_part_ref[0] = winner_part
            print(
                "[eval] latched first winner "
                f"source={source} commit={commit[:10]} "
                f"part={winner_part} "
                f"score={evaluation.passed}/{evaluation.total}"
            )
            return True

        async def on_async_winner(
            commit: str,
            evaluation: EvaluationRecord,
        ) -> None:
            did_latch = latch_winner(
                commit, evaluation, source="commit_async",
            )
            if not did_latch:
                return
            # Best-effort: interrupt in-flight turn command so solved runs stop fast.
            try:
                await sandbox.run(
                    "pkill -f '/sandbox/codex_client.py chat-stream' "
                    "> /dev/null 2>&1 || true\n"
                    "pkill -f '/sandbox/opencode_agent.py chat-stream' "
                    "> /dev/null 2>&1 || true",
                    quiet=True,
                    timeout=10,
                )
            except Exception as kill_error:
                print(
                    "[eval] winner interrupt failed: "
                    f"{kill_error}"
                )

        evaluator = EvaluationScheduler(
            sandbox=sandbox,
            agent_trace=agent_trace,
            trajectory_id=trajectory_id,
            environment=environment,
            task_params=task_params_loaded,
            test_paths=selected_test_paths,
            test_timeout_seconds=test_timeout_seconds,
            should_stop=winner_latched,
            on_winner=on_async_winner,
        )

        existing_winner = first_winning_commit(agent_trace.evaluations)
        if existing_winner is not None:
            existing_winner_commit, existing_winner_eval = existing_winner
            latch_winner(
                existing_winner_commit,
                existing_winner_eval,
                source="resume",
            )

        async def stop_for_winner(
            *,
            detection_point: str,
        ) -> bool:
            nonlocal part_count, turn_count, end_reason, latest_git_commit
            winner = first_winning_commit(agent_trace.evaluations)
            if winner is None:
                return False
            winner_commit, winner_eval = winner
            latest_git_commit = winner_commit
            latch_winner(
                winner_commit,
                winner_eval,
                source=detection_point,
            )
            winner_part = apply_winning_projection(
                agent_trace,
                winner_commit=winner_commit,
                winner_eval=winner_eval,
            )
            if isinstance(winner_part, int):
                part_count = winner_part
                turn_count = get_trace_last_turn(agent_trace)
            save_trace_parquet(
                trajectory_id, agent_trace,
                environment=environment,
                task_params=task_params_loaded,
            )
            await checkout_workspace_commit(
                sandbox,
                winner_commit,
            )
            print(
                f"[eval] winner detected {detection_point} "
                f"commit={winner_commit[:10]} "
                f"part={winner_eval.part} "
                f"score={winner_eval.passed}/{winner_eval.total}"
            )
            end_reason = "solved"
            return True

        prompt_text = (
            prompt if part_count == 0
            else build_followup_prompt(tracker)
        )
        next_turn_feedback_eval_id: str | None = None
        consecutive_turn_failures = 0

        while True:
            update_log_context(turn=turn_count + 1, part=part_count)
            if await stop_for_winner(
                detection_point="before turn start",
            ):
                break
            if (
                isinstance(max_parts, int)
                and max_parts > 0
                and part_count >= max_parts
            ):
                end_reason = "part_limit"
                break
            if max_turns is not None and turn_count >= max_turns:
                print(
                    "[progress] reached turn limit "
                    f"({turn_count}/{max_turns})"
                )
                end_reason = "part_limit"
                break
            elapsed = time.monotonic() - start_time
            if elapsed > timeout_seconds:
                end_reason = "timeout"
                break
            remaining_run_seconds = timeout_seconds - elapsed
            remaining_parts_budget = (
                max(1, max_parts - part_count)
                if isinstance(max_parts, int)
                and max_parts > 0
                else 0
            )
            remaining_parts_for_timeout = (
                remaining_parts_budget
                if remaining_parts_budget > 0
                else max(1, int(remaining_run_seconds // 60))
            )

            # Agent decides its own timeout (no branching)
            turn_timeout_seconds = agent_backend.compute_turn_timeout(
                remaining_parts=remaining_parts_for_timeout,
                remaining_run_seconds=remaining_run_seconds,
                message_timeout_seconds=message_timeout_seconds,
            )

            turn_banner = "=" * 60
            part_counter_label = format_progress_counter(
                name="part",
                current=part_count,
                limit=max_parts,
            )
            turn_counter_label = format_progress_counter(
                name="turn",
                current=turn_count + 1,
                limit=max_turns,
            )
            builtins.print(f"\n{turn_banner}", flush=True)
            builtins.print(
                f" TURN {turn_count + 1}  "
                f"({part_counter_label} {turn_counter_label} "
                f"timeout={turn_timeout_seconds}s)",
                flush=True,
            )
            builtins.print(turn_banner, flush=True)

            turn_started_at = datetime.now(UTC).isoformat()
            previous_part_count = part_count
            streamed_parts = 0
            observed_parts = 0
            git_commit = await get_git_commit(sandbox)
            if isinstance(git_commit, str) and git_commit:
                latest_git_commit = git_commit

            turn_count += 1
            turn_record = TurnRecord(
                trajectory_id=trajectory_id,
                session_id=session_id,
                agent=agent_name,
                turn=turn_count,
                part_start=None,
                part_end=None,
                timestamp=turn_started_at,
                agent_model=resolved_model,
                prompt=prompt_text,
                git_commit=git_commit,
                repo_checkpoint=None,
                feedback_eval_id=next_turn_feedback_eval_id,
                parts=[],
            )
            agent_trace.turns.append(turn_record)

            stream_part_counter: list[int] = [part_count]
            stream_git_commit_ref: list[str | None] = [git_commit]
            stream_last_part_ts_ref: list[int | None] = [None]
            stream_part_cb = make_stream_part_callback(
                sandbox=sandbox,
                trajectory_id=trajectory_id,
                agent_trace=agent_trace,
                tracker=tracker,
                environment=environment,
                task_params=task_params_loaded,
                agent_name=agent_name,
                resolved_model=resolved_model,
                effective_max_parts=max_parts,
                part_counter=stream_part_counter,
                git_commit_ref=stream_git_commit_ref,
                last_part_timestamp_ms_ref=stream_last_part_ts_ref,
                turn_record=turn_record,
                session_id=session_id,
                stop_at_part_ref=winner_stop_part_ref,
                schedule_commit_evaluation=evaluator.schedule,
            )

            turn_outcome = await agent_backend.run_turn(
                prompt_text=prompt_text,
                timeout=turn_timeout_seconds,
                current_turn=turn_count,
                remaining_parts_budget=remaining_parts_budget,
                global_part_count=part_count,
                global_max_parts=max_parts or 0,
                global_max_turns=max_turns or 0,
                global_elapsed_seconds=int(max(0, elapsed)),
                on_stream_part=stream_part_cb,
            )
            part_count = stream_part_counter[0]
            git_commit = stream_git_commit_ref[0]
            if isinstance(git_commit, str) and git_commit:
                latest_git_commit = git_commit
            update_log_context(part=part_count, git_commit=git_commit)

            if turn_outcome is None:
                if await stop_for_winner(
                    detection_point="after interrupted turn",
                ):
                    break
                if turn_record is not None and not turn_record.parts:
                    agent_trace.turns.pop()
                consecutive_turn_failures += 1
                print(
                    "[progress] no response from agent "
                    f"(recovery {consecutive_turn_failures}"
                    f"/{TURN_RECOVERY_RETRIES})"
                )
                await dump_sandbox_logs(
                    sandbox, agent=agent_backend,
                )
                if consecutive_turn_failures <= TURN_RECOVERY_RETRIES:
                    recovered_session_id = (
                        await agent_backend.recover_session(
                            trajectory_id,
                            consecutive_turn_failures,
                        )
                    )
                    if recovered_session_id:
                        session_id = recovered_session_id
                        agent_trace.session_id = (
                            recovered_session_id
                        )
                        save_trace_parquet(
                            trajectory_id, agent_trace,
                            environment=environment,
                            task_params=task_params_loaded,
                        )
                        prompt_text = build_followup_prompt(
                            tracker,
                        )
                        continue
                end_reason = "agent_error"
                break
            consecutive_turn_failures = 0
            agent_backend.on_turn_complete(turn_outcome)

            response = turn_outcome.response
            session_id = turn_outcome.session_id
            if agent_trace.session_id != session_id:
                agent_trace.session_id = session_id
            update_log_context(session_id=session_id)

            info = response.get("info", {})
            parts = response.get("parts", [])
            response_message_id = info.get("id")
            print(
                f"[progress] response "
                f"id={response_message_id} "
                f"parts={len(parts)}"
            )

            session_ids = turn_outcome.session_ids
            session_objects = turn_outcome.session_objects
            new_messages = turn_outcome.new_messages
            print(f"[progress] new_messages={len(new_messages)}")
            if turn_record is not None:
                turn_record.session_ids = session_ids
                turn_record.session_objects = session_objects
                turn_record.new_messages = new_messages
                turn_record.token_usage = (
                    extract_turn_token_usage(
                        response, new_messages,
                    )
                )

            new_envoi_calls: list[EnvoiCall] = []
            for msg in new_messages:
                msg_parts = msg.get("parts", [])
                if isinstance(msg_parts, list):
                    new_envoi_calls.extend(
                        extract_envoi_calls(msg_parts),
                    )

            tracker.update(new_envoi_calls)

            stream_meta = (
                response.get("_stream", {})
                if isinstance(response, dict)
                else {}
            )
            stream_meta_obj = (
                stream_meta
                if isinstance(stream_meta, dict)
                else {}
            )
            streamed_parts = int(
                stream_meta_obj.get(
                    "meaningful_parts_seen", 0,
                )
                or 0
            )
            observed_parts = count_meaningful_parts(
                new_messages,
            )

            new_parts = part_count - previous_part_count
            if turn_record is not None:
                turn_record.session_id = session_id
                for record in turn_record.parts:
                    record.session_id = session_id
                if turn_record.parts:
                    last_part_record = turn_record.parts[-1]
                    existing_keys = {
                        tracker.call_key(call)
                        for call in last_part_record.envoi_calls
                    }
                    for call in new_envoi_calls:
                        key = tracker.call_key(call)
                        if key not in existing_keys:
                            last_part_record.envoi_calls.append(
                                call,
                            )
                            existing_keys.add(key)
                    last_part_record.testing_state = (
                        tracker.snapshot()
                    )
                    if turn_record.git_commit is None:
                        turn_record.git_commit = (
                            last_part_record.git_commit
                        )
                else:
                    turn_record.git_commit = git_commit
            save_trace_parquet(
                trajectory_id, agent_trace,
                environment=environment,
                task_params=task_params_loaded,
            )
            if await stop_for_winner(
                detection_point="after turn",
            ):
                break
            turn_end_eval_feedback = ""
            turn_end_passed: int | None = None
            turn_end_total: int | None = None
            turn_end_has_error = True
            turn_end_eval_payload: dict[str, Any] | None = None
            turn_end_eval_payload_body: dict[str, Any] | None = None
            advisor_assessment: str | None = None
            turn_end_event: EvalEvent | None = None
            try:
                turn_end_eval_payload = await run_workspace_evaluation(
                    sandbox=sandbox,
                    test_paths=selected_test_paths,
                    timeout_seconds=test_timeout_seconds,
                )
                payload = turn_end_eval_payload.get("payload")
                if isinstance(payload, dict):
                    turn_end_eval_payload_body = payload
                    turn_end_passed = int(
                        payload.get("passed", 0) or 0,
                    )
                    turn_end_total = int(
                        payload.get("total", 0) or 0,
                    )
                    turn_end_error = payload.get("error")
                    turn_end_has_error = bool(
                        isinstance(turn_end_error, str)
                        and turn_end_error.strip()
                    )
                    if (
                        turn_end_total == 0
                        and turn_end_passed == 0
                        and not turn_end_has_error
                    ):
                        print("[eval] turn_end status=no_tests")
                    else:
                        print(
                            "[eval] turn_end "
                            f"passed={turn_end_passed}/{turn_end_total} "
                            f"status_error={turn_end_has_error}"
                        )
                else:
                    turn_end_has_error = True
                    print("[eval] turn_end payload missing")

                if (
                    normalized_advisor_model is not None
                    and isinstance(turn_end_eval_payload_body, dict)
                    and not (
                        isinstance(turn_end_passed, int)
                        and isinstance(turn_end_total, int)
                        and turn_end_total > 0
                        and turn_end_passed == turn_end_total
                        and not turn_end_has_error
                    )
                ):
                    advisor_started_at = time.monotonic()
                    print(
                        "[advisor] run_start "
                        f"model={normalized_advisor_model} "
                        f"thinking={normalized_advisor_thinking_level} "
                        f"commit={(git_commit or 'unknown')[:12]} "
                        f"turn={turn_number} "
                        f"failed_tests_limit={failed_tests_feedback_limit}"
                    )
                    try:
                        advisor_assessment = await build_advisor_assessment(
                            sandbox=sandbox,
                            task_prompt=prompt,
                            commit=git_commit,
                            payload=turn_end_eval_payload_body,
                            advisor_model=normalized_advisor_model,
                            advisor_model_thinking_level=(
                                normalized_advisor_thinking_level
                            ),
                            failed_tests_limit=failed_tests_feedback_limit,
                        )
                        print(
                            "[advisor] run_success "
                            f"elapsed_ms={int((time.monotonic() - advisor_started_at) * 1000)} "
                            f"assessment_chars={len(advisor_assessment)}"
                        )
                    except Exception as advisor_error:
                        error_message = str(advisor_error).strip() or repr(advisor_error)
                        advisor_assessment = (
                            "External assessment unavailable: "
                            + error_message
                        )
                        print(
                            "[advisor] failed: "
                            f"type={type(advisor_error).__name__} "
                            f"elapsed_ms={int((time.monotonic() - advisor_started_at) * 1000)} "
                            f"error={error_message}"
                        )
                        print(
                            "[advisor] failed_traceback:\n"
                            + traceback.format_exc().strip()
                        )
                elif normalized_advisor_model is not None:
                    if not isinstance(turn_end_eval_payload_body, dict):
                        print("[advisor] skipped reason=turn_end_payload_missing")
                    else:
                        print(
                            "[advisor] skipped reason=all_tests_passing "
                            f"passed={turn_end_passed} total={turn_end_total} "
                            f"status_error={turn_end_has_error}"
                        )

                turn_end_eval_feedback = (
                    format_turn_end_evaluation_feedback(
                        turn_end_eval_payload,
                        failed_tests_limit=failed_tests_feedback_limit,
                        advisor_assessment=advisor_assessment,
                        previous_turn_end_tests=previous_turn_end_tests,
                    )
                )
            except Exception as turn_end_eval_error:
                turn_end_eval_feedback = (
                    "Turn-end full evaluation failed:\n"
                    + str(turn_end_eval_error)
                )
                turn_end_has_error = True
                print(
                    "[eval] turn_end failed: "
                    f"{turn_end_eval_error}"
                )

            turn_eval_part = (
                turn_record.part_end
                if (
                    isinstance(turn_record.part_end, int)
                    and turn_record.part_end > 0
                )
                else part_count
            )
            if turn_eval_part > 0:
                turn_end_event = build_turn_end_eval_event(
                    turn=turn_count,
                    part=turn_eval_part,
                    commit=git_commit,
                    run_payload=turn_end_eval_payload,
                    error=(
                        turn_end_eval_feedback
                        if turn_end_eval_payload is None
                        else None
                    ),
                )
                append_eval_event_delta(
                    agent_trace,
                    turn_end_event,
                )
                save_trace_parquet(
                    trajectory_id,
                    agent_trace,
                    environment=environment,
                    task_params=task_params_loaded,
                )
                if turn_end_event.status == "completed" and turn_end_event.tests:
                    previous_turn_end_tests = list(turn_end_event.tests)

            eval_label = (
                f"{turn_end_passed}/{turn_end_total}"
                if isinstance(turn_end_passed, int)
                and isinstance(turn_end_total, int)
                else "unknown"
            )
            part_counter_label = format_progress_counter(
                name="part",
                current=part_count,
                limit=max_parts,
            )
            turn_counter_label = format_progress_counter(
                name="turn",
                current=turn_count,
                limit=max_turns,
            )
            print(
                f"[progress] {turn_counter_label} "
                f"{part_counter_label} "
                f"commit={git_commit} "
                f"parts_delta=+{new_parts} "
                f"turn_end_eval={eval_label} "
                f"envoi_calls={len(new_envoi_calls)} "
                f"observed_parts={observed_parts} "
                f"streamed_parts={streamed_parts} "
                f"started={turn_started_at}"
            )
            await flush_logs(force=True)

            if (
                isinstance(turn_end_passed, int)
                and isinstance(turn_end_total, int)
                and turn_end_total > 0
                and turn_end_passed == turn_end_total
                and not turn_end_has_error
            ):
                end_reason = "solved"
                break

            if (
                isinstance(max_parts, int)
                and max_parts > 0
                and part_count >= max_parts
            ):
                end_reason = "part_limit"
                break

            next_turn_feedback_eval_id = (
                turn_end_event.eval_id
                if turn_end_event is not None
                else None
            )
            prompt_text = build_followup_prompt(
                tracker,
                evaluation_feedback=turn_end_eval_feedback,
            )

        if end_reason == "agent_error":
            end_reason = "part_limit"

    except Exception as exc:
        print(f"[error] {type(exc).__name__}: {exc}")
        print(traceback.format_exc())
        if sandbox and agent_backend:
            await dump_sandbox_logs(
                sandbox, agent=agent_backend,
            )
        end_reason = "agent_error"
        # Crash recovery via protocol (no name checks)
        try:
            if (
                agent_trace is not None
                and agent_backend is not None
                and session_id
            ):
                crash_messages = (
                    await agent_backend.collect_crash_messages(
                        session_id,
                    )
                )
                if crash_messages:
                    crash_record = TurnRecord(
                        trajectory_id=trajectory_id,
                        session_id=session_id,
                        agent=agent_name,
                        turn=turn_count + 1,
                        part_start=part_count + 1,
                        part_end=part_count,
                        timestamp=datetime.now(UTC).isoformat(),
                        agent_model=resolved_model,
                        prompt=prompt_text or "",
                        git_commit=(
                            await get_git_commit(sandbox)
                            if sandbox
                            else None
                        ),
                        parts=[],
                    )
                    agent_trace.turns.append(crash_record)
                    save_trace_parquet(
                        trajectory_id, agent_trace,
                        environment=environment,
                        task_params=task_params_loaded,
                    )
                    print(
                        f"[error] saved {len(crash_messages)} "
                        "new messages before crash"
                    )
        except Exception:
            print("[error] could not save crash messages")

    finally:
        if logs_flush_stop is not None:
            logs_flush_stop.set()
        if logs_flush_wakeup is not None:
            logs_flush_wakeup.set()
        if logs_flush_task is not None:
            try:
                await logs_flush_task
            except Exception:
                pass
        if agent_trace is not None:
            trace_part_count = get_trace_last_part(agent_trace)
            trace_turn_count = get_trace_last_turn(agent_trace)
            if trace_part_count > part_count or trace_turn_count > turn_count:
                print(
                    "[end] syncing counters from trace "
                    f"(parts {part_count}->{trace_part_count}, "
                    f"turns {turn_count}->{trace_turn_count})"
                )
            part_count = max(part_count, trace_part_count)
            turn_count = max(turn_count, trace_turn_count)
        if evaluator is not None:
            try:
                if EVALUATOR_DRAIN_TIMEOUT_SECONDS > 0:
                    await asyncio.wait_for(
                        evaluator.wait(),
                        timeout=EVALUATOR_DRAIN_TIMEOUT_SECONDS,
                    )
                else:
                    await evaluator.wait()
            except TimeoutError:
                print(
                    "[eval] shutdown drain timed out after "
                    f"{EVALUATOR_DRAIN_TIMEOUT_SECONDS}s; "
                    "cancelling pending evaluations"
                )
                try:
                    await evaluator.cancel_pending(
                        reason=(
                            "Cancelled during shutdown: "
                            "evaluation drain timed out"
                        ),
                    )
                except Exception:
                    pass
            except Exception as drain_error:
                print(f"[eval] shutdown drain failed: {drain_error}")
                try:
                    await evaluator.cancel_pending(
                        reason=(
                            "Cancelled during shutdown: "
                            "evaluation drain failed"
                        ),
                    )
                except Exception:
                    pass
        logs_parquet_uri = artifact_uri(trajectory_id, "logs.parquet")
        if sandbox is not None and agent_trace is not None:
            try:
                winner = first_winning_commit(agent_trace.evaluations)
                if winner is not None:
                    winner_commit, winner_eval = winner
                    winner_part = apply_winning_projection(
                        agent_trace,
                        winner_commit=winner_commit,
                        winner_eval=winner_eval,
                    )
                    if isinstance(winner_part, int):
                        part_count = winner_part
                        turn_count = get_trace_last_turn(agent_trace)
                    latest_git_commit = winner_commit
                    save_trace_parquet(
                        trajectory_id, agent_trace,
                        environment=environment,
                        task_params=task_params_loaded,
                    )
                    checked_out = await checkout_workspace_commit(
                        sandbox,
                        winner_commit,
                    )
                    if checked_out:
                        print(
                            "[eval] final winner "
                            f"commit={winner_commit[:10]} "
                            f"part={winner_eval.part} "
                            f"score={winner_eval.passed}/{winner_eval.total}"
                        )
                        end_reason = "solved"
            except Exception as winner_finalize_error:
                print(
                    "[eval] final winner projection failed: "
                    f"{winner_finalize_error}"
                )

            try:
                sandbox_logs = await collect_sandbox_structured_logs(sandbox)
                if sandbox_logs:
                    structured_logs.extend(sandbox_logs)
            except Exception as log_error:
                print(f"[logs] failed collecting sandbox logs: {log_error}")

            try:
                await flush_logs(force=True)
            except Exception as flush_error:
                print(f"[logs] pre-end flush failed: {flush_error}")

            try:
                await end_session(
                    sandbox,
                    agent_trace,
                    part_count,
                    turn_count,
                    end_reason,
                    environment=environment,
                    task_params=task_params_loaded,
                    logs_parquet_uri=logs_parquet_uri,
                    final_commit_hint=latest_git_commit,
                )
            except Exception as end_err:
                print(
                    "[error] failed to finalize session: "
                    f"{end_err}"
                )
        if sandbox is not None:
            try:
                await sandbox.terminate()
            except Exception:
                pass
        if (
            (sandbox is None or agent_trace is None)
            and structured_logs
        ):
            try:
                await flush_logs(force=True)
            except Exception as log_save_error:
                print(f"[logs] failed saving logs.parquet: {log_save_error}")
        try:
            await flush_logs(force=True)
        except Exception as log_save_error:
            print(f"[logs] final flush failed: {log_save_error}")
        if log_callback_token is not None:
            reset_log_callback(log_callback_token)
        if log_context_token is not None:
            reset_log_context(log_context_token)

    return trajectory_id


# ---------------------------------------------------------------------------
# Direct execution entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run trajectory directly.",
    )
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-parts", type=int, default=None)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=7200,
    )
    parser.add_argument(
        "--test",
        action="append",
        default=None,
        help=(
            "Optional test path to run during evaluation. "
            "Repeat to target multiple paths. "
            "If omitted, all tests run."
        ),
    )
    parser.add_argument(
        "--test-timeout-seconds",
        type=int,
        default=None,
        help=(
            "Timeout for each commit/turn-end evaluation run. "
            "Defaults to EVALUATION_TIMEOUT_SECONDS."
        ),
    )
    parser.add_argument("--sandbox-provider", default="modal")
    parser.add_argument("--trajectory-id", default=None)
    parser.add_argument(
        "--message-timeout-seconds",
        type=int,
        default=MESSAGE_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--codex-auth-file", default="~/.codex/auth.json",
    )
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--environment-dir", required=True)
    args = parser.parse_args()

    codex_auth_b64: str | None = None
    agent_name_raw = (args.agent or DEFAULT_AGENT).strip().lower()
    cls = AGENT_BACKENDS.get(agent_name_raw)
    if (
        cls is not None
        and hasattr(cls, "load_local_auth_b64")
        and args.codex_auth_file
    ):
        codex_auth_b64 = cls.load_local_auth_b64(
            args.codex_auth_file.strip(),
        )

    asyncio.run(
        run_trajectory(
            agent=args.agent,
            model=args.model,
            max_parts=args.max_parts,
            max_turns=args.max_turns,
            test=args.test,
            test_timeout_seconds=args.test_timeout_seconds,
            message_timeout_seconds=args.message_timeout_seconds,
            timeout_seconds=args.timeout_seconds,
            trajectory_id=args.trajectory_id,
            codex_auth_json_b64=codex_auth_b64,
            sandbox_provider=args.sandbox_provider,
            task_dir=args.task_dir,
            environment_dir=args.environment_dir,
        )
    )
