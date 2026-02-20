"""
c-testsuite — ~220 single-file C conformance tests.

Source: github.com/c-testsuite/c-testsuite
Each .c file has a corresponding .c.expected file with the expected stdout.
All tests are expected to exit 0.

Routes:
- @c_testsuite runs all shards.
- @c_testsuite/part_{part} runs one fixed-size shard.
"""

from __future__ import annotations

import math
from pathlib import Path

import envoi

from .utils import TestResult, run_case, select_cases, to_result

c_testsuite = envoi.suite("c_testsuite")


@c_testsuite.test()
async def run_c_testsuite_all(
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    return await run_c_testsuite(
        part=None,
        n_tests=n_tests,
        test_name=test_name,
        offset=offset,
    )


@c_testsuite.test("part_{part}")
async def run_c_testsuite(
    part: int | None = None,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    tests_dir = Path("/opt/tests/c-testsuite/tests/single-exec")
    part_size = 48
    if not tests_dir.is_dir():
        raise RuntimeError(f"Missing c-testsuite fixtures directory: {tests_dir}")

    cases: list[dict] = []
    for source_file in sorted(tests_dir.glob("*.c")):
        expected_file = source_file.parent / f"{source_file.name}.expected"
        expected_stdout = expected_file.read_text().strip() if expected_file.exists() else ""
        cases.append(
            {
                "name": source_file.stem,
                "source": source_file.read_text(),
                "expected_stdout": expected_stdout,
                "expected_exit_code": 0,
            }
        )
    if not cases:
        raise RuntimeError(f"No c-testsuite cases found in fixtures directory: {tests_dir}")

    if part is None:
        selected_cases = cases
    else:
        if part < 1:
            raise ValueError("part must be >= 1")

        start = (part - 1) * part_size
        end = start + part_size
        selected_cases = cases[start:end]
        if not selected_cases:
            max_part = max(1, math.ceil(len(cases) / part_size))
            raise ValueError(f"part must be between 1 and {max_part}")

    selected = select_cases(selected_cases, n_tests=n_tests, test_name=test_name, offset=offset)
    return to_result([await run_case(c) for c in selected])
