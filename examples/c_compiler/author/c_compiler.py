"""
C Compiler environment.

Submit a Rust project (with build.sh + Cargo.toml) that compiles C to executables.
The build produces a ./cc binary. Usage: ./cc input.c -o output

Test .c files live in tests/<suite>/ directories. Each file has comment headers:
    // expect_stdout: <line>   (one per expected output line)
    // expect_exit: <code>     (default 0)
"""

import json
import os
import re
import shlex
import time
from pathlib import Path

import envoi
from envoi.utils import working_dir
from pydantic import BaseModel

TESTS_DIR = Path(__file__).resolve().parent / "tests"
C_TESTSUITE_DIR = Path("/opt/tests/c-testsuite/tests/single-exec")
WACCT_DIR = Path("/opt/tests/wacct")
WACCT_TESTS_DIR = WACCT_DIR / "tests"


# -- Models ----------------------------------------------------------------

class CaseResult(BaseModel):
    name: str
    phase: str
    passed: bool
    c_source: str
    expected_stdout: str
    actual_stdout: str
    expected_exit_code: int
    actual_exit_code: int
    compile_time_ms: float
    gcc_compile_time_ms: float | None = None
    binary_size_bytes: int | None = None
    gcc_binary_size_bytes: int | None = None
    run_time_ms: float | None = None
    gcc_run_time_ms: float | None = None
    stderr: str | None = None


class TestResult(BaseModel):
    passed: int
    failed: int
    total: int
    cases: list[CaseResult]


# -- Load suites from disk -------------------------------------------------

def load_suite(name: str) -> list[dict]:
    """Read every .c file in tests/<name>/, parse expect_* headers."""
    suite_dir = TESTS_DIR / name
    cases = []
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


# -- Setup: build the compiler once per session ----------------------------

def _session_path() -> Path:
    try:
        return Path(working_dir.get())
    except LookupError:
        return Path.cwd()


def _file_size(path: Path) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


@envoi.setup
async def build_compiler(submission: envoi.Documents) -> None:
    _ = submission
    build = await envoi.run("chmod +x build.sh && ./build.sh", timeout_seconds=300)
    if build.exit_code != 0:
        raise RuntimeError(f"Build failed (exit {build.exit_code}).\n{build.stderr}")


# -- Run a single test case ------------------------------------------------

async def run_case(case: dict) -> CaseResult:
    name, src = case["name"], case["source"]
    expected_stdout = case["expected_stdout"]
    expected_exit = case["expected_exit_code"]
    sp = _session_path()

    c_file = sp / f"test_{name}.c"
    out_file = sp / f"test_{name}"
    gcc_out_file = sp / f"test_{name}_gcc"
    c_file.write_text(src)

    # Compile with submitted compiler (timed)
    t0 = time.monotonic()
    cc = await envoi.run(
        f"./cc {shlex.quote(c_file.name)} -o {shlex.quote(out_file.name)}",
        timeout_seconds=45,
    )
    compile_time_ms = (time.monotonic() - t0) * 1000

    # Benchmark: compile same file with gcc (timed)
    gcc_compile_time_ms = None
    t0 = time.monotonic()
    gcc = await envoi.run(
        f"gcc {shlex.quote(c_file.name)} -o {shlex.quote(gcc_out_file.name)}",
        timeout_seconds=45,
    )
    if gcc.exit_code == 0:
        gcc_compile_time_ms = (time.monotonic() - t0) * 1000

    # Binary sizes
    binary_size_bytes = _file_size(out_file)
    gcc_binary_size_bytes = _file_size(gcc_out_file) if gcc.exit_code == 0 else None

    if cc.exit_code != 0:
        return CaseResult(
            name=name, phase="compile", passed=False, c_source=src,
            expected_stdout=expected_stdout, actual_stdout="",
            expected_exit_code=expected_exit, actual_exit_code=cc.exit_code,
            compile_time_ms=compile_time_ms, gcc_compile_time_ms=gcc_compile_time_ms,
            binary_size_bytes=None, gcc_binary_size_bytes=gcc_binary_size_bytes,
            stderr=(cc.stderr or cc.stdout or "compilation failed")[:8000],
        )

    # Execute the compiled binary (timed)
    t0 = time.monotonic()
    run = await envoi.run(shlex.quote(f"./{out_file.name}"), timeout_seconds=15)
    run_time_ms = (time.monotonic() - t0) * 1000

    # Benchmark: execute gcc-compiled binary (timed)
    gcc_run_time_ms = None
    if gcc.exit_code == 0:
        t0 = time.monotonic()
        gcc_run = await envoi.run(shlex.quote(f"./{gcc_out_file.name}"), timeout_seconds=15)
        if gcc_run.exit_code == expected_exit:
            gcc_run_time_ms = (time.monotonic() - t0) * 1000

    passed = (
        run.stdout.strip() == expected_stdout.strip()
        and run.exit_code == expected_exit
    )

    # Build detailed stderr on verify failure
    stderr = None
    if not passed:
        parts = []
        if run.stdout.strip() != expected_stdout.strip():
            parts.append(f"stdout mismatch:\n  expected: {expected_stdout!r}\n  actual:   {run.stdout.strip()!r}")
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


