#!/bin/bash
set -e

echo "=== Starting envoi runtime ==="
cd /environment
python3 -m envoi.runtime --file main.py --port 8000 &
ENVOI_PID=$!

echo "=== Waiting for envoi on port 8000 ==="
for i in {1..60}; do
    if curl -sf http://localhost:8000/schema > /dev/null 2>&1; then
        echo "envoi ready on port 8000"
        break
    fi
    sleep 1
done

echo "=== Installing Node.js ==="
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

echo "=== Installing OpenCode ==="
npm install -g @opencode-ai/cli

echo "=== Initializing workspace ==="
mkdir -p /workspace
cd /workspace
git init
git config user.email "agent@example.com"
git config user.name "Agent"
git commit --allow-empty -m "Initial empty commit"

echo "=== Copying MCP server and config ==="
mkdir -p /sandbox
cp /tmp/upload/mcp_server.py /sandbox/
cp /tmp/upload/opencode.jsonc /sandbox/

echo "=== Reading OpenCode API key ==="
OPENCODE_API_KEY=$(cat /tmp/upload/opencode_api_key.txt)

echo "=== Starting OpenCode on port 4096 ==="
cd /workspace
OPENCODE_API_KEY="$OPENCODE_API_KEY" opencode serve --port 4096 &
OPENCODE_PID=$!

echo "=== Waiting for OpenCode on port 4096 ==="
for i in {1..60}; do
    if curl -sf http://localhost:4096/global/health > /dev/null 2>&1; then
        echo "OpenCode ready on port 4096"
        break
    fi
    sleep 1
done

echo "=== Setup complete ==="
echo "envoi on port 8000, OpenCode on port 4096"
