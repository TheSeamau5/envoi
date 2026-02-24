from __future__ import annotations

from envoi_code.scripts.graph_trace import build_report_from_trace


def test_graph_report_includes_diagnostic_cluster_metadata() -> None:
    trace = {
        "trajectory_id": "traj_1",
        "agent_model": "gpt-test",
        "started_at": "2026-02-24T00:00:00+00:00",
        "parts": [
            {
                "part": 5,
                "timestamp": "2026-02-24T00:00:05+00:00",
                "eval_events_delta": [
                    {
                        "kind": "commit_async",
                        "target_commit": "abc123",
                        "status": "completed",
                        "payload": {
                            "diagnostic_clusters": [
                                {
                                    "key": "compile_error|-|expected <q>",
                                    "kind": "compile_error",
                                    "count": 8,
                                },
                            ],
                        },
                    },
                ],
            },
        ],
        "evaluations": {
            "abc123": {
                "part": 5,
                "status": "completed",
                "passed": 2,
                "total": 10,
                "suite_results": {"basics": {"passed": 2, "total": 10}},
            },
        },
    }

    report = build_report_from_trace(trace)
    assert report["counts"]["commit_points"] == 1
    assert report["counts"]["diagnostic_clusters"] == 1
    assert "compile_error|-|expected <q>" in report["diagnostic_cluster_keys"]
    assert report["diagnostic_top_clusters"] == [
        "compile_error|-|expected <q>",
    ]
