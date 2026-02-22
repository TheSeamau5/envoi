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
