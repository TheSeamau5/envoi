"""
GCC C Torture execute tests (standard-C subset).

Source: LLVM test-suite mirror of GCC torture tests.
~1500 total files, filtered down to ~370 that use only standard C
(no GNU extensions). The blacklist is pre-generated and checked in
(see generate_blacklist.py in this directory).

Accepts an optional `count` parameter to limit how many tests run.

Routes:
- @torture runs all shards.
- @torture/part_{part} runs one fixed-size shard.
"""

from __future__ import annotations

import math
from pathlib import Path

import envoi

from .utils import TestResult, fixture_path, run_case, select_cases, to_result

torture = envoi.suite("torture")


def load_blacklist() -> set[str]:
    blacklist_file = Path(__file__).resolve().parent / "torture" / "torture-blacklist.txt"
    if blacklist_file.exists():
        return {line.strip() for line in blacklist_file.read_text().splitlines() if line.strip()}
    return set()


async def run_torture_impl(
    part: int | None = None,
    count: int = 0,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    part_size = 40
    blacklist = load_blacklist()
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
    source_files = sorted(
        source_file for source_file in torture_dir.glob("*.c") if source_file.name not in blacklist
    )
    if not source_files:
        raise RuntimeError(f"No torture test files found in fixtures directory: {torture_dir}")
    cases = [
        {
            "name": source_file.stem,
            "source": source_file.read_text(errors="replace"),
            "expected_stdout": "",
            "expected_exit_code": 0,
        }
        for source_file in source_files
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
    return to_result([await run_case(c) for c in selected])


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
