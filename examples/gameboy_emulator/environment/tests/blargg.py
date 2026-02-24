"""
Blargg test suites — precompiled ROMs using serial-output protocol.

Suites:
  blargg_cpu       — cpu_instrs (11 sub-tests) + cpu_instrs combined
  blargg_timing    — instr_timing, mem_timing, mem_timing-2
  blargg_sound     — dmg_sound (12 sub-tests)
  blargg_misc      — oam_bug, halt_bug, interrupt_time
"""

from __future__ import annotations

import envoi

from .utils import (
    RomResult,
    TestResult,
    fixture_path,
    run_rom_serial,
    select_cases,
    to_result,
)

blargg_cpu = envoi.suite("blargg_cpu")
blargg_timing = envoi.suite("blargg_timing")
blargg_sound = envoi.suite("blargg_sound")
blargg_misc = envoi.suite("blargg_misc")


def discover_roms(base: str, subdir: str = "") -> list[dict]:
    """Discover .gb files under a Blargg test directory."""
    root = fixture_path("blargg", base, subdir) if subdir else fixture_path("blargg", base)
    if not root.is_dir():
        return []
    cases = []
    for gb_file in sorted(root.glob("*.gb")):
        cases.append({
            "name": gb_file.stem,
            "rom_path": str(gb_file),
        })
    return cases


# ─── CPU instruction tests ───


@blargg_cpu.test("individual")
async def cpu_instrs_individual(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    """Run each of the 11 individual cpu_instrs test ROMs."""
    cases = select_cases(
        discover_roms("cpu_instrs", "individual"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        r = await run_rom_serial(case["rom_path"], "blargg_cpu", case["name"])
        results.append(r)
    return to_result(results)


@blargg_cpu.test("combined")
async def cpu_instrs_combined(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    """Run the combined cpu_instrs ROM (all 11 in sequence)."""
    rom = fixture_path("blargg", "cpu_instrs", "cpu_instrs.gb")
    r = await run_rom_serial(
        str(rom), "blargg_cpu", "cpu_instrs_combined",
        max_cycles=200_000_000,  # combined ROM takes longer
        timeout_seconds=120,
    )
    return to_result([r])


# ─── Timing tests ───


@blargg_timing.test("instr_timing")
async def instr_timing(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    rom = fixture_path("blargg", "instr_timing", "instr_timing.gb")
    r = await run_rom_serial(str(rom), "blargg_timing", "instr_timing")
    return to_result([r])


@blargg_timing.test("mem_timing")
async def mem_timing(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    cases = select_cases(
        discover_roms("mem_timing", "individual"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_serial(
            case["rom_path"], "blargg_timing", case["name"]
        )
        results.append(result)
    return to_result(results)


@blargg_timing.test("mem_timing_2")
async def mem_timing_2(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    cases = select_cases(
        discover_roms("mem_timing-2", "rom_singles"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_serial(
            case["rom_path"], "blargg_timing", case["name"]
        )
        results.append(result)
    return to_result(results)


# ─── Sound tests ───


@blargg_sound.test("dmg_sound")
async def dmg_sound(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    cases = select_cases(
        discover_roms("dmg_sound", "rom_singles"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_serial(
            case["rom_path"], "blargg_sound", case["name"]
        )
        results.append(result)
    return to_result(results)


# ─── Misc tests ───


@blargg_misc.test("oam_bug")
async def oam_bug(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    cases = select_cases(
        discover_roms("oam_bug", "rom_singles"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_serial(
            case["rom_path"], "blargg_misc", case["name"]
        )
        results.append(result)
    return to_result(results)


@blargg_misc.test("halt_bug")
async def halt_bug(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    rom = fixture_path("blargg", "halt_bug.gb")
    r = await run_rom_serial(str(rom), "blargg_misc", "halt_bug")
    return to_result([r])


@blargg_misc.test("interrupt_time")
async def interrupt_time(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    rom = fixture_path("blargg", "interrupt_time", "interrupt_time.gb")
    r = await run_rom_serial(str(rom), "blargg_misc", "interrupt_time")
    return to_result([r])
