"""
Mooneye test suites — compiled from source, using magic-breakpoint protocol.

Suites:
  mooneye_timer      — acceptance/timer/ (13 tests)
  mooneye_mbc        — emulator-only/mbc1/ + mbc2/ + mbc5/ (28 tests)
  mooneye_acceptance — everything in acceptance/ (75 tests)
"""

from __future__ import annotations

from pathlib import Path

import envoi

from .utils import (
    RomResult,
    TestResult,
    fixture_path,
    run_rom_breakpoint,
    select_cases,
    to_result,
)

mooneye_timer = envoi.suite("mooneye_timer")
mooneye_mbc = envoi.suite("mooneye_mbc")
mooneye_acceptance = envoi.suite("mooneye_acceptance")


def discover_mooneye_roms(*path_parts: str) -> list[dict]:
    """Discover .gb files under a Mooneye directory."""
    root = fixture_path("mooneye", *path_parts)
    if not root.is_dir():
        return []
    cases = []
    for gb_file in sorted(root.rglob("*.gb")):
        rel = gb_file.relative_to(root)
        cases.append({
            "name": str(rel.with_suffix("")),
            "rom_path": str(gb_file),
        })
    return cases


# ─── Timer tests ───


@mooneye_timer.test()
async def timer_all(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_mooneye_roms("acceptance", "timer"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "mooneye_timer", case["name"]
        )
        results.append(result)
    return to_result(results)


# ─── MBC tests ───


@mooneye_mbc.test("mbc1")
async def mbc1(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_mooneye_roms("emulator-only", "mbc1"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "mooneye_mbc", case["name"]
        )
        results.append(result)
    return to_result(results)


@mooneye_mbc.test("mbc2")
async def mbc2(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_mooneye_roms("emulator-only", "mbc2"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "mooneye_mbc", case["name"]
        )
        results.append(result)
    return to_result(results)


@mooneye_mbc.test("mbc5")
async def mbc5(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_mooneye_roms("emulator-only", "mbc5"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "mooneye_mbc", case["name"]
        )
        results.append(result)
    return to_result(results)


# ─── Full acceptance ───


@mooneye_acceptance.test("bits")
async def bits(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_mooneye_roms("acceptance", "bits"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "mooneye_acceptance", case["name"]
        )
        results.append(result)
    return to_result(results)


@mooneye_acceptance.test("ppu")
async def ppu(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_mooneye_roms("acceptance", "ppu"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "mooneye_acceptance", case["name"]
        )
        results.append(result)
    return to_result(results)


@mooneye_acceptance.test("oam_dma")
async def oam_dma(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_mooneye_roms("acceptance", "oam_dma"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "mooneye_acceptance", case["name"]
        )
        results.append(result)
    return to_result(results)


@mooneye_acceptance.test("interrupts")
async def interrupts(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_mooneye_roms("acceptance", "interrupts"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "mooneye_acceptance", case["name"]
        )
        results.append(result)
    return to_result(results)


@mooneye_acceptance.test("all")
async def acceptance_all(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    """All Mooneye acceptance tests (excluding boot-ROM-dependent tests)."""
    all_roms = discover_mooneye_roms("acceptance")
    # Filter out boot_* tests — they require a boot ROM we don't provide
    filtered = [c for c in all_roms if not Path(c["name"]).name.startswith("boot_")]
    cases = select_cases(filtered, n_tests=n_tests, test_name=test_name)
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "mooneye_acceptance", case["name"]
        )
        results.append(result)
    return to_result(results)
