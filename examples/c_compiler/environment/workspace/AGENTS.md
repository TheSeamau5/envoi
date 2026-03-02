# C Compiler Workspace

## Quick Reference
- Build: `./build.sh` (produces `./cc`)
- Test: `./run_tests.sh` (runs all your accumulated tests against `./cc` and `gcc`)
- Debug artifacts: write to `./debug_artifacts/` (auto-captured on test failure)
- Progress notes: maintain `PROGRESS.md`

## Workflow

Red-green-refactor. One feature at a time.

1. Write a small `.c` test in `tests/` for the feature you are about to implement
2. Run `./run_tests.sh` — the new test should fail (red)
3. Implement just enough to make it pass (green)
4. Run `./run_tests.sh` again — ALL tests must pass, not just the new one
5. Only then move to the next feature

Never leave a failing test behind. If something breaks, fix it before moving on.

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
└── debug_artifacts/       # Intermediate representations (auto-cleared per test)
    ├── tokens.txt
    ├── ast.json
    └── asm.s
```

## Rules
- Write the test BEFORE the feature. Watch it fail. Then implement.
- Run ALL tests after every change. Not just the one you're working on.
- One thing at a time. Do not implement two features in parallel.
- Do not refactor while something is broken. Get green first, then refactor.
- Debug artifacts are your fastest path to diagnosing failures. Don't skip them.
