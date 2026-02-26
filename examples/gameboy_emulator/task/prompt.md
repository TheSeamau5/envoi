# Game Boy Emulator — Build from Scratch in Rust

Build a Game Boy (DMG) and Game Boy Color (GBC) emulator in Rust from scratch.
This is EXTREMELY IMPORTANT: no cheating, no wrappers, no shortcuts.
Do NOT use any existing emulator core, library, or crate (e.g. no gameboy crate,
no mooneye-gb, no SameBoy bindings). Write all emulation components yourself in
Rust: CPU, PPU, APU, timer, memory map, MBC, joypad, serial, DMA.

## Submission Requirements

Your submission must include:

- `Cargo.toml`
- `build.sh` (must produce `./gb_emu` binary when run)
- `src/` (your Rust source code)

## Command-Line Interface

```
./gb_emu <rom.gb> [OPTIONS]
```

**Required flags:**

| Flag | Description |
|------|-------------|
| `--headless` | No GUI, no window, no audio output. |
| `--max-cycles <N>` | Stop after N machine cycles (safety limit). |
| `--serial-log <path>` | Log every byte written to the serial port (writes to `$FF01` triggered by `$FF02` bit 7) as raw bytes to the given file path. Use `/dev/stdout` to print to stdout. |
| `--screenshot-on-breakpoint <path>` | When opcode `0x40` (`LD B,B`) executes: dump the current 160×144 framebuffer as a raw 160×144 grayscale PNG to `<path>`, then exit with code 0 if registers contain `B=3, C=5, D=8, E=13, H=21, L=34` (pass) or exit with code 1 otherwise (fail). |

**Palette mapping for screenshots:**

- **DMG**: shade `0 → 0x00`, `1 → 0x55`, `2 → 0xAA`, `3 → 0xFF`
- **CGB**: each 5-bit color component c → `(c << 3) | (c >> 2)`

**Optional flags (for CGB support):**

| Flag | Description |
|------|-------------|
| `--mode dmg\|cgb\|auto` | Force DMG or CGB mode (default: `auto` from ROM header byte at `0x143`). |

## Development and Testing Strategy

Do not rely on external testing tools. Create and run your own local tests in the workspace (small focused test programs and shell scripts).

1. Write small local tests for each feature before/while implementing it
2. Run local tests frequently after each change
3. When tests fail: read the error output carefully, fix the code, rerun
4. After fixing, rerun previously passing local tests to check for regressions
5. Commit after each meaningful change

Your goal is to pass ALL test suites. Work methodically: CPU instruction correctness (Blargg cpu_instrs) → timing → PPU rendering → MBC → CGB extensions.

## Initial State (Boot ROM NOT Required)

Do not implement a boot ROM. Initialize CPU registers to post-boot state:

**DMG:**
```
A=0x01  F=0xB0  B=0x00  C=0x13  D=0x00  E=0xD8  H=0x01  L=0x4D
SP=0xFFFE  PC=0x0100
```

**CGB:**
```
A=0x11  F=0x80  B=0x00  C=0x00  D=0xFF  E=0x56  H=0x00  L=0x0D
SP=0xFFFE  PC=0x0100
```

**I/O registers (DMG post-boot):**
```
LCDC=0x91  STAT=0x85  SCY=0x00  SCX=0x00  LY=0x00  LYC=0x00
BGP=0xFC   OBP0=0xFF  OBP1=0xFF WY=0x00   WX=0x00
IF=0xE1    IE=0x00    DIV=0xAB  TIMA=0x00  TMA=0x00  TAC=0xF8
NR10=0x80  NR11=0xBF  NR12=0xF3 NR14=0xBF NR21=0x3F NR22=0x00
NR24=0xBF  NR30=0x7F  NR31=0xFF NR32=0x9F NR34=0xBF
NR41=0xFF  NR42=0x00  NR43=0x00 NR44=0xBF NR50=0x77 NR51=0xF3 NR52=0xF1
P1=0xCF    SB=0x00    SC=0x7E
```

---

## Reference Documentation (READ BEFORE IMPLEMENTING)

The `/workspace/reference/` directory contains authoritative documentation.
Do not guess at hardware behavior — look it up.

| Resource | Path | Use For |
|----------|------|---------|
| CPU instruction timing | `reference/gbctr.pdf` | Per-M-cycle bus activity for every opcode. MUST view as PDF (contains diagrams). |
| Opcode table (machine-readable) | `reference/dmgops.json` | 512 opcodes with cycle counts, flags, timing. Parse to generate dispatch tables. |
| Memory map, PPU, timer, MBC, DMA | `reference/pandocs/*.md` | Text-based, read directly. Start here for conceptual understanding. |
| Opcode mnemonics cross-ref | `reference/opcodes.toml` | Compact reference for instruction categories. |

### Recommended reading order:
1. `reference/pandocs/CPU_Instruction_Set.md` — SM83 instruction set
2. `reference/dmgops.json` — parse for opcode dispatch table
3. `reference/pandocs/Memory_Map.md` — address space layout
4. `reference/pandocs/Timer.md` — timer subsystem
5. `reference/pandocs/Rendering.md` — PPU modes and pixel pipeline
6. `reference/gbctr.pdf` — exact timing when tests fail

### Critical traps (read the reference docs, but know these exist):
- F register lower nibble is ALWAYS 0. `POP AF` must mask: `F = F & 0xF0`
- `ADD SP,e` / `LD HL,SP+e`: H and C flags from unsigned LOW BYTE add only
- Accumulator rotates (RLCA/RRCA/RLA/RRA) always set Z=0; CB-prefixed rotates set Z from result
- EI enables interrupts after the NEXT instruction, not immediately
- HALT bug: when IME=0 and `(IE & IF) != 0`, PC fails to increment
- Writing to DIV resets the entire 16-bit internal counter
- STAT interrupt fires on rising edge only — overlapping conditions suppress it

## Reference Emulator (READ-ONLY)

A copy of [SameBoy](https://github.com/LIJI32/SameBoy), the most accurate Game Boy emulator,
is available at `/workspace/reference/sameboy/`. It is written in C.

You may read this code to understand how hardware behaviors are implemented:
- `Core/sm83_cpu.c` — CPU instruction execution
- `Core/display.c` — PPU rendering pipeline
- `Core/timing.c` — timer subsystem
- `Core/memory.c` — memory map and MBC implementations
- `Core/apu.c` — audio processing

**STRICT RULES:**
- You MUST NOT call, link, import, or FFI-bind to any SameBoy code
- You MUST NOT copy code verbatim — understand the logic, then write your own Rust implementation
- Your Rust code must have zero dependencies on SameBoy — it is reference material only

## Debug Artifacts (REQUIRED)

Your emulator MUST write debugging output to `./debug_artifacts/` during
test ROM execution. This directory is cleared before each test and
captured automatically on failure.

Required artifacts:
- `cpu_trace.log` — last 100 instructions executed (PC, opcode, registers)
- `ppu_state.txt` — PPU mode, LY, LCDC at breakpoint/crash

Without these, you must guess where bugs are, which wastes turns.

## Progress Tracking (REQUIRED)

Maintain a `PROGRESS.md` file in your workspace root. Update it after every
significant change. Structure:

```
# Progress

## Current Status
- Tests passing: <suite> X/Y
- Current focus: <what you're working on>

## What Works
- <list of working components>

## What I've Tried That Failed
- [Turn ~N] <description of failed approach>

## Current Plan
1. <next item>
2. <next item>
```

This helps you remember what you've tried across many turns.
