#!/bin/bash
set -e

# Rust cargo is installed in the image but not on PATH by default
export PATH="$HOME/.cargo/bin:$PATH"
AGENT_KIND="${AGENT_KIND:-opencode}"

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

echo "=== Initializing workspace ==="
mkdir -p /workspace
cd /workspace
git init
git config user.email "agent@example.com"
git config user.name "Agent"
git commit --allow-empty -m "Initial empty commit"

echo "=== Installing common CLI tools ==="
apt-get update -qq
apt-get install -y -qq ripgrep

if [ "$AGENT_KIND" = "opencode" ]; then
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
    exit 0
fi

if [ "$AGENT_KIND" = "codex" ]; then
    echo "=== Installing Codex CLI binary ==="
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64)
            TARGET_TRIPLE="x86_64-unknown-linux-musl"
            ;;
        aarch64|arm64)
            TARGET_TRIPLE="aarch64-unknown-linux-musl"
            ;;
        *)
            echo "ERROR: unsupported architecture for Codex binary: $ARCH"
            exit 1
            ;;
    esac

    CODEX_TARBALL_URL="https://github.com/openai/codex/releases/latest/download/codex-${TARGET_TRIPLE}.tar.gz"
    tmpdir="$(mktemp -d)"
    curl -fsSL "$CODEX_TARBALL_URL" -o "$tmpdir/codex.tar.gz"
    tar -xzf "$tmpdir/codex.tar.gz" -C "$tmpdir"
    CODEX_EXTRACTED_BIN="$tmpdir/codex-${TARGET_TRIPLE}"
    if [ ! -f "$CODEX_EXTRACTED_BIN" ]; then
        echo "ERROR: expected Codex binary not found at $CODEX_EXTRACTED_BIN"
        ls -la "$tmpdir"
        exit 1
    fi
    install -m 0755 "$CODEX_EXTRACTED_BIN" /usr/local/bin/codex
    codex --version
    mkdir -p /workspace/.codex

    echo "=== Setup complete ==="
    echo "envoi PID=$ENVOI_PID on port 8000"
    echo "Codex binary installed at /usr/local/bin/codex"
    exit 0
fi

echo "ERROR: unsupported AGENT_KIND=$AGENT_KIND"
exit 1
