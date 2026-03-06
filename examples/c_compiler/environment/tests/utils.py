"""
Shared types and test runner used by all test suites.

CaseResult / TestResult are the structured output models returned by every
@envoi.test route. run_case() compiles a single .c file with the submitted
compiler, benchmarks it against gcc, and returns a CaseResult.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import shutil
import time
from pathlib import Path

import envoi
from pydantic import BaseModel, Field


class DebugArtifact(BaseModel):
    path: str
    kind: str
    size_bytes: int
    sha256: str
    line_count: int | None = None
    text_chunks: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    stderr: str | None = None
    debug_artifacts: list[DebugArtifact] = Field(default_factory=list)


class TestResult(BaseModel):
    passed: int
    failed: int
    total: int
    cases: list[CaseResult]
arch_verified = False
arch_lock: asyncio.Lock | None = None
case_run_semaphore: asyncio.Semaphore | None = None
case_run_semaphore_limit: int | None = None


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


def reset_debug_artifacts_dir(root: Path) -> Path:
    debug_dir = root / "debug_artifacts"
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir


def split_text_chunks(text: str, max_chars: int = 12_000) -> list[str]:
    if not text:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for line in text.splitlines(keepends=True):
        line_parts = [line[i : i + max_chars] for i in range(0, len(line), max_chars)]
        if not line_parts:
            line_parts = [line]

        for part in line_parts:
            if current and current_size + len(part) > max_chars:
                chunks.append("".join(current))
                current = [part]
                current_size = len(part)
                continue
            current.append(part)
            current_size += len(part)

    if current:
        chunks.append("".join(current))

    return chunks


def artifact_kind(path: Path, is_text: bool) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".s", ".asm"}:
        return "assembly"
    if suffix in {".ast", ".parse", ".tree"}:
        return "ast"
    if suffix in {".ir", ".ll"}:
        return "ir"
    if suffix in {".txt", ".log", ".trace", ".stderr", ".stdout"}:
        return "log"
    return "text" if is_text else "binary"


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

    arch_verified = False
    arch_lock = None
    case_run_semaphore = None
    case_run_semaphore_limit = None


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
            return (
                f"wrong target architecture: expected {expected_arch_label}, got {machine}"
            )

        arch_verified = True
        return None


def collect_debug_artifacts(debug_dir: Path) -> list[DebugArtifact]:
    if not debug_dir.exists():
        return []

    artifacts: list[DebugArtifact] = []
    for file_path in sorted(
        (path for path in debug_dir.rglob("*") if path.is_file()),
        key=lambda path: str(path.relative_to(debug_dir)),
    ):
        payload = file_path.read_bytes()
        notes: list[str] = []

        is_binary = b"\x00" in payload
        text_chunks: list[str] = []
        line_count: int | None = None

        if not is_binary:
            text = payload.decode("utf-8", errors="replace")
            if text.encode("utf-8", errors="replace") != payload:
                notes.append("Decoded as UTF-8 with replacement.")

            text_chunks = split_text_chunks(text)
            line_count = text.count("\n")
            if text and not text.endswith("\n"):
                line_count += 1
        else:
            notes.append("Binary content omitted from payload.")

        artifacts.append(
            DebugArtifact(
                path=str(file_path.relative_to(debug_dir)),
                kind=artifact_kind(file_path, not is_binary),
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                line_count=line_count,
                text_chunks=text_chunks,
                notes=notes,
            )
        )

    return artifacts


async def run_case(
    case: dict,
    *,
    case_root: Path | None = None,
) -> CaseResult:
    name = str(case["name"])
    src = str(case["source"])
    expected_stdout = str(case["expected_stdout"])
    expected_exit = int(case["expected_exit_code"])
    expect_compile_success = bool(case.get("expect_compile_success", True))
    session_root = session_path()
    case_parent = session_root if case_root is None else case_root
    case_dir = create_case_dir(case_parent, name)

    file_stem = sanitize_fragment(f"test-{name}", fallback="test", max_length=64)
    source_path_value = case.get("source_path")
    if source_path_value is None:
        c_file = case_dir / f"{file_stem}.c"
        c_file.write_text(src, encoding="utf-8")
    else:
        c_file = Path(str(source_path_value)).expanduser().resolve()
        if not c_file.is_file():
            raise RuntimeError(f"Missing case source file: {c_file}")
    out_file = case_dir / file_stem
    gcc_out_file = case_dir / f"{file_stem}_gcc"
    debug_dir = reset_debug_artifacts_dir(case_dir)
    cc_binary = (session_root / "cc").resolve()

    cc_command = (
        f"{shlex.quote(str(cc_binary))} {shlex.quote(str(c_file))} "
        f"-o {shlex.quote(str(out_file))}"
    )
    gcc_command = (
        f"gcc {shlex.quote(str(c_file))} -o {shlex.quote(str(gcc_out_file))}"
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

    binary_size_bytes = file_size(out_file)
    gcc_binary_size_bytes = file_size(gcc_out_file)

    if not expect_compile_success:
        passed = cc.exit_code != 0
        return CaseResult(
            name=name,
            phase="compile",
            passed=passed,
            c_source=src,
            expected_stdout="",
            actual_stdout="",
            expected_exit_code=expected_exit,
            actual_exit_code=cc.exit_code,
            compile_time_ms=compile_time_ms,
            gcc_compile_time_ms=gcc_compile_time_ms,
            binary_size_bytes=None,
            gcc_binary_size_bytes=gcc_binary_size_bytes,
            stderr=None if passed else "expected compilation to fail but it succeeded",
            debug_artifacts=collect_debug_artifacts(debug_dir) if not passed else [],
        )

    if cc.exit_code != 0:
        return CaseResult(
            name=name,
            phase="compile",
            passed=False,
            c_source=src,
            expected_stdout=expected_stdout,
            actual_stdout="",
            expected_exit_code=expected_exit,
            actual_exit_code=cc.exit_code,
            compile_time_ms=compile_time_ms,
            gcc_compile_time_ms=gcc_compile_time_ms,
            binary_size_bytes=None,
            gcc_binary_size_bytes=gcc_binary_size_bytes,
            stderr=(cc.stderr or cc.stdout or "compilation failed"),
            debug_artifacts=collect_debug_artifacts(debug_dir),
        )

    arch_error = await maybe_verify_arch(out_file)
    if arch_error is not None:
        return CaseResult(
            name=name,
            phase="verify",
            passed=False,
            c_source=src,
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
            stderr=arch_error,
            debug_artifacts=collect_debug_artifacts(debug_dir),
        )

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

    run_results = await asyncio.gather(*run_tasks)
    run, run_time_ms = run_results[0]
    gcc_run_time_ms: float | None = None
    if should_run_gcc_binary:
        _, gcc_run_time_ms = run_results[1]

    passed = (
        run.stdout.strip() == expected_stdout.strip()
        and run.exit_code == expected_exit
    )

    stderr = None
    if not passed:
        parts: list[str] = []
        if run.stdout.strip() != expected_stdout.strip():
            parts.append(
                "stdout mismatch:\n"
                f"  expected: {expected_stdout!r}\n"
                f"  actual:   {run.stdout.strip()!r}"
            )
        if run.exit_code != expected_exit:
            parts.append(
                f"exit code mismatch: expected {expected_exit}, got {run.exit_code}"
            )
        if run.stderr:
            parts.append(f"stderr:\n  {run.stderr.strip()}")
        stderr = "\n".join(parts)

    return CaseResult(
        name=name,
        phase="verify",
        passed=passed,
        c_source=src,
        expected_stdout=expected_stdout,
        actual_stdout=run.stdout,
        expected_exit_code=expected_exit,
        actual_exit_code=run.exit_code,
        compile_time_ms=compile_time_ms,
        gcc_compile_time_ms=gcc_compile_time_ms,
        binary_size_bytes=binary_size_bytes,
        gcc_binary_size_bytes=gcc_binary_size_bytes,
        run_time_ms=run_time_ms,
        gcc_run_time_ms=gcc_run_time_ms,
        stderr=stderr,
        debug_artifacts=collect_debug_artifacts(debug_dir) if not passed else [],
    )


async def run_cases_parallel(
    cases: list[dict],
    *,
    suite_name: str,
    run_name: str | None = None,
) -> list[CaseResult]:
    case_root = create_suite_case_root(suite_name, run_name)
    semaphore = get_case_run_semaphore()

    async def run_one(case: dict) -> CaseResult:
        async with semaphore:
            return await run_case(case, case_root=case_root)

    try:
        return await asyncio.gather(*(run_one(case) for case in cases))
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
