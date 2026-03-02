Build a REAL C compiler in Rust from scratch. One that could eventually compile Doom.

You are not building a toy. You are building a compiler that handles real C: structs, pointers, arrays, the preprocessor, all of it. The test suites you are evaluated against include hundreds of programs from the c-testsuite conformance tests, the "Writing a C Compiler" book tests, and a subset of the GCC torture tests. A compiler that can pass all of them is a compiler that can compile real software.

No cheating, no wrappers, no shortcuts.
Do NOT call or wrap cc/gcc/clang/tcc.
Do NOT use saltwater or ANY existing C compiler implementation.
Write all core compiler components yourself in Rust: lexer, parser, codegen, etc.
Target Linux x86_64 (x86-64). Do NOT generate AArch64/ARM64 assembly.

Your submission must include:
- Cargo.toml
- build.sh (must produce ./cc binary when run)
- src/ (your Rust source code)

Interface: ./cc input.c -o output

Your goal is to pass ALL test suites. Work methodically. Start small, build up.

## How to Work

You are doing extreme programming. Test-driven development. Red-green-refactor.

The discipline is simple:

1. Write a tiny C program that exercises ONE thing your compiler should handle.
2. Compile it with `./cc`. Watch it fail.
3. Fix the compiler until that test passes.
4. Run ALL your previous tests. Make sure nothing broke.
5. Only then move on to the next feature.

Never move on from a broken test. If something fails, stay on it until it passes. Do not start implementing the next feature while a previous test is red. The whole point is that your test suite is always green except for the one thing you are actively working on.

Your tests accumulate. By the end, you should have a large collection of small C programs that cover every feature you have implemented. This is your regression suite. Run it constantly.

## One Thing at a Time

Do NOT try to implement multiple features in parallel. Do NOT do a big refactor while also adding a new feature. Do NOT move to the next thing when the current thing is broken.

The sequence matters:
- Get the simplest possible program working first: `int main() { return 0; }`
- Then arithmetic: `return 1 + 2;`
- Then variables, then control flow, then functions, then pointers, then strings, etc.
- Each feature builds on the previous ones. If you skip ahead, you will waste time debugging interactions between features that are all broken at once.

When you fix a bug, write a test that reproduces it FIRST, then fix it. That test stays in your suite forever.

## Testing Mechanics

Create a `tests/` directory in your workspace. Put each test as a `.c` file. Write a `run_tests.sh` script that compiles each one with `./cc`, runs it, and compares the output against gcc. Something like:

```bash
#!/bin/bash
PASS=0; FAIL=0
for f in tests/*.c; do
    # Compile with your compiler
    ./cc "$f" -o /tmp/mine 2>/dev/null
    mine_status=$?
    # Compile with gcc for reference
    gcc "$f" -o /tmp/ref 2>/dev/null
    # Compare outputs
    mine_out=$(/tmp/mine 2>/dev/null; echo "EXIT:$?")
    ref_out=$(/tmp/ref 2>/dev/null; echo "EXIT:$?")
    if [ "$mine_out" = "$ref_out" ] && [ $mine_status -eq 0 ]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        echo "FAIL: $f"
        echo "  expected: $ref_out"
        echo "  got:      $mine_out"
    fi
done
echo "$PASS passed, $FAIL failed"
```

Run this after every change. No exceptions.

## Recommended Architecture

Only `src/main.rs` is required. Beyond that, organize however you see fit. A common starting point:
- `src/main.rs` — CLI entry point, file I/O, pipeline
- `src/lexer.rs` — tokenization
- `src/parser.rs` — AST construction
- `src/codegen.rs` — x86_64 assembly generation

Add as many modules as you need. When a file gets hard to navigate, split it.

## Debug Artifacts (REQUIRED)

Your compiler MUST write intermediate representations to `./debug_artifacts/`
during every compilation. This directory is cleared before each test case and
captured automatically on failure.

Required artifacts:
- `tokens.txt` — lexer output (one token per line: `<type> <value> <line>:<col>`)
- `ast.json` — parsed AST as JSON
- `asm.s` — generated x86_64 assembly

Without these, you must guess where bugs are, which wastes turns.

## Progress Tracking (REQUIRED)

Maintain a `PROGRESS.md` file in your workspace root. Update it after every
significant change. Structure:

```
# Progress

## Current Status
- Tests passing: X/Y (run_tests.sh)
- Current focus: <what you're working on>

## What Works
- <list of working features with test coverage>

## What's Broken
- <current failing test and what you're doing about it>

## What I've Tried That Failed
- [Turn ~N] <description of failed approach>
```

This helps you remember what you've tried across many turns.
