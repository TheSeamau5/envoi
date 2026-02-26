# C Compiler Workspace

## Quick Reference
- Build: `./build.sh` (produces `./cc`)
- Test locally: `./cc test.c -o test && ./test`
- Debug artifacts: write to `./debug_artifacts/` (auto-captured on test failure)
- Progress notes: maintain `PROGRESS.md`

## File Structure
```
./
├── Cargo.toml
├── build.sh
├── src/
│   ├── main.rs          # CLI entry point
│   ├── lexer.rs         # Tokenizer
│   ├── parser.rs        # AST construction
│   └── codegen.rs       # x86_64 assembly generation
├── PROGRESS.md          # Your progress notes (maintain this)
└── debug_artifacts/     # Intermediate representations (auto-cleared per test)
    ├── tokens.txt
    ├── ast.json
    └── asm.s
```

## Common Mistakes
- Don't put everything in main.rs. Split by compiler phase.
- Don't skip local testing. Write small C programs to isolate bugs.
- Don't ignore debug artifacts. They're your fastest path to diagnosing failures.
