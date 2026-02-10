"""
C Compiler environment.

Submit a Rust project (with build.sh + Cargo.toml) that compiles C to executables.
The build produces a ./cc binary. Usage: ./cc input.c -o output

Test .c files live in tests/<suite>/ directories. Each file has comment headers:
    // expect_stdout: <line>   (one per expected output line)
    // expect_exit: <code>     (default 0)
"""

import re
import shlex
from pathlib import Path

from pydantic import BaseModel

import envoi
from envoi.utils import working_dir

TESTS_DIR = Path(__file__).resolve().parent / "tests"


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

    def fail(phase: str, stderr: str, exit_code: int = 1) -> CaseResult:
        return CaseResult(
            name=name, phase=phase, passed=False, c_source=src,
            expected_stdout=expected_stdout, actual_stdout="",
            expected_exit_code=expected_exit, actual_exit_code=exit_code,
            stderr=stderr[:8000],
        )

    # Write source file and compile
    sp = _session_path()
    c_file = sp / f"test_{name}.c"
    out_file = sp / f"test_{name}"
    c_file.write_text(src)

    cc = await envoi.run(
        f"./cc {shlex.quote(c_file.name)} -o {shlex.quote(out_file.name)}",
        timeout_seconds=45,
    )
    if cc.exit_code != 0:
        return fail("compile", cc.stderr or cc.stdout or "compilation failed", cc.exit_code)

    # Execute the compiled binary
    run = await envoi.run(shlex.quote(f"./{out_file.name}"), timeout_seconds=15)

    passed = (
        run.stdout.strip() == expected_stdout.strip()
        and run.exit_code == expected_exit
    )
    return CaseResult(
        name=name, phase="verify", passed=passed, c_source=src,
        expected_stdout=expected_stdout, actual_stdout=run.stdout,
        expected_exit_code=expected_exit, actual_exit_code=run.exit_code,
        stderr=run.stderr[:8000] if (run.stderr and not passed) else None,
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
