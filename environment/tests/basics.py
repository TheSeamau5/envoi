"""
Basics test suite — hand-written .c files covering core compiler features.

Tests live in tests/basics/<category>/*.c  (smoke, variables, control_flow, etc.)
Each file declares expected output via comment headers:

    // expect_stdout: Hello
    // expect_stdout: World
    // expect_exit: 0          (optional, default 0)
"""

from __future__ import annotations

import re
from pathlib import Path

import envoi

from .utils import TestResult, run_case, select_cases, to_result

basics = envoi.suite("basics")


async def run_basics(
    n_tests: int = 0,
    test_name: str | None = None,
    *,
    categories: tuple[str, ...] | None = None,
) -> TestResult:
    basics_dir = Path(__file__).resolve().parent / "basics"
    category_names = (
        categories
        if categories is not None
        else (
            "smoke",
            "variables",
            "control_flow",
            "functions",
            "expressions",
            "edge_cases",
            "stress",
        )
    )

    all_cases: list[dict] = []
    for category in category_names:
        category_dir = basics_dir / category
        if not category_dir.is_dir():
            continue

        for source_file in sorted(category_dir.glob("*.c")):
            source = source_file.read_text()
            stdout_lines = re.findall(r"^//\s*expect_stdout:\s*(.+)$", source, re.MULTILINE)
            exit_match = re.search(r"^//\s*expect_exit:\s*(\d+)", source, re.MULTILINE)
            all_cases.append(
                {
                    "name": source_file.stem,
                    "source": source,
                    "expected_stdout": "\n".join(stdout_lines),
                    "expected_exit_code": int(exit_match.group(1)) if exit_match else 0,
                }
            )

    cases = select_cases(all_cases, n_tests=n_tests, test_name=test_name)
    return to_result([await run_case(c) for c in cases])


@basics.test("smoke")
async def smoke(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(n_tests=n_tests, test_name=test_name, categories=("smoke",))


@basics.test("variables")
async def variables(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(n_tests=n_tests, test_name=test_name, categories=("variables",))


@basics.test("control_flow")
async def control_flow(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(n_tests=n_tests, test_name=test_name, categories=("control_flow",))


@basics.test("functions")
async def functions(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(n_tests=n_tests, test_name=test_name, categories=("functions",))


@basics.test("expressions")
async def expressions(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(n_tests=n_tests, test_name=test_name, categories=("expressions",))


@basics.test("edge_cases")
async def edge_cases(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(n_tests=n_tests, test_name=test_name, categories=("edge_cases",))


@basics.test("stress")
async def stress(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(n_tests=n_tests, test_name=test_name, categories=("stress",))
