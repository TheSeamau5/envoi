from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import envoi_code.orchestrator as orchestrator
import pytest
from envoi_code.agents.base import (
    Agent,
    AgentCredentials,
    AgentFatalError,
    AgentSetupContext,
    AgentTurnOutcome,
)
from envoi_code.models import AgentTrace
from envoi_code.sandbox.base import CommandResult, Sandbox, SandboxImageRequirements


class FakeSandbox:
    name = "fake"
    sandbox_id = "sandbox-001"

    async def run(
        self,
        cmd: str,
        *,
        timeout: int = 60,
        quiet: bool = False,
        stream_output: bool = False,
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
        on_stderr_line: Callable[[str], Awaitable[None]] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        del cmd, timeout, quiet, stream_output, cwd, env
        if on_stdout_line is not None:
            await on_stdout_line("")
        if on_stderr_line is not None:
            await on_stderr_line("")
        return CommandResult(exit_code=0, stdout="", stderr="", duration_ms=0)

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        ensure_dir: bool = True,
        log_upload: bool = False,
    ) -> None:
        del path, content, ensure_dir, log_upload

    async def read_file(self, path: str) -> str:
        del path
        return ""

    async def read_file_bytes(self, path: str) -> bytes:
        del path
        return b""

    async def terminate(self) -> None:
        return None


class NullTurnAgent:
    name = "fake-agent"
    session_id = "sess-001"
    log_files: list[str] = []

    @staticmethod
    def resolve_credentials(
        auth_json_b64: str | None = None,
    ) -> AgentCredentials:
        del auth_json_b64
        return AgentCredentials(api_key="")

    @staticmethod
    def resolve_model(model: str | None) -> str:
        return model or "gpt-5"

    @staticmethod
    def image_requirements() -> SandboxImageRequirements:
        return SandboxImageRequirements()

    def compute_turn_timeout(
        self,
        *,
        remaining_parts: int | None,
        remaining_run_seconds: float,
        message_timeout_seconds: int | None,
    ) -> int:
        del remaining_parts, remaining_run_seconds
        return message_timeout_seconds or 60

    async def setup(
        self,
        sandbox: Sandbox,
        ctx: AgentSetupContext,
    ) -> None:
        del sandbox, ctx

    async def create_session(
        self,
        trajectory_id: str,
    ) -> str:
        del trajectory_id
        return self.session_id

    async def run_turn(
        self,
        **kwargs,
    ) -> AgentTurnOutcome | None:
        del kwargs
        return None

    def on_resume(
        self,
        existing_messages: list[dict[str, Any]],
    ) -> None:
        del existing_messages

    async def recover_session(
        self,
        trajectory_id: str,
        attempt: int,
    ) -> str:
        del trajectory_id, attempt
        return ""

    async def collect_crash_messages(
        self,
        session_id: str,
    ) -> list[dict[str, Any]] | None:
        del session_id
        return None

    async def stop(self) -> None:
        return None

    def on_turn_complete(self, outcome: AgentTurnOutcome) -> None:
        del outcome


class FatalStopAgent(NullTurnAgent):
    async def run_turn(self, **kwargs) -> AgentTurnOutcome | None:
        del kwargs
        raise AgentFatalError("stopped", stop_reason="part_limit")


class UsageLimitAgent(NullTurnAgent):
    async def run_turn(self, **kwargs) -> AgentTurnOutcome | None:
        del kwargs
        raise AgentFatalError("Codex usage limit reached", stop_reason="agent_error")


class RecoveringNullTurnAgent(NullTurnAgent):
    def __init__(self) -> None:
        self.recovery_attempts: list[int] = []

    async def recover_session(
        self,
        trajectory_id: str,
        attempt: int,
    ) -> str:
        del trajectory_id
        self.recovery_attempts.append(attempt)
        return f"recovery-{attempt}"


