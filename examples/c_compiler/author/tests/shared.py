"""
Shared types and test runner used by all test suites.

CaseResult / TestResult are the structured output models returned by every
@envoi.test route.  run_case() compiles a single .c file with the submitted
compiler, benchmarks it against gcc, and returns a CaseResult.
"""

import os
import shlex
import time
from pathlib import Path

import envoi
from envoi.utils import working_dir
from pydantic import BaseModel

class CaseResult(BaseModel):
    name: str                          # test file stem, e.g. "return_2"
    phase: str                         # "compile" or "verify"
    passed: bool
    c_source: str                      # full C source that was compiled
    expected_stdout: str
    actual_stdout: str
    expected_exit_code: int
    actual_exit_code: int
    compile_time_ms: float             # submitted compiler
    gcc_compile_time_ms: float         # gcc baseline (always measured)
    binary_size_bytes: int | None = None
    gcc_binary_size_bytes: int | None = None
    run_time_ms: float | None = None
    gcc_run_time_ms: float | None = None
    stderr: str | None = None          # populated on failure


class TestResult(BaseModel):
    passed: int
    failed: int
    total: int
    cases: list[CaseResult]


def session_path() -> Path:
    """Return the working directory for the current envoi session."""
    try:
        return Path(working_dir.get())
    except LookupError:
        return Path.cwd()


def file_size(path: Path) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def to_result(results: list[CaseResult]) -> TestResult:
    """Aggregate a list of CaseResults into a TestResult."""
    passed = sum(1 for r in results if r.passed)
    return TestResult(
        passed=passed,
        failed=len(results) - passed,
        total=len(results),
        cases=results,
    )


async def run_case(case: dict) -> CaseResult:
    """
    Run a single test case through the submitted compiler and gcc.

    Expects case = {"name", "source", "expected_stdout", "expected_exit_code"}.
    """
    name = case["name"]
    src = case["source"]
    expected_stdout = case["expected_stdout"]
    expected_exit = case["expected_exit_code"]
    sp = session_path()

    c_file = sp / f"test_{name}.c"
    out_file = sp / f"test_{name}"
    gcc_out_file = sp / f"test_{name}_gcc"
    c_file.write_text(src)

    t0 = time.monotonic()
    cc = await envoi.run(
        f"./cc {shlex.quote(c_file.name)} -o {shlex.quote(out_file.name)}",
        timeout_seconds=45,
    )
    compile_time_ms = (time.monotonic() - t0) * 1000

    # gcc baseline
    t0 = time.monotonic()
    gcc = await envoi.run(
        f"gcc {shlex.quote(c_file.name)} -o {shlex.quote(gcc_out_file.name)}",
        timeout_seconds=45,
    )
    gcc_compile_time_ms = (time.monotonic() - t0) * 1000

    binary_size_bytes = file_size(out_file)
    gcc_binary_size_bytes = file_size(gcc_out_file)

    if cc.exit_code != 0:
        return CaseResult(
            name=name, phase="compile", passed=False, c_source=src,
            expected_stdout=expected_stdout, actual_stdout="",
            expected_exit_code=expected_exit, actual_exit_code=cc.exit_code,
            compile_time_ms=compile_time_ms, gcc_compile_time_ms=gcc_compile_time_ms,
            binary_size_bytes=None, gcc_binary_size_bytes=gcc_binary_size_bytes,
            stderr=(cc.stderr or cc.stdout or "compilation failed")[:8000],
        )

    t0 = time.monotonic()
    run = await envoi.run(shlex.quote(f"./{out_file.name}"), timeout_seconds=15)
    run_time_ms = (time.monotonic() - t0) * 1000

    # gcc baseline
    gcc_run_time_ms = None
    if gcc.exit_code == 0:
        t0 = time.monotonic()
        await envoi.run(shlex.quote(f"./{gcc_out_file.name}"), timeout_seconds=15)
        gcc_run_time_ms = (time.monotonic() - t0) * 1000

    passed = (
        run.stdout.strip() == expected_stdout.strip()
        and run.exit_code == expected_exit
    )

    stderr = None
    if not passed:
        parts = []
        if run.stdout.strip() != expected_stdout.strip():
            parts.append(
                f"stdout mismatch:\n"
                f"  expected: {expected_stdout!r}\n"
                f"  actual:   {run.stdout.strip()!r}"
            )
        if run.exit_code != expected_exit:
            parts.append(f"exit code mismatch: expected {expected_exit}, got {run.exit_code}")
        if run.stderr:
            parts.append(f"stderr:\n  {run.stderr.strip()}")
        stderr = "\n".join(parts)[:8000]

    return CaseResult(
        name=name, phase="verify", passed=passed, c_source=src,
        expected_stdout=expected_stdout, actual_stdout=run.stdout,
        expected_exit_code=expected_exit, actual_exit_code=run.exit_code,
        compile_time_ms=compile_time_ms, gcc_compile_time_ms=gcc_compile_time_ms,
        binary_size_bytes=binary_size_bytes, gcc_binary_size_bytes=gcc_binary_size_bytes,
        run_time_ms=run_time_ms, gcc_run_time_ms=gcc_run_time_ms,
        stderr=stderr,
    )
