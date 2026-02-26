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

Debug artifact contract (REQUIRED — see task prompt):
  - The submitted emulator MUST write debugging output to ./debug_artifacts/.
  - This directory is cleared before each test case.
  - Any files written there are captured and returned in structured failure data.
  - Required: cpu_trace.log, ppu_state.txt (see prompt for format).
"""

from __future__ import annotations

import os
import shlex
import textwrap
from pathlib import Path

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


def fixtures_root() -> Path:
    root = os.environ.get("ENVOI_TESTS_ROOT", "/opt/tests")
    return Path(root).expanduser().resolve()


def fixtures_ready(root: Path) -> bool:
    required_paths = [
        "blargg/cpu_instrs/cpu_instrs.gb",
        "blargg/instr_timing/instr_timing.gb",
        "blargg/halt_bug.gb",
        "blargg/interrupt_time/interrupt_time.gb",
        "mooneye/acceptance",
        "mooneye/emulator-only/mbc1",
        "acid2/dmg/dmg-acid2.gb",
        "acid2/dmg/reference.png",
        "acid2/cgb/cgb-acid2.gbc",
        "acid2/cgb/reference.png",
        "mealybug/roms/m3_bgp_change.gb",
        "mealybug/expected/dmg/m3_bgp_change.png",
        "mealybug/expected/cgb/m3_lcdc_bg_en_change2.png",
        "controls/breakpoint_fail.gb",
        "controls/breakpoint_pass.gb",
    ]
    for rel in required_paths:
        if not (root / rel).exists():
            return False
    samesuite_root = root / "samesuite"
    if not samesuite_root.is_dir():
        return False
    if not any(samesuite_root.rglob("*.gb")):
        return False
    return True


def fixture_bootstrap_command(root: Path) -> str:
    root_q = shlex.quote(str(root))
    return textwrap.dedent(
        f"""
        set -euo pipefail
        ROOT={root_q}
        mkdir -p "$ROOT"

        apt-get update
        apt-get install -y --no-install-recommends \\
            build-essential gcc g++ git curl wget cmake unzip \\
            bison flex \\
            pkg-config libssl-dev libpng-dev \\
            python3 python3-pip python3-pil ripgrep \\
            imagemagick
        rm -rf /var/lib/apt/lists/*

        if [ ! -f "$ROOT/blargg/cpu_instrs/cpu_instrs.gb" ]; then
            rm -rf "$ROOT/blargg"
            git clone --depth 1 https://github.com/retrio/gb-test-roms.git "$ROOT/blargg"
        fi

        if ! command -v wla-gb >/dev/null 2>&1 || ! command -v wlalink >/dev/null 2>&1; then
            WLA_DX_COMMIT=89a90a56be5c2b8cf19a9afa3e1b32384ddb1a97
            TMP_WLA="$(mktemp -d)"
            curl -fsSL "https://github.com/vhelin/wla-dx/archive/${{WLA_DX_COMMIT}}.tar.gz" | tar xz -C "$TMP_WLA"
            cd "$TMP_WLA/wla-dx-${{WLA_DX_COMMIT}}"
            cmake .
            make -j"$(nproc)"
            cp binaries/wla-gb binaries/wlalink /usr/local/bin/
            cd /
            rm -rf "$TMP_WLA"
        fi

        if [ ! -d "$ROOT/mooneye/acceptance" ] || [ ! -d "$ROOT/mooneye/emulator-only" ]; then
            rm -rf "$ROOT/mooneye"
            TMP_MOONEYE="$(mktemp -d)"
            git clone --depth 1 https://github.com/Gekkio/mooneye-test-suite.git "$TMP_MOONEYE/mooneye-src"
            cd "$TMP_MOONEYE/mooneye-src"
            make clean all WLA=wla-gb WLALINK=wlalink
            mkdir -p "$ROOT/mooneye"
            cp -r build/* "$ROOT/mooneye/"
            cd /
            rm -rf "$TMP_MOONEYE"
        fi

        if ! command -v rgbasm >/dev/null 2>&1 || ! command -v rgblink >/dev/null 2>&1 || ! command -v rgbfix >/dev/null 2>&1; then
            TMP_RGBDS="$(mktemp -d)"
            git clone --depth 1 --branch v0.6.1 https://github.com/gbdev/rgbds.git "$TMP_RGBDS/rgbds"
            cd "$TMP_RGBDS/rgbds"
            make -j"$(nproc)"
            make install
            cd /
            rm -rf "$TMP_RGBDS"
        fi

        if [ ! -f "$ROOT/controls/breakpoint_fail.gb" ] || [ ! -f "$ROOT/controls/breakpoint_pass.gb" ]; then
            TMP_GUARD="$(mktemp -d)"
            mkdir -p "$ROOT/controls"
            cat > "$TMP_GUARD/fail.asm" <<'ASM'
        SECTION "Entry", ROM0[$0100]
            jp Start
            ds $0150 - @, 0

        SECTION "Code", ROM0[$0150]
        Start:
            ld b, $00
            ld c, $00
            ld d, $00
            ld e, $00
            ld h, $00
            ld l, $00
            db $40
        Hang:
            jr Hang
        ASM
            cat > "$TMP_GUARD/pass.asm" <<'ASM'
        SECTION "Entry", ROM0[$0100]
            jp Start
            ds $0150 - @, 0

        SECTION "Code", ROM0[$0150]
        Start:
            ld b, $03
            ld c, $05
            ld d, $08
            ld e, $0D
            ld h, $15
            ld l, $22
            db $40
        Hang:
            jr Hang
        ASM
            cd "$TMP_GUARD"
            rgbasm -o fail.o fail.asm
            rgblink -o breakpoint_fail.gb fail.o
            rgbfix -v -p 255 breakpoint_fail.gb
            rgbasm -o pass.o pass.asm
            rgblink -o breakpoint_pass.gb pass.o
            rgbfix -v -p 255 breakpoint_pass.gb
            cp breakpoint_fail.gb breakpoint_pass.gb "$ROOT/controls/"
            cd /
            rm -rf "$TMP_GUARD"
        fi

        if [ ! -f "$ROOT/acid2/dmg/dmg-acid2.gb" ] || [ ! -f "$ROOT/acid2/dmg/reference.png" ]; then
            rm -rf "$ROOT/acid2/dmg"
            TMP_DMG_ACID2="$(mktemp -d)"
            git clone --recurse-submodules --depth 1 https://github.com/mattcurrie/dmg-acid2.git "$TMP_DMG_ACID2/dmg-acid2"
            cd "$TMP_DMG_ACID2/dmg-acid2"
            sed -E -i 's/^[[:space:]]*HARDWARE_INC[[:space:]]+SET[[:space:]]+1[[:space:]]*$/DEF HARDWARE_INC EQU 1/' mgblib/src/hardware.inc
            make
            mkdir -p "$ROOT/acid2/dmg"
            cp build/dmg-acid2.gb "$ROOT/acid2/dmg/"
            cp img/reference-dmg.png "$ROOT/acid2/dmg/reference.png"
            cd /
            rm -rf "$TMP_DMG_ACID2"
        fi

        if [ ! -f "$ROOT/acid2/cgb/cgb-acid2.gbc" ] || [ ! -f "$ROOT/acid2/cgb/reference.png" ]; then
            rm -rf "$ROOT/acid2/cgb"
            TMP_CGB_ACID2="$(mktemp -d)"
            git clone --recurse-submodules --depth 1 https://github.com/mattcurrie/cgb-acid2.git "$TMP_CGB_ACID2/cgb-acid2"
            cd "$TMP_CGB_ACID2/cgb-acid2"
            sed -E -i 's/^[[:space:]]*HARDWARE_INC[[:space:]]+SET[[:space:]]+1[[:space:]]*$/DEF HARDWARE_INC EQU 1/' mgblib/src/hardware.inc
            make
            mkdir -p "$ROOT/acid2/cgb"
            if [ -f build/cgb-acid2.gbc ]; then
                cp build/cgb-acid2.gbc "$ROOT/acid2/cgb/"
            else
                cp build/cgb-acid2.gb "$ROOT/acid2/cgb/cgb-acid2.gbc"
            fi
            cp img/reference.png "$ROOT/acid2/cgb/reference.png"
            cd /
            rm -rf "$TMP_CGB_ACID2"
        fi

        if [ ! -f "$ROOT/mealybug/roms/m3_bgp_change.gb" ] || [ ! -f "$ROOT/mealybug/expected/dmg/m3_bgp_change.png" ] || [ ! -f "$ROOT/mealybug/expected/cgb/m3_lcdc_bg_en_change2.png" ]; then
            rm -rf "$ROOT/mealybug"
            TMP_MEALYBUG="$(mktemp -d)"
            git clone --depth 1 https://github.com/mattcurrie/mealybug-tearoom-tests.git "$TMP_MEALYBUG/mealybug"
            mkdir -p "$ROOT/mealybug/roms" "$ROOT/mealybug/expected"
            cd "$TMP_MEALYBUG/mealybug"
            unzip -o mealybug-tearoom-tests.zip -d "$ROOT/mealybug/roms/"
            cp -r expected/DMG-blob "$ROOT/mealybug/expected/dmg"
            cp -r "expected/CPU CGB C" "$ROOT/mealybug/expected/cgb"
            cd /
            rm -rf "$TMP_MEALYBUG"
        fi

        if [ ! -d "$ROOT/samesuite" ] || [ -z "$(find "$ROOT/samesuite" -name '*.gb' -print -quit 2>/dev/null)" ]; then
            rm -rf "$ROOT/samesuite"
            TMP_SAMESUITE="$(mktemp -d)"
            git clone --depth 1 https://github.com/LIJI32/SameSuite.git "$TMP_SAMESUITE/samesuite"
            cd "$TMP_SAMESUITE/samesuite"
            make
            mkdir -p "$ROOT/samesuite"
            find . -name '*.gb' -exec cp --parents {{}} "$ROOT/samesuite/" \\;
            cd /
            rm -rf "$TMP_SAMESUITE"
        fi
        """
    ).strip()


async def ensure_gameboy_fixtures() -> None:
    root = fixtures_root()
    if fixtures_ready(root):
        return
    setup_cmd = fixture_bootstrap_command(root)
    provision = await envoi.run(setup_cmd, timeout_seconds=3000)
    if provision.exit_code != 0:
        raise RuntimeError(
            "Failed to provision Game Boy test fixtures.\n"
            f"stdout:\n{provision.stdout}\n"
            f"stderr:\n{provision.stderr}"
        )
    if not fixtures_ready(root):
        raise RuntimeError(
            "Fixture provisioning completed but required files are still missing "
            f"under {root}"
        )


@envoi.setup
async def build_emulator(submission: envoi.Documents) -> None:
    await ensure_gameboy_fixtures()
    build = await envoi.run("chmod +x build.sh && ./build.sh", timeout_seconds=300)
    if build.exit_code != 0:
        raise RuntimeError(f"Build failed (exit {build.exit_code}).\n{build.stderr}")
