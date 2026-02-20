"""
C Compiler evaluation environment.

Evaluates a submitted Rust project that compiles C source code to ARM64
executables. The submission must produce a ./cc binary via build.sh.

Usage:  ./cc input.c -o output

Test suites (run in order):
  1. basics
  2. wacct/chapter_1 ... wacct/chapter_20 (or just "wacct" to run all chapters)
  3. c_testsuite/part_* (or just "c_testsuite" to run all parts)
  4. torture/part_* (or just "torture" to run all parts)

Use "all" to run every suite in one call.

Each test suite lives in tests/<name>.py and exposes a run_<name>() coroutine.
See tests/utils.py for the result models and core test runner.

Debug artifact contract (optional, no flags required):
  - The submitted compiler may write debugging output to ./debug_artifacts/.
  - This directory is cleared before each test case.
  - Any files written there are captured and returned in structured failure data.
  - Suggested files include AST/IR/assembly/error traces, but naming is flexible.
"""

from __future__ import annotations

import envoi
from tests.basics import basics, run_basics
from tests.c_testsuite import c_testsuite, run_c_testsuite
from tests.torture import run_torture, torture
from tests.utils import TestResult, to_result
from tests.wacct import run_wacct_tests, wacct

__all__ = ["all_tests", "basics", "c_testsuite", "torture", "wacct", "build_compiler"]

all_tests = envoi.suite("all")


@all_tests.test()
async def run_all() -> TestResult:
    """Run all four test suites and return combined results."""
    results = [
        await run_basics(),
        await run_wacct_tests(),
        await run_c_testsuite(),
        await run_torture(),
    ]
    all_cases = [case for r in results for case in r.cases]
    return to_result(all_cases)


@envoi.setup
async def build_compiler(submission: envoi.Documents) -> None:
    build = await envoi.run("chmod +x build.sh && ./build.sh", timeout_seconds=300)
    if build.exit_code != 0:
        raise RuntimeError(f"Build failed (exit {build.exit_code}).\n{build.stderr}")
