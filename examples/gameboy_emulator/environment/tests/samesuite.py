"""
SameSuite — SameBoy's test suite. Uses Fibonacci/0x42 breakpoint protocol.
Tests APU, DMA, interrupts, PPU.
"""

from __future__ import annotations

import envoi

from .utils import (
    RomResult,
    TestResult,
    fixture_path,
    run_rom_breakpoint,
    select_cases,
    to_result,
)

samesuite = envoi.suite("samesuite")


def discover_samesuite_roms(subdir: str) -> list[dict]:
    root = fixture_path("samesuite", subdir)
    if not root.is_dir():
        return []
    cases = []
    for gb_file in sorted(root.rglob("*.gb")):
        rel = gb_file.relative_to(root)
        cases.append({
            "name": f"{subdir}/{rel.with_suffix('')}",
            "rom_path": str(gb_file),
        })
    return cases


@samesuite.test("apu")
async def apu(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_samesuite_roms("apu"), n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "samesuite", case["name"]
        )
        results.append(result)
    return to_result(results)


@samesuite.test("dma")
async def dma(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_samesuite_roms("dma"), n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "samesuite", case["name"]
        )
        results.append(result)
    return to_result(results)


@samesuite.test("interrupt")
async def interrupt(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_samesuite_roms("interrupt"), n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "samesuite", case["name"]
        )
        results.append(result)
    return to_result(results)


@samesuite.test("ppu")
async def ppu(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    cases = select_cases(
        discover_samesuite_roms("ppu"), n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_breakpoint(
            case["rom_path"], "samesuite", case["name"]
        )
        results.append(result)
    return to_result(results)
