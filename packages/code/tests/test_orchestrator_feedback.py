from __future__ import annotations

import envoi_code.orchestrator as orchestrator


def test_resolve_suite_feedback_priority_defaults_to_none() -> None:
    assert orchestrator.resolve_suite_feedback_priority(None) == ()


def test_failed_tests_feedback_section_shows_none_priority() -> None:
    previous = orchestrator.CURRENT_SUITE_FEEDBACK_PRIORITY
    try:
        orchestrator.CURRENT_SUITE_FEEDBACK_PRIORITY = ()
        payload = {
            "tests": [
                {
                    "suite": "suite_a/smoke",
                    "test_id": "case_1",
                    "status": "failed",
                    "source": "int main() { return 0; }",
                },
            ],
        }
        rendered, selected = orchestrator.build_failed_tests_feedback_section(payload, limit=5)
        assert "prioritized: none" in rendered
        assert len(selected) == 1
    finally:
        orchestrator.CURRENT_SUITE_FEEDBACK_PRIORITY = previous


def test_failed_tests_selection_keeps_same_test_id_across_suites_without_priority() -> None:
    previous = orchestrator.CURRENT_SUITE_FEEDBACK_PRIORITY
    try:
        orchestrator.CURRENT_SUITE_FEEDBACK_PRIORITY = ()
        payload = {
            "tests": [
                {
                    "suite": "suite_a/parser",
                    "test_id": "smoke",
                    "status": "failed",
                    "source": "a",
                },
                {
                    "suite": "suite_b/parser",
                    "test_id": "smoke",
                    "status": "failed",
                    "source": "b",
                },
            ],
        }
        selected = orchestrator.select_failed_tests_for_feedback(payload, limit=5)
        assert len(selected) == 2
    finally:
        orchestrator.CURRENT_SUITE_FEEDBACK_PRIORITY = previous


def test_failed_tests_selection_deduplicates_with_configured_family_priority() -> None:
    previous = orchestrator.CURRENT_SUITE_FEEDBACK_PRIORITY
    try:
        orchestrator.CURRENT_SUITE_FEEDBACK_PRIORITY = ("basics",)
        payload = {
            "tests": [
                {
                    "suite": "basics/parser",
                    "test_id": "smoke",
                    "status": "failed",
                    "source": "a",
                },
                {
                    "suite": "basics/control_flow",
                    "test_id": "smoke",
                    "status": "failed",
                    "source": "b",
                },
            ],
        }
        selected = orchestrator.select_failed_tests_for_feedback(payload, limit=5)
        assert len(selected) == 1
    finally:
        orchestrator.CURRENT_SUITE_FEEDBACK_PRIORITY = previous
