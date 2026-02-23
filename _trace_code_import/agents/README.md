# agents/

Agent backends that run inside the remote sandbox and talk to coding LLMs.

## How It Works

The orchestrator (`runner.py`) never talks to an LLM directly. Instead, it uses the `AgentBackend` protocol defined in `base.py`. Each agent implementation:

1. Gets uploaded into the sandbox as a Python script
2. Starts a local agent process (Codex app-server, OpenCode server)
3. Sends prompts and receives streamed part events over stdio/stderr
4. Returns structured JSON results that `runner.py` parses into `PartRecord`s

The key abstraction is that the orchestrator only sees parts — it doesn't know or care how the agent internally processes them.

## Files

### `base.py` — AgentBackend Protocol

Defines the interface every agent must implement:

- `start()` — Provision the agent inside the sandbox (upload scripts, start server)
- `create_session()` — Initialize a session for a trajectory
- `run_turn()` — Execute one agent turn with a prompt, return an `AgentTurnOutcome`
- `on_turn_complete()` — Post-turn bookkeeping (session sync)
- `on_resume()` — Restore state from a prior trace (for crash recovery)
- `recover_session()` — Create a fresh session after a turn failure
- `stop()` — Tear down the agent process

Also exports `AgentTurnOutcome`, the structured return value from `run_turn()`.

### `codex.py` — Codex Agent

Wraps OpenAI's Codex app-server. The script runs inside the sandbox:

- Starts `codex app-server` over stdio
- Sends JSON-RPC requests (`create_session`, `send_message`)
- Parses streamed `item.*` notifications into `TraceEvent` objects
- Emits `TRACE_EVENT {...}` lines over stderr for the orchestrator to consume
- Returns a `CodexTurnResult` as JSON over stdout

The Codex agent uses MCP for tool access — the MCP server config is baked into a TOML config file written to the sandbox at startup.

### `opencode.py` — OpenCode Agent

Wraps the OpenCode Python SDK. Also runs inside the sandbox:

- Starts the OpenCode server as a background process
- Uses `AsyncOpencode` client to create sessions and send messages
- Streams parts from the session response, emitting `TRACE_EVENT` lines
- Returns JSON over stdout, same shape as Codex

The OpenCode config template (`OPENCODE_CONFIG_TEMPLATE`) is defined here and used by `runner.py` to write the `.opencode.jsonc` config into the sandbox workspace.

## Adding a New Agent

1. Create `agents/my_agent.py` implementing the `AgentBackend` protocol
2. Register it in `runner.py`'s `AGENT_BACKENDS` dict
3. Your agent script runs inside the sandbox — it must emit `TRACE_EVENT` JSON lines on stderr for real-time part streaming
