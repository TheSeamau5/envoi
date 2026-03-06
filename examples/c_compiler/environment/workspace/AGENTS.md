# C Compiler Workspace

## Quick Reference
- Build: `./build.sh` (produces `./cc`)
- Test: `./run_tests.sh` (runs all your accumulated tests against `./cc` and `gcc`)
- Progress notes: maintain `PROGRESS.md`
- Interface: `./cc input.c [more_input.c ...] [helper.s ...] [linker_flag ...] -o output`
- Reference docs: `/workspace/reference/` — READ BEFORE STARTING
  - `c23-n3220.pdf` — public WG14 working-draft PDF for ISO/IEC 9899:2024
  - `x86_64-SysV-psABI.pdf` — authoritative System V AMD64 ABI PDF
  - `sysv-abi-summary.md` — calling convention and stack alignment
  - `x86-64-instructions.md` — instruction reference for codegen
  - `codegen-examples/` — gcc output for simple C programs
  - `wacct-chapter-map.md` — what each chapter covers and your build order
  - `c-language-traps.md` — C23 edge cases that trip up compilers
  - `x86-64-codegen-traps.md` — assembly generation pitfalls

## Workflow

Red-green-refactor. One feature at a time.

1. Write a small `.c` test in `tests/` for the feature you are about to implement
2. Run `./run_tests.sh` — the new test should fail (red)
3. Implement just enough to make it pass (green)
4. Run `./run_tests.sh` again — ALL tests must pass, not just the new one
5. Only then move to the next feature

Never leave a failing test behind. If something breaks, fix it before moving on.
Before writing assembly generation code, read the examples in `reference/codegen-examples/`. Study how gcc compiles simple programs and match those patterns.
Use the PDFs when you need the full standard or full ABI wording; use the
markdown summaries for quick refreshers.

## File Structure
```
./
├── Cargo.toml
├── build.sh               # Must produce ./cc
├── run_tests.sh           # Your test runner (compares ./cc output vs gcc)
├── src/
│   └── main.rs            # Only this is required; add modules as you see fit
├── tests/
│   ├── return_0.c         # Your first test
│   ├── add.c              # Arithmetic
│   ├── variables.c        # Variables
│   └── ...                # Tests accumulate as you add features
├── PROGRESS.md            # Your progress notes (maintain this)
└── reference/             # Read-only ABI, codegen, and chapter reference docs
```

## Rules
- Write the test BEFORE the feature. Watch it fail. Then implement.
- Run ALL tests after every change. Not just the one you're working on.
- One thing at a time. Do not implement two features in parallel.
- Do not refactor while something is broken. Get green first, then refactor.
