from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

import envoi_code.orchestrator as orchestrator
import envoi_code.utils.stream as stream_utils
from envoi_code.models import AgentTrace, PartRecord, TurnRecord
from envoi_code.sandbox.base import CommandResult
from envoi_code.utils.solve import SolveTracker


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


class FakeEvaluator:
    def __init__(self) -> None:
        self.wait_called = False
        self.stop_called = False
        self.cancel_reason: str | None = None

    async def wait(self) -> None:
        self.wait_called = True

    async def stop(self) -> None:
        self.stop_called = True

    async def cancel_pending(self, *, reason: str) -> None:
        self.cancel_reason = reason


async def noop_flush_logs(**kwargs) -> None:
    del kwargs


async def invoke_stream_callback(
    callback: Callable[[dict[str, Any]], Awaitable[None]],
    event: dict[str, Any],
) -> None:
    await callback(event)


def make_trace() -> AgentTrace:
    return AgentTrace(
        trajectory_id="traj-001",
        session_id="sess-001",
        agent="codex",
        agent_model="gpt-5",
        started_at="2026-01-01T00:00:00+00:00",
    )


def make_turn() -> TurnRecord:
    return TurnRecord(
        trajectory_id="traj-001",
        session_id="sess-001",
        agent="codex",
        turn=1,
        timestamp="2026-01-01T00:00:00+00:00",
        agent_model="gpt-5",
        parts=[],
    )


def test_stream_part_callback_uses_orchestrator_time_for_timestamp(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        stream_utils,
        "get_changed_files",
        lambda sandbox: asyncio.sleep(0, result=[]),
    )
    monkeypatch.setattr(
        stream_utils,
        "save_trace_parquet",
        lambda *args, **kwargs: None,
    )

    time_values = iter([1_000.0, 1_005.0])
    monkeypatch.setattr(stream_utils.time, "time", lambda: next(time_values))

    trace = make_trace()
    turn = make_turn()
    callback = stream_utils.make_stream_part_callback(
        sandbox=FakeSandbox(),
        trajectory_id="traj-001",
        agent_trace=trace,
        tracker=SolveTracker([]),
        environment="c_compiler",
        task_params={},
        agent_name="codex",
        resolved_model="gpt-5",
        effective_max_parts=None,
        part_counter=[0],
        git_commit_ref=[None],
        last_part_timestamp_ms_ref=[None],
        turn_record=turn,
        session_id="sess-001",
    )

    asyncio.run(
        invoke_stream_callback(
            callback,
            {
                "event": "part.completed",
                "role": "assistant",
                "part_type": "text",
                "item_type": "agent_message",
                "summary": "hello",
                "timestamp_ms": 946684800000,
            },
        )
    )
    asyncio.run(
        invoke_stream_callback(
            callback,
            {
                "event": "part.completed",
                "role": "assistant",
                "part_type": "text",
                "item_type": "agent_message",
                "summary": "world",
                "timestamp_ms": 946684801000,
            },
        )
    )

    assert len(trace.parts) == 2
    first_timestamp = datetime.fromisoformat(trace.parts[0].timestamp)
    second_timestamp = datetime.fromisoformat(trace.parts[1].timestamp)
    assert first_timestamp.year >= 2025
    assert second_timestamp.year >= 2025
    assert trace.parts[0].timestamp != "2000-01-01T00:00:00+00:00"
    assert trace.parts[1].duration_ms == 5000


def test_finalize_trajectory_run_persists_minimal_session_end_before_shutdown(
    monkeypatch,
) -> None:
    saved_reasons: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "save_trace_parquet",
        lambda trajectory_id, trace, **kwargs: saved_reasons.append(
            trace.session_end.reason if trace.session_end else "",
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "artifact_uri",
        lambda trajectory_id, filename, **kwargs: f"s3://bucket/{trajectory_id}/{filename}",
    )

    trace = make_trace()
    trace.parts.append(
        PartRecord(
            trajectory_id="traj-001",
            session_id="sess-001",
            agent="codex",
            agent_model="gpt-5",
            part=1,
            timestamp="2026-01-01T00:00:01+00:00",
        )
    )
    trace.turns.append(make_turn())

    part_count, turn_count, end_reason, latest_commit = asyncio.run(
        orchestrator.finalize_trajectory_run(
            trajectory_id="traj-001",
            project="c-compiler",
            sandbox=None,
            eval_sandbox=None,
            agent_trace=trace,
            evaluator=None,
            part_count=0,
            turn_count=0,
            end_reason="agent_error",
            latest_git_commit="abc123",
            environment="c_compiler",
            task_params_loaded={},
            structured_logs=[],
            eval_structured_logs=[],
            flush_logs=noop_flush_logs,
            logs_flush_task=None,
            logs_flush_wakeup=None,
            logs_flush_stop=None,
            eval_logs_flush=noop_flush_logs,
            eval_logs_flush_task=None,
            eval_logs_flush_wakeup=None,
            eval_logs_flush_stop=None,
            log_callback_token=None,
            log_context_token=None,
        )
    )

    assert trace.session_end is not None
    assert trace.session_end.reason == "agent_error"
    assert trace.session_end.total_parts == 1
    assert trace.session_end.total_turns == 1
    assert saved_reasons[0] == "agent_error"
    assert part_count == 1
    assert turn_count == 1
    assert end_reason == "agent_error"
    assert latest_commit == "abc123"


def test_finalize_trajectory_run_drains_and_stops_evaluator(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "save_trace_parquet", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        orchestrator,
        "artifact_uri",
        lambda trajectory_id, filename, **kwargs: f"s3://bucket/{trajectory_id}/{filename}",
    )
    monkeypatch.setattr(orchestrator, "EVALUATOR_DRAIN_TIMEOUT_SECONDS", 0)

    trace = make_trace()
    trace.parts.append(
        PartRecord(
            trajectory_id="traj-001",
            session_id="sess-001",
            agent="codex",
            agent_model="gpt-5",
            part=1,
            timestamp="2026-01-01T00:00:01+00:00",
        )
    )
    trace.turns.append(make_turn())
    evaluator = FakeEvaluator()

    asyncio.run(
        orchestrator.finalize_trajectory_run(
            trajectory_id="traj-001",
            project="c-compiler",
            sandbox=None,
            eval_sandbox=None,
            agent_trace=trace,
            evaluator=cast(orchestrator.EvaluationScheduler, evaluator),
            part_count=1,
            turn_count=1,
            end_reason="agent_error",
            latest_git_commit="abc123",
            environment="c_compiler",
            task_params_loaded={},
            structured_logs=[],
            eval_structured_logs=[],
            flush_logs=noop_flush_logs,
            logs_flush_task=None,
            logs_flush_wakeup=None,
            logs_flush_stop=None,
            eval_logs_flush=noop_flush_logs,
            eval_logs_flush_task=None,
            eval_logs_flush_wakeup=None,
            eval_logs_flush_stop=None,
            log_callback_token=None,
            log_context_token=None,
        )
    )

    assert evaluator.wait_called is True
    assert evaluator.stop_called is True
    assert evaluator.cancel_reason is None
