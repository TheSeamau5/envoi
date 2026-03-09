"""
Shared types and test runner used by all test suites.

CaseResult / TestResult are the structured output models returned by every
@envoi.test route. run_case() compiles one or more source inputs with the
submitted compiler, benchmarks them against gcc, and returns a CaseResult.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path

import envoi
from envoi.logging import make_component_logger
from pydantic import BaseModel


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
    gcc_compile_time_ms: float
    binary_size_bytes: int | None = None
    gcc_binary_size_bytes: int | None = None
    run_time_ms: float | None = None
    gcc_run_time_ms: float | None = None
    failure_type: str | None = None
    signal_name: str | None = None
    stdout_diff_summary: str | None = None
    compiler_warnings: str | None = None
    gcc_warnings: str | None = None
    runtime_stderr: str | None = None
    timed_out: bool = False
    stderr: str | None = None


class TestResult(BaseModel):
    passed: int
    failed: int
    total: int
    cases: list[CaseResult]


arch_verified = False
arch_lock: asyncio.Lock | None = None
case_run_semaphore: asyncio.Semaphore | None = None
case_run_semaphore_limit: int | None = None
reference_gcc_standard_flag_cache: str | None = None
emit_environment_log = make_component_logger("environment")


def session_path() -> Path:
    try:
        return envoi.session_path()
    except LookupError:
        return Path.cwd()


def fixtures_root() -> Path:
    root = os.environ.get("ENVOI_TESTS_ROOT", "/opt/tests")
    return Path(root).expanduser().resolve()


def fixture_path(*parts: str) -> Path:
    return fixtures_root().joinpath(*parts)


def file_size(path: Path) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def expected_target_arch() -> tuple[str, set[str]]:
    host_arch = os.uname().machine.lower()
    if host_arch in {"x86_64", "amd64"}:
        return ("x86_64", {"Advanced Micro Devices X86-64", "X86-64", "x86-64"})
    if host_arch in {"aarch64", "arm64"}:
        return ("AArch64", {"AArch64"})
    return (host_arch, {host_arch})


def to_result(results: list[CaseResult]) -> TestResult:
    # A compiler that rejects EVERYTHING trivially "passes" all invalid-program
    # tests (expect_compile_success=False).  Detect this: if no valid program
    # reached the verify phase (i.e., the compiler never produced a working
    # binary) AND there are valid programs that failed compilation, then the
    # compiler is broken and invalid-program rejections should not be credited.
    any_valid_compiled = any(result.phase == "verify" for result in results)
    has_valid_compile_failures = any(
        result.phase == "compile" and not result.passed for result in results
    )
    if not any_valid_compiled and has_valid_compile_failures:
        adjusted: list[CaseResult] = []
        for result in results:
            if result.phase == "compile" and result.passed:
                adjusted.append(
                    result.model_copy(
                        update={
                            "passed": False,
                            "stderr": (
                                "compiler did not successfully compile any valid "
                                "program — rejection not credited"
                            ),
                        },
                    )
                )
            else:
                adjusted.append(result)
        results = adjusted

    passed = sum(1 for result in results if result.passed)
    return TestResult(
        passed=passed,
        failed=len(results) - passed,
        total=len(results),
        cases=results,
    )


def select_cases(
    cases: list[dict],
    *,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> list[dict]:
    if n_tests < 0:
        raise ValueError("n_tests must be >= 0")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    normalized_name = (test_name or "").strip()
    if normalized_name:
        matches = [case for case in cases if case.get("name") == normalized_name]
        if not matches:
            raise ValueError(f"Unknown test_name: {normalized_name}")
        return [matches[0]]

    if n_tests > 0:
        return cases[offset : offset + n_tests]

    return cases[offset:]


def max_test_concurrency() -> int:
    raw = os.environ.get("ENVOI_TEST_CONCURRENCY", "8").strip()
    try:
        value = int(raw)
    except ValueError:
        return 8
    return max(1, value)


def skip_gcc_benchmark() -> bool:
    raw = os.environ.get("ENVOI_SKIP_GCC_BENCHMARK", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def reference_c_standard() -> str:
    raw = os.environ.get("ENVOI_REFERENCE_C_STANDARD", "").strip().lower()
    return raw or "c23"


def detect_reference_gcc_standard_flag() -> str:
    global reference_gcc_standard_flag_cache

    if reference_gcc_standard_flag_cache is not None:
        return reference_gcc_standard_flag_cache

    preferred = reference_c_standard()
    candidates = [preferred]
    if preferred == "c23":
        candidates.append("c2x")
    elif preferred == "c2x":
        candidates.append("c23")

    emit_environment_log(
        "reference_gcc_standard.detect.start",
        preferred=preferred,
        candidates=list(dict.fromkeys(candidates)),
    )
    for candidate in dict.fromkeys(candidates):
        try:
            probe = subprocess.run(
                ["gcc", f"-std={candidate}", "-pedantic-errors", "-x", "c", "-", "-fsyntax-only"],
                input="int main(void) { return 0; }\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        if probe.returncode == 0:
            reference_gcc_standard_flag_cache = candidate
            emit_environment_log(
                "reference_gcc_standard.detect.complete",
                selected=candidate,
            )
            return candidate

    reference_gcc_standard_flag_cache = "c2x"
    emit_environment_log(
        "reference_gcc_standard.detect.fallback",
        selected=reference_gcc_standard_flag_cache,
    )
    return reference_gcc_standard_flag_cache


def reference_gcc_compile_args() -> list[str]:
    standard_flag = detect_reference_gcc_standard_flag()
    return [f"-std={standard_flag}", "-pedantic-errors"]


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def sanitize_fragment(value: str, *, fallback: str = "item", max_length: int = 80) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length]


def create_suite_case_root(suite_name: str, run_name: str | None = None) -> Path:
    root = session_path() / "case_runs"
    label = run_name or suite_name or "suite"
    suffix = hashlib.sha1(f"{label}:{time.monotonic_ns()}".encode()).hexdigest()[:12]
    root_name = f"{sanitize_fragment(label, fallback='suite', max_length=48)}-{suffix}"
    case_root = root / root_name
    case_root.mkdir(parents=True, exist_ok=True)
    return case_root


def create_case_dir(case_root: Path, case_name: str) -> Path:
    suffix = hashlib.sha1(case_name.encode("utf-8")).hexdigest()[:12]
    directory_name = f"{sanitize_fragment(case_name, fallback='case', max_length=48)}-{suffix}"
    case_dir = case_root / directory_name
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def get_case_run_semaphore() -> asyncio.Semaphore:
    global case_run_semaphore, case_run_semaphore_limit

    limit = max_test_concurrency()
    if case_run_semaphore is None or case_run_semaphore_limit != limit:
        case_run_semaphore = asyncio.Semaphore(limit)
        case_run_semaphore_limit = limit
    return case_run_semaphore


def get_arch_lock() -> asyncio.Lock:
    global arch_lock
    if arch_lock is None:
        arch_lock = asyncio.Lock()
    return arch_lock


def reset_runner_state() -> None:
    global arch_verified, arch_lock, case_run_semaphore, case_run_semaphore_limit
    global reference_gcc_standard_flag_cache

    arch_verified = False
    arch_lock = None
    case_run_semaphore = None
    case_run_semaphore_limit = None
    reference_gcc_standard_flag_cache = None


async def detect_elf_machine(path: Path) -> str | None:
    probe = await envoi.run(
        f"readelf -h {shlex.quote(str(path))}",
        cwd=str(path.parent),
        timeout_seconds=5,
    )
    if probe.exit_code != 0:
        return None

    for line in probe.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Machine:"):
            return stripped.split(":", maxsplit=1)[1].strip()

    return None


async def run_timed_command(
    *,
    command: str,
    cwd: Path,
    timeout_seconds: int,
) -> tuple[envoi.RunResult, float]:
    started = time.monotonic()
    result = await envoi.run(
        command,
        cwd=str(cwd),
        timeout_seconds=timeout_seconds,
    )
    ended = time.monotonic()
    return result, (ended - started) * 1000


async def maybe_verify_arch(out_file: Path) -> str | None:
    global arch_verified

    if arch_verified:
        return None

    async with get_arch_lock():
        if arch_verified:
            return None

        machine = await detect_elf_machine(out_file)

        expected_arch_label, expected_machine_values = expected_target_arch()
        if machine is not None and machine not in expected_machine_values:
            return f"wrong target architecture: expected {expected_arch_label}, got {machine}"

        arch_verified = True
        return None


def command_timed_out(result: envoi.RunResult) -> bool:
    return result.exit_code == -1 and result.stderr.strip().lower() == "timeout"


def decode_signal_name(exit_code: int) -> str | None:
    if exit_code <= 128:
        return None
    try:
        return signal.Signals(exit_code - 128).name
    except ValueError:
        return None


def raw_stdout_text(result: envoi.RunResult) -> str:
    if result.stdout_bytes:
        return result.stdout_bytes.decode(errors="replace")
    return result.stdout


def summarize_stdout_difference(expected: str, actual: str) -> str | None:
    if expected == actual:
        return None
    if expected.rstrip() == actual.rstrip():
        return "outputs match except trailing whitespace/newlines"

    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    for line_no, (expected_line, actual_line) in enumerate(
        zip(expected_lines, actual_lines, strict=False),
        start=1,
    ):
        if expected_line != actual_line:
            return (
                f"first difference at line {line_no}: "
                f"expected {expected_line!r}, got {actual_line!r}"
            )

    if len(expected_lines) != len(actual_lines):
        return f"expected {len(expected_lines)} lines, got {len(actual_lines)} lines"

    return f"stdout differs: expected {len(expected)} chars, got {len(actual)} chars"


def render_case_sources(source_files: list[tuple[str, str]]) -> str:
    if not source_files:
        return ""
    if len(source_files) == 1:
        return source_files[0][1]
    return "\n\n".join(f"// file: {filename}\n{content}" for filename, content in source_files)


def default_inline_compile_inputs(
    source_files: list[tuple[str, Path]],
) -> list[Path]:
    compile_inputs = [
        path for filename, path in source_files if Path(filename).suffix.lower() in {".c", ".s"}
    ]
    if not compile_inputs:
        raise RuntimeError("Inline multi-file case has no .c or .s compile inputs")
    return compile_inputs


def prepare_case_files(
    *,
    case: dict,
    case_dir: Path,
    file_stem: str,
) -> tuple[list[Path], str]:
    inline_sources_value = case.get("sources")
    input_path_values = case.get("input_paths")

    if isinstance(inline_sources_value, dict):
        inline_source_files: list[tuple[str, Path]] = []
        rendered_sources: list[tuple[str, str]] = []
        for raw_name, raw_content in inline_sources_value.items():
            filename = str(raw_name)
            content = str(raw_content)
            target = case_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            inline_source_files.append((filename, target))
            rendered_sources.append((filename, content))

        raw_compile_inputs = case.get("compile_inputs")
        if raw_compile_inputs is None:
            compile_inputs = default_inline_compile_inputs(inline_source_files)
        else:
            compile_inputs = []
            for raw_name in list(raw_compile_inputs):
                compile_input = case_dir / str(raw_name)
                if not compile_input.is_file():
                    raise RuntimeError(
                        "Missing inline compile input for case "
                        f"{case['name']}: {compile_input.name}"
                    )
                compile_inputs.append(compile_input)
            if not compile_inputs:
                raise RuntimeError(f"Case {case['name']} has no compile inputs")

        return compile_inputs, render_case_sources(rendered_sources)

    if input_path_values is None:
        source_path_value = case.get("source_path")
        if source_path_value is None:
            src = str(case["source"])
            c_file = case_dir / f"{file_stem}.c"
            c_file.write_text(src, encoding="utf-8")
        else:
            c_file = Path(str(source_path_value)).expanduser().resolve()
            if not c_file.is_file():
                raise RuntimeError(f"Missing case source file: {c_file}")
            src = str(case["source"])
        return [c_file], src

    compile_inputs = []
    for raw_path in list(input_path_values):
        compile_input = Path(str(raw_path)).expanduser().resolve()
        if not compile_input.is_file():
            raise RuntimeError(f"Missing case compile input: {compile_input}")
        compile_inputs.append(compile_input)
    if not compile_inputs:
        raise RuntimeError(f"Case {case['name']} has no compile inputs")
    return compile_inputs, str(case["source"])


async def run_case(
    case: dict,
    *,
    case_root: Path | None = None,
) -> CaseResult:
    name = str(case["name"])
    suite_name = str(case.get("suite_name") or "")
    run_name = str(case.get("run_name") or suite_name or "suite")
    expected_stdout = str(case["expected_stdout"])
    expected_exit = int(case["expected_exit_code"])
    expect_compile_success = bool(case.get("expect_compile_success", True))
    session_root = session_path()
    case_parent = session_root if case_root is None else case_root
    case_dir = create_case_dir(case_parent, name)
    case_started = time.monotonic()

    def finalize_case(result: CaseResult) -> CaseResult:
        emit_environment_log(
            "suite.case.complete",
            suite_name=suite_name or None,
            run_name=run_name,
            case_name=name,
            phase=result.phase,
            passed=result.passed,
            failure_type=result.failure_type,
            timed_out=result.timed_out,
            actual_exit_code=result.actual_exit_code,
            duration_ms=int((time.monotonic() - case_started) * 1000),
        )
        return result

    file_stem = sanitize_fragment(f"test-{name}", fallback="test", max_length=64)
    compile_inputs, c_source = prepare_case_files(
        case=case,
        case_dir=case_dir,
        file_stem=file_stem,
    )

    extra_link_args = [str(arg) for arg in list(case.get("link_args", []))]
    out_file = case_dir / file_stem
    gcc_out_file = case_dir / f"{file_stem}_gcc"
    cc_binary = (session_root / "cc").resolve()

    cc_command_parts = [
        str(cc_binary),
        *(str(path) for path in compile_inputs),
        *extra_link_args,
        "-o",
        str(out_file),
    ]
    cc_command = shell_join(cc_command_parts)
    gcc_command = shell_join(
        [
            "gcc",
            *reference_gcc_compile_args(),
            *(str(path) for path in compile_inputs),
            *extra_link_args,
            "-o",
            str(gcc_out_file),
        ]
    )
    emit_environment_log(
        "suite.case.start",
        suite_name=suite_name or None,
        run_name=run_name,
        case_name=name,
        compile_input_count=len(compile_inputs),
        compile_inputs=[str(path.name) for path in compile_inputs],
        link_args=extra_link_args,
        expect_compile_success=expect_compile_success,
    )
    emit_environment_log(
        "suite.case.compile.start",
        suite_name=suite_name or None,
        run_name=run_name,
        case_name=name,
        cc_command=cc_command,
        gcc_command=gcc_command if not skip_gcc_benchmark() and expect_compile_success else None,
    )

    compile_tasks = [
        run_timed_command(
            command=cc_command,
            cwd=case_dir,
            timeout_seconds=45,
        )
    ]
    should_benchmark_with_gcc = not skip_gcc_benchmark() and expect_compile_success
    if should_benchmark_with_gcc:
        compile_tasks.append(
            run_timed_command(
                command=gcc_command,
                cwd=case_dir,
                timeout_seconds=45,
            )
        )

    compile_results = await asyncio.gather(*compile_tasks)
    cc, compile_time_ms = compile_results[0]
    gcc: envoi.RunResult | None = None
    gcc_compile_time_ms = 0.0
    if should_benchmark_with_gcc:
        gcc, gcc_compile_time_ms = compile_results[1]

    compiler_warnings = cc.stderr.strip() or None if cc.exit_code == 0 else None
    gcc_warnings = gcc.stderr.strip() or None if gcc is not None else None
    compile_timed_out = command_timed_out(cc)
    binary_size_bytes = file_size(out_file)
    gcc_binary_size_bytes = file_size(gcc_out_file)
    emit_environment_log(
        "suite.case.compile.complete",
        suite_name=suite_name or None,
        run_name=run_name,
        case_name=name,
        cc_exit_code=cc.exit_code,
        gcc_exit_code=gcc.exit_code if gcc is not None else None,
        compile_time_ms=compile_time_ms,
        gcc_compile_time_ms=gcc_compile_time_ms if gcc is not None else None,
        timed_out=compile_timed_out,
    )

    if should_benchmark_with_gcc and gcc is not None and gcc.exit_code != 0:
        reference_error = gcc.stderr or gcc.stdout or "gcc failed to compile the reference case"
        return finalize_case(CaseResult(
            name=name,
            phase="compile",
            passed=False,
            c_source=c_source,
            expected_stdout=expected_stdout,
            actual_stdout="",
            expected_exit_code=expected_exit,
            actual_exit_code=cc.exit_code,
            compile_time_ms=compile_time_ms,
            gcc_compile_time_ms=gcc_compile_time_ms,
            binary_size_bytes=binary_size_bytes,
            gcc_binary_size_bytes=gcc_binary_size_bytes,
            failure_type="compile_error",
            compiler_warnings=compiler_warnings,
            gcc_warnings=gcc_warnings,
            timed_out=compile_timed_out,
            stderr=(
                "reference gcc could not compile this case "
                "(the test inputs may be invalid C)\n" + reference_error
            ),
        ))

    if not expect_compile_success:
        passed = cc.exit_code != 0 and not compile_timed_out
        return finalize_case(CaseResult(
            name=name,
            phase="compile",
            passed=passed,
            c_source=c_source,
            expected_stdout="",
            actual_stdout="",
            expected_exit_code=expected_exit,
            actual_exit_code=cc.exit_code,
            compile_time_ms=compile_time_ms,
            gcc_compile_time_ms=gcc_compile_time_ms,
            binary_size_bytes=None,
            gcc_binary_size_bytes=gcc_binary_size_bytes,
            failure_type=None if passed else ("timeout" if compile_timed_out else "compile_error"),
            compiler_warnings=compiler_warnings,
            gcc_warnings=gcc_warnings,
            timed_out=compile_timed_out,
            stderr=(
                None
                if passed
                else (
                    "compilation timed out"
                    if compile_timed_out
                    else "expected compilation to fail but it succeeded"
                )
            ),
        ))

    if cc.exit_code != 0:
        return finalize_case(CaseResult(
            name=name,
            phase="compile",
            passed=False,
            c_source=c_source,
            expected_stdout=expected_stdout,
            actual_stdout="",
            expected_exit_code=expected_exit,
            actual_exit_code=cc.exit_code,
            compile_time_ms=compile_time_ms,
            gcc_compile_time_ms=gcc_compile_time_ms,
            binary_size_bytes=None,
            gcc_binary_size_bytes=gcc_binary_size_bytes,
            failure_type="timeout" if compile_timed_out else "compile_error",
            compiler_warnings=compiler_warnings,
            gcc_warnings=gcc_warnings,
            timed_out=compile_timed_out,
            stderr=(cc.stderr or cc.stdout or "compilation failed"),
        ))

    arch_error = await maybe_verify_arch(out_file)
    if arch_error is not None:
        return finalize_case(CaseResult(
            name=name,
            phase="verify",
            passed=False,
            c_source=c_source,
            expected_stdout=expected_stdout,
            actual_stdout="",
            expected_exit_code=expected_exit,
            actual_exit_code=-1,
            compile_time_ms=compile_time_ms,
            gcc_compile_time_ms=gcc_compile_time_ms,
            binary_size_bytes=binary_size_bytes,
            gcc_binary_size_bytes=gcc_binary_size_bytes,
            run_time_ms=None,
            gcc_run_time_ms=None,
            failure_type="wrong_arch",
            compiler_warnings=compiler_warnings,
            gcc_warnings=gcc_warnings,
            stderr=arch_error,
        ))

    run_tasks = [
        run_timed_command(
            command=shlex.quote(str(out_file)),
            cwd=case_dir,
            timeout_seconds=15,
        )
    ]
    should_run_gcc_binary = gcc is not None and gcc.exit_code == 0
    if should_run_gcc_binary:
        run_tasks.append(
            run_timed_command(
                command=shlex.quote(str(gcc_out_file)),
                cwd=case_dir,
                timeout_seconds=15,
            )
        )
    emit_environment_log(
        "suite.case.run.start",
        suite_name=suite_name or None,
        run_name=run_name,
        case_name=name,
        output_binary=str(out_file.name),
        reference_binary=str(gcc_out_file.name) if should_run_gcc_binary else None,
    )

    run_results = await asyncio.gather(*run_tasks)
    run, run_time_ms = run_results[0]
    gcc_run_time_ms: float | None = None
    if should_run_gcc_binary:
        _, gcc_run_time_ms = run_results[1]
    emit_environment_log(
        "suite.case.run.complete",
        suite_name=suite_name or None,
        run_name=run_name,
        case_name=name,
        exit_code=run.exit_code,
        run_time_ms=run_time_ms,
        gcc_run_time_ms=gcc_run_time_ms,
        timed_out=command_timed_out(run),
    )

    actual_stdout = raw_stdout_text(run)
    normalized_actual_stdout = actual_stdout.strip()
    normalized_expected_stdout = expected_stdout.strip()
    run_timed_out = command_timed_out(run)
    stdout_mismatch = normalized_actual_stdout != normalized_expected_stdout
    exit_code_mismatch = run.exit_code != expected_exit
    passed = not stdout_mismatch and not exit_code_mismatch
    signal_name = decode_signal_name(run.exit_code)
    stdout_diff_summary = (
        summarize_stdout_difference(expected_stdout, actual_stdout) if stdout_mismatch else None
    )
    runtime_stderr = run.stderr or None

    failure_type: str | None = None
    if not passed:
        if run_timed_out:
            failure_type = "timeout"
        elif signal_name is not None:
            failure_type = "crash"
        elif stdout_mismatch:
            failure_type = "wrong_output"
        elif exit_code_mismatch:
            failure_type = "wrong_exit_code"

    stderr = None
    if not passed:
        parts: list[str] = []
        if failure_type == "timeout":
            parts.append("execution timed out")
        if signal_name is not None:
            parts.append(f"process crashed with {signal_name}")
        if stdout_mismatch:
            if stdout_diff_summary is not None:
                parts.append(stdout_diff_summary)
            parts.append(
                f"stdout mismatch:\n  expected: {expected_stdout!r}\n  actual:   {actual_stdout!r}"
            )
        if exit_code_mismatch:
            parts.append(f"exit code mismatch: expected {expected_exit}, got {run.exit_code}")
        if run.stderr:
            parts.append(f"stderr:\n  {run.stderr.strip()}")
        stderr = "\n".join(parts)

    return finalize_case(CaseResult(
        name=name,
        phase="verify",
        passed=passed,
        c_source=c_source,
        expected_stdout=expected_stdout,
        actual_stdout=actual_stdout,
        expected_exit_code=expected_exit,
        actual_exit_code=run.exit_code,
        compile_time_ms=compile_time_ms,
        gcc_compile_time_ms=gcc_compile_time_ms,
        binary_size_bytes=binary_size_bytes,
        gcc_binary_size_bytes=gcc_binary_size_bytes,
        run_time_ms=run_time_ms,
        gcc_run_time_ms=gcc_run_time_ms,
        failure_type=failure_type,
        signal_name=signal_name,
        stdout_diff_summary=stdout_diff_summary,
        compiler_warnings=compiler_warnings,
        gcc_warnings=gcc_warnings,
        runtime_stderr=runtime_stderr,
        timed_out=run_timed_out,
        stderr=stderr,
    ))


async def run_cases_parallel(
    cases: list[dict],
    *,
    suite_name: str,
    run_name: str | None = None,
) -> list[CaseResult]:
    case_root = create_suite_case_root(suite_name, run_name)
    semaphore = get_case_run_semaphore()
    suite_started = time.monotonic()
    effective_run_name = run_name or suite_name
    emit_environment_log(
        "suite.run.start",
        suite_name=suite_name,
        run_name=effective_run_name,
        total_cases=len(cases),
        concurrency=max_test_concurrency(),
    )

    async def run_one(case: dict) -> CaseResult:
        async with semaphore:
            case_payload = dict(case)
            case_payload.setdefault("suite_name", suite_name)
            case_payload.setdefault("run_name", effective_run_name)
            return await run_case(case_payload, case_root=case_root)

    try:
        results = await asyncio.gather(*(run_one(case) for case in cases))
        emit_environment_log(
            "suite.run.complete",
            suite_name=suite_name,
            run_name=effective_run_name,
            total_cases=len(results),
            passed=sum(1 for result in results if result.passed),
            failed=sum(1 for result in results if not result.passed),
            duration_ms=int((time.monotonic() - suite_started) * 1000),
        )
        return results
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
