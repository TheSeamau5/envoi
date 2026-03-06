from __future__ import annotations

import asyncio
import time

import envoi_code.orchestrator as orchestrator
from envoi_code.agents.base import AgentFatalError
from envoi_code.models import AgentTrace


class FakeCommandResult:
    def unpack(self) -> tuple[int, str, str]:
        return 0, "", ""


class FakeSandbox:
    async def run(self, *args, **kwargs) -> FakeCommandResult:
        return FakeCommandResult()


class NullTurnAgent:
    def compute_turn_timeout(
        self,
        *,
        remaining_parts: int,
        remaining_run_seconds: float,
        message_timeout_seconds: int,
    ) -> int:
        del remaining_parts, remaining_run_seconds
        return message_timeout_seconds

    async def run_turn(self, **kwargs):
        del kwargs
        return None

    async def recover_session(
        self,
        trajectory_id: str,
        attempt: int,
    ) -> str | None:
        del trajectory_id, attempt
        return None

    def on_turn_complete(self, turn_outcome) -> None:
        del turn_outcome


class FatalStopAgent(NullTurnAgent):
    async def run_turn(self, **kwargs):
        del kwargs
        raise AgentFatalError("stopped", stop_reason="part_limit")


async def noop_flush_logs(**kwargs) -> None:
    del kwargs


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


def run_turn_loop_with_agent(agent_backend, monkeypatch) -> orchestrator.TurnLoopResult:
    patch_turn_loop_dependencies(monkeypatch)
    return asyncio.run(
        orchestrator.run_turn_loop(
            sandbox=FakeSandbox(),
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
