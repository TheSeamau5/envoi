"""
C Compiler environment.

Submit a Rust project that compiles C source files into executables.
The project must include a build.sh that produces a ./cc binary.

Usage: ./cc input.c -o output
The compiler reads a C source file and produces an executable binary.

Supported C subset (for the minimal test suite):
- int main() with return statements
- Integer literals and arithmetic (+, -, *, /)
- Local variable declarations (int only)
- Assignment
- if/else statements
- while loops
- Functions with int parameters and int return type
- printf with %d format specifier (link against libc)
"""

import shlex
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel

import envoi
from envoi.utils import working_dir

BASE_DIR = Path(__file__).resolve().parent
TESTS_DIR = BASE_DIR / "tests"
EXPECTED_DIR = TESTS_DIR / "expected"
MAX_OUTPUT_CHARS = 8000


class CaseResult(BaseModel):
    name: str
    phase: str  # "build" | "compile" | "link" | "execute" | "verify"
    passed: bool
    source_file: str
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


class CaseSpec(NamedTuple):
    name: str
    expected_exit_code: int = 0


def get_session_path() -> Path:
    try:
        return Path(working_dir.get())
    except LookupError:
        return Path.cwd()


def load_case_source(name: str) -> str:
    case_path = TESTS_DIR / f"{name}.c"
    if not case_path.is_file():
        raise FileNotFoundError(f"Missing test source file: {case_path}")
    return case_path.read_text(encoding="utf-8")


def load_expected_stdout(name: str) -> str:
    expected_path = EXPECTED_DIR / f"{name}.expected"
    if not expected_path.is_file():
        raise FileNotFoundError(f"Missing expected output file: {expected_path}")
    return expected_path.read_text(encoding="utf-8").strip()


def is_execute_failure(exit_code: int, stderr: str | None) -> bool:
    if exit_code == -1:
        return True

    runtime_failure_codes = {126, 127, 134, 136, 137, 139}
    if exit_code in runtime_failure_codes:
        return True

    stderr_lower = (stderr or "").lower()
    return "not found" in stderr_lower or "permission denied" in stderr_lower


def truncate_text(value: str | None, limit: int = MAX_OUTPUT_CHARS) -> str:
    text = value or ""
    if len(text) <= limit:
        return text

    truncated_chars = len(text) - limit
    return f"{text[:limit]}\n\n...[truncated {truncated_chars} chars]"


def format_command_output(stdout: str | None, stderr: str | None) -> str:
    stdout_text = truncate_text(stdout)
    stderr_text = truncate_text(stderr)
    return "\n".join(
        [
            "---- stdout ----",
            stdout_text,
            "---- stderr ----",
            stderr_text,
        ]
    ).strip()


def infer_compile_phase(stdout: str | None, stderr: str | None) -> str:
    combined = f"{stdout or ''}\n{stderr or ''}".lower()
    link_markers = (
        "ld:",
        "collect2:",
        "undefined reference",
        "linker",
        "cannot find -l",
        "link failed",
    )
    if any(marker in combined for marker in link_markers):
        return "link"
    return "compile"


@envoi.setup
async def build_compiler(submission: envoi.Documents) -> None:
    """Build the submitted Rust compiler project once per session."""
    _ = submission

    session_path = get_session_path()

    missing_required_files = []
    for required_file in ("build.sh", "Cargo.toml"):
        if not (session_path / required_file).is_file():
            missing_required_files.append(required_file)
    if missing_required_files:
        raise RuntimeError(
            "Missing required file(s) in submission root: "
            + ", ".join(sorted(missing_required_files))
        )

    build = await envoi.run("chmod +x build.sh && ./build.sh", timeout_seconds=300)
    if build.exit_code != 0:
        raise RuntimeError(
            "\n".join(
                [
                    f"Build failed (exit {build.exit_code}).",
                    format_command_output(build.stdout, build.stderr),
                ]
            ).strip()
        )

    cc_path = session_path / "cc"
    if not cc_path.is_file():
        raise RuntimeError("build.sh did not produce a ./cc binary")

    executable_check = await envoi.run("test -x ./cc")
    if executable_check.exit_code != 0:
        raise RuntimeError(
            "\n".join(
                [
                    "./cc exists but is not executable.",
                    format_command_output(executable_check.stdout, executable_check.stderr),
                ]
            ).strip()
        )