# -- Run a full suite ------------------------------------------------------

async def run_suite(suite_name: str) -> TestResult:
    cases = load_suite(suite_name)
    results = [await run_case(c) for c in cases]
    passed = sum(1 for r in results if r.passed)
    return TestResult(passed=passed, failed=len(results) - passed, total=len(results), cases=results)


# -- Test routes (one per suite directory) ---------------------------------

@envoi.test
async def smoke() -> TestResult:
    return await run_suite("smoke")

@envoi.test
async def variables() -> TestResult:
    return await run_suite("variables")

@envoi.test
async def control_flow() -> TestResult:
    return await run_suite("control_flow")

@envoi.test
async def functions() -> TestResult:
    return await run_suite("functions")

@envoi.test
async def expressions() -> TestResult:
    return await run_suite("expressions")

@envoi.test
async def edge_cases() -> TestResult:
    return await run_suite("edge_cases")

@envoi.test
async def stress() -> TestResult:
    return await run_suite("stress")


# -- External test suites --------------------------------------------------

@envoi.test
async def c_testsuite() -> TestResult:
    """~220 single-file C tests from github.com/c-testsuite/c-testsuite."""
    cases = []
    for f in sorted(C_TESTSUITE_DIR.glob("*.c")):
        expected_file = f.parent / f"{f.name}.expected"
        expected_stdout = expected_file.read_text().strip() if expected_file.exists() else ""
        cases.append({
            "name": f.stem,
            "source": f.read_text(),
            "expected_stdout": expected_stdout,
            "expected_exit_code": 0,
        })
    results = [await run_case(c) for c in cases]
    passed = sum(1 for r in results if r.passed)
    return TestResult(passed=passed, failed=len(results) - passed, total=len(results), cases=results)


def _load_wacct_expected() -> dict:
    path = WACCT_DIR / "expected_results.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


async def _run_wacct_valid(chapter: int) -> TestResult:
    expected_map = _load_wacct_expected()
    chapter_dir = WACCT_TESTS_DIR / f"chapter_{chapter}" / "valid"
    if not chapter_dir.is_dir():
        return TestResult(passed=0, failed=0, total=0, cases=[])

    cases = []
    for f in sorted(chapter_dir.rglob("*.c")):
        src = f.read_text()
        rel = f.relative_to(WACCT_TESTS_DIR)
        entry = expected_map.get(str(rel), {})
        expected_exit = entry.get("return_code", 0) if isinstance(entry, dict) else 0
        expected_stdout = entry.get("stdout", "").strip() if isinstance(entry, dict) else ""
        cases.append({
            "name": f.stem,
            "source": src,
            "expected_stdout": expected_stdout,
            "expected_exit_code": expected_exit,
        })
    results = [await run_case(c) for c in cases]
    passed = sum(1 for r in results if r.passed)
    return TestResult(passed=passed, failed=len(results) - passed, total=len(results), cases=results)


async def _run_wacct_invalid(chapter: int) -> TestResult:
    chapter_dir = WACCT_TESTS_DIR / f"chapter_{chapter}"
    if not chapter_dir.is_dir():
        return TestResult(passed=0, failed=0, total=0, cases=[])

    cases = []
    for invalid_dir in sorted(chapter_dir.glob("invalid_*")):
        for f in sorted(invalid_dir.rglob("*.c")):
            cases.append({"name": f.stem, "source": f.read_text()})

    sp = _session_path()
    results = []
    for case in cases:
        name, src = case["name"], case["source"]
        c_file = sp / f"test_{name}.c"
        out_file = sp / f"test_{name}"
        c_file.write_text(src)

        t0 = time.monotonic()
        cc = await envoi.run(
            f"./cc {shlex.quote(c_file.name)} -o {shlex.quote(out_file.name)}",
            timeout_seconds=45,
        )
        compile_time_ms = (time.monotonic() - t0) * 1000

        passed = cc.exit_code != 0
        results.append(CaseResult(
            name=name,
            phase="compile",
            passed=passed,
            c_source=src,
            expected_stdout="",
            actual_stdout="",
            expected_exit_code=1,
            actual_exit_code=cc.exit_code,
            compile_time_ms=compile_time_ms,
            stderr=None if passed else "expected compilation to fail but it succeeded",
        ))

    passed = sum(1 for r in results if r.passed)
    return TestResult(passed=passed, failed=len(results) - passed, total=len(results), cases=results)


# Register one test route per wacct chapter (1-20)
for _ch in range(1, 21):
    def _make_valid(ch: int = _ch):
        async def _test() -> TestResult:
            return await _run_wacct_valid(ch)
        _test.__name__ = f"wacct_ch{ch}"
        _test.__qualname__ = f"wacct_ch{ch}"
        return _test

    def _make_invalid(ch: int = _ch):
        async def _test() -> TestResult:
            return await _run_wacct_invalid(ch)
        _test.__name__ = f"wacct_ch{ch}_invalid"
        _test.__qualname__ = f"wacct_ch{ch}_invalid"
        return _test

    envoi.test(_make_valid())
    envoi.test(_make_invalid())
