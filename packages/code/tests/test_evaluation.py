from __future__ import annotations

import json

from envoi_code.utils import evaluation


def test_generated_evaluation_script_attaches_environment_test_source() -> None:
    script = evaluation._build_evaluation_python_script(
        repo_dir_json=json.dumps("/workspace"),
        envoi_url_json=json.dumps("http://localhost:8000"),
        eval_test_paths_json=json.dumps([]),
        eval_timeout_seconds_json=json.dumps(120),
        marker_json=json.dumps("__MARKER__"),
    )

    assert "def _load_environment_test_sources():" in script
    assert "test_source_map = _load_environment_test_sources()" in script
    assert "extracted_tests = _attach_test_sources(" in script


def test_extract_leaf_paths_uses_schema_v1_flat_tests() -> None:
    schema = {
        "schema_version": "envoi.schema.v1",
        "tests": ["basics", "wacct/ch1"],
        "capabilities": {"requires_session": True},
    }

    assert evaluation.extract_leaf_paths(schema) == ["basics", "wacct/ch1"]
