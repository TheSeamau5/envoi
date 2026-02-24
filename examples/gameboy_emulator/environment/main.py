"""
Game Boy emulator evaluation environment.

Evaluates a submitted Rust project that emulates Game Boy (DMG) and
Game Boy Color (GBC) hardware. The submission must produce a ./gb_emu
binary via build.sh.

Interface: ./gb_emu <rom.gb> [OPTIONS]
Required flags: --headless, --max-cycles, --serial-log, --screenshot-on-breakpoint

Test suites (progressive difficulty):
  1. blargg/cpu_instrs          — CPU opcode correctness
  2. blargg/instr_timing        — instruction cycle counts
     mooneye/timer              — timer hardware accuracy
  3. acid2/dmg                  — pixel-perfect DMG PPU rendering
  4. mooneye/mbc                — MBC1/MBC2/MBC5 bank switching
  5. mooneye/acceptance         — interrupts, DMA, joypad, misc timing
     blargg/interrupt_time
  6. blargg/dmg_sound           — APU registers and behavior
  7. acid2/cgb                  — CGB PPU rendering
  8. blargg + mooneye + mealybug + samesuite full sweep

Debug artifact contract (optional, no flags required):
  - The submitted emulator may write debugging output to ./debug_artifacts/.
  - This directory is cleared before each test case.
  - Any files written there are captured and returned in structured failure data.
"""

from __future__ import annotations

import envoi
from tests.acid2 import acid2_cgb, acid2_dmg
from tests.blargg import blargg_cpu, blargg_misc, blargg_sound, blargg_timing
from tests.mealybug import mealybug_cgb, mealybug_dmg
from tests.mooneye import mooneye_acceptance, mooneye_mbc, mooneye_timer
from tests.samesuite import samesuite

__all__ = [
    "blargg_cpu",
    "blargg_timing",
    "blargg_sound",
    "blargg_misc",
    "mooneye_timer",
    "mooneye_mbc",
    "mooneye_acceptance",
    "acid2_dmg",
    "acid2_cgb",
    "mealybug_dmg",
    "mealybug_cgb",
    "samesuite",
    "build_emulator",
]


@envoi.setup
async def build_emulator(submission: envoi.Documents) -> None:
    build = await envoi.run("chmod +x build.sh && ./build.sh", timeout_seconds=300)
    if build.exit_code != 0:
        raise RuntimeError(f"Build failed (exit {build.exit_code}).\n{build.stderr}")