async def run_case(case: CaseSpec) -> CaseResult:
    """Compile one C test file with ./cc and verify output + exit code."""
    source_file = f"{case.name}.c"
    c_source = ""
    expected_stdout = ""
    try:
        c_source = load_case_source(case.name)
        expected_stdout = load_expected_stdout(case.name)
    except Exception as error:
        return CaseResult(
            name=case.name,
            phase="build",
            passed=False,
            source_file=source_file,
            c_source=c_source,
            expected_stdout=expected_stdout,
            actual_stdout="",
            expected_exit_code=case.expected_exit_code,
            actual_exit_code=1,
            stderr=f"Failed to load test fixtures: {error}",
        )

    session_path = get_session_path()

    c_file = session_path / f"test_{case.name}.c"
    out_file = session_path / f"test_{case.name}"
    try:
        c_file.write_text(c_source, encoding="utf-8")
    except Exception as error:
        return CaseResult(
            name=case.name,
            phase="compile",
            passed=False,
            source_file=source_file,
            c_source=c_source,
            expected_stdout=expected_stdout,
            actual_stdout="",
            expected_exit_code=case.expected_exit_code,
            actual_exit_code=1,
            stderr=f"Failed to write test source file {c_file.name}: {error}",
        )

    compile_command = f"./cc {shlex.quote(c_file.name)} -o {shlex.quote(out_file.name)}"
    try:
        compile_result = await envoi.run(compile_command, timeout_seconds=45)
    except Exception as error:
        return CaseResult(
            name=case.name,
            phase="compile",
            passed=False,
            source_file=source_file,
            c_source=c_source,
            expected_stdout=expected_stdout,
            actual_stdout="",
            expected_exit_code=case.expected_exit_code,
            actual_exit_code=1,
            stderr=f"Compiler execution failed: {error}",
        )
    if compile_result.exit_code != 0:
        phase = infer_compile_phase(compile_result.stdout, compile_result.stderr)
        return CaseResult(
            name=case.name,
            phase=phase,
            passed=False,
            source_file=source_file,
            c_source=c_source,
            expected_stdout=expected_stdout,
            actual_stdout="",
            expected_exit_code=case.expected_exit_code,
            actual_exit_code=compile_result.exit_code,
            stderr=format_command_output(compile_result.stdout, compile_result.stderr),
        )

    run_command = shlex.quote(f"./{out_file.name}")
    try:
        run_result = await envoi.run(run_command, timeout_seconds=15)
    except Exception as error:
        return CaseResult(
            name=case.name,
            phase="execute",
            passed=False,
            source_file=source_file,
            c_source=c_source,
            expected_stdout=expected_stdout,
            actual_stdout="",
            expected_exit_code=case.expected_exit_code,
            actual_exit_code=1,
            stderr=f"Program execution failed: {error}",
        )

    stdout_match = run_result.stdout.strip() == expected_stdout.strip()
    exit_match = run_result.exit_code == case.expected_exit_code
    passed = stdout_match and exit_match

    phase = "verify"
    if not passed and is_execute_failure(run_result.exit_code, run_result.stderr):
        phase = "execute"

    return CaseResult(
        name=case.name,
        phase=phase,
        passed=passed,
        source_file=source_file,
        c_source=c_source,
        expected_stdout=expected_stdout,
        actual_stdout=run_result.stdout,
        expected_exit_code=case.expected_exit_code,
        actual_exit_code=run_result.exit_code,
        stderr=truncate_text(run_result.stderr) if (run_result.stderr and not passed) else None,
    )


async def run_suite(cases: list[CaseSpec]) -> TestResult:
    """Run a case list and return aggregated structured results."""
    results = []
    for case in cases:
        try:
            results.append(await run_case(case))
        except Exception as error:
            results.append(
                CaseResult(
                    name=case.name,
                    phase="build",
                    passed=False,
                    source_file=f"{case.name}.c",
                    c_source="",
                    expected_stdout="",
                    actual_stdout="",
                    expected_exit_code=case.expected_exit_code,
                    actual_exit_code=1,
                    stderr=f"Unexpected test harness error: {error}",
                )
            )

    passed = sum(1 for result in results if result.passed)
    total = len(results)
    return TestResult(
        passed=passed,
        failed=total - passed,
        total=total,
        cases=results,
    )


SMOKE_CASES = [
    CaseSpec("return_0", expected_exit_code=0),
    CaseSpec("return_42", expected_exit_code=42),
    CaseSpec("print_number"),
    CaseSpec("add"),
    CaseSpec("subtract"),
    CaseSpec("multiply"),
    CaseSpec("divide"),
]

VARIABLE_CASES = [
    CaseSpec("local_var"),
    CaseSpec("var_arithmetic"),
    CaseSpec("reassignment"),
]

CONTROL_FLOW_CASES = [
    CaseSpec("if_true"),
    CaseSpec("if_false"),
    CaseSpec("while_loop"),
    CaseSpec("countdown"),
]

FUNCTION_CASES = [
    CaseSpec("simple_function"),
    CaseSpec("nested_calls"),
    CaseSpec("recursive"),
]


@envoi.test
async def smoke() -> TestResult:
    """Basic tests: return literals and arithmetic."""
    return await run_suite(SMOKE_CASES)


@envoi.test
async def variables() -> TestResult:
    """Local variables and assignment."""
    return await run_suite(VARIABLE_CASES)


@envoi.test
async def control_flow() -> TestResult:
    """If/else and while loops."""
    return await run_suite(CONTROL_FLOW_CASES)


@envoi.test
async def functions() -> TestResult:
    """Function definitions and calls."""
    return await run_suite(FUNCTION_CASES)
