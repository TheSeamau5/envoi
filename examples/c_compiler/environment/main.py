"""
C Compiler evaluation environment.

Evaluates a submitted Rust project that compiles C source code to the
container architecture (x86_64 in this environment). The submission must
produce a ./cc binary via build.sh.

Usage:  ./cc input.c -o output

Test suites (run in order):
  1. basics
  2. wacct/chapter_1 ... wacct/chapter_20 (or just "wacct" to run all chapters)
  3. c_testsuite/part_* (or just "c_testsuite" to run all parts)
  4. torture/part_* (or just "torture" to run all parts)

Each test suite lives in tests/<name>.py and exposes a run_<name>() coroutine.
See tests/utils.py for the result models and core test runner.

Debug artifact contract (REQUIRED — see task prompt):
  - The submitted compiler MUST write debugging output to ./debug_artifacts/.
  - This directory is cleared before each test case.
  - Any files written there are captured and returned in structured failure data.
  - Required: tokens.txt, ast.json, asm.s (see prompt for format).
"""

from __future__ import annotations

import envoi
from tests.basics import basics
from tests.c_testsuite import c_testsuite
from tests.torture import torture
from tests.wacct import wacct

__all__ = ["basics", "c_testsuite", "torture", "wacct", "build_compiler"]


@envoi.setup
async def build_compiler(submission: envoi.Documents) -> None:
    lint = await envoi.run("chmod +x lint.sh && ./lint.sh", timeout_seconds=30)
    if lint.exit_code != 0:
        raise RuntimeError(
            f"Structural lint failed:\n{lint.stdout}\n{lint.stderr}"
        )
    build = await envoi.run("chmod +x build.sh && ./build.sh", timeout_seconds=300)
    if build.exit_code != 0:
        raise RuntimeError(f"Build failed (exit {build.exit_code}).\n{build.stderr}")
