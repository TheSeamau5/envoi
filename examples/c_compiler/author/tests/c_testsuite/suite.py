"""
c-testsuite — ~220 single-file C conformance tests.

Source: github.com/c-testsuite/c-testsuite
Each .c file has a corresponding .c.expected file with the expected stdout.
All tests are expected to exit 0.
"""

from pathlib import Path

from tests.shared import TestResult, run_case, select_cases, to_result

TESTS_DIR = Path("/opt/tests/c-testsuite/tests/single-exec")


async def run_c_testsuite(
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    cases = []
    for f in sorted(TESTS_DIR.glob("*.c")):
        expected_file = f.parent / f"{f.name}.expected"
        expected_stdout = expected_file.read_text().strip() if expected_file.exists() else ""
        cases.append({
            "name": f.stem,
            "source": f.read_text(),
            "expected_stdout": expected_stdout,
            "expected_exit_code": 0,
        })
    selected = select_cases(cases, n_tests=n_tests, test_name=test_name, offset=offset)
    return to_result([await run_case(c) for c in selected])
