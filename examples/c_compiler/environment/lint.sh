#!/bin/bash
# Structural lint for agent-generated C compiler.
# Errors here block the build. Messages are written as remediation instructions.
set -e

ERRORS=()

# Check file sizes (no single file > 1500 lines)
while IFS= read -r file; do
    lines=$(wc -l < "$file")
    if [ "$lines" -gt 1500 ]; then
        ERRORS+=("LINT: $file has $lines lines (max 1500). Split into smaller modules. For example, extract parser logic into src/parser.rs and codegen into src/codegen.rs.")
    fi
done < <(find src/ -name "*.rs" 2>/dev/null)

# Check minimum module structure when code is large enough
if [ -d src/ ]; then
    rs_count=$(find src/ -name "*.rs" | wc -l)
    total_lines=$(find src/ -name "*.rs" -exec cat {} + 2>/dev/null | wc -l)
    if [ "$total_lines" -gt 500 ] && [ "$rs_count" -lt 3 ]; then
        ERRORS+=("LINT: You have $total_lines lines of Rust across only $rs_count file(s). At this size, split into separate modules: src/lexer.rs, src/parser.rs, src/codegen.rs, src/main.rs.")
    fi
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
