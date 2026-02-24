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

## Reference Documentation

A `/workspace/reference/` directory is provided containing authoritative documentation. **READ THESE before implementing.**

### `reference/gbctr.pdf` — Game Boy: Complete Technical Reference (Gekkio)

This is a 174-page PDF and the single best resource for CPU instruction timing.
Every instruction has per-M-cycle pseudocode showing exact bus activity
(which address is on the bus, whether it's a read or write, what the ALU
and IDU are doing in parallel).

**IMPORTANT: This PDF contains critical DIAGRAMS that cannot be understood
from extracted text alone.** You MUST visually inspect the PDF pages — not just
extract text. Key visual content includes:

- **Figure 4.3**: Block diagram of the SM83 CPU core (register file, ALU, IDU,
  bus connections) — essential for understanding the architecture
- **Figure 5.4**: Fetch/execute overlap timing diagram with waveforms showing
  how NOP, RST, LDH, and INC A pipeline across M-cycles
- **Instruction timing tables** (Appendix A, pages 163-165): Two full-page
  16×16 opcode grids showing every instruction mnemonic — these render as
  visual tables, not readable text when extracted
- **Bus timing diagrams** (Appendix C, pages 171-172): Waveform diagrams for
  external bus read/write/idle cycles and OAM DMA bus behavior
- **Chip pinout diagrams** (Appendix D): DMG/MGB CPU and MBC chip pinouts
- **Register definition boxes**: Throughout the document, hardware registers are
  shown as visual bit-field diagrams with R/W annotations per bit

When you need to understand a specific instruction's timing or a hardware
register's bit layout, **open the PDF and look at the actual page** rather
than relying solely on text extraction. Use a tool that can render PDF pages
as images so you can see the diagrams.

### `reference/pandocs/` — Pan Docs (Markdown)

The canonical community reference. ~35 markdown files covering PPU, timer,
audio, memory map, MBC, interrupts, DMA, and everything else. This is
text-based and can be read directly. Start here for conceptual understanding
of each subsystem, then cross-reference gbctr for exact timing.

### `reference/dmgops.json` — Machine-Readable Opcode Table

512 opcodes in JSON. Each entry has: `Name`, `Group`, `TCyclesBranch`,
`TCyclesNoBranch`, `Length`, `Flags` (Z/N/H/C with values `"0"`/`"1"`/`"-"`/`"Z"`/`"H"`/`"C"`),
and per-M-cycle `TimingNoBranch` arrays (`fetch`/`read`/`write`/`internal`).
You can parse this JSON to generate your opcode dispatch table or verify
cycle counts programmatically.

### `reference/opcodes.toml` — Compact Mnemonic Reference

From the gb-ctr repository. Useful for cross-referencing instruction categories.

---

## Sharp SM83 CPU Architecture

The Game Boy CPU is the Sharp SM83. It is NEITHER a Z80 nor an 8080 — it has
a unique instruction set that borrows from both but has critical differences.

### Registers

```
16-bit  Hi   Lo   Name
AF      A    F    Accumulator & Flags
BC      B    C    General purpose
DE      D    E    General purpose
HL      H    L    General purpose / indirect addressing
SP      -    -    Stack Pointer
PC      -    -    Program Counter
```

**Flags register F** (bits 7-4 used, bits 3-0 ALWAYS zero):

| Bit | Name | Purpose |
|-----|------|---------|
| 7 | Z | Zero flag |
| 6 | N | Subtract flag (BCD) |
| 5 | H | Half-carry flag (BCD) |
| 4 | C | Carry flag |

**The F register lower nibble is ALWAYS 0.** `POP AF` must mask: `F = F & 0xF0`.

### Clocks and Timing

- **System clock**: 4.194304 MHz (4 MiHz). Called "T-cycles" or "clocks".
- **Machine cycle (M-cycle)**: 4 T-cycles. CPU performs one bus operation per M-cycle.
- All instruction durations in this document are in **M-cycles** unless noted.
- CGB double-speed: system clock doubles to 8.388608 MHz. PPU and audio are NOT affected.

### Fetch/Execute Overlap

The SM83 uses fetch/execute overlap: the fetch of the NEXT instruction overlaps
with the last M-cycle of the CURRENT instruction. See **gbctr Figure 5.4** for
the waveform diagram.

