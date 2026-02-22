# sandbox/

Remote sandbox backends where agents run in isolation.

## How It Works

The orchestrator needs a remote Linux environment to run agents in. The `Sandbox` protocol in `base.py` abstracts this — you can swap Modal for E2B (or anything else) without changing the orchestrator.

A sandbox provides four operations:
- **`run(cmd)`** — Execute a shell command (with timeout, streaming, env vars)
- **`write_file(path, content)`** — Upload a text file
- **`read_file(path)` / `read_file_bytes(path)`** — Download a file
- **`terminate()`** — Tear down the sandbox

The orchestrator uses these to upload environment files, start agents, run git operations, and collect artifacts.

## Files

### `base.py` — Sandbox Protocol + CommandResult

The protocol interface. Also defines `CommandResult`, a frozen dataclass with `exit_code`, `stdout`, `stderr`, and `duration_ms`. Use `.unpack()` for tuple destructuring:

```python
exit_code, stdout, stderr = (await sb.run("ls /workspace")).unpack()
```

### `modal/backend.py` — Modal Implementation

Uses Modal's `Sandbox` API. The sandbox runs on Modal's cloud infrastructure with:
- A pre-built Docker image (Ubuntu 24.04, Python 3.12, Rust, Node, build tools)
- `setup.sh` for environment boot (starts envoi server on `:8000`)
- `mcp_server.py` for exposing test tools to agents via MCP

Modal sandboxes are ephemeral — they're created per trajectory run and torn down after.

### `modal/setup.sh` — Sandbox Boot Script

Runs at sandbox start. Installs the envoi package, starts the envoi server (`main.py`) on port 8000, and verifies it's healthy before returning.

### `mcp_server.py` — MCP Test Server

An MCP server that exposes a `run_tests(test_path)` tool. When an agent calls this tool, it hits the envoi server at `localhost:8000/run/{test_path}` and returns structured test results.

### `e2b/backend.py` — E2B Implementation

Alternative sandbox using E2B's Code Interpreter. Same `Sandbox` interface, different cloud provider. Use with `--sandbox e2b`.

## Adding a New Sandbox Provider

1. Create `sandbox/my_provider/backend.py` implementing `Sandbox`
2. Add a lazy import in `create_sandbox()` in `sandbox/__init__.py`
3. Add it as a choice in `scripts/trace.py`'s `--sandbox` argument
