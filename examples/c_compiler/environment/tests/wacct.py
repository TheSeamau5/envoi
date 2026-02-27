"""
Writing-a-C-Compiler tests (wacct).

Source: github.com/nlsandler/writing-a-c-compiler-tests
20 chapters of progressively harder C features.

Routes:
- @wacct runs all chapters.
- @wacct/chapter_{chapter} runs one chapter.
"""

from __future__ import annotations

import json
from pathlib import Path

import envoi

from .utils import TestResult, fixture_path, run_case, select_cases, to_result

wacct = envoi.suite("wacct")


async def run_wacct_tests_impl(
    chapter: int | None = None,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    tests_dir = fixture_path("wacct", "tests")
    expected_path = fixture_path("wacct", "expected_results.json")
    if not tests_dir.is_dir():
        raise RuntimeError(f"Missing WACCT fixtures directory: {tests_dir}")
    if not expected_path.is_file():
        raise RuntimeError(f"Missing WACCT expected results file: {expected_path}")
    expected_map = json.loads(expected_path.read_text()) if expected_path.exists() else {}

    if chapter is not None and not 1 <= chapter <= 20:
        raise ValueError("chapter must be between 1 and 20")

    chapters = [chapter] if chapter is not None else list(range(1, 21))
    cases: list[dict] = []
    for chapter_number in chapters:
        chapter_prefix = f"chapter_{chapter_number}/"

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
            parts = rel_path.with_suffix("").parts
            suffix = "__".join(parts[1:]) if len(parts) > 1 else rel_path.stem
            cases.append(
                {
                    "name": f"chapter_{chapter_number}:{suffix}",
                    "source": source_path.read_text(errors="replace"),
                    "expected_stdout": expected_stdout,
                    "expected_exit_code": expected_exit,
                }
            )

        chapter_dir = tests_dir / f"chapter_{chapter_number}"
        if chapter_dir.is_dir():
            for invalid_dir in sorted(chapter_dir.glob("invalid_*")):
                for source_path in sorted(invalid_dir.rglob("*.c")):
                    rel_path = source_path.relative_to(tests_dir)
                    parts = rel_path.with_suffix("").parts
                    suffix = "__".join(parts[1:]) if len(parts) > 1 else rel_path.stem
                    cases.append(
                        {
                            "name": f"chapter_{chapter_number}:{suffix}",
                            "source": source_path.read_text(errors="replace"),
                            "expected_stdout": "",
                            "expected_exit_code": 1,
                            "expect_compile_success": False,
                        }
                    )

    selected = select_cases(cases, n_tests=n_tests, test_name=test_name, offset=offset)
    if not selected and n_tests == 0 and test_name is None and offset == 0:
        chapter_label = f"chapter_{chapter}" if chapter is not None else "all chapters"
        raise RuntimeError(
            f"No WACCT cases discovered for {chapter_label}; check fixtures under {tests_dir}"
        )
    return to_result([await run_case(c) for c in selected])


@wacct.test()
async def run_wacct_all(
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    return await run_wacct_tests_impl(
        chapter=None,
        n_tests=n_tests,
        test_name=test_name,
        offset=offset,
    )


@wacct.test("chapter_{chapter}")
async def run_wacct_tests(
    chapter: int | None = None,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    return await run_wacct_tests_impl(
        chapter=chapter,
        n_tests=n_tests,
        test_name=test_name,
        offset=offset,
    )
