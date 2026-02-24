"""
Shared types and test runner used by all Game Boy test suites.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
from pathlib import Path

import envoi
from pydantic import BaseModel, Field

# ─── Result models ───


class DebugArtifact(BaseModel):
    path: str
    kind: str
    size_bytes: int
    sha256: str
    line_count: int | None = None
    text_chunks: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RomResult(BaseModel):
    """Result of running a single test ROM."""

    name: str
    suite: str
    protocol: str  # "serial" or "breakpoint" or "screenshot"
    passed: bool
    rom_path: str
    serial_output: str = ""  # for serial protocol
    exit_code: int = 0
    cycles_run: int | None = None
    stderr: str | None = None
    screenshot_match: bool | None = None  # for screenshot protocol
    screenshot_diff_pixels: int | None = None
    debug_artifacts: list[DebugArtifact] = Field(default_factory=list)


class TestResult(BaseModel):
    passed: int
    failed: int
    total: int
    cases: list[RomResult]


# ─── Helpers ───


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


def to_result(results: list[RomResult]) -> TestResult:
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
        matches = [c for c in cases if c.get("name") == normalized_name]
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
        if current and current_size + len(line) > max_chars:
            chunks.append("".join(current))
            current = [line]
            current_size = len(line)
            continue
        current.append(line)
        current_size += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def collect_debug_artifacts(debug_dir: Path) -> list[DebugArtifact]:
    if not debug_dir.exists():
        return []
    artifacts: list[DebugArtifact] = []
    for file_path in sorted(
        (p for p in debug_dir.rglob("*") if p.is_file()),
        key=lambda p: str(p.relative_to(debug_dir)),
    ):
        payload = file_path.read_bytes()
        is_binary = b"\x00" in payload
        text_chunks: list[str] = []
        line_count: int | None = None
        notes: list[str] = []
        if not is_binary:
            text = payload.decode("utf-8", errors="replace")
            text_chunks = split_text_chunks(text)
            line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        else:
            notes.append("Binary content omitted.")
        artifacts.append(
            DebugArtifact(
                path=str(file_path.relative_to(debug_dir)),
                kind="text" if not is_binary else "binary",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                line_count=line_count,
                text_chunks=text_chunks,
                notes=notes,
            )
        )
    return artifacts


# ─── Constants ───

# 50M machine cycles ≈ 12 seconds of GB time. Enough for any test ROM.
DEFAULT_MAX_CYCLES = 50_000_000
# Wall-clock timeout per ROM execution. Safety net for hangs.
DEFAULT_TIMEOUT_SECONDS = 30


# ─── Protocol 1: Serial output (Blargg) ───


async def run_rom_serial(
    rom_path: str,
    suite: str,
    name: str,
    *,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    mode: str = "dmg",
) -> RomResult:
    """Run a Blargg-style test ROM and parse serial output for pass/fail."""
    sp = session_path()
    debug_dir = reset_debug_artifacts_dir(sp)
    serial_log = sp / "serial_output.bin"

    cmd = (
        f"./gb_emu {shlex.quote(rom_path)} --headless"
        f" --max-cycles {max_cycles}"
        f" --serial-log {shlex.quote(str(serial_log))}"
        f" --mode {mode}"
    )

    result = await envoi.run(cmd, timeout_seconds=timeout_seconds)
    serial_text = ""
    if serial_log.exists():
        serial_text = serial_log.read_bytes().decode("ascii", errors="replace")

    passed = "Passed" in serial_text and "Failed" not in serial_text

    stderr: str | None = None
    if not passed:
        parts: list[str] = []
        if result.exit_code != 0:
            parts.append(f"emulator exit code: {result.exit_code}")
        if result.stderr:
            parts.append(f"emulator stderr:\n{result.stderr.strip()}")
        if serial_text:
            parts.append(f"serial output:\n{serial_text.strip()}")
        else:
            parts.append("no serial output captured")
        stderr = "\n".join(parts)

    return RomResult(
        name=name,
        suite=suite,
        protocol="serial",
        passed=passed,
        rom_path=rom_path,
        serial_output=serial_text,
        exit_code=result.exit_code,
        stderr=stderr,
        debug_artifacts=collect_debug_artifacts(debug_dir) if not passed else [],
    )


# ─── Protocol 2: Magic breakpoint (Mooneye / SameSuite) ───


async def run_rom_breakpoint(
    rom_path: str,
    suite: str,
    name: str,
    *,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    mode: str = "dmg",
) -> RomResult:
    """Run a Mooneye/SameSuite test ROM. Pass/fail via exit code."""
    sp = session_path()
    debug_dir = reset_debug_artifacts_dir(sp)
    screenshot_path = sp / "breakpoint_screenshot.png"

    cmd = (
        f"./gb_emu {shlex.quote(rom_path)} --headless"
        f" --max-cycles {max_cycles}"
        f" --screenshot-on-breakpoint {shlex.quote(str(screenshot_path))}"
        f" --mode {mode}"
    )

    result = await envoi.run(cmd, timeout_seconds=timeout_seconds)

    # exit code 0 = Fibonacci registers (pass)
    # exit code 1 = 0x42 registers (fail)
    # other = crash or timeout
    passed = result.exit_code == 0

    stderr: str | None = None
    if not passed:
        parts: list[str] = []
        if result.exit_code == 1:
            parts.append("test ROM signaled failure (registers = 0x42)")
        elif result.exit_code != 0:
            parts.append(f"emulator exit code: {result.exit_code}")
        if result.stderr:
            parts.append(f"emulator stderr:\n{result.stderr.strip()}")
        if result.stdout:
            parts.append(f"emulator stdout:\n{result.stdout.strip()}")
        stderr = "\n".join(parts)

    return RomResult(
        name=name,
        suite=suite,
        protocol="breakpoint",
        passed=passed,
        rom_path=rom_path,
        exit_code=result.exit_code,
        stderr=stderr,
        debug_artifacts=collect_debug_artifacts(debug_dir) if not passed else [],
    )


# ─── Protocol 3: Screenshot comparison (acid2 / mealybug) ───


async def run_rom_screenshot(
    rom_path: str,
    reference_png_path: str,
    suite: str,
    name: str,
    *,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    mode: str = "dmg",
) -> RomResult:
    """Run a test ROM, dump framebuffer on breakpoint, compare against reference PNG."""
    sp = session_path()
    debug_dir = reset_debug_artifacts_dir(sp)
    screenshot_path = sp / "screenshot.png"

    cmd = (
        f"./gb_emu {shlex.quote(rom_path)} --headless"
        f" --max-cycles {max_cycles}"
        f" --screenshot-on-breakpoint {shlex.quote(str(screenshot_path))}"
        f" --mode {mode}"
    )

    result = await envoi.run(cmd, timeout_seconds=timeout_seconds)

    # Did the emulator hit the breakpoint and produce a screenshot?
    if result.exit_code != 0 or not screenshot_path.exists():
        failure_stderr: str = (
            "emulator did not produce screenshot "
            f"(exit code {result.exit_code})"
            + (f"\nstderr: {result.stderr.strip()}" if result.stderr else "")
        )
        return RomResult(
            name=name,
            suite=suite,
            protocol="screenshot",
            passed=False,
            rom_path=rom_path,
            exit_code=result.exit_code,
            screenshot_match=False,
            stderr=failure_stderr,
            debug_artifacts=collect_debug_artifacts(debug_dir),
        )

    # Pixel-for-pixel comparison using imagemagick
    compare_cmd = (
        "compare -metric AE"
        f" {shlex.quote(str(screenshot_path))}"
        f" {shlex.quote(reference_png_path)}"
        " NULL: 2>&1"
    )
    compare_result = await envoi.run(compare_cmd, timeout_seconds=10)

    diff_pixels = -1
    try:
        diff_pixels = int(compare_result.stdout.strip())
    except (ValueError, AttributeError):
        pass

    passed = diff_pixels == 0

    stderr: str | None = None
    if not passed:
        parts: list[str] = []
        if diff_pixels > 0:
            parts.append(f"screenshot differs from reference by {diff_pixels} pixels")
        elif diff_pixels == -1:
            parts.append(
                "imagemagick compare failed: "
                f"{compare_result.stdout} {compare_result.stderr}"
            )
        parts.append(f"reference: {reference_png_path}")
        parts.append(f"actual: {screenshot_path}")
        stderr = "\n".join(parts)

    return RomResult(
        name=name,
        suite=suite,
        protocol="screenshot",
        passed=passed,
        rom_path=rom_path,
        exit_code=result.exit_code,
        screenshot_match=passed,
        screenshot_diff_pixels=diff_pixels if diff_pixels >= 0 else None,
        stderr=stderr,
        debug_artifacts=collect_debug_artifacts(debug_dir) if not passed else [],
    )