class RecordingTimeoutAgent(NullTurnAgent):
    def __init__(self) -> None:
        self.remaining_parts: int | None = None
        self.remaining_run_seconds: float | None = None
        self.message_timeout_seconds: int | None = None
        self.seen_timeout: int | None = None

    def compute_turn_timeout(
        self,
        *,
        remaining_parts: int | None,
        remaining_run_seconds: float,
        message_timeout_seconds: int | None,
    ) -> int:
        self.remaining_parts = remaining_parts
        self.remaining_run_seconds = remaining_run_seconds
        self.message_timeout_seconds = message_timeout_seconds
        return max(1, int(remaining_run_seconds))

    async def run_turn(self, **kwargs) -> AgentTurnOutcome | None:
        self.seen_timeout = kwargs["timeout"]
        return None


async def noop_flush_logs(**kwargs) -> None:
    del kwargs


def capture_eval_log_record(record: dict[str, object]) -> None:
    del record


class FakeEvaluator:
    def schedule(self, *args, **kwargs) -> None:
        del args, kwargs


def make_trace() -> AgentTrace:
    return AgentTrace(
        trajectory_id="traj-001",
        session_id="sess-001",
        agent="codex",
        agent_model="gpt-5",
        started_at="2026-01-01T00:00:00+00:00",
    )


def patch_turn_loop_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "TURN_RECOVERY_RETRIES", 0)
    monkeypatch.setattr(
        orchestrator,
        "EvaluationScheduler",
        lambda **kwargs: FakeEvaluator(),
    )
    monkeypatch.setattr(
        orchestrator,
        "get_git_commit",
        lambda *args, **kwargs: asyncio.sleep(0, result=None),
    )
    monkeypatch.setattr(
        orchestrator,
        "make_stream_part_callback",
        lambda **kwargs: (lambda *args, **inner_kwargs: None),
    )
    monkeypatch.setattr(
        orchestrator,
        "dump_sandbox_logs",
        lambda *args, **kwargs: asyncio.sleep(0),
    )
    monkeypatch.setattr(orchestrator, "save_trace_parquet", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "first_winning_commit", lambda evaluations: None)
    monkeypatch.setattr(
        orchestrator,
        "find_latest_completed_turn_end_tests",
        lambda agent_trace: [],
    )
    monkeypatch.setattr(orchestrator, "update_log_context", lambda **kwargs: None)


def run_turn_loop_with_agent(
    agent_backend: Agent,
    monkeypatch,
) -> orchestrator.TurnLoopResult:
    patch_turn_loop_dependencies(monkeypatch)
    return asyncio.run(
        orchestrator.run_turn_loop(
            sandbox=FakeSandbox(),
            eval_sandbox=FakeSandbox(),
            agent_backend=agent_backend,
            agent_trace=make_trace(),
            trajectory_id="traj-001",
            project="default",
            session_id="sess-001",
            agent_name="codex",
            resolved_model="gpt-5",
            environment="c_compiler",
            task_params_loaded={},
            prompt="solve it",
            required_test_paths=[],
            selected_test_paths=[],
            test_timeout_seconds=None,
            max_parts=None,
            max_turns=None,
            timeout_seconds=3_600,
            message_timeout_seconds=60,
            start_time=time.monotonic(),
            initial_turn_count=0,
            initial_part_count=0,
            initial_git_commit=None,
            failed_tests_feedback_limit=50,
            normalized_advisor_model=None,
            normalized_advisor_thinking_level="low",
            advisor_max_output_tokens=None,
            advisor_system_prompt_override=None,
            advisor_user_prompt_prefix_override=None,
            flush_logs=noop_flush_logs,
            capture_eval_log_record=capture_eval_log_record,
        ),
    )


def test_run_turn_loop_preserves_agent_error_after_recovery_exhaustion(
    monkeypatch,
) -> None:
    result = run_turn_loop_with_agent(NullTurnAgent(), monkeypatch)
    assert result.end_reason == "agent_error"


def test_run_turn_loop_preserves_explicit_fatal_part_limit(monkeypatch) -> None:
    result = run_turn_loop_with_agent(FatalStopAgent(), monkeypatch)
    assert result.end_reason == "part_limit"


