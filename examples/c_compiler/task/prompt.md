Build a REAL C compiler in Rust from scratch. One that could eventually compile Doom.

You are not building a toy. You are building a compiler that handles real C: structs, pointers, arrays, the preprocessor, all of it. The test suites you are evaluated against include hundreds of programs from the c-testsuite conformance tests, the "Writing a C Compiler" book tests, and a subset of the GCC torture tests. A compiler that can pass all of them is a compiler that can compile real software.

No cheating, no wrappers, no shortcuts.
Do NOT call or wrap cc/gcc/clang/tcc to compile C source code for you.
Do NOT use saltwater or ANY existing C compiler implementation.
Write all core compiler components yourself in Rust: lexer, parser, codegen, etc.
Target Linux x86_64 (x86-64). Do NOT generate AArch64/ARM64 assembly.
Language baseline: ISO C23. Programs that are valid C23 should compile; programs
that are invalid under C23 should be rejected. In this sandbox, GCC 13 uses the
`-std=c2x` spelling for C23 mode. Some GCC torture fixtures still exercise
GCC-compatible behavior beyond the pure ISO C23 baseline.

Your submission must include:
- Cargo.toml
- build.sh (must produce ./cc binary when run)
- src/ (your Rust source code)

Interface: `./cc input.c [more_input.c ...] [helper.s ...] [linker_flag ...] -o output`

The evaluation harness may pass multiple C translation units for one program.
Treat `./cc` as a compiler-plus-linker driver: compile the inputs, link them,
and produce one runnable executable.

Boundary of responsibility:
- Your job: lexing, parsing, semantic analysis, and x86_64 code generation from C source.
- OK to delegate: calling `as` to assemble your own `.s` files into `.o`, and calling `gcc` or `ld` to link `.o` files into the final executable.
- NOT OK: using gcc/clang/tcc to compile C into assembly or object code for you.

Read `/workspace/reference/wacct-chapter-map.md` before you start. The full
reference bundle lives in `/workspace/reference/`.

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

## CRITICAL: Bootstrap First

Your FIRST priority — before anything else — is to get a minimal compiler that passes at least one test. Do this in your first turn:

1. Create `Cargo.toml`, `build.sh`, and a minimal `src/main.rs`
2. The minimal compiler must: read a `.c` file, parse `int main() { return <number>; }`, and emit x86-64 assembly that returns that number as the exit code
3. Use `as` to assemble and `gcc` to link — that's allowed
4. Run `./build.sh && ./cc tests/return_0.c -o /tmp/test && /tmp/test; echo $?` and verify it works
5. End your turn here. You will receive test results and can continue from there.

Do NOT write a preprocessor, do NOT write a semantic analyzer, do NOT write a type system. Just get `return <number>` working end-to-end first. Everything else comes later, one feature at a time.

If you write more than 500 lines of Rust before passing your first test, you are doing it wrong. Stop and simplify.

## One Thing at a Time

Do NOT try to implement multiple features in parallel. Do NOT do a big refactor while also adding a new feature. Do NOT move to the next thing when the current thing is broken.

The sequence matters. Build in the same order as the wacct chapters:

| Phase | wacct Chapters | Feature | Do not proceed until... |
| --- | --- | --- | --- |
| 1 | Ch 1 | Return statements | `int main() { return 42; }` produces the correct exit code |
| 2 | Ch 2-3 | Unary + binary operators | Arithmetic expressions evaluate correctly |
| 3 | Ch 4 | Logical + relational operators | Comparisons produce correct `0`/`1` values |
| 4 | Ch 5 | Local variables | Variables can be declared, assigned, and read back correctly |
| 5 | Ch 6-7 | `if`/`else`, blocks, scoping | Branching works and block scope is correct |
| 6 | Ch 8 | Loops | `while`, `for`, `do-while`, `break`, and `continue` all work |
| 7 | Ch 9 | Functions | Multi-argument calls and recursion work reliably |
| 8 | Ch 10 | Globals, `static`, `extern` | File-scope state and symbol visibility work |
| 9 | Ch 11-13 | `long`, unsigned, float | Type handling beyond plain `int` works |
| 10 | Ch 14-18 | Pointers, arrays, strings, structs | Memory layout and addressing work correctly |

After each phase, run your full local suite and the basics suite again. If basics regresses, stop and fix the regression before moving on.

When you fix a bug, write a test that reproduces it FIRST, then fix it. That test stays in your suite forever.

