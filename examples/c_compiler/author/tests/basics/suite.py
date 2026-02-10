"""
Basics test suite — hand-written .c files covering core compiler features.

Tests live in tests/basics/<category>/*.c  (smoke, variables, control_flow, etc.)
Each file declares expected output via comment headers:

    // expect_stdout: Hello
    // expect_stdout: World
    // expect_exit: 0          (optional, default 0)
"""

import re
from pathlib import Path

from tests.shared import TestResult, run_case, to_result

# .c files are in subdirectories alongside this file
TESTS_DIR = Path(__file__).resolve().parent


def _load_all() -> list[dict]:
    """Walk every sub-suite directory and parse expect_* headers."""
    cases = []
    for suite_dir in sorted(TESTS_DIR.iterdir()):
        if not suite_dir.is_dir():
            continue
        for f in sorted(suite_dir.glob("*.c")):
            src = f.read_text()
            stdout_lines = re.findall(r"^//\s*expect_stdout:\s*(.+)$", src, re.MULTILINE)
            exit_m = re.search(r"^//\s*expect_exit:\s*(\d+)", src, re.MULTILINE)
            cases.append({
                "name": f.stem,
                "source": src,
                "expected_stdout": "\n".join(stdout_lines),
                "expected_exit_code": int(exit_m.group(1)) if exit_m else 0,
            })
    return cases


async def run_basics() -> TestResult:
    return to_result([await run_case(c) for c in _load_all()])
