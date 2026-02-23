# envoi-trace

Run a coding agent inside a remote sandbox, record every action as a trace, and evaluate the results.

## Quick Start

```bash
envoi-trace \
  --agent codex \
  --max-parts 1000 \
  --task examples/tasks/c_compiler \
  --env examples/environments/c_compiler
```

This does three things:

1. Spins up a Modal sandbox with your environment (test fixtures, compilers, etc.)
2. Runs the agent (Codex or OpenCode) against your task prompt
3. Saves a `trace.parquet` to S3 after every part, plus a `repo.bundle` at the end

The launcher prints a `TRAJECTORY_ID` at startup. Save it — you use it for everything else.

## What a Simple Environment Looks Like

An environment is an [envoi](https://github.com/TheSeamau5/envoi.git) test harness. Here is the simplest possible one:

```
my_task/
  prompt.md          # what to tell the agent
my_env/
  main.py            # envoi test harness
  setup.sh           # (optional) install fixtures
```

**prompt.md** — the system prompt the agent receives:

```markdown
Write a Python function that sorts a list using merge sort.
You have access to a run_tests tool. Use it after each change.
```

**main.py** — the envoi environment entrypoint:

```python
import envoi

sort_tests = envoi.suite("sort")

@sort_tests.test()
async def run_sort_tests():
    result = await envoi.run("python3 -m pytest /opt/tests/", timeout_seconds=60)
    return {"passed": ..., "failed": ..., "total": ...}

@envoi.setup
async def setup(submission: envoi.Documents):
    await envoi.run("pip install pytest", timeout_seconds=60)
```

Then run it:

```bash
envoi-trace --task my_task --env my_env
```

The `--task` directory must contain a prompt file (`en.md`, `prompt.md`, or a `task.py`).
The `--env` directory must contain a `main.py` that uses the envoi SDK.

## Prerequisites

- Python 3.12+
- `uv` installed
- Modal CLI installed and authenticated (`modal setup`)
- AWS credentials with S3 access (for trace + bundle upload)
- Agent credentials:
  - **Codex**: `~/.codex/auth.json` (or `CODEX_API_KEY` / `OPENAI_API_KEY`)
  - **OpenCode**: `OPENCODE_API_KEY`

## Setup

```bash
uv sync
cp .env.example .env
```

Edit `.env`:

```env
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=...
AWS_S3_BUCKET=...

# Only needed for --agent opencode
OPENCODE_API_KEY=...
```

## CLI Reference

### Run a Trajectory

```bash
# Minimal (Codex, 1000 parts, Modal, non-preemptible)
envoi-trace --task examples/tasks/c_compiler --env examples/environments/c_compiler

# OpenCode with a specific model
envoi-trace --agent opencode --model opencode/gpt-5-nano --task <path> --env <path>

# Preemptible execution (cheaper, may be interrupted)
envoi-trace --preemptible --task <path> --env <path>

# Detach from Modal (run in background)
envoi-trace --detach --task <path> --env <path>

# E2B sandbox instead of Modal
envoi-trace --sandbox e2b --task <path> --env <path>
```

The launcher automatically resumes on retryable failures (agent errors, timeouts). Disable with `--no-auto-resume`.

### Analyze a Trajectory

```bash
# Full analysis with suite-level graphs
envoi-trace graph <trajectory_id>

# Checkout repo state at a specific part
envoi-trace graph <trajectory_id> --part 42 --checkout-dest ./part_42_repo
```

## What Gets Stored

For trajectory `<id>`, two artifacts go to S3:

| Artifact | Path | Description |
|---|---|---|
| `trace.parquet` | `trajectories/<id>/trace.parquet` | One row per part. Test calls, git state, timing, content summaries. |
| `repo.bundle` | `trajectories/<id>/repo.bundle` | Full git history. Reconstruct the repo at any part. |

`trace.parquet` is written after every part, so you always have a partial trace even if the run dies. `repo.bundle` is uploaded at end-of-run.

## How the Trace Works

Every observable action the agent takes is a **part**. Parts are the fundamental unit of measurement — not turns, not steps.

A part is one of: `reasoning`, `text`, `tool`, `tool_use`, `tool_result`, or `patch`.

Each row in `trace.parquet` records:
- **Identity**: `part` index, `timestamp`, `duration_ms`
- **Semantics**: `role`, `part_type`, `item_type`, `summary`
- **Repository state**: `git_commit`, `repo_checkpoint` (before/after commits, changed files)
- **Test state**: `envoi_calls` (test invocations), `testing_state` (solve progress)
- **Session end** (null while in-progress): `session_end_reason`, `session_end_total_parts`

Trajectory-level fields are denormalized into every row, so any single row tells you the full trajectory context.

## Project Layout

```
runner.py              Main orchestrator. Runs inside Modal.
models.py              Pydantic models (PartRecord, TurnRecord, AgentTrace, etc.)
agents/                Agent backends (Codex, OpenCode). See agents/README.md.
sandbox/               Sandbox backends (Modal, E2B). See sandbox/README.md.
scripts/               CLI entrypoints. See scripts/README.md.
examples/              Task prompts + environment harnesses. See examples/README.md.
utils/                 Internal helpers (parsing, storage, git, evaluation).
```

## Dev

```bash
uv run ruff check
```
