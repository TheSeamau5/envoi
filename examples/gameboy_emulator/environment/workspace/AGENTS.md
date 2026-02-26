# Game Boy Emulator Workspace

## Quick Reference
- Build: `./build.sh` (produces `./gb_emu`)
- Test locally: `./gb_emu test.gb --headless --max-cycles 1000000 --serial-log /dev/stdout`
- Debug artifacts: write to `./debug_artifacts/` (auto-captured on test failure)
- Progress notes: maintain `PROGRESS.md`
- Reference docs: `/workspace/reference/` (pandocs, gbctr.pdf, dmgops.json, opcodes.toml)
- Reference emulator: `/workspace/reference/sameboy/` (SameBoy, C — read-only)

## File Structure
```
./
├── Cargo.toml
├── build.sh
├── src/
│   ├── main.rs          # CLI entry point, argument parsing
│   ├── cpu.rs           # SM83 CPU, instruction decode/execute
│   ├── ppu.rs           # PPU rendering, scanline modes
│   ├── memory.rs        # Memory map, I/O registers
│   ├── timer.rs         # Timer subsystem (DIV, TIMA)
│   ├── mbc.rs           # Memory bank controllers
│   └── cartridge.rs     # ROM loading, header parsing
├── PROGRESS.md          # Your progress notes (maintain this)
└── debug_artifacts/     # Debug output (auto-cleared per test)
    ├── cpu_trace.log
    └── ppu_state.txt
```

## Common Mistakes
- Don't put everything in main.rs. Split by hardware subsystem.
- Don't guess at hardware behavior. Read the reference docs and SameBoy source.
- Don't ignore debug artifacts. They're your fastest path to diagnosing failures.
- Don't FFI-bind to SameBoy. Read it for understanding, write your own Rust code.