## Common Failure Modes (DO NOT DO THESE)

1. Implementing without testing. Example: you write `if`-statement parsing, it "looks right," and you move on. Later you discover it was broken the entire time and everything built on top of it is now suspect. Do not trust code that has not been exercised by a real test.
2. Trying to fix everything at once. Example: five tests fail after a refactor and you try to patch all five in one edit. Then you do not know which change fixed which bug. Fix ONE failing test at a time. Get green. Then move to the next.
3. Moving on from partial implementations. Example: function calls work for 2 arguments but fail for 6, and you tell yourself you will "come back later" after starting pointers. You will not come back later. Stay on the feature until it fully works.
4. Weakening tests to make them pass. Example: a test expects `42`, your compiler prints `43`, and you change the expected output to `43`. This is the worst possible move. The test is right. The compiler is wrong. Fix the compiler.
5. Big refactors while tests are red. Example: "I need to redesign the AST before I can fix this bug." No. Get back to green first, even with ugly code. Refactor only after the failing test is fixed.
6. Declaring something impossible. Example: you hit a codegen bug and decide that x86_64 function calls "cannot work" without a full register allocator. That is false. Read the reference material, study the codegen examples, and think harder.
7. Silent scope reduction. Example: you stop running some suites, keep only the easy ones green, and then report "all tests pass." Never do that. Report scores for ALL suites you evaluate every time, including suites where you currently score `0`.

## Testing Mechanics

Create a `tests/` directory in your workspace. Put each test as a `.c` file. Write a `run_tests.sh` script that compiles each one with `./cc`, runs it, and compares both stdout and exit code against gcc. This is how the real evaluation harness determines pass/fail — your output and exit code must match gcc exactly.

```bash
#!/bin/bash
PASS=0; FAIL=0
for f in tests/*.c; do
    name=$(basename "$f")
    # Compile with gcc (reference)
    if ! gcc -std=c2x -pedantic-errors "$f" -o /tmp/ref 2>/tmp/gcc_stderr; then
        echo "SKIP $name: gcc cannot compile this (your test may be invalid C)"
        cat /tmp/gcc_stderr
        continue
    fi
    ref_out=$(/tmp/ref 2>/dev/null)
    ref_exit=$?
    # Compile with your compiler
    if ! ./cc "$f" -o /tmp/mine 2>/tmp/cc_stderr; then
        FAIL=$((FAIL + 1))
        echo "FAIL $name: compilation failed"
        cat /tmp/cc_stderr
        continue
    fi
    # Run and compare
    mine_out=$(/tmp/mine 2>/dev/null)
    mine_exit=$?
    if [ "$mine_out" = "$ref_out" ] && [ "$mine_exit" = "$ref_exit" ]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        echo "FAIL $name"
        [ "$mine_out" != "$ref_out" ] && echo "  stdout: expected $(echo "$ref_out" | head -c 200), got $(echo "$mine_out" | head -c 200)"
        [ "$mine_exit" != "$ref_exit" ] && echo "  exit: expected $ref_exit, got $mine_exit"
    fi
done
echo "$PASS passed, $FAIL failed out of $((PASS + FAIL))"
```

Run this after every change. No exceptions.

## Reference Material

Read `/workspace/reference/` before you guess:
- `c23-n3220.pdf` — full C23 reference PDF (public WG14 draft of ISO/IEC 9899:2024)
- `x86_64-SysV-psABI.pdf` — System V AMD64 ABI PDF
- `wacct-chapter-map.md` — exact implementation order and chapter coverage
- `sysv-abi-summary.md` — calling convention and stack alignment rules
- `x86-64-instructions.md` — the small instruction set you actually need
- `codegen-examples/` — gcc `-S -O0` patterns for simple programs
- `c-language-traps.md` — C23 edge cases that break parsers and type systems
- `x86-64-codegen-traps.md` — common assembly-generation mistakes

Use the PDFs for authoritative deep reference and the markdown files for faster
task-oriented summaries.

## Recommended Architecture

Only `src/main.rs` is required. Beyond that, organize however you see fit. A common starting point:
- `src/main.rs` — CLI entry point, file I/O, pipeline
- `src/lexer.rs` — tokenization
- `src/parser.rs` — AST construction
- `src/codegen.rs` — x86_64 assembly generation

Add as many modules as you need. When a file gets hard to navigate, split it.

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
