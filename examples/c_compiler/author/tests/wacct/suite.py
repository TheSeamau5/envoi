"""
Writing-a-C-Compiler tests (wacct).

Source: github.com/nlsandler/writing-a-c-compiler-tests
20 chapters of progressively harder C features. Each chapter has:
  - valid/   — programs that should compile and produce correct output
  - invalid_*/ — programs that should be rejected at compile time
"""

import json
import shlex
import time
from pathlib import Path

import envoi
from tests.shared import CaseResult, TestResult, run_case, session_path, to_result

WACCT_DIR = Path("/opt/tests/wacct")
TESTS_DIR = WACCT_DIR / "tests"


def _load_expected() -> dict:
    path = WACCT_DIR / "expected_results.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


async def run_wacct() -> TestResult:
    expected_map = _load_expected()
    results: list[CaseResult] = []
    sp = session_path()

    for ch in range(1, 21):
        # --- Valid tests: compile + run + check output ---
        valid_dir = TESTS_DIR / f"chapter_{ch}" / "valid"
        if valid_dir.is_dir():
            for f in sorted(valid_dir.rglob("*.c")):
                src = f.read_text()
                rel = f.relative_to(TESTS_DIR)
                entry = expected_map.get(str(rel), {})
                expected_exit = entry.get("return_code", 0) if isinstance(entry, dict) else 0
                expected_stdout = entry.get("stdout", "").strip() if isinstance(entry, dict) else ""
                results.append(await run_case({
                    "name": f.stem,
                    "source": src,
                    "expected_stdout": expected_stdout,
                    "expected_exit_code": expected_exit,
                }))

        # --- Invalid tests: should fail to compile ---
        chapter_dir = TESTS_DIR / f"chapter_{ch}"
        if not chapter_dir.is_dir():
            continue
        for invalid_dir in sorted(chapter_dir.glob("invalid_*")):
            for f in sorted(invalid_dir.rglob("*.c")):
                name, src = f.stem, f.read_text()
                c_file = sp / f"test_{name}.c"
                out_file = sp / f"test_{name}"
                c_file.write_text(src)

                # Compile with submitted compiler
                t0 = time.monotonic()
                cc = await envoi.run(
                    f"./cc {shlex.quote(c_file.name)} -o {shlex.quote(out_file.name)}",
                    timeout_seconds=45,
                )
                compile_time_ms = (time.monotonic() - t0) * 1000

                # gcc benchmark (even for invalid programs)
                t0 = time.monotonic()
                await envoi.run(
                    f"gcc {shlex.quote(c_file.name)} -o {shlex.quote(out_file.name)}_gcc",
                    timeout_seconds=45,
                )
                gcc_compile_time_ms = (time.monotonic() - t0) * 1000

                passed = cc.exit_code != 0
                results.append(CaseResult(
                    name=name, phase="compile", passed=passed, c_source=src,
                    expected_stdout="", actual_stdout="",
                    expected_exit_code=1, actual_exit_code=cc.exit_code,
                    compile_time_ms=compile_time_ms,
                    gcc_compile_time_ms=gcc_compile_time_ms,
                    stderr=None if passed else "expected compilation to fail but it succeeded",
                ))

    return to_result(results)
