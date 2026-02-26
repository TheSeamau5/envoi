#!/bin/bash
# Structural lint for agent-generated Game Boy emulator.
# Errors here block the build. Messages are written as remediation instructions.
set -e

ERRORS=()

# Check file sizes (no single file > 1500 lines)
while IFS= read -r file; do
    lines=$(wc -l < "$file")
    if [ "$lines" -gt 1500 ]; then
        ERRORS+=("LINT: $file has $lines lines (max 1500). Split into smaller modules. For example, extract CPU logic into src/cpu.rs and PPU into src/ppu.rs.")
    fi
done < <(find src/ -name "*.rs" 2>/dev/null)

# Check minimum module structure when code is large enough
if [ -d src/ ]; then
    rs_count=$(find src/ -name "*.rs" | wc -l)
    total_lines=$(find src/ -name "*.rs" -exec cat {} + 2>/dev/null | wc -l)
    if [ "$total_lines" -gt 500 ] && [ "$rs_count" -lt 3 ]; then
        ERRORS+=("LINT: You have $total_lines lines of Rust across only $rs_count file(s). At this size, split into separate modules: src/cpu.rs, src/ppu.rs, src/memory.rs, src/main.rs.")
    fi
fi

# Check for FFI/extern bindings (agent must not call reference C code)
if grep -rq 'extern "C"' src/ 2>/dev/null || grep -rq 'cc::Build' src/ Cargo.toml 2>/dev/null; then
    ERRORS+=("LINT: Found extern C or cc crate usage. You must not FFI-bind to reference code. Write your own Rust implementation.")
fi

if [ ${#ERRORS[@]} -gt 0 ]; then
    echo "=== STRUCTURAL LINT FAILURES ==="
    for err in "${ERRORS[@]}"; do
        echo "$err"
    done
    echo "=== Fix these issues before continuing ==="
    exit 1
fi

echo "Structural lint passed."