def test_run_turn_loop_preserves_usage_limit_as_agent_error(monkeypatch) -> None:
    result = run_turn_loop_with_agent(UsageLimitAgent(), monkeypatch)
    assert result.end_reason == "agent_error"


def test_run_turn_loop_allows_unbounded_recovery_until_timeout(monkeypatch) -> None:
    agent = RecoveringNullTurnAgent()
    patch_turn_loop_dependencies(monkeypatch)
    monkeypatch.setattr(orchestrator, "TURN_RECOVERY_RETRIES", None)
    stop_reasons = [None, None, None, None, "timeout"]

    def fake_resolve_turn_start_stop_reason(**kwargs):
        del kwargs
        return stop_reasons.pop(0)

    monkeypatch.setattr(
        orchestrator,
        "resolve_turn_start_stop_reason",
        fake_resolve_turn_start_stop_reason,
    )

    result = asyncio.run(
        orchestrator.run_turn_loop(
            sandbox=FakeSandbox(),
            eval_sandbox=FakeSandbox(),
            agent_backend=agent,
            agent_trace=make_trace(),
            trajectory_id="traj-001",
            project="default",
            session_id="sess-001",
            agent_name="codex",
            resolved_model="gpt-5",
            environment="c_compiler",
            task_params_loaded={},
            prompt="solve it",
            required_test_paths=[],
            selected_test_paths=[],
            test_timeout_seconds=None,
            max_parts=None,
            max_turns=None,
            timeout_seconds=3_600,
            message_timeout_seconds=None,
            start_time=time.monotonic(),
            initial_turn_count=0,
            initial_part_count=0,
            initial_git_commit=None,
            failed_tests_feedback_limit=50,
            normalized_advisor_model=None,
            normalized_advisor_thinking_level="low",
            advisor_max_output_tokens=None,
            advisor_system_prompt_override=None,
            advisor_user_prompt_prefix_override=None,
            flush_logs=noop_flush_logs,
            capture_eval_log_record=capture_eval_log_record,
        ),
    )

    assert result.end_reason == "timeout"
    assert agent.recovery_attempts == [1, 2, 3, 4]


def test_run_turn_loop_uses_run_budget_when_message_timeout_is_unset(monkeypatch) -> None:
    agent = RecordingTimeoutAgent()
    patch_turn_loop_dependencies(monkeypatch)

    result = asyncio.run(
        orchestrator.run_turn_loop(
            sandbox=FakeSandbox(),
            eval_sandbox=FakeSandbox(),
            agent_backend=agent,
            agent_trace=make_trace(),
            trajectory_id="traj-001",
            project="default",
            session_id="sess-001",
            agent_name="codex",
            resolved_model="gpt-5",
            environment="c_compiler",
            task_params_loaded={},
            prompt="solve it",
            required_test_paths=[],
            selected_test_paths=[],
            test_timeout_seconds=None,
            max_parts=None,
            max_turns=None,
            timeout_seconds=5_000,
            message_timeout_seconds=None,
            start_time=time.monotonic(),
            initial_turn_count=0,
            initial_part_count=0,
            initial_git_commit=None,
            failed_tests_feedback_limit=50,
            normalized_advisor_model=None,
            normalized_advisor_thinking_level="low",
            advisor_max_output_tokens=None,
            advisor_system_prompt_override=None,
            advisor_user_prompt_prefix_override=None,
            flush_logs=noop_flush_logs,
            capture_eval_log_record=capture_eval_log_record,
        ),
    )

    assert result.end_reason == "agent_error"
    assert agent.remaining_parts is None
    assert agent.message_timeout_seconds is None
    assert isinstance(agent.seen_timeout, int)
    assert agent.seen_timeout > 600


def test_run_trajectory_rejects_modal_timeout_above_function_ceiling() -> None:
    with pytest.raises(ValueError, match="Modal function ceiling"):
        asyncio.run(
            orchestrator.run_trajectory(
                project="c-compiler",
                timeout_seconds=orchestrator.MODAL_FUNCTION_TIMEOUT_SECONDS + 1,
            )
        )
