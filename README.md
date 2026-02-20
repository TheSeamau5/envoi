# envoi-trace

This repo runs agent trajectories where the agent tries to build a real C compiler and improve it against the envoi test harness.

The practical loop is:
`run agent -> collect trace/artifacts -> analyze -> adjust -> run again`.

## 1) Prerequisites

- Python 3.12+
- `uv` installed
- Modal CLI installed and authenticated (`modal setup`)
- AWS credentials with S3 access (for trace + bundle upload)
- Agent credentials for whichever backend you use:
  - Codex: `~/.codex/auth.json` (default path used by `uv run trace`)
    - fallback: `CODEX_API_KEY` or `OPENAI_API_KEY`
  - OpenCode: `OPENCODE_API_KEY`

## 2) Setup

From repo root:

```bash
uv sync
cp .env.example .env
```

Edit `.env` and fill values:

```env
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=...
AWS_S3_BUCKET=...

OPENCODE_API_KEY=...
```

Notes:
- `OPENCODE_API_KEY` is only required when using `--agent opencode`.
- If you plan to run local analysis commands (`uv run graph_trace`, `uv run replay`), make sure your shell has the same env vars from `.env`.

## 3) Run A Trajectory

Default run (recommended starting point):

```bash
uv run trace
```

Defaults:
- agent: `codex`
- max parts: `1000`

The launcher prints these immediately at startup:
- `TRAJECTORY_ID`
- `TRACE_S3_URI`
- `BUNDLE_S3_URI`

Save the `TRAJECTORY_ID`; you use it for analysis.

Useful options:

```bash
uv run trace --detach
uv run trace --max-parts 200
uv run trace --agent opencode --model opencode/gpt-5-nano
```

## 4) Analyze A Trajectory

Given a trajectory id:

```bash
uv run graph_trace <trajectory_id>
```

Checkout repository state at a specific part:

```bash
uv run graph_trace <trajectory_id> --part <p>
```

## 5) What Gets Stored

For trajectory `<id>`, artifacts are uploaded to:

- `s3://<bucket>/trajectories/<id>/agent_trace.json`
- `s3://<bucket>/trajectories/<id>/repo.bundle`

`agent_trace.json` is part-centric and records per-part timing, content summary, repo checkpoint state, and test call state.

## 6) Where To Change Behavior

- Task definition and prompt: `task.py`
- Main orchestration: `runner.py`
- Agent backends: `agents/codex.py`, `agents/opencode.py`
- Modal sandbox runtime: `sandbox/modal/setup.sh`
- Test MCP server: `sandbox/modal/mcp_server.py`
- Offline replay/analysis engine: `scripts/offline_replay.py`

## 7) Quick Dev Check

```bash
uv run ruff check task.py runner.py agents/opencode.py agents/codex.py scripts/offline_replay.py scripts/trace.py scripts/graph_trace.py
```
