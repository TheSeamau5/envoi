"""
GCC C Torture execute tests (standard-C subset).

Source: LLVM test-suite mirror of GCC torture tests.
~1500 total files, filtered down to ~370 that use only standard C
(no GNU extensions). The blacklist is pre-generated and checked in
(see generate_blacklist.py in this directory).

Accepts an optional `count` parameter to limit how many tests run.

Routes:
- @torture/part_{part} runs one fixed-size shard when `part` is provided.
- @torture/part_{part} with no `part` runs all shards.
"""

import math
from pathlib import Path

import envoi

from .utils import TestResult, run_case, select_cases, to_result

torture = envoi.suite("torture")


def load_blacklist() -> set[str]:
    blacklist_file = Path(__file__).resolve().parent / "torture" / "torture-blacklist.txt"
    if blacklist_file.exists():
        return {line.strip() for line in blacklist_file.read_text().splitlines() if line.strip()}
    return set()


@torture.test("part_{part}")
async def run_torture(
    part: int | None = None,
    count: int = 0,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    part_size = 40
    blacklist = load_blacklist()
    torture_dir = Path("/opt/tests/llvm-test-suite/SingleSource/Regression/C/gcc-c-torture/execute")
    source_files = sorted(
        source_file for source_file in torture_dir.glob("*.c") if source_file.name not in blacklist
    )
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

    # Backward-compatible alias: `count` behaves like `n_tests` if n_tests unset.
    effective_n_tests = n_tests if n_tests > 0 else max(0, count)
    selected = select_cases(
        selected_cases,
        n_tests=effective_n_tests,
        test_name=test_name,
        offset=offset,
    )
    return to_result([await run_case(c) for c in selected])
