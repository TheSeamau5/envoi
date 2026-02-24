"""Environment-level runner configuration for envoi-code."""

from __future__ import annotations


def params() -> dict[str, object]:
    return {
        "advisor_model": "@anthropic/claude-opus-4.6",
        "advisor_model_thinking_level": "high",
        "diagnostics_suite_priority": [
            "basics",
            "c_testsuite",
            "wacct",
            "torture",
        ],
        "failed_tests_feedback_limit": 50,
    }
