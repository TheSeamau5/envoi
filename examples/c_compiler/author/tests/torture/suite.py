"""
GCC C Torture execute tests (standard-C subset).

Source: LLVM test-suite mirror of GCC torture tests.
~1500 total files, filtered down to ~370 that use only standard C
(no GNU extensions). The blacklist is pre-generated and checked in
(see generate_blacklist.py in this directory).

Accepts an optional `count` parameter to limit how many tests run.
"""

from pathlib import Path

from tests.shared import TestResult, run_case, select_cases, to_result

TORTURE_DIR = Path("/opt/tests/llvm-test-suite/SingleSource/Regression/C/gcc-c-torture/execute")
BLACKLIST_FILE = Path(__file__).resolve().parent / "torture-blacklist.txt"


def _load_blacklist() -> set[str]:
    if BLACKLIST_FILE.exists():
        return {line.strip() for line in BLACKLIST_FILE.read_text().splitlines() if line.strip()}
    return set()


async def run_torture(
    count: int = 0,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    blacklist = _load_blacklist()
    all_files = sorted(f for f in TORTURE_DIR.glob("*.c") if f.name not in blacklist)
    cases = []
    for f in all_files:
        cases.append({
            "name": f.stem,
            "source": f.read_text(errors="replace"),
            "expected_stdout": "",
            "expected_exit_code": 0,
        })

    # Backward-compatible alias: `count` behaves like `n_tests` if n_tests unset.
    effective_n_tests = n_tests if n_tests > 0 else max(0, count)
    selected = select_cases(
        cases,
        n_tests=effective_n_tests,
        test_name=test_name,
        offset=offset,
    )
    return to_result([await run_case(c) for c in selected])
