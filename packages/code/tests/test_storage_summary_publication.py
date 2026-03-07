from __future__ import annotations

import json

from envoi_code.models import (
    AgentTrace,
    EvalEvent,
    EvaluationRecord,
    PartRecord,
    SessionEnd,
)
from envoi_code.scripts.materialize_summaries import (
    EVALUATION_SUMMARY_SCHEMA,
    TRAJECTORY_SUMMARY_SCHEMA,
    read_table_rows,
)
from envoi_code.utils.storage import publish_completed_trajectory_summary


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[tuple[str, bytes, str]] = []

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803
        del Bucket
        if Key not in self.objects:
            error = Exception("missing")
            error.response = {"Error": {"Code": "NoSuchKey"}}  # type: ignore[attr-defined]
            raise error
        return {"Body": FakeBody(self.objects[Key])}

    def put_object(
        self,
        *,
        Bucket: str,  # noqa: N803
        Key: str,  # noqa: N803
        Body: bytes,  # noqa: N803
        ContentType: str,  # noqa: N803
    ) -> None:
        del Bucket
        self.objects[Key] = Body
        self.put_calls.append((Key, Body, ContentType))


def make_trace(
    trajectory_id: str,
    *,
    passed: int,
    total: int,
) -> AgentTrace:
    eval_event = EvalEvent(
        eval_id=f"eval-{trajectory_id}",
        kind="commit_async",
        trigger_part=0,
        trigger_turn=0,
        target_commit=f"commit-{trajectory_id}",
        status="completed",
        passed=passed,
        failed=max(0, total - passed),
        total=total,
        suite_results={"all/basics/smoke": {"passed": passed, "total": total}},
    )
    part = PartRecord(
        trajectory_id=trajectory_id,
        session_id=f"sess-{trajectory_id}",
        agent="codex",
        agent_model="gpt-5.3-codex",
        part=0,
        timestamp="2026-03-06T00:00:01Z",
        eval_events_delta=[eval_event],
        git_commit=f"commit-{trajectory_id}",
    )
    trace = AgentTrace(
        trajectory_id=trajectory_id,
        session_id=f"sess-{trajectory_id}",
        agent="codex",
        agent_model="gpt-5.3-codex",
        started_at="2026-03-06T00:00:00Z",
        parts=[part],
        evaluations={
            f"commit-{trajectory_id}": EvaluationRecord(
                eval_id=eval_event.eval_id,
                commit=f"commit-{trajectory_id}",
                part=0,
                trigger_turn=0,
                status="completed",
                queued_at="2026-03-06T00:00:01Z",
                completed_at="2026-03-06T00:00:02Z",
                passed=passed,
                failed=max(0, total - passed),
                total=total,
                suite_results=eval_event.suite_results,
            )
        },
        session_end=SessionEnd(
            reason="solved",
            total_parts=1,
            total_turns=1,
            final_git_commit=f"commit-{trajectory_id}",
        ),
    )
    return trace


def test_publish_completed_summary_bootstraps_when_missing(monkeypatch) -> None:
    fake_s3 = FakeS3Client()
    monkeypatch.setattr("envoi_code.utils.storage.get_s3_client", lambda: fake_s3)
    monkeypatch.setattr("envoi_code.utils.storage.get_prefix", lambda: "bucket")

    trace = make_trace("traj-one", passed=7, total=7)
    publish_completed_trajectory_summary(
        trace,
        environment="c_compiler",
        task_params={"target": "x86_64-linux"},
        project="c-compiler",
    )

    keys = [call[0] for call in fake_s3.put_calls]
    assert keys == [
        "project/c-compiler/trajectories/summaries/trajectory_summary.parquet",
        "project/c-compiler/trajectories/summaries/evaluation_summary.parquet",
        "project/c-compiler/trajectories/summaries/manifest.json",
    ]
    trajectory_rows = read_table_rows(
        fake_s3.objects[
            "project/c-compiler/trajectories/summaries/trajectory_summary.parquet"
        ],
        TRAJECTORY_SUMMARY_SCHEMA,
    )
    assert len(trajectory_rows) == 1
    assert trajectory_rows[0]["trajectory_id"] == "traj-one"


def test_publish_completed_summary_replaces_existing_rows(monkeypatch) -> None:
    fake_s3 = FakeS3Client()
    monkeypatch.setattr("envoi_code.utils.storage.get_s3_client", lambda: fake_s3)
    monkeypatch.setattr("envoi_code.utils.storage.get_prefix", lambda: "bucket")

    first = make_trace("traj-one", passed=3, total=7)
    second = make_trace("traj-one", passed=7, total=7)

    publish_completed_trajectory_summary(
        first,
        environment="c_compiler",
        task_params={"target": "x86_64-linux"},
        project="c-compiler",
    )
    publish_completed_trajectory_summary(
        second,
        environment="c_compiler",
        task_params={"target": "x86_64-linux"},
        project="c-compiler",
    )

    trajectory_rows = read_table_rows(
        fake_s3.objects[
            "project/c-compiler/trajectories/summaries/trajectory_summary.parquet"
        ],
        TRAJECTORY_SUMMARY_SCHEMA,
    )
    evaluation_rows = read_table_rows(
        fake_s3.objects[
            "project/c-compiler/trajectories/summaries/evaluation_summary.parquet"
        ],
        EVALUATION_SUMMARY_SCHEMA,
    )
    manifest = json.loads(
        fake_s3.objects[
            "project/c-compiler/trajectories/summaries/manifest.json"
        ].decode("utf-8"),
    )
    assert manifest["revision"]
    assert len(trajectory_rows) == 1
    assert trajectory_rows[0]["final_passed"] == 7
    assert len(evaluation_rows) == 1
    assert evaluation_rows[0]["passed"] == 7


def test_publish_completed_summary_uploads_manifest_last(monkeypatch) -> None:
    fake_s3 = FakeS3Client()
    monkeypatch.setattr("envoi_code.utils.storage.get_s3_client", lambda: fake_s3)
    monkeypatch.setattr("envoi_code.utils.storage.get_prefix", lambda: "bucket")

    trace = make_trace("traj-order", passed=5, total=7)
    publish_completed_trajectory_summary(
        trace,
        environment="c_compiler",
        task_params={"target": "x86_64-linux"},
        project="c-compiler",
    )

    assert fake_s3.put_calls[-1][0].endswith("manifest.json")
