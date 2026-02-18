#!/usr/bin/env python3
"""
Generate a blacklist of GCC torture execute tests that use GNU extensions
or otherwise can't compile with a standard C compiler.

Run this inside the Docker container where clang is available:
    python3 /environment/scripts/generate_torture_blacklist.py > /environment/torture-blacklist.txt

Two-pass filtering:
  1. Grep for known GNU-isms (__attribute__, __builtin_, asm, typeof, etc.)
  2. Try compiling remaining files with clang -std=c11 (reject only hard errors)
"""

import re
import subprocess
import sys
from pathlib import Path

TORTURE_DIR = Path("/opt/tests/llvm-test-suite/SingleSource/Regression/C/gcc-c-torture/execute")

def main():
    if not TORTURE_DIR.is_dir():
        print(f"ERROR: {TORTURE_DIR} not found", file=sys.stderr)
        sys.exit(1)

    gnu_patterns = re.compile(
        r"__attribute__"
        r"|__builtin_"
        r"|__extension__"
        r"|__asm__"
        r"|__typeof__"
        r"|__label__"
        r"|__int128"
        r"|__complex__"
        r"|__real__"
        r"|__imag__"
        r"|__auto_type"
        r"|\btypeof\b"
        r"|\basm\b\s*\("
        r"|\basm\b\s*volatile"
        r"|__attribute\s*\("
        r"|__VA_OPT__"
        r"|_Complex"
        r"|_Decimal"
        r"|__float128"
        r"|__SIZEOF_"
        r"|#include\s*<stdatomic\.h>"
    )

    c_files = sorted(TORTURE_DIR.glob("*.c"))
    print(f"Found {len(c_files)} .c files in {TORTURE_DIR}", file=sys.stderr)

    blacklisted = set()

    # Pass 1: grep for GNU extensions
    for f in c_files:
        try:
            src = f.read_text(errors="replace")
        except Exception:
            blacklisted.add(f.name)
            continue
        if gnu_patterns.search(src):
            blacklisted.add(f.name)

    print(f"Pass 1 (grep): {len(blacklisted)} files blacklisted", file=sys.stderr)

    # Pass 2: try compiling remaining files with clang -std=c11 (errors only, no -Werror)
    remaining = [f for f in c_files if f.name not in blacklisted]
    pass2_count = 0
    for f in remaining:
        result = subprocess.run(
            ["clang", "-std=c11", "-fsyntax-only", str(f)],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            blacklisted.add(f.name)
            pass2_count += 1

    print(f"Pass 2 (clang): {pass2_count} additional files blacklisted", file=sys.stderr)
    print(f"Total blacklisted: {len(blacklisted)} / {len(c_files)}", file=sys.stderr)

    # Output sorted blacklist to stdout
    for name in sorted(blacklisted):
        print(name)


if __name__ == "__main__":
    main()
