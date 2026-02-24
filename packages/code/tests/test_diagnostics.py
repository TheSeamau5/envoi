from __future__ import annotations

from envoi_code.utils.diagnostics import (
    enrich_evaluation_payload,
    extract_test_diagnostics,
)


def test_extracts_rust_style_diagnostic_with_rendered_snippet() -> None:
    test = {
        "suite": "basics/control_flow",
        "test_id": "borrow_check_like_case",
        "status": "failed",
        "message": (
            "error[E0499]: cannot borrow `foo.bar1` as mutable more than once at a time\n"
            " --> src/test/borrow.c:2:9\n"
            "  |\n"
            "2 | let x = foo.bar1;\n"
            "  |         ^^^ first mutable borrow occurs here\n"
        ),
        "source": "int main() {\nlet x = foo.bar1;\nreturn 0;\n}\n",
    }

    diagnostics = extract_test_diagnostics(test)
    assert diagnostics
    first = diagnostics[0]
    assert first["code"] == "E0499"
    assert first["cluster_key"]
    rendered = first.get("rendered") or ""
    assert "error[E0499]" in rendered
    assert "--> src/test/borrow.c:2:9" in rendered
    assert "^" in rendered


def test_extracts_gcc_style_location() -> None:
    test = {
        "suite": "basics/parser",
        "test_id": "missing_semicolon",
        "status": "failed",
        "stderr_tail": "tmp/test.c:7:3: error: expected ';' after expression",
        "source": "int main() {\n  return 1\n}\n",
    }

    diagnostics = extract_test_diagnostics(test)
    assert diagnostics
    first = diagnostics[0]
    assert first["kind"] == "compile_error"
    assert first["primary"]["file"] == "tmp/test.c"
    assert first["primary"]["line"] == 7
    assert first["primary"]["col"] == 3


def test_enrich_payload_adds_clusters_and_rendered_entries() -> None:
    payload = {
        "passed": 0,
        "failed": 2,
        "total": 2,
        "tests": [
            {
                "suite": "basics/control_flow",
                "test_id": "if_else_missing",
                "status": "failed",
                "message": "tmp/a.c:2:7: error: expected expression",
                "source": "int main(){\n  return ;\n}\n",
            },
            {
                "suite": "wacct/expr",
                "test_id": "offset_parse",
                "status": "failed",
                "message": "unexpected token at byte 18",
                "source": "int main(){\n  return 10;\n}\n",
            },
        ],
    }

    enriched = enrich_evaluation_payload(payload)
    assert isinstance(enriched.get("diagnostic_clusters"), list)
    assert len(enriched["diagnostic_clusters"]) >= 1

    tests = enriched["tests"]
    assert tests[0]["rendered_diagnostic"]
    assert tests[0]["cluster_key"]
    assert tests[1]["rendered_diagnostic"]
    assert tests[1]["cluster_key"]
