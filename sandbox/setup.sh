#!/bin/bash
set -e

# Rust cargo is installed in the image but not on PATH by default
export PATH="$HOME/.cargo/bin:$PATH"

echo "=== Starting envoi runtime ==="
cd /environment
python3 -m envoi.runtime --file main.py --port 8000 2>&1 | tee /tmp/envoi.log &
ENVOI_PID=$!
echo "$ENVOI_PID" > /tmp/envoi.pid

echo "=== Waiting for envoi on port 8000 ==="
for i in {1..60}; do
    if curl -sf http://localhost:8000/schema > /dev/null 2>&1; then
        echo "envoi ready on port 8000"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "ERROR: envoi failed to start"
        exit 1
    fi
    sleep 1
done

echo "=== Installing Node.js ==="
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y -qq nodejs

echo "=== Installing OpenCode ==="
curl -fsSL https://opencode.ai/install | bash
OPENCODE_BIN="$HOME/.opencode/bin/opencode"
echo "Looking for opencode at: $OPENCODE_BIN"
ls -la "$HOME/.opencode/bin/" || true
if [ ! -f "$OPENCODE_BIN" ]; then
    echo "Binary not at expected location, searching..."
    find / -name opencode -type f 2>/dev/null || true
    exit 1
fi

echo "=== Initializing workspace ==="
mkdir -p /workspace
cd /workspace
git init
git config user.email "agent@example.com"
git config user.name "Agent"
git commit --allow-empty -m "Initial empty commit"

echo "=== Reading OpenCode API key ==="
OPENCODE_API_KEY=$(cat /tmp/upload/opencode_api_key.txt)

echo "=== Starting OpenCode on port 4096 ==="
cd /workspace
OPENCODE_API_KEY="$OPENCODE_API_KEY" \
OPENCODE_CONFIG="/workspace/opencode.jsonc" \
    "$OPENCODE_BIN" serve --port 4096 --hostname 0.0.0.0 2>&1 | tee /tmp/opencode.log &
OPENCODE_PID=$!
echo "$OPENCODE_PID" > /tmp/opencode.pid

echo "=== Waiting for OpenCode on port 4096 ==="
for i in {1..60}; do
    if curl -sf http://localhost:4096/global/health > /dev/null 2>&1; then
        echo "OpenCode ready on port 4096"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "ERROR: OpenCode failed to start"
        exit 1
    fi
    sleep 1
done

echo "=== Setup complete ==="
echo "envoi PID=$ENVOI_PID on port 8000"
echo "OpenCode PID=$OPENCODE_PID on port 4096"
