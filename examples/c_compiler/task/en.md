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
