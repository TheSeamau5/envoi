from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import envoi_code.orchestrator as orchestrator
from envoi_code.models import AgentTrace, PartRecord
from envoi_code.sandbox.base import CommandResult


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


def make_trace() -> AgentTrace:
    trace = AgentTrace(
        trajectory_id="traj-001",
        session_id="sess-001",
        agent="codex",
        agent_model="gpt-5",
        started_at="2026-01-01T00:00:00+00:00",
    )
    trace.parts.extend(
        [
            PartRecord(
                trajectory_id="traj-001",
                session_id="sess-001",
                agent="codex",
                agent_model="gpt-5",
                part=1,
                timestamp="2026-01-01T00:00:01+00:00",
            ),
            PartRecord(
                trajectory_id="traj-001",
                session_id="sess-001",
                agent="codex",
                agent_model="gpt-5",
                part=2,
                timestamp="2026-01-01T00:00:02+00:00",
            ),
        ]
    )
    return trace


def make_payload(passed: int = 1, total: int = 1) -> dict[str, object]:
    return {
        "command": "run-eval",
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "payload": {
            "passed": passed,
            "failed": total - passed,
            "total": total,
            "duration_ms": 10,
            "suite_results": {},
            "tests": [],
        },
    }


def test_evaluation_scheduler_processes_commits_in_fifo_order(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "save_trace_parquet", lambda *args, **kwargs: None)

    trace = make_trace()
    sandbox = FakeSandbox()
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    call_order: list[tuple[str, str]] = []

    async def fake_run_commit_evaluation(**kwargs):
        commit = kwargs["commit"]
        call_order.append(("start", commit))
        if commit == "a" * 40:
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        await asyncio.sleep(0)
        call_order.append(("done", commit))
        return make_payload()

    monkeypatch.setattr(
        orchestrator,
        "run_commit_evaluation",
        fake_run_commit_evaluation,
    )

    async def scenario() -> None:
        scheduler = orchestrator.EvaluationScheduler(
            sandbox=sandbox,
            agent_sandbox=sandbox,
            agent_trace=trace,
            trajectory_id="traj-001",
            project="c-compiler",
            environment="c_compiler",
            task_params={},
        )
        scheduler.schedule("a" * 40, 1, 1)
        scheduler.schedule("b" * 40, 2, 1)

        await asyncio.wait_for(first_started.wait(), timeout=1)
        await asyncio.sleep(0.05)
        assert not second_started.is_set()

        release_first.set()
        await asyncio.wait_for(scheduler.wait(), timeout=1)
        await asyncio.wait_for(scheduler.stop(), timeout=1)

    asyncio.run(scenario())

    assert call_order == [
        ("start", "a" * 40),
        ("done", "a" * 40),
        ("start", "b" * 40),
        ("done", "b" * 40),
    ]
    assert trace.evaluations["a" * 40].status == "completed"
    assert trace.evaluations["b" * 40].status == "completed"
    assert [event.status for event in trace.parts[0].eval_events_delta] == [
        "queued",
        "running",
        "completed",
    ]
    assert [event.status for event in trace.parts[1].eval_events_delta] == [
        "queued",
        "running",
        "completed",
    ]


def test_evaluation_scheduler_cancel_pending_marks_running_and_queued_failed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(orchestrator, "save_trace_parquet", lambda *args, **kwargs: None)

    trace = make_trace()
    sandbox = FakeSandbox()
    first_started = asyncio.Event()

    async def fake_run_commit_evaluation(**kwargs):
        del kwargs
        first_started.set()
        await asyncio.Future()
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        orchestrator,
        "run_commit_evaluation",
        fake_run_commit_evaluation,
    )

    async def scenario() -> None:
        scheduler = orchestrator.EvaluationScheduler(
            sandbox=sandbox,
            agent_sandbox=sandbox,
            agent_trace=trace,
            trajectory_id="traj-001",
            project="c-compiler",
            environment="c_compiler",
            task_params={},
        )
        scheduler.schedule("a" * 40, 1, 1)
        scheduler.schedule("b" * 40, 2, 1)
        await asyncio.wait_for(first_started.wait(), timeout=1)
        await asyncio.wait_for(
            scheduler.cancel_pending(reason="Cancelled during test"),
            timeout=1,
        )
        await asyncio.wait_for(scheduler.stop(), timeout=1)

    asyncio.run(scenario())

    assert trace.evaluations["a" * 40].status == "failed"
    assert trace.evaluations["a" * 40].error == "Cancelled during test"
    assert trace.evaluations["a" * 40].completed_at is not None
    assert trace.evaluations["b" * 40].status == "failed"
    assert trace.evaluations["b" * 40].error == "Cancelled during test"

    # Verify running eval's cancellation was emitted to the trace
    # (part 0 = eval "a" which was running when cancelled)
    a_statuses = [event.status for event in trace.parts[0].eval_events_delta]
    assert "failed" in a_statuses, (
        "cancel_pending should emit a failed event for running evaluations"
    )
    # (part 1 = eval "b" which was queued when cancelled)
    b_statuses = [event.status for event in trace.parts[1].eval_events_delta]
    assert "failed" in b_statuses


def test_resolve_sandbox_timeout_seconds_uses_modal_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "SHUTDOWN_GRACE_SECONDS", 300)
    monkeypatch.setattr(orchestrator, "MODAL_FUNCTION_TIMEOUT_SECONDS", 43_200)

    assert (
        orchestrator.resolve_sandbox_timeout_seconds(
            timeout_seconds=7_200,
            sandbox_provider="modal",
        )
        == 43_200
    )
    assert (
        orchestrator.resolve_sandbox_timeout_seconds(
            timeout_seconds=7_200,
            sandbox_provider="e2b",
        )
        == 7_500
    )
