from __future__ import annotations

import envoi_code.orchestrator as orchestrator
from envoi_code.models import EvalTestResult


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


def test_regression_feedback_section_flags_newly_broken_tests_first() -> None:
    summary = orchestrator.build_turn_regression_summary(
        current_tests=[
            EvalTestResult(
                suite="basics/functions",
                test_id="recursive",
                status="failed",
                failure_type="crash",
                message="process crashed with SIGSEGV",
            ),
            EvalTestResult(
                suite="basics/smoke",
                test_id="return_42",
                status="passed",
            ),
        ],
        previous_tests=[
            EvalTestResult(
                suite="basics/functions",
                test_id="recursive",
                status="passed",
            ),
            EvalTestResult(
                suite="basics/smoke",
                test_id="return_42",
                status="passed",
            ),
        ],
    )

    rendered = orchestrator.build_turn_regression_feedback_section(summary)
    assert "REGRESSIONS: these tests were passing and now fail." in rendered
    assert "Fix regressions before adding new features." in rendered
    assert "regressions: 1" in rendered
    assert "basics/functions/recursive: passed -> failed/crash" in rendered


def test_parse_progress_md_claim_reads_explicit_progress_line() -> None:
    content = """
    # Progress

    ## Current Status
    - Tests passing: 52/60 (run_tests.sh)
    - Current focus: parser fixes
    """

    claim = orchestrator.parse_progress_md_claim(content)
    assert claim == {"claimed_passed": 52, "claimed_total": 60}


def test_progress_md_feedback_section_renders_warning() -> None:
    rendered = orchestrator.build_progress_md_feedback_section(
        {
            "warning": (
                "Your PROGRESS.md claims 52/60 tests passing, but evaluation "
                "shows 38/60. Update PROGRESS.md to reflect reality."
            )
        }
    )

    assert rendered is not None
    assert "PROGRESS.md check" in rendered
    assert "52/60" in rendered
    assert "38/60" in rendered


def test_build_turn_end_eval_event_carries_regression_count() -> None:
    event = orchestrator.build_turn_end_eval_event(
        turn=4,
        part=12,
        commit="abc123",
        run_payload={
            "exit_code": 0,
            "payload": {
                "passed": 3,
                "failed": 1,
                "total": 4,
                "regression_summary": {"regressions": 2},
                "suite_results": {},
                "tests": [],
            },
        },
    )

    assert event.regressions == 2


def test_is_terminal_zero_test_evaluation_only_when_no_error() -> None:
    assert (
        orchestrator.is_terminal_zero_test_evaluation(
            passed=0,
            total=0,
            has_error=False,
        )
        is True
    )
    assert (
        orchestrator.is_terminal_zero_test_evaluation(
            passed=0,
            total=0,
            has_error=True,
        )
        is False
    )
    assert (
        orchestrator.is_terminal_zero_test_evaluation(
            passed=0,
            total=5,
            has_error=True,
        )
        is False
    )
