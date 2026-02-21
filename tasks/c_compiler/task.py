"""C compiler task definition."""

from __future__ import annotations

from pathlib import Path

ENVIRONMENT = "c_compiler"

CONTINUE_PROMPT = "Continue working on the compiler. Run tests and pass ALL suites."

SUITE_PATHS: tuple[str, ...] = ("basics", "wacct", "c_testsuite", "torture")

REQUIRED_TEST_PATHS: tuple[str, ...] = (
    "basics",
    *tuple(f"wacct/chapter_{i}" for i in range(1, 21)),
    *tuple(f"c_testsuite/part_{i}" for i in range(1, 6)),
    *tuple(f"torture/part_{i}" for i in range(1, 11)),
)

HEAVY_TEST_ROOTS: dict[str, str] = {
    "wacct": "/opt/tests/wacct/tests",
    "c_testsuite": "/opt/tests/c-testsuite/tests/single-exec",
    "torture": (
        "/opt/tests/llvm-test-suite/SingleSource/Regression/C/"
        "gcc-c-torture/execute"
    ),
}

SETUP_SH = """\
#!/bin/bash
set -euo pipefail

echo "=== Ensuring task fixtures under /opt/tests ==="
mkdir -p /opt/tests

if [ ! -d /opt/tests/c-testsuite/tests/single-exec ] || \
   ! ls /opt/tests/c-testsuite/tests/single-exec/*.c >/dev/null 2>&1; then
    echo "[fixtures] syncing c-testsuite..."
    rm -rf /opt/tests/c-testsuite
    git clone --depth 1 https://github.com/c-testsuite/c-testsuite.git /opt/tests/c-testsuite
    echo "[fixtures] done: c-testsuite synced"
else
    echo "[fixtures] c-testsuite already present"
fi

if [ ! -d /opt/tests/wacct/tests ] || [ ! -f /opt/tests/wacct/expected_results.json ]; then
    echo "[fixtures] syncing writing-a-c-compiler-tests..."
    rm -rf /opt/tests/wacct
    git clone --depth 1 https://github.com/nlsandler/writing-a-c-compiler-tests.git /opt/tests/wacct
    echo "[fixtures] done: writing-a-c-compiler-tests synced"
else
    echo "[fixtures] wacct already present"
fi

TORTURE_EXEC_DIR="/opt/tests/llvm-test-suite/SingleSource/Regression/C/gcc-c-torture/execute"
if [ ! -d "$TORTURE_EXEC_DIR" ] || ! ls "$TORTURE_EXEC_DIR"/*.c >/dev/null 2>&1; then
    echo "[fixtures] syncing llvm-test-suite torture execute shard..."
    echo "[fixtures] note: this sync is large and can take several minutes (often 3-10 min)"
    rm -rf /opt/tests/llvm-test-suite
    mkdir -p /opt/tests/llvm-test-suite
    cd /opt/tests/llvm-test-suite
    git init
    git remote add origin https://github.com/llvm/llvm-test-suite.git
    git config core.sparseCheckout true
    echo "SingleSource/Regression/C/gcc-c-torture/execute/" > .git/info/sparse-checkout
    git pull --depth 1 origin main
    echo "[fixtures] done: llvm-test-suite torture execute shard synced"
else
    echo "[fixtures] torture execute shard already present"
fi

echo "[fixtures] all task fixtures ready"
"""


def load_prompt(*, lang: str = "en") -> str:
    """Load the system prompt for the given language."""
    prompt_file = Path(__file__).parent / f"{lang}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(
            f"No prompt file for lang={lang!r} at {prompt_file}"
        )
    return prompt_file.read_text().strip()


def build_followup_prompt(status_lines: list[str]) -> str:
    if not status_lines:
        return CONTINUE_PROMPT
    return (
        CONTINUE_PROMPT
        + "\n\nCurrent test status:\n"
        + "\n".join(status_lines)
    )
