"""
Shared types and test runner used by all test suites.

CaseResult / TestResult are the structured output models returned by every
@envoi.test route. run_case() compiles a single .c file with the submitted
compiler, benchmarks it against gcc, and returns a CaseResult.
"""

from __future__ import annotations

import hashlib
import os
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


def session_path() -> Path:
    try:
        return envoi.session_path()
    except LookupError:
        return Path.cwd()


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
    passed = sum(1 for r in results if r.passed)
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


def reset_debug_artifacts_dir(sp: Path) -> Path:
    debug_dir = sp / "debug_artifacts"
    shutil.rmtree(debug_dir, ignore_errors=True)
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


async def detect_elf_machine(path: Path) -> str | None:
    probe = await envoi.run(f"readelf -h {shlex.quote(path.name)}", timeout_seconds=5)
    if probe.exit_code != 0:
        return None

    for line in probe.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Machine:"):
            return stripped.split(":", maxsplit=1)[1].strip()

    return None


def collect_debug_artifacts(debug_dir: Path) -> list[DebugArtifact]:
    if not debug_dir.exists():
        return []

    artifacts: list[DebugArtifact] = []
    for file_path in sorted(
        (p for p in debug_dir.rglob("*") if p.is_file()),
        key=lambda p: str(p.relative_to(debug_dir)),
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


async def run_case(case: dict) -> CaseResult:
    name = case["name"]
    src = case["source"]
    expected_stdout = case["expected_stdout"]
    expected_exit = case["expected_exit_code"]
    expect_compile_success = case.get("expect_compile_success", True)
    sp = session_path()

    c_file = sp / f"test_{name}.c"
    out_file = sp / f"test_{name}"
    gcc_out_file = sp / f"test_{name}_gcc"
    c_file.write_text(src)
    debug_dir = reset_debug_artifacts_dir(sp)

    t0 = time.monotonic()
    cc = await envoi.run(
        f"./cc {shlex.quote(c_file.name)} -o {shlex.quote(out_file.name)}",
        timeout_seconds=45,
    )
    compile_time_ms = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    gcc = await envoi.run(
        f"gcc {shlex.quote(c_file.name)} -o {shlex.quote(gcc_out_file.name)}",
        timeout_seconds=45,
    )
    gcc_compile_time_ms = (time.monotonic() - t0) * 1000

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

    machine = await detect_elf_machine(out_file)
    expected_arch_label, expected_machine_values = expected_target_arch()
    if machine is not None and machine not in expected_machine_values:
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
            stderr=f"wrong target architecture: expected {expected_arch_label}, got {machine}",
            debug_artifacts=collect_debug_artifacts(debug_dir),
        )

    t0 = time.monotonic()
    run = await envoi.run(shlex.quote(f"./{out_file.name}"), timeout_seconds=15)
    run_time_ms = (time.monotonic() - t0) * 1000

    gcc_run_time_ms = None
    if gcc.exit_code == 0:
        t0 = time.monotonic()
        await envoi.run(shlex.quote(f"./{gcc_out_file.name}"), timeout_seconds=15)
        gcc_run_time_ms = (time.monotonic() - t0) * 1000

    passed = run.stdout.strip() == expected_stdout.strip() and run.exit_code == expected_exit

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
