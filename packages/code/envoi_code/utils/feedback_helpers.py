from __future__ import annotations

from typing import Any

from envoi_code.models import EvalTestResult


def string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def normalize_suite_path(value: str | None) -> str:
    if not value:
        return ""
    parts = [part for part in value.split("/") if part]
    if not parts:
        return ""
    normalized = [parts[0]]
    for part in parts[1:]:
        if part == normalized[-1]:
            continue
        normalized.append(part)
    return "/".join(normalized)


def suite_family(
    suite_path: str,
    suite_feedback_priority: tuple[str, ...],
) -> str | None:
    normalized = normalize_suite_path(suite_path)
    for family in suite_feedback_priority:
        if normalized == family:
            return family
        if normalized.startswith(f"{family}/"):
            return family
        if f"/{family}/" in f"/{normalized}/":
            return family
    return None


def suite_rank(
    suite_path: str,
    suite_feedback_priority: tuple[str, ...],
) -> tuple[int, int, str]:
    normalized = normalize_suite_path(suite_path)
    family = suite_family(normalized, suite_feedback_priority)
    if family in suite_feedback_priority:
        return (
            suite_feedback_priority.index(family),
            0,
            normalized,
        )
    if normalized.endswith("/run_all") or normalized == "all/run_all":
        return (
            len(suite_feedback_priority),
            1,
            normalized,
        )
    return (
        len(suite_feedback_priority),
        0,
        normalized,
    )


def format_suite_feedback_priority(priority: tuple[str, ...]) -> str:
    if not priority:
        return "none"
    return " -> ".join(priority)


def test_sort_key(
    test: dict[str, Any],
    suite_feedback_priority: tuple[str, ...],
) -> tuple[int, int, str, str]:
    suite = normalize_suite_path(string_or_none(test.get("suite")))
    test_id = string_or_none(test.get("test_id")) or ""
    family_rank, run_all_rank, suite_rank_value = suite_rank(
        suite,
        suite_feedback_priority,
    )
    return (family_rank, run_all_rank, suite_rank_value, test_id)


def format_single_failed_test(
    index: int,
    test: dict[str, Any],
) -> str:
    suite = normalize_suite_path(string_or_none(test.get("suite"))) or "unknown_suite"
    test_id = string_or_none(test.get("test_id")) or "unknown_test"
    status = (string_or_none(test.get("status")) or "failed").lower()
    failure_type = string_or_none(test.get("failure_type"))
    label = f"{status}/{failure_type}" if failure_type else status
    message = string_or_none(test.get("message"))
    if message is None:
        message = string_or_none(test.get("stderr_tail"))
    if message is None:
        message = string_or_none(test.get("stdout_tail"))
    source = string_or_none(test.get("source"))

    lines = [
        f"{index}. {suite}/{test_id}",
        f"status: {label}",
    ]
    if message is not None:
        lines.append("error:")
        lines.append(message)
    rendered_diagnostic = string_or_none(
        test.get("rendered_diagnostic"),
    )
    if rendered_diagnostic is not None:
        lines.extend([
            "diagnostic:",
            "```text",
            rendered_diagnostic,
            "```",
        ])
    if source is not None:
        lines.extend([
            "source:",
            "```c",
            source,
            "```",
        ])
    else:
        lines.append("source: (missing)")
    return "\n".join(lines)


def eval_result_key(test: EvalTestResult) -> tuple[str, str]:
    suite = normalize_suite_path(string_or_none(test.suite) or "")
    test_id = string_or_none(test.test_id) or "unknown_test"
    return suite, test_id


def eval_result_sort_key(
    test: EvalTestResult,
    suite_feedback_priority: tuple[str, ...],
) -> tuple[int, int, str, str]:
    suite = normalize_suite_path(string_or_none(test.suite) or "")
    test_id = string_or_none(test.test_id) or ""
    family_rank, run_all_rank, suite_rank_value = suite_rank(
        suite,
        suite_feedback_priority,
    )
    return (family_rank, run_all_rank, suite_rank_value, test_id)


def eval_result_is_passed(test: EvalTestResult) -> bool:
    return (test.status or "").strip().lower() == "passed"


def eval_result_ref(test: EvalTestResult) -> str:
    suite = normalize_suite_path(string_or_none(test.suite) or "")
    test_id = string_or_none(test.test_id) or "unknown_test"
    if suite:
        return f"{suite}/{test_id}"
    return test_id


def eval_result_message(test: EvalTestResult) -> str | None:
    return (
        string_or_none(test.message)
        or string_or_none(test.stderr_tail)
        or string_or_none(test.stdout_tail)
    )
