"""
GCC torture execute tests, excluding only known harness-incompatible cases.

Source: LLVM test-suite mirror of GCC torture execute tests.
The suite includes every file that passes under GNU GCC in this environment
using the same single-file compile/run contract as the real harness.

Accepts an optional `count` parameter to limit how many tests run.

Routes:
- @torture runs all shards.
- @torture/part_{part} runs one fixed-size shard.
"""

from __future__ import annotations

import math
from pathlib import Path

import envoi

from .utils import TestResult, fixture_path, run_cases_parallel, select_cases, to_result

torture = envoi.suite("torture")

def load_incompatible_cases() -> set[str]:
    incompatible_file = Path(__file__).resolve().parent / "torture" / "torture-incompatible.txt"
    if incompatible_file.exists():
        return {line.strip() for line in incompatible_file.read_text().splitlines() if line.strip()}
    return set()


async def run_torture_impl(
    part: int | None = None,
    count: int = 0,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    part_size = 40
    incompatible_cases = load_incompatible_cases()
    torture_dir = fixture_path(
        "llvm-test-suite",
        "SingleSource",
        "Regression",
        "C",
        "gcc-c-torture",
        "execute",
    )
    if not torture_dir.is_dir():
        raise RuntimeError(f"Missing torture fixtures directory: {torture_dir}")
    source_files = []
    for source_file in sorted(torture_dir.glob("*.c")):
        if source_file.name in incompatible_cases:
            continue
        source_text = source_file.read_text(errors="replace")
        source_files.append((source_file, source_text))
    if not source_files:
        raise RuntimeError(f"No torture test files found in fixtures directory: {torture_dir}")
    cases = [
        {
            "name": source_file.stem,
            "source": source_text,
            "source_path": str(source_file),
            "expected_stdout": "",
            "expected_exit_code": 0,
        }
        for source_file, source_text in source_files
    ]

    if part is not None:
        if part < 1:
            raise ValueError("part must be >= 1")

        start = (part - 1) * part_size
        end = start + part_size
        selected_cases = cases[start:end]
        if not selected_cases:
            max_part = max(1, math.ceil(len(cases) / part_size))
            raise ValueError(f"part must be between 1 and {max_part}")
    else:
        selected_cases = cases

    effective_n_tests = n_tests if n_tests > 0 else max(0, count)
    selected = select_cases(
        selected_cases,
        n_tests=effective_n_tests,
        test_name=test_name,
        offset=offset,
    )
    return to_result(
        await run_cases_parallel(
            selected,
            suite_name="torture",
            run_name="torture/all" if part is None else f"torture/part_{part}",
        )
    )


@torture.test()
async def run_torture_all(
    count: int = 0,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    return await run_torture_impl(
        part=None,
        count=count,
        n_tests=n_tests,
        test_name=test_name,
        offset=offset,
    )


@torture.test("part_{part}")
async def run_torture(
    part: int | None = None,
    count: int = 0,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    return await run_torture_impl(
        part=part,
        count=count,
        n_tests=n_tests,
        test_name=test_name,
        offset=offset,
    )
