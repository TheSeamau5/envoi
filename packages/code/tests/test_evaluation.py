from __future__ import annotations

import json

from envoi_code.utils import evaluation


def test_generated_evaluation_script_attaches_environment_test_source() -> None:
    script = evaluation.build_evaluation_python_script(
        repo_dir_json=json.dumps("/workspace"),
        envoi_url_json=json.dumps("http://localhost:8000"),
        eval_test_paths_json=json.dumps([]),
        eval_timeout_seconds_json=json.dumps(120),
        marker_json=json.dumps("__MARKER__"),
    )

    assert "def load_environment_test_sources():" in script
    assert "async def mirror_sandbox_logs(stop_event):" in script
    assert "test_source_map = load_environment_test_sources()" in script
    assert "extracted_tests = attach_test_sources(" in script


def test_extract_leaf_paths_uses_schema_v1_flat_tests() -> None:
    schema = {
        "schema_version": "envoi.schema.v1",
        "tests": ["basics", "wacct/ch1"],
        "capabilities": {"requires_session": True},
    }

    assert evaluation.extract_leaf_paths(schema) == ["basics", "wacct/ch1"]


def test_parse_evaluation_log_records_reads_structured_marker_lines() -> None:
    stdout = "\n".join(
        [
            "[eval-shell] repo cloned",
            (
                evaluation.EVALUATION_LOG_MARKER
                + json.dumps(
                    {
                        "ts": "2026-01-01T00:00:00+00:00",
                        "component": "evaluation",
                        "event": "test.start",
                        "level": "info",
                        "message": "",
                    }
                )
            ),
            (
                evaluation.EVALUATION_LOG_MARKER
                + json.dumps(
                    {
                        "ts": "2026-01-01T00:00:01+00:00",
                        "component": "session_worker",
                        "event": "worker.test.start",
                        "level": "info",
                        "message": "",
                        "source": "eval_sandbox",
                    }
                )
            ),
            evaluation.EVALUATION_JSON_MARKER + json.dumps({"passed": 1, "total": 1}),
        ]
    )

    records = evaluation.parse_evaluation_log_records(stdout)

    assert len(records) == 2
    assert records[0]["event"] == "test.start"
    assert records[1]["component"] == "session_worker"
