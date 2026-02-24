"""Shared setup helpers for agent provisioning.

Both CodexAgent and OpenCodeAgent call these in their setup() methods.
run_workspace_init() starts the envoi runtime and initializes the git repo.
"""

from __future__ import annotations

import builtins

from envoi_code.sandbox.base import Sandbox

WORKSPACE_INIT_SCRIPT = """\
set -euo pipefail
export PATH="$HOME/.cargo/bin:$PATH"

# Ensure envoi SDK is installed in the sandbox
if ! python3 -c "import envoi" 2>/dev/null; then
    echo "[setup] installing envoi SDK"
    pip3 install --break-system-packages \
        "envoi @ git+https://github.com/TheSeamau5/envoi.git@main#subdirectory=packages/envoi" \
        2>&1 | tail -1
fi

echo "[setup] starting envoi runtime on :8000"
cd /environment
python3 -m envoi.runtime --file main.py --port 8000 > /tmp/envoi.log 2>&1 &
ENVOI_PID=$!
echo "$ENVOI_PID" > /tmp/envoi.pid
echo "[setup] envoi process started (pid=${ENVOI_PID})"

# Wait for envoi
for i in $(seq 1 120); do
    if curl -sf http://localhost:8000/schema >/dev/null 2>&1; then
        echo "[setup] envoi ready"
        break
    fi
    if [ $((i % 5)) -eq 0 ]; then
        echo "[setup] still waiting for envoi (${i}s)"
    fi
    sleep 1
done
curl -sf http://localhost:8000/schema >/dev/null 2>&1 || {
    echo "[setup] ERROR: timeout waiting for envoi"
    exit 1
}

echo "[setup] initializing workspace git repo"
mkdir -p /workspace
cd /workspace
git init >/dev/null
git config user.email "agent@example.com"
git config user.name "Agent"
git commit --allow-empty -m "Initial empty commit" >/dev/null
echo "[setup] workspace git repo ready"
"""


async def run_workspace_init(sandbox: Sandbox) -> None:
    """Start the envoi runtime and initialize the workspace git repo."""
    await sandbox.write_file(
        "/tmp/workspace_init.sh",
        WORKSPACE_INIT_SCRIPT,
        ensure_dir=False,
    )

    async def handle_line(line: str) -> None:
        stripped = line.strip()
        if stripped and stripped.startswith("[setup]"):
            builtins.print(stripped, flush=True)

    result = await sandbox.run(
        "bash /tmp/workspace_init.sh",
        timeout=300,
        on_stdout_line=handle_line,
        on_stderr_line=handle_line,
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"Workspace init failed (exit {result.exit_code})"
        )
