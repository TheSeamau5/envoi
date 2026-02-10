"""
Writing-a-C-Compiler tests (wacct).

Source: github.com/nlsandler/writing-a-c-compiler-tests
20 chapters of progressively harder C features. Each chapter has:
  - valid/   — programs that should compile and produce correct output
  - invalid_*/ — programs that should be rejected at compile time
"""

import json
from pathlib import Path

from tests.shared import TestResult, run_case, select_cases, to_result

WACCT_DIR = Path("/opt/tests/wacct")
TESTS_DIR = WACCT_DIR / "tests"


def _load_expected() -> dict:
    path = WACCT_DIR / "expected_results.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


async def run_wacct(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    expected_map = _load_expected()
    cases: list[dict] = []

    for ch in range(1, 21):
        # --- Valid tests: compile + run + check output ---
        valid_dir = TESTS_DIR / f"chapter_{ch}" / "valid"
        if valid_dir.is_dir():
            for f in sorted(valid_dir.rglob("*.c")):
                src = f.read_text()
                rel = f.relative_to(TESTS_DIR)
                entry = expected_map.get(str(rel), {})
                expected_exit = entry.get("return_code", 0) if isinstance(entry, dict) else 0
                expected_stdout = entry.get("stdout", "").strip() if isinstance(entry, dict) else ""
                cases.append({
                    "name": f.stem,
                    "source": src,
                    "expected_stdout": expected_stdout,
                    "expected_exit_code": expected_exit,
                })

        # --- Invalid tests: should fail to compile ---
        chapter_dir = TESTS_DIR / f"chapter_{ch}"
        if not chapter_dir.is_dir():
            continue
        for invalid_dir in sorted(chapter_dir.glob("invalid_*")):
            for f in sorted(invalid_dir.rglob("*.c")):
                cases.append({
                    "name": f.stem,
                    "source": f.read_text(),
                    "expected_stdout": "",
                    "expected_exit_code": 1,
                    "expect_compile_success": False,
                })

    selected = select_cases(cases, n_tests=n_tests, test_name=test_name)
    return to_result([await run_case(c) for c in selected])