- A "1-M-cycle" instruction (NOP, LD r,r', ADD r) performs its register
  operation AND fetches the next opcode in the same M-cycle.
- Instruction timings as documented already account for this overlap.
- Branch instructions (JP, CALL, RET) have an extra internal M-cycle because
  the fetch uses the NEW PC.

### Instruction Set Summary

256 base opcodes + 256 CB-prefixed opcodes = 512 total.
11 base opcodes are undefined (`D3, DB, DD, E3, E4, EB, EC, ED, F4, FC, FD`)
— executing them hangs the CPU.

The CB prefix (`0xCB`) is followed by a second byte selecting: RLC, RRC, RL,
RR, SLA, SRA, SWAP, SRL, BIT, RES, SET on registers B/C/D/E/H/L/(HL)/A.

### Critical Flag Behaviors (Common Emulator Bugs)

**Half-carry (H flag)**:
- 8-bit ops: carry from bit 3 → bit 4
- `ADD HL,rr`: carry from bit 11 → bit 12
- `ADD SP,e` and `LD HL,SP+e`: carry from bit 3 of the LOW BYTE add only

**`ADD SP,e` / `LD HL,SP+e` flags** (VERY tricky — most emulators get wrong):

Z=0, N=0 ALWAYS. H and C are based on the addition of the UNSIGNED low byte
of SP with the UNSIGNED operand byte — NOT the 16-bit result:

```
result_lo, carry = (SP & 0xFF) + (e as u8)   // unsigned 8-bit add
H = carry from bit 3 of this 8-bit add
C = carry from bit 7 of this 8-bit add
```

The high byte is computed separately: `SPH + sign_extend(e) + carry`.
(See gbctr pages 80-81 and 44 for the detailed M-cycle pseudocode.)

**DAA (Decimal Adjust Accumulator)**:
```
if N == 0:
    if C or A > 0x99: A += 0x60; C = 1
    if H or (A & 0x0F) > 0x09: A += 0x06
else:
    if C: A -= 0x60
    if H: A -= 0x06
Z = (A == 0), H = 0  // N unchanged, C may be set but never cleared
```

**Rotate instructions (RLCA/RRCA/RLA/RRA vs RLC/RRC/RL/RR)**:
- Accumulator rotates (RLCA, RRCA, RLA, RRA): **ALWAYS set Z=0**
- CB-prefixed rotates (RLC r, RRC r, RL r, RR r): **set Z based on result**
- This difference is a very common emulator bug.

### Instruction Timing Table (M-cycles)

| Category | Cycles | Examples |
|----------|--------|----------|
| Register-only 8-bit ops | 1 | `NOP`, `LD r,r'`, `ADD r`, `XOR r`, `INC r` |
| 8-bit with immediate | 2 | `LD r,n`, `ADD n`, `CP n` |
| 8-bit with (HL) read | 2 | `LD r,(HL)`, `ADD (HL)`, `BIT b,(HL)` |
| 8-bit with (HL) write | 3 | `LD (HL),r`, `LD (HL),n`, `INC (HL)` |
| CB-prefix register | 2 | `RLC r`, `SLA r`, `SWAP r` |
| CB-prefix (HL) read-only | 3 | `BIT b,(HL)` (only 3, not 4) |
| CB-prefix (HL) read-modify-write | 4 | `RLC (HL)`, `SET b,(HL)`, `RES b,(HL)` |
| 16-bit register ops | 2 | `INC rr`, `DEC rr`, `LD SP,HL` |
| 16-bit load immediate | 3 | `LD rr,nn` |
| `ADD HL,rr` | 2 | 1 internal cycle for 16-bit ALU |
| `ADD SP,e` | 4 | Read e, ALU low, ALU high, writeback |
| `LD HL,SP+e` | 3 | Read e, ALU low, ALU high |
| `LD A,(BC/DE)` | 2 | Read from address |
| `LD A,(nn)` | 4 | Read nn_lo, nn_hi, read data |
| `LD (nn),A` | 4 | Read nn_lo, nn_hi, write data |
| `LD (nn),SP` | 5 | Read nn_lo, nn_hi, write SPL, write SPH |
| `LDH A,(n)` / `LDH (n),A` | 3 | Read n, read/write at FF00+n |
| `LDH A,(C)` / `LDH (C),A` | 2 | Read/write at FF00+C |
| `PUSH rr` | 4 | Internal, write msb, write lsb |
| `POP rr` | 3 | Read lsb, read msb |
| `JP nn` | 4 | Read lo, read hi, internal |
| `JP cc,nn` (taken) | 4 | Same as JP nn |
| `JP cc,nn` (not taken) | 3 | Read lo, read hi (no jump) |
| `JP HL` | 1 | Sets PC=HL |
| `JR e` | 3 | Read e, internal |
| `JR cc,e` (taken) | 3 | Same as JR e |
| `JR cc,e` (not taken) | 2 | Read e |
| `CALL nn` | 6 | Read lo, read hi, internal, push msb, push lsb |
| `CALL cc,nn` (taken) | 6 | Same as CALL nn |
| `CALL cc,nn` (not taken) | 3 | Read lo, read hi |
| `RET` | 4 | Pop lo, pop hi, internal |
| `RET cc` (taken) | 5 | Internal (cc check), pop lo, pop hi, internal |
| `RET cc` (not taken) | 2 | Internal (cc check) |
| `RETI` | 4 | Same as RET + IME=1 (immediate, no delay) |
| `RST n` | 4 | Internal, push msb, push lsb |
| `DI` | 1 | IME=0 immediately |
| `EI` | 1 | IME=1 after NEXT instruction |

### EI Instruction Delay

EI does NOT enable interrupts immediately. IME is set to 1 **after the
instruction FOLLOWING EI executes**:

```
EI
INC A    ; ← runs with IME still 0
NOP      ; ← interrupts can fire here
```

RETI does NOT have this delay — it enables IME immediately.
DI does NOT have a delay either — IME=0 takes effect immediately.

### HALT Behavior

Three cases depending on IME, IE, and IF:

**Case 1: IME=1** — CPU halts. Resumes when `(IE & IF & 0x1F) != 0`.
CPU dispatches interrupt normally (jumps to vector, clears IF bit).

**Case 2: IME=0, `(IE & IF & 0x1F) == 0`** — CPU halts. Resumes on
interrupt condition. CPU does NOT jump to vector. IF bit NOT cleared.

**Case 3: IME=0, `(IE & IF & 0x1F) != 0`** — **THE HALT BUG**.
CPU does NOT halt. The byte after HALT is read twice (PC fails to
increment). Example: `HALT` / `INC A` → `INC A` executes TWICE.

### Interrupt Handling

Interrupts checked between instructions. Priority (highest first):

| Bit | Interrupt | Vector |
|-----|-----------|--------|
| 0 | VBlank | `0x0040` |
| 1 | STAT | `0x0048` |
| 2 | Timer | `0x0050` |
| 3 | Serial | `0x0058` |
| 4 | Joypad | `0x0060` |

**Dispatch sequence** (when IME=1 and `(IE & IF & 0x1F) != 0`):

1. IME = 0
2. Two internal M-cycles (NOPs)
3. Push PC high byte to `(--SP)`
4. Push PC low byte to `(--SP)`
5. PC = vector address; clear the corresponding IF bit

**Total: 5 M-cycles** (20 T-cycles). If waking from HALT: +1 M-cycle.

---

## Memory Map

```
Address Range     Size    Name    Description
0x0000 - 0x3FFF   16 KB   ROM0    Non-switchable ROM bank 0
0x4000 - 0x7FFF   16 KB   ROMX    Switchable ROM bank (via MBC)
0x8000 - 0x9FFF    8 KB   VRAM    Video RAM (bank 0-1 in CGB)
0xA000 - 0xBFFF    8 KB   SRAM    External cartridge RAM
0xC000 - 0xCFFF    4 KB   WRAM0   Work RAM bank 0
0xD000 - 0xDFFF    4 KB   WRAMX   Work RAM bank 1-7 (CGB)
0xE000 - 0xFDFF   7680 B  ECHO    Mirror of C000-DDFF
0xFE00 - 0xFE9F    160 B  OAM     Sprite attribute table
0xFEA0 - 0xFEFF    96 B   ---     Unusable (reads 0x00 on DMG)
0xFF00 - 0xFF7F    128 B  I/O     Hardware I/O registers
0xFF80 - 0xFFFE    127 B  HRAM    High RAM (fast, DMA-safe)
0xFFFF             1 B    IE      Interrupt Enable register
```

### I/O Register Map

```
Addr    Name   Bits (7..0)                                       Notes
0xFF00  P1     --  P15 P14 P13 P12 P11 P10                      Joypad (active-low)
0xFF01  SB     Serial data byte                                  R/W
0xFF02  SC     SIO_EN -- -- -- -- -- SIO_CLK                     Serial control
0xFF04  DIV    Upper 8 bits of 16-bit system counter             Resets to 0 on write
0xFF05  TIMA   Timer counter                                     Increments at TAC freq
0xFF06  TMA    Timer modulo (reload value)                       Loaded on TIMA overflow
0xFF07  TAC    -- -- -- -- -- TAC_EN TAC_CLK1 TAC_CLK0           Timer control
0xFF0F  IF     -- -- -- Joypad Serial Timer STAT VBlank          Interrupt flags
0xFF40  LCDC   LCD_EN WIN_MAP WIN_EN TILE_SEL BG_MAP OBJ_SIZE OBJ_EN BG_EN
0xFF41  STAT   -- INTR_LYC INTR_M2 INTR_M1 INTR_M0 LYC_STAT MODE1 MODE0
0xFF42  SCY    Background scroll Y
0xFF43  SCX    Background scroll X
0xFF44  LY     Current scanline (0-153, read-only)
0xFF45  LYC    LY compare (triggers STAT interrupt)
0xFF46  DMA    OAM DMA source address (upper byte)
0xFF47  BGP    BG palette (shade mapping, DMG only)
0xFF48  OBP0   Sprite palette 0 (DMG only)
0xFF49  OBP1   Sprite palette 1 (DMG only)
0xFF4A  WY     Window Y position
0xFF4B  WX     Window X position + 7
0xFF4D  KEY1   CGB speed switch (bit 7=current, bit 0=prepare)
0xFF4F  VBK    CGB VRAM bank select (bit 0)
0xFF50  BOOT   Boot ROM disable (write 1 to unmap)
0xFF55  HDMA5  CGB HDMA length/mode/start
0xFF68  BCPS   CGB BG palette index
0xFF69  BCPD   CGB BG palette data
0xFF6A  OCPS   CGB OBJ palette index
0xFF6B  OCPD   CGB OBJ palette data
0xFF70  SVBK   CGB WRAM bank select (bits 0-2)
0xFFFF  IE     Interrupt enable (same layout as IF)
```

**Unreadable bits in I/O registers return 1.** Key masks:

| Register | Mask | Notes |
|----------|------|-------|
| P1 | `0xCF` | Bits 7-6 always read as 1 |
| SC | `0x7E` | DMG: only bits 7 and 0 used |
| TAC | `0xF8` | Bits 7-3 always read as 1 |
| IF | `0xE0` | Bits 7-5 always read as 1 |
| STAT | `0x80` | Bit 7 always reads as 1 |

---

## Timer Subsystem

The timer is driven by a **16-bit internal counter** that increments every
T-cycle. DIV (`0xFF04`) exposes the upper 8 bits. **Writing ANY value to
DIV resets the ENTIRE 16-bit counter to 0.**

TIMA (`0xFF05`) increments when a selected bit of the internal counter
falls from 1→0 AND TAC enable bit is set:

| TAC & 0x03 | Counter bit | Frequency |
|------------|-------------|-----------|
| 00 | bit 9 | 4096 Hz |
| 01 | bit 3 | 262144 Hz |
| 10 | bit 5 | 65536 Hz |
| 11 | bit 7 | 16384 Hz |

**TIMA overflow behavior** (1-cycle delay):
1. TIMA overflows (0xFF → 0x00)
2. For 1 M-cycle, TIMA reads as `0x00` (not TMA yet)
3. After that cycle: TIMA reloaded from TMA, IF timer bit set
4. Writing TIMA during the delay cycle **cancels** both the reload and IF set
5. Writing TIMA during the reload cycle **is ignored** (TMA value wins)

**Falling-edge glitches** (see gbctr timer schematic diagrams):
- Writing to DIV can cause a spurious TIMA increment
- Disabling timer (TAC enable 1→0) can increment TIMA if selected bit is 1

---

## PPU (Picture Processing Unit)

LCD: 160×144 pixels. Frame rate: ~59.7275 Hz. Frame: 70224 T-cycles.
Scanline: 456 T-cycles (114 M-cycles).

**Mode progression per scanline (lines 0-143):**

| Mode | Name | Duration | CPU access |
|------|------|----------|------------|
| 2 | OAM scan | 80 T-cycles | No OAM |
| 3 | Drawing | 168-291 T-cycles (variable) | No OAM, no VRAM |
| 0 | HBlank | remainder of 456 | Full access |

Lines 144-153: **Mode 1 (VBlank)**, 4560 T-cycles total (10 scanlines).

Mode 3 duration varies: base 172 T-cycles, +~6 per sprite on line, +SCX%8.

### STAT Interrupt Signal

```
signal = (LY==LYC && STAT.LYC_EN) ||
         (mode==0 && STAT.M0_EN)   ||
         (mode==2 && STAT.M2_EN)   ||
         (mode==1 && (STAT.M1_EN || STAT.M2_EN))
```

IF STAT bit is set on **RISING EDGE** of this signal (0→1 transition only).
If two conditions overlap (e.g., HBlank→OAM with both enabled), signal
stays 1 — **no interrupt fires**. This is a common source of test failures.

VBlank interrupt (IF bit 0) fires at the start of line 144, independent
of STAT settings.

### Pixel Rendering

**Background**: 32×32 tile map at `0x9800` or `0x9C00` (LCDC bit 3). Each tile
is 8×8 pixels, 2bpp. Tile data: 2 bytes per row. For pixel x: `color = (byte1_bit << 1) | byte0_bit`. Palette BGP maps 2-bit colors to 4 shades.

**Window**: Alternate background at (WX-7, WY). Own tile map (LCDC bit 6). No wrapping.

**Sprites**: 40 entries in OAM (`0xFE00`), 4 bytes each:

| Byte | Content |
|------|---------|
| 0 | Y position - 16 |
| 1 | X position - 8 |
| 2 | Tile index |
| 3 | Attributes (priority, Y-flip, X-flip, palette, CGB bank/palette) |

Max 10 sprites per scanline. **DMG priority**: lower X first; equal X → lower OAM index. **CGB priority**: lower OAM index always wins.

### OAM DMA

Writing to DMA (`0xFF46`) starts a 160-byte transfer from `XX00-XX9F` to
`FE00-FE9F`. Takes 160 M-cycles. During transfer, CPU should only access
HRAM (`FF80-FFFE`). Source = `written_value << 8`.

---

## Memory Bank Controllers

### MBC1 (up to 2MB ROM, 32KB RAM)

| Address | Register | Bits | Notes |
|---------|----------|------|-------|
| `0x0000-0x1FFF` | RAMG | 4 | Write `0x0A` to enable RAM |
| `0x2000-0x3FFF` | BANK1 | 5 | Lower ROM bank. **Writing 0 becomes 1.** |
| `0x4000-0x5FFF` | BANK2 | 2 | Upper ROM bits OR RAM bank |
| `0x6000-0x7FFF` | MODE | 1 | 0=ROM banking, 1=RAM banking |

ROM bank = `(BANK2 << 5) | BANK1`. Banks 0x00/0x20/0x40/0x60 inaccessible (get +1).
MODE 0: RAM bank always 0. MODE 1: BANK2 selects RAM bank.

### MBC2 (up to 256KB ROM, 512×4 bit internal RAM)

`0x0000-0x3FFF`: If address bit 8 is 0 → RAMG. If bit 8 is 1 → BANK (4-bit).
RAM at `0xA000-0xA1FF`, only lower 4 bits valid per byte.

### MBC3 (up to 2MB ROM, 64KB RAM, optional RTC)

`0x2000-0x3FFF`: BANK (7-bit). Writing 0 becomes 1.
`0x4000-0x5FFF`: RAM bank 0-7 OR RTC register select (0x08-0x0C).

### MBC5 (up to 8MB ROM, 128KB RAM)

| Address | Register | Bits | Notes |
|---------|----------|------|-------|
| `0x0000-0x1FFF` | RAMG | 8 | **Full 8-bit check**: ONLY `0x0A` enables (not just lower nibble) |
| `0x2000-0x2FFF` | ROMB0 | 8 | Lower ROM bank bits. **0 IS valid** (unlike MBC1). |
| `0x3000-0x3FFF` | ROMB1 | 1 | Bit 8 of ROM bank number |
| `0x4000-0x5FFF` | RAMB | 4 | RAM bank 0-15 |

---

## CGB Extensions

CGB mode activated when header byte `0x143` has bit 7 set (`0x80` = compatible, `0xC0` = CGB only).

Key features:
- **VRAM bank 1** (VBK register): BG tile attributes
- **8 BG + 8 OBJ color palettes** (BCPS/BCPD, OCPS/OCPD): 64 bytes each, 5-bit RGB per component
- **WRAM banks 1-7** (SVBK register)
- **HDMA/GDMA** (HDMA1-HDMA5): fast VRAM copy
- **Double speed mode** (KEY1 register + STOP)

**BG tile attributes** (VRAM bank 1, at tile map addresses):

| Bit | Purpose |
|-----|---------|
| 7 | BG-to-OAM priority |
| 6 | Vertical flip |
| 5 | Horizontal flip |
| 3 | VRAM bank for tile data |
| 2-0 | Palette number (0-7) |

**HDMA**: GDMA (bit 7=0) copies all at once, CPU halted. HDMA (bit 7=1) copies 16 bytes per HBlank. Cancel by writing bit 7=0.
