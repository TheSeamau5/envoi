"""
Mealybug Tearoom Tests — PPU mode-3 register change timing via screenshots.

31 precompiled ROMs. Each ROM triggers LD B,B breakpoint.
Compare emulator framebuffer against reference PNG.
Separate references for DMG (24 tests) and CGB (31 tests).
"""

from __future__ import annotations

import envoi

from .utils import (
    RomResult,
    TestResult,
    fixture_path,
    run_rom_screenshot,
    select_cases,
    to_result,
)

mealybug_dmg = envoi.suite("mealybug_dmg")
mealybug_cgb = envoi.suite("mealybug_cgb")


def discover_mealybug(variant: str) -> list[dict]:
    roms_dir = fixture_path("mealybug", "roms")
    expected_dir = fixture_path("mealybug", "expected", variant)
    if not roms_dir.is_dir() or not expected_dir.is_dir():
        return []
    cases = []
    for ref_png in sorted(expected_dir.glob("*.png")):
        rom_name = ref_png.stem + ".gb"
        rom_path = roms_dir / rom_name
        if rom_path.exists():
            cases.append({
                "name": ref_png.stem,
                "rom_path": str(rom_path),
                "reference_path": str(ref_png),
            })
    return cases


@mealybug_dmg.test()
async def mealybug_dmg_all(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    cases = select_cases(
        discover_mealybug("dmg"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_screenshot(
            case["rom_path"],
            case["reference_path"],
            "mealybug_dmg",
            case["name"],
            mode="dmg",
        )
        results.append(result)
    return to_result(results)


@mealybug_cgb.test()
async def mealybug_cgb_all(
    n_tests: int = 0, test_name: str | None = None
) -> TestResult:
    cases = select_cases(
        discover_mealybug("cgb"),
        n_tests=n_tests, test_name=test_name,
    )
    results: list[RomResult] = []
    for case in cases:
        result = await run_rom_screenshot(
            case["rom_path"],
            case["reference_path"],
            "mealybug_cgb",
            case["name"],
            mode="cgb",
        )
        results.append(result)
    return to_result(results)
