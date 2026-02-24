"""
Acid2 pixel-perfect PPU tests — screenshot comparison protocol.

  acid2_dmg — dmg-acid2.gb vs reference-dmg.png (160×144, grayscale)
  acid2_cgb — cgb-acid2.gbc vs reference.png (160×144, color)
"""

from __future__ import annotations

import envoi

from .utils import (
    RomResult,
    TestResult,
    fixture_path,
    run_rom_screenshot,
    to_result,
)

acid2_dmg = envoi.suite("acid2_dmg")
acid2_cgb = envoi.suite("acid2_cgb")


@acid2_dmg.test()
async def dmg_acid2(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    rom = str(fixture_path("acid2", "dmg", "dmg-acid2.gb"))
    ref = str(fixture_path("acid2", "dmg", "reference.png"))
    r: RomResult = await run_rom_screenshot(
        rom, ref, "acid2_dmg", "dmg-acid2", mode="dmg"
    )
    return to_result([r])


@acid2_cgb.test()
async def cgb_acid2(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    rom = str(fixture_path("acid2", "cgb", "cgb-acid2.gbc"))
    ref = str(fixture_path("acid2", "cgb", "reference.png"))
    r: RomResult = await run_rom_screenshot(
        rom, ref, "acid2_cgb", "cgb-acid2", mode="cgb"
    )
    return to_result([r])
