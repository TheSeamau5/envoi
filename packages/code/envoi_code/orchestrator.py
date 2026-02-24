"""
Main orchestrator for envoi-trace.

Creates a sandbox, provisions an agent (Codex or OpenCode), and runs a turn
loop with a bounded part budget. After every part, it persists trace.parquet
to S3. After every file change, it creates a git checkpoint. At end-of-run,
it uploads a repo.bundle for the final export commit.

The two core abstractions are Agent (how to talk to an agent) and
Sandbox (where the agent runs). This file wires them together and
manages the turn loop, resume logic, and artifact persistence.
It has zero knowledge of specific sandbox providers.

Usage (via CLI):
    uv run trace run --task examples/tasks/c_compiler --env examples/environments/c_compiler
    python3 runner.py --agent codex --max-parts 1000 --task-dir <path> --environment-dir <path>
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import builtins
import importlib.util
import inspect
import json
import os
import shlex
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

from envoi_code.agents.base import Agent, AgentSetupContext
from envoi_code.agents.codex import CodexAgent
from envoi_code.agents.opencode import OpenCodeAgent
from envoi_code.models import (
    AgentTrace,
    EvalEvent,
    EvalTestResult,
    EnvoiCall,
    EvaluationRecord,
    PartRecord,
    SessionEnd,
    TurnRecord,
)
from envoi_code.sandbox import SandboxConfig, create_sandbox
from envoi_code.sandbox.base import Sandbox
from envoi_code.utils.evaluation import (
    EVALUATION_CONCURRENCY,
    extract_leaf_paths,
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
    log_message_parts,
)
from envoi_code.utils.solve import SolveTracker
from envoi_code.utils.storage import (
    artifact_uri,
    get_bucket,
    get_s3_client,
    load_trace_snapshot,
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
MAX_INLINE_FAILED_TESTS = max(
    1, int(os.environ.get("MAX_INLINE_FAILED_TESTS", "40"))
)
MAX_INLINE_TEST_MESSAGE_CHARS = max(
    80, int(os.environ.get("MAX_INLINE_TEST_MESSAGE_CHARS", "220"))
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
) -> None:
    print(f"[end] reason={reason} parts={part_count}")

    if part_count == 0 and turn_count == 0:
        print("[end] nothing to save (0 parts), skipping S3 upload")
        return

    final_commit = await get_git_commit(sandbox)
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
    ) -> None:
        self.sandbox = sandbox
        self.agent_trace = agent_trace
        self.trajectory_id = trajectory_id
        self.environment = environment
        self.task_params = task_params
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
                print(
                    f"[eval] commit {commit[:10]} "
                    f"status={evaluation.status} "
                    f"passed={evaluation.passed}/{evaluation.total}"
                )
                self.emit_event(evaluation)

    def on_done(self, done_task: asyncio.Task[None]) -> None:
        self.tasks.discard(done_task)
        try:
            done_task.result()
        except Exception as task_error:
            print(f"[eval] unexpected task error: {task_error}")

    async def wait(self) -> None:
        while self.tasks:
            pending = list(self.tasks)
            if not pending:
                break
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


def format_turn_end_evaluation_feedback(
    run_payload: dict[str, Any],
) -> str:
    """Render compact, actionable turn-end evaluation feedback."""
    payload = run_payload.get("payload")
    exit_code = run_payload.get("exit_code")

    lines: list[str] = [
        "Turn-end full evaluation result:",
    ]
    if isinstance(exit_code, int):
        lines.append(f"exit_code: {exit_code}")

    def _text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped if stripped else None

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
                suite_passed = int(
                    suite_payload.get("passed", 0) or 0
                )
                suite_total = int(
                    suite_payload.get("total", 0) or 0
                )
                lines.append(
                    f"- {suite_name}: {suite_passed}/{suite_total}"
                )

        tests = payload.get("tests")
        failed_tests: list[dict[str, Any]] = []
        if isinstance(tests, list):
            for item in tests:
                if not isinstance(item, dict):
                    continue
                status = item.get("status")
                status_text = (
                    status.strip().lower()
                    if isinstance(status, str)
                    else ""
                )
                if status_text and status_text != "passed":
                    failed_tests.append(item)
        if failed_tests:
            lines.append(
                "failed_tests: "
                f"{len(failed_tests)} "
                f"(showing up to {MAX_INLINE_FAILED_TESTS})"
            )
            for test in failed_tests[:MAX_INLINE_FAILED_TESTS]:
                suite = _text(test.get("suite")) or "unknown_suite"
                test_id = _text(test.get("test_id")) or "unknown_test"
                status = _text(test.get("status")) or "failed"
                failure_type = _text(test.get("failure_type"))
                detail = _text(test.get("message"))
                if detail is None:
                    detail = _text(test.get("stderr_tail"))
                if detail is None:
                    detail = _text(test.get("stdout_tail"))
                detail = _truncate(detail, MAX_INLINE_TEST_MESSAGE_CHARS)
                status_label = (
                    f"{status}/{failure_type}"
                    if failure_type is not None
                    else status
                )
                if detail is not None:
                    lines.append(
                        f"- {suite}/{test_id}: {status_label}: {detail}"
                    )
                else:
                    lines.append(
                        f"- {suite}/{test_id}: {status_label}"
                    )
            remaining = len(failed_tests) - MAX_INLINE_FAILED_TESTS
            if remaining > 0:
                lines.append(
                    f"... and {remaining} more failing tests."
                )
        else:
            lines.append("failed_tests: 0")

        payload_error = _text(payload.get("error"))
        if payload_error is not None:
            lines.append(f"error: {payload_error}")
    else:
        lines.append("payload: null")
        stdout = _text(run_payload.get("stdout"))
        stderr = _text(run_payload.get("stderr"))
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
    suite_results: dict[str, Any] = {}
    tests: list[EvalTestResult] = []
    event_error = error
    if isinstance(payload, dict):
        passed = int(payload.get("passed", 0) or 0)
        failed = int(payload.get("failed", 0) or 0)
        total = int(payload.get("total", 0) or 0)
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
        suite_results=suite_results,
        tests=tests,
        error=event_error,
    )


async def run_trajectory(
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
    task_lang: str = "en",
    task_params: dict[str, str] | None = None,
) -> str:
    if trajectory_id is None:
        trajectory_id = str(uuid.uuid4())
    if max_turns is not None and max_turns <= 0:
        max_turns = None
    agent_name = (agent or DEFAULT_AGENT).strip().lower()

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
    banner = "=" * 72
    print(banner)
    print(f"TRAJECTORY_ID: {trajectory_id}")
    print(f"TRACE_S3_URI: {trace_s3_uri}")
    print(f"BUNDLE_S3_URI: {bundle_s3_uri}")
    print(
        f"agent={agent_name} model={resolved_model} "
        f"max_parts={max_parts} max_turns={max_turns} "
        f"timeout={timeout_seconds}s "
        f"message_timeout={message_timeout_seconds}s"
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
    end_reason: str = "agent_error"

    try:
        # --- Create sandbox ---
        config = SandboxConfig(
            timeout=timeout_seconds,
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
            setup_script=setup_script,
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

        # --- Create session ---
        session_id = await agent_backend.create_session(
            trajectory_id,
        )
        if not session_id:
            raise RuntimeError(
                f"Failed to create session for agent={agent_name}",
            )

        if existing_trace is not None:
            agent_trace = existing_trace
            agent_trace.session_id = session_id
            agent_trace.agent = agent_name
            agent_trace.agent_model = resolved_model
            agent_trace.session_end = None
            part_count = get_trace_last_part(agent_trace)
            turn_count = get_trace_last_turn(agent_trace)
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

        evaluator = EvaluationScheduler(
            sandbox=sandbox,
            agent_trace=agent_trace,
            trajectory_id=trajectory_id,
            environment=environment,
            task_params=task_params_loaded,
        )

        # --- Main loop ---
        tracker = SolveTracker(required_test_paths)
        for part_record in agent_trace.parts:
            tracker.update(list(part_record.envoi_calls))

        prompt_text = (
            prompt if part_count == 0
            else build_followup_prompt(tracker)
        )
        next_turn_feedback_eval_id: str | None = None
        consecutive_turn_failures = 0

        while part_count < max_parts:
            winner = first_winning_commit(agent_trace.evaluations)
            if winner is not None:
                winner_commit, winner_eval = winner
                await checkout_workspace_commit(
                    sandbox,
                    winner_commit,
                )
                print(
                    "[eval] winner detected before turn start "
                    f"commit={winner_commit[:10]} "
                    f"part={winner_eval.part} "
                    f"score={winner_eval.passed}/{winner_eval.total}"
                )
                end_reason = "solved"
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
            remaining_parts = max(1, max_parts - part_count)

            # Agent decides its own timeout (no branching)
            turn_timeout_seconds = agent_backend.compute_turn_timeout(
                remaining_parts=remaining_parts,
                remaining_run_seconds=remaining_run_seconds,
                message_timeout_seconds=message_timeout_seconds,
            )

            turn_banner = "=" * 60
            builtins.print(f"\n{turn_banner}", flush=True)
            builtins.print(
                f" TURN {turn_count + 1}  "
                f"(part_count {part_count}/{max_parts}, "
                f"timeout {turn_timeout_seconds}s)",
                flush=True,
            )
            builtins.print(turn_banner, flush=True)

            turn_started_at = datetime.now(UTC).isoformat()
            previous_part_count = part_count
            streamed_parts = 0
            observed_parts = 0
            git_commit = await get_git_commit(sandbox)

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
                schedule_commit_evaluation=evaluator.schedule,
            )

            turn_outcome = await agent_backend.run_turn(
                prompt_text=prompt_text,
                timeout=turn_timeout_seconds,
                remaining_parts_budget=remaining_parts,
                global_part_count=part_count,
                global_max_parts=max_parts,
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

            info = response.get("info", {})
            parts = response.get("parts", [])
            response_message_id = info.get("id")
            print(
                f"[progress] response "
                f"id={response_message_id} "
                f"parts={len(parts)}"
            )
            log_message_parts(response)

            session_ids = turn_outcome.session_ids
            session_objects = turn_outcome.session_objects
            new_messages = turn_outcome.new_messages
            print(
                f"[progress] new_messages="
                f"{len(new_messages)} "
                f"sessions={len(session_ids)}"
            )
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
            winner = first_winning_commit(agent_trace.evaluations)
            if winner is not None:
                winner_commit, winner_eval = winner
                await checkout_workspace_commit(
                    sandbox,
                    winner_commit,
                )
                print(
                    "[eval] winner detected after turn "
                    f"commit={winner_commit[:10]} "
                    f"part={winner_eval.part} "
                    f"score={winner_eval.passed}/{winner_eval.total}"
                )
                end_reason = "solved"
                break
            turn_end_eval_feedback = ""
            turn_end_passed: int | None = None
            turn_end_total: int | None = None
            turn_end_has_error = True
            turn_end_eval_payload: dict[str, Any] | None = None
            turn_end_event: EvalEvent | None = None
            try:
                turn_end_eval_payload = await run_workspace_evaluation(
                    sandbox=sandbox,
                )
                turn_end_eval_feedback = (
                    format_turn_end_evaluation_feedback(
                        turn_end_eval_payload,
                    )
                )
                payload = turn_end_eval_payload.get("payload")
                if isinstance(payload, dict):
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
                    print(
                        "[eval] turn_end "
                        f"passed={turn_end_passed}/{turn_end_total} "
                        f"status_error={turn_end_has_error}"
                    )
                else:
                    turn_end_has_error = True
                    print("[eval] turn_end payload missing")
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

            eval_label = (
                f"{turn_end_passed}/{turn_end_total}"
                if isinstance(turn_end_passed, int)
                and isinstance(turn_end_total, int)
                else "unknown"
            )
            print(
                f"[progress] turn={turn_count} "
                f"commit={git_commit} "
                f"parts=+{new_parts} "
                f"total={part_count}/{max_parts} "
                f"(observed_parts={observed_parts} "
                f"streamed_parts={streamed_parts}) "
                f"envoi_calls={len(new_envoi_calls)} "
                f"turn_end_eval={eval_label} "
                f"started={turn_started_at}"
            )

            if (
                isinstance(turn_end_passed, int)
                and isinstance(turn_end_total, int)
                and turn_end_total > 0
                and turn_end_passed == turn_end_total
                and not turn_end_has_error
            ):
                end_reason = "solved"
                break

            if part_count >= max_parts:
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
        print(f"[error] {exc}")
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
        if evaluator is not None:
            try:
                await evaluator.wait()
            except Exception:
                pass
        if sandbox is not None and agent_trace is not None:
            try:
                winner = first_winning_commit(agent_trace.evaluations)
                if winner is not None:
                    winner_commit, winner_eval = winner
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
                await end_session(
                    sandbox,
                    agent_trace,
                    part_count,
                    turn_count,
                    end_reason,
                    environment=environment,
                    task_params=task_params_loaded,
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
    parser.add_argument("--max-parts", type=int, default=1000)
    parser.add_argument("--max-turns", type=int, default=None)
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
            message_timeout_seconds=args.message_timeout_seconds,
            trajectory_id=args.trajectory_id,
            codex_auth_json_b64=codex_auth_b64,
            sandbox_provider=args.sandbox_provider,
            task_dir=args.task_dir,
            environment_dir=args.environment_dir,
        )
    )
