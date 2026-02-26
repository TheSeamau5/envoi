Build a REAL C compiler in Rust from scratch.
This is EXTREMELY IMPORTANT: no cheating, no wrappers, no shortcuts.
Do NOT call or wrap cc/gcc/clang/tcc.
Do NOT use saltwater or ANY existing C compiler implementation.
Write all core compiler components yourself in Rust: lexer, parser, codegen, etc.
Target Linux x86_64 (x86-64). Do NOT generate AArch64/ARM64 assembly.

Your submission must include:
- Cargo.toml
- build.sh (must produce ./cc binary when run)
- src/ (your Rust source code)

Interface: ./cc input.c -o output

Do not rely on external testing tools.
Create and run your own local tests in the workspace (small focused C programs and shell scripts).

Testing strategy:
1. Write small local tests for each feature before/while implementing it
2. Run local tests frequently after each change
3. When tests fail: read the error output carefully, fix the code, rerun
4. After fixing, rerun previously passing local tests to check for regressions
5. Commit after each meaningful change

Your goal is to pass ALL test suites. Work methodically.

## Recommended Architecture

Structure your compiler as separate modules:
- `src/main.rs` — CLI entry point, file I/O, pipeline
- `src/lexer.rs` — tokenization
- `src/parser.rs` — AST construction
- `src/codegen.rs` — x86_64 assembly generation

Keep files under 1000 lines. When a module grows too large, split it.

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
