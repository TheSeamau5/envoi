"""Task definition for the current environment."""

from __future__ import annotations

TASK_ID = "c-compiler"

TASK_SYSTEM_PROMPT = """Build a REAL C compiler in Rust from scratch.
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

You have access to a run_tests tool. Use it to test your compiler frequently.

Available test paths:
  - basics (runs all basics tests)
  - basics/smoke, basics/variables, basics/control_flow, etc.
  - wacct (all chapters) or wacct/chapter_1 through wacct/chapter_20
  - c_testsuite (all shards) or c_testsuite/part_<n>
  - torture (all shards) or torture/part_<n>

Testing strategy:
1. Run tests frequently after making changes
2. When tests fail: read the error output carefully, fix the code, rerun
3. After fixing, rerun previously passing suites to check for regressions
4. Commit after each meaningful change

Your goal is to pass ALL test suites. Work methodically.
"""

TASK_CONTINUE_PROMPT = "Continue working on the compiler. Run tests and pass ALL suites."

TASK_REQUIRED_TEST_PATHS: tuple[str, ...] = (
    "basics",
    *tuple(f"wacct/chapter_{i}" for i in range(1, 21)),
    *tuple(f"c_testsuite/part_{i}" for i in range(1, 6)),
    *tuple(f"torture/part_{i}" for i in range(1, 11)),
)

TASK_SUITE_PATHS: tuple[str, ...] = ("basics", "wacct", "c_testsuite", "torture")

TASK_HEAVY_TEST_ROOTS: dict[str, str] = {
    "wacct": "/opt/tests/wacct/tests",
    "c_testsuite": "/opt/tests/c-testsuite/tests/single-exec",
    "torture": "/opt/tests/llvm-test-suite/SingleSource/Regression/C/gcc-c-torture/execute",
}

TASK_SANDBOX_SETUP_SH = """#!/bin/bash
set -euo pipefail

echo "=== Ensuring task fixtures under /opt/tests ==="
mkdir -p /opt/tests

if [ ! -d /opt/tests/c-testsuite/tests/single-exec ] || \
   ! ls /opt/tests/c-testsuite/tests/single-exec/*.c >/dev/null 2>&1; then
    echo "[fixtures] syncing c-testsuite..."
    rm -rf /opt/tests/c-testsuite
    git clone --depth 1 https://github.com/c-testsuite/c-testsuite.git /opt/tests/c-testsuite
else
    echo "[fixtures] c-testsuite already present"
fi

if [ ! -d /opt/tests/wacct/tests ] || [ ! -f /opt/tests/wacct/expected_results.json ]; then
    echo "[fixtures] syncing writing-a-c-compiler-tests..."
    rm -rf /opt/tests/wacct
    git clone --depth 1 https://github.com/nlsandler/writing-a-c-compiler-tests.git /opt/tests/wacct
else
    echo "[fixtures] wacct already present"
fi

TORTURE_EXEC_DIR="/opt/tests/llvm-test-suite/SingleSource/Regression/C/gcc-c-torture/execute"
if [ ! -d "$TORTURE_EXEC_DIR" ] || ! ls "$TORTURE_EXEC_DIR"/*.c >/dev/null 2>&1; then
    echo "[fixtures] syncing llvm-test-suite torture execute shard..."
    rm -rf /opt/tests/llvm-test-suite
    mkdir -p /opt/tests/llvm-test-suite
    cd /opt/tests/llvm-test-suite
    git init
    git remote add origin https://github.com/llvm/llvm-test-suite.git
    git config core.sparseCheckout true
    echo "SingleSource/Regression/C/gcc-c-torture/execute/" > .git/info/sparse-checkout
    git pull --depth 1 origin main
else
    echo "[fixtures] torture execute shard already present"
fi
"""


def build_followup_prompt(status_lines: list[str]) -> str:
    if not status_lines:
        return TASK_CONTINUE_PROMPT
    return TASK_CONTINUE_PROMPT + "\n\nCurrent test status:\n" + "\n".join(status_lines)
