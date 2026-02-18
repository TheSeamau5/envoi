"""
Writing-a-C-Compiler tests (wacct).

Source: github.com/nlsandler/writing-a-c-compiler-tests
20 chapters of progressively harder C features.

Routes:
- @wacct/chapter_{chapter} runs one chapter.
"""

import json
from pathlib import Path

import envoi

from .utils import TestResult, run_case, select_cases, to_result

wacct = envoi.suite("wacct")


def load_expected() -> dict:
    path = Path("/opt/tests/wacct/expected_results.json")
    if path.exists():
        return json.loads(path.read_text())
    return {}


def case_name(chapter: int, rel_path: Path) -> str:
    # Strip the top-level "chapter_<n>/" prefix from case display names.
    parts = rel_path.with_suffix("").parts
    # Avoid "/" in test names because run_case() embeds names in local file paths.
    suffix = "__".join(parts[1:]) if len(parts) > 1 else rel_path.stem
    return f"chapter_{chapter}:{suffix}"


def build_cases_for_chapter(chapter: int, expected_map: dict) -> list[dict]:
    cases: list[dict] = []
    chapter_prefix = f"chapter_{chapter}/"
    tests_dir = Path("/opt/tests/wacct/tests")

    # Pull runnable valid tests from expected_results.json keys. This includes
    # chapter layouts like 19/20 that don't use valid/invalid_* folders.
    for rel_str in sorted(
        key for key in expected_map if key.startswith(chapter_prefix) and key.endswith(".c")
    ):
        rel_path = Path(rel_str)
        source_path = tests_dir / rel_path
        if not source_path.is_file():
            continue

        entry = expected_map.get(rel_str, {})
        expected_exit = entry.get("return_code", 0) if isinstance(entry, dict) else 0
        expected_stdout = entry.get("stdout", "").strip() if isinstance(entry, dict) else ""
        cases.append({
            "name": case_name(chapter, rel_path),
            "source": source_path.read_text(errors="replace"),
            "expected_stdout": expected_stdout,
            "expected_exit_code": expected_exit,
        })

    # Invalid tests remain folder-driven.
    chapter_dir = tests_dir / f"chapter_{chapter}"
    if chapter_dir.is_dir():
        for invalid_dir in sorted(chapter_dir.glob("invalid_*")):
            for source_path in sorted(invalid_dir.rglob("*.c")):
                rel_path = source_path.relative_to(tests_dir)
                cases.append({
                    "name": case_name(chapter, rel_path),
                    "source": source_path.read_text(errors="replace"),
                    "expected_stdout": "",
                    "expected_exit_code": 1,
                    "expect_compile_success": False,
                })

    return cases


@wacct.test("chapter_{chapter}")
async def run_wacct_tests(
    chapter: int,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    expected_map = load_expected()

    if not 1 <= chapter <= 20:
        raise ValueError("chapter must be between 1 and 20")

    cases = build_cases_for_chapter(chapter, expected_map)
    selected = select_cases(cases, n_tests=n_tests, test_name=test_name, offset=offset)
    return to_result([await run_case(c) for c in selected])
