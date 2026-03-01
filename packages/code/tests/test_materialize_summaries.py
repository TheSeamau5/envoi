"""Tests for the materialize_summaries script."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

from envoi_code.models import AgentTrace, EvalEvent, PartRecord, SessionEnd, TurnRecord
from envoi_code.scripts.materialize_summaries import (
    compute_added_lines,
    materialize_command,
    process_trajectory,
)
from envoi_code.utils.trace_parquet import agent_trace_to_rows, write_trace_parquet


def make_trace(
    trajectory_id: str = "traj-001",
    agent: str = "codex",
    agent_model: str = "gpt-4",
    environment: str = "c_compiler",
    eval_events: list[list[EvalEvent]] | None = None,
    session_end_reason: str = "solved",
    num_parts: int = 5,
) -> tuple[AgentTrace, dict[str, object]]:
    """Create a test AgentTrace with optional evaluation events."""
    parts: list[PartRecord] = []
    for idx in range(num_parts):
        events = eval_events[idx] if eval_events and idx < len(eval_events) else []
        parts.append(
            PartRecord(
                trajectory_id=trajectory_id,
                session_id="sess-001",
                agent=agent,
                agent_model=agent_model,
                part=idx,
                timestamp=f"2026-01-01T00:{idx:02d}:00+00:00",
                content_token_estimate=100,
                eval_events_delta=events,
                git_commit=f"commit{idx:03d}",
            ),
        )

    turns = [
        TurnRecord(
            trajectory_id=trajectory_id,
            session_id="sess-001",
            agent=agent,
            turn=0,
            part_start=0,
            part_end=num_parts - 1,
            timestamp="2026-01-01T00:00:00+00:00",
            agent_model=agent_model,
        ),
    ]

    trace = AgentTrace(
        trajectory_id=trajectory_id,
        session_id="sess-001",
        agent=agent,
        agent_model=agent_model,
        started_at="2026-01-01T00:00:00+00:00",
        parts=parts,
        turns=turns,
        session_end=SessionEnd(
            reason=session_end_reason,
            total_parts=num_parts,
            total_turns=1,
        ),
    )

    suites = {"all/basics/smoke": {"passed": 7, "total": 7}}
    task_params = {"target": "x86_64"}
    meta = {
        "environment": environment,
        "task_params": task_params,
        "suites": suites,
        "bundle_uri": f"s3://bucket/trajectories/{trajectory_id}/repo.bundle",
    }
    return trace, meta


def write_test_trace(
    base_dir: Path,
    trace: AgentTrace,
    meta: dict[str, object],
) -> Path:
    """Write a trace to a subdirectory under base_dir."""
    traj_dir = base_dir / trace.trajectory_id
    traj_dir.mkdir(parents=True, exist_ok=True)
    rows = agent_trace_to_rows(
        trace,
        environment=str(meta["environment"]),
        task_params=meta.get("task_params") or {},
        suites=meta.get("suites") or {},
        bundle_uri=meta.get("bundle_uri"),
    )
    trace_path = traj_dir / "trace.parquet"
    write_trace_parquet(rows, str(trace_path))
    return trace_path


def test_process_trajectory_with_evaluations() -> None:
    """Test that process_trajectory extracts correct summary data."""
    eval_events = [[] for _ in range(5)]
    eval_events[2] = [
        EvalEvent(
            eval_id="eval-1",
            kind="commit_async",
            trigger_part=2,
            trigger_turn=0,
            target_commit="abc123",
            status="completed",
            passed=5,
            failed=2,
            total=7,
            suite_results={"all/basics/smoke": {"passed": 5, "total": 7}},
            queued_at="2026-01-01T00:02:00+00:00",
            finished_at="2026-01-01T00:02:30+00:00",
        ),
    ]
    eval_events[4] = [
        EvalEvent(
            eval_id="eval-2",
            kind="commit_async",
            trigger_part=4,
            trigger_turn=0,
            target_commit="def456",
            status="completed",
            passed=7,
            failed=0,
            total=7,
            suite_results={"all/basics/smoke": {"passed": 7, "total": 7}},
            queued_at="2026-01-01T00:04:00+00:00",
            finished_at="2026-01-01T00:04:30+00:00",
        ),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        trace, meta = make_trace(eval_events=eval_events)
        trace_path = write_test_trace(Path(tmp), trace, meta)
        traj_summary, eval_rows = process_trajectory(trace_path)

    assert traj_summary["trajectory_id"] == "traj-001"
    assert traj_summary["environment"] == "c_compiler"
    assert traj_summary["agent_model"] == "gpt-4"
    assert traj_summary["total_parts"] == 5
    assert traj_summary["total_tokens"] == 500
    assert traj_summary["final_passed"] == 7
    assert traj_summary["final_failed"] == 0
    assert traj_summary["final_total"] == 7
    assert traj_summary["session_end_reason"] == "solved"

    # Should have 2 completed evaluations
    assert len(eval_rows) == 2
    assert eval_rows[0]["eval_id"] == "eval-1"
    assert eval_rows[0]["passed"] == 5
    assert eval_rows[1]["eval_id"] == "eval-2"
    assert eval_rows[1]["passed"] == 7


def test_process_trajectory_no_evaluations() -> None:
    """Test trajectory with no evaluation events."""
    with tempfile.TemporaryDirectory() as tmp:
        trace, meta = make_trace(eval_events=None)
        trace_path = write_test_trace(Path(tmp), trace, meta)
        traj_summary, eval_rows = process_trajectory(trace_path)

    assert traj_summary["trajectory_id"] == "traj-001"
    assert traj_summary["final_passed"] == 0
    assert traj_summary["final_failed"] == 0
    assert traj_summary["final_total"] == 0
    assert traj_summary["final_suite_results"] is None
    assert len(eval_rows) == 0


def test_materialize_command_full_pipeline() -> None:
    """Test the full materialize_command pipeline with multiple trajectories."""
    eval_events_1 = [[] for _ in range(3)]
    eval_events_1[2] = [
        EvalEvent(
            eval_id="e1",
            kind="commit_async",
            trigger_part=2,
            trigger_turn=0,
            target_commit="commit-a",
            status="completed",
            passed=10,
            failed=5,
            total=15,
            suite_results={"all/basics": {"passed": 10, "total": 15}},
        ),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        source_dir = Path(tmp) / "source"
        dest_dir = Path(tmp) / "dest"
        source_dir.mkdir()

        # Write two trajectories
        trace1, meta1 = make_trace(
            trajectory_id="traj-aaa",
            eval_events=eval_events_1,
            num_parts=3,
        )
        write_test_trace(source_dir, trace1, meta1)

        trace2, meta2 = make_trace(
            trajectory_id="traj-bbb",
            eval_events=None,
            num_parts=2,
        )
        write_test_trace(source_dir, trace2, meta2)

        # Run materialization
        args = argparse.Namespace(
            source=str(source_dir),
            dest=str(dest_dir),
            extract_code=False,
            incremental=False,
        )
        materialize_command(args)

        # Verify trajectory summary
        traj_table = pq.read_table(str(dest_dir / "trajectory_summary.parquet"))
        assert traj_table.num_rows == 2
        traj_rows = traj_table.to_pylist()
        ids = {row["trajectory_id"] for row in traj_rows}
        assert ids == {"traj-aaa", "traj-bbb"}

        # Verify evaluation summary
        eval_table = pq.read_table(str(dest_dir / "evaluation_summary.parquet"))
        assert eval_table.num_rows == 1  # only traj-aaa has an evaluation
        eval_row = eval_table.to_pylist()[0]
        assert eval_row["trajectory_id"] == "traj-aaa"
        assert eval_row["passed"] == 10


def test_materialize_command_incremental() -> None:
    """Test incremental mode skips already-processed trajectories."""
    with tempfile.TemporaryDirectory() as tmp:
        source_dir = Path(tmp) / "source"
        dest_dir = Path(tmp) / "dest"
        source_dir.mkdir()

        # First run: write one trajectory
        trace1, meta1 = make_trace(trajectory_id="traj-first", num_parts=2)
        write_test_trace(source_dir, trace1, meta1)

        args = argparse.Namespace(
            source=str(source_dir),
            dest=str(dest_dir),
            extract_code=False,
            incremental=False,
        )
        materialize_command(args)

        traj_table = pq.read_table(str(dest_dir / "trajectory_summary.parquet"))
        assert traj_table.num_rows == 1

        # Second run: add another trajectory, use incremental mode
        trace2, meta2 = make_trace(trajectory_id="traj-second", num_parts=3)
        write_test_trace(source_dir, trace2, meta2)

        args_inc = argparse.Namespace(
            source=str(source_dir),
            dest=str(dest_dir),
            extract_code=False,
            incremental=True,
        )
        materialize_command(args_inc)

        traj_table = pq.read_table(str(dest_dir / "trajectory_summary.parquet"))
        assert traj_table.num_rows == 2
        ids = {row["trajectory_id"] for row in traj_table.to_pylist()}
        assert ids == {"traj-first", "traj-second"}


def test_materialize_command_empty_source() -> None:
    """Test materialization with no trace files produces empty parquet."""
    with tempfile.TemporaryDirectory() as tmp:
        source_dir = Path(tmp) / "empty_source"
        dest_dir = Path(tmp) / "dest"
        source_dir.mkdir()

        args = argparse.Namespace(
            source=str(source_dir),
            dest=str(dest_dir),
            extract_code=False,
            incremental=False,
        )
        materialize_command(args)

        traj_table = pq.read_table(str(dest_dir / "trajectory_summary.parquet"))
        assert traj_table.num_rows == 0

        eval_table = pq.read_table(str(dest_dir / "evaluation_summary.parquet"))
        assert eval_table.num_rows == 0


def test_compute_added_lines_unified_diff() -> None:
    """Test compute_added_lines correctly parses unified diff format."""
    diff = (
        "--- a/file.c\n"
        "+++ b/file.c\n"
        "@@ -1,3 +1,5 @@\n"
        " #include <stdio.h>\n"
        "+#include <stdlib.h>\n"
        "+#include <string.h>\n"
        " \n"
        " int main() {\n"
    )
    added = compute_added_lines(diff)
    assert added == [1, 2]


def test_compute_added_lines_multiple_hunks() -> None:
    """Test compute_added_lines with multiple diff hunks."""
    diff = (
        "--- a/file.c\n"
        "+++ b/file.c\n"
        "@@ -1,2 +1,3 @@\n"
        " line1\n"
        "+added_at_2\n"
        " line2\n"
        "@@ -10,2 +11,3 @@\n"
        " line10\n"
        "+added_at_12\n"
        " line11\n"
    )
    added = compute_added_lines(diff)
    # First hunk: +1 => 0-indexed, context line -> 0, add -> 1, context -> 2
    # Second hunk: +11 => 0-indexed 10, context -> 10, add -> 11, context -> 12
    assert added == [1, 11]


def test_suite_results_json_in_summary() -> None:
    """Test that final_suite_results is valid JSON in the summary."""
    eval_events = [[] for _ in range(3)]
    eval_events[2] = [
        EvalEvent(
            eval_id="e1",
            kind="commit_async",
            trigger_part=2,
            trigger_turn=0,
            target_commit="abc",
            status="completed",
            passed=10,
            failed=2,
            total=12,
            suite_results={
                "all/basics/smoke": {"passed": 7, "total": 7},
                "all/wacct/ch1": {"passed": 3, "total": 5},
            },
        ),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        trace, meta = make_trace(eval_events=eval_events, num_parts=3)
        trace_path = write_test_trace(Path(tmp), trace, meta)
        traj_summary, eval_rows = process_trajectory(trace_path)

    assert traj_summary["final_suite_results"] is not None
    parsed = json.loads(traj_summary["final_suite_results"])
    assert "all/basics/smoke" in parsed
    assert parsed["all/basics/smoke"]["passed"] == 7
