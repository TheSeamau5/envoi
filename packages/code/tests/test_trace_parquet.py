from __future__ import annotations

from envoi_code.utils.trace_parquet import build_evaluations_from_parts


def test_recovered_commit_eval_ignores_environment_specific_fields() -> None:
    parts = [
        {
            "part": 12,
            "timestamp": "2026-02-24T00:00:00+00:00",
            "eval_events_delta": [
                {
                    "eval_id": "evt_1",
                    "kind": "commit_async",
                    "target_commit": "abc123",
                    "trigger_part": 12,
                    "trigger_turn": 3,
                    "status": "completed",
                    "passed": 10,
                    "failed": 2,
                    "total": 12,
                    "payload": {
                        "diagnostic_clusters": [
                            {
                                "key": "compile_error|-|expected <q>",
                                "kind": "compile_error",
                                "count": 2,
                                "suites": ["basics"],
                                "sample_tests": ["basics/if_else"],
                            },
                        ],
                        "advisor_assessment": "Root cause is parser precedence.",
                    },
                    "suite_results": {"basics": {"passed": 10, "total": 12}},
                    "tests": [
                        {
                            "suite": "basics",
                            "test_id": "if_else",
                            "status": "failed",
                        },
                    ],
                    "error": None,
                },
            ],
        },
    ]

    evaluations = build_evaluations_from_parts(parts)
    recovered = evaluations["abc123"]
    assert "diagnostic_clusters" not in recovered
    assert "advisor_assessment" not in recovered
    assert recovered["payload"]["diagnostic_clusters"][0]["kind"] == "compile_error"
    assert recovered["payload"]["advisor_assessment"] == "Root cause is parser precedence."
