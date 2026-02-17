"""
C Compiler evaluation environment.

Evaluates a submitted Rust project that compiles C source code to x86_64
executables.  The submission must produce a ./cc binary via build.sh.

Usage:  ./cc input.c -o output

Test suites (run in order):

  1. basics          Hand-written tests covering core C features
  2. wacct           "Writing a C Compiler" textbook tests (20 chapters)
                     accepts optional chapter=<1..20> to run one chapter
  3. c_testsuite     ~220 conformance tests from c-testsuite
  4. torture_execute ~370 GCC torture tests (standard-C subset)

Each test suite lives in tests/<name>/suite.py and exposes a run_<name>() coroutine.
See tests/shared.py for the result models and core test runner.

Debug artifact contract (optional, no flags required):
  - The submitted compiler may write debugging output to ./debug_artifacts/.
  - This directory is cleared before each test case.
  - Any files written there are captured and returned in structured failure data.
  - Suggested files include AST/IR/assembly/error traces, but naming is flexible.
"""

import envoi

from tests import TestResult, run_basics, run_c_testsuite, run_torture, run_wacct


@envoi.setup
async def build_compiler(submission: envoi.Documents) -> None:
    """Compile the submitted Rust project into the ./cc binary."""
    build = await envoi.run("chmod +x build.sh && ./build.sh", timeout_seconds=300)
    if build.exit_code != 0:
        raise RuntimeError(f"Build failed (exit {build.exit_code}).\n{build.stderr}")


@envoi.test
async def basics(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    """Hand-written tests: smoke, variables, control flow, functions, expressions, edge cases, stress."""
    return await run_basics(n_tests=n_tests, test_name=test_name)


@envoi.test
async def wacct(
    n_tests: int = 0,
    test_name: str | None = None,
    chapter: int | None = None,
) -> TestResult:
    """Writing-a-C-Compiler tests; pass chapter=1..20 to scope to a single chapter."""
    return await run_wacct(n_tests=n_tests, test_name=test_name, chapter=chapter)


@envoi.test
async def c_testsuite(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    """~220 single-file C conformance tests from c-testsuite."""
    return await run_c_testsuite(n_tests=n_tests, test_name=test_name)


@envoi.test
async def torture_execute(
    count: int = 0,
    n_tests: int = 0,
    test_name: str | None = None,
) -> TestResult:
    """~370 GCC torture execute tests (standard-C subset, blacklist-filtered)."""
    return await run_torture(count=count, n_tests=n_tests, test_name=test_name)
