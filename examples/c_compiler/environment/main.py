"""
C Compiler evaluation environment.

Evaluates a submitted Rust project that compiles C source code to the
container architecture (x86_64 in this environment). The submission must
produce a ./cc binary via build.sh.

Usage:  ./cc input.c [more_input.c ...] [helper.s ...] [linker_flag ...] -o output

Test suites (run in order):
  1. basics
  2. wacct/chapter_1 ... wacct/chapter_20 (or just "wacct" to run all chapters)
  3. c_testsuite/part_* (or just "c_testsuite" to run all parts)
  4. torture/part_* (or just "torture" to run all parts)

Each test suite lives in tests/<name>.py and exposes a run_<name>() coroutine.
See tests/utils.py for the result models and core test runner.
"""

from __future__ import annotations

import envoi
from tests.basics import basics
from tests.c_testsuite import c_testsuite
from tests.torture import torture
from tests.utils import reset_runner_state
from tests.wacct import wacct

__all__ = [
    "basics",
    "c_testsuite",
    "torture",
    "wacct",
    "build_compiler",
]


@envoi.setup
async def build_compiler(submission: envoi.Documents) -> None:
    reset_runner_state()
    build = await envoi.run("chmod +x build.sh && ./build.sh", timeout_seconds=300)
    if build.exit_code != 0:
        diagnostics = await envoi.run(
            "\n".join(
                [
                    "echo '[env] pwd'; pwd",
                    "echo '[env] ls -la'; ls -la",
                    "echo '[env] src files'; find src -maxdepth 2 -type f | sort 2>/dev/null || true",
                    "echo '[env] build.sh'; sed -n '1,200p' build.sh 2>/dev/null || true",
                    "echo '[env] Cargo.toml'; sed -n '1,200p' Cargo.toml 2>/dev/null || true",
                ]
            ),
            timeout_seconds=30,
        )
        raise RuntimeError(
            "\n".join(
                [
                    f"Build failed (exit {build.exit_code}).",
                    "",
                    "[build stdout]",
                    build.stdout or "(empty)",
                    "",
                    "[build stderr]",
                    build.stderr or "(empty)",
                    "",
                    "[workspace diagnostics]",
                    diagnostics.stdout or diagnostics.stderr or "(empty)",
                ]
            )
        )
