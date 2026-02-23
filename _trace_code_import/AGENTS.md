# AGENTS.md

## What This Repo Does

envoi-trace is an agent evaluation framework. It runs a coding agent inside a remote sandbox, lets the agent iteratively edit code and run tests against an envoi environment, and records everything as a parquet trace.

Primary objective:
- Evaluate how well a coding agent performs against a test harness over a part budget.

Operational objective:
- Make every run replayable and diagnosable at part granularity.

Core loop:
1. Spin up a sandbox (Modal or E2B) with the environment installed.
2. Run the agent (Codex or OpenCode) with a bounded part budget.
3. The agent edits code, runs tests, and iterates.
4. Capture every action as a part in `trace.parquet`.
5. Checkpoint the repo via git whenever files change.
6. Persist artifacts to S3 for replay and analysis.

Hard requirements:
- Persist `trace.parquet` after every recorded part.
- Create a git checkpoint immediately for any part that changed files.

Schema policy:
- No deprecated fields, aliases, compatibility shims, or dual schemas.
- If a schema or term changes, migrate and delete the old one.
- Rule: fix or delete.

## Vocabulary (Canonical)

- `part`:
  - Most granular observable unit in the trace.
  - A meaningful assistant action: `reasoning`, `text`, `tool`, `tool_use`, `tool_result`, or `patch`.
  - Global part index is the authoritative progress counter.
  - Budgeting and limits are based on parts (`--max-parts`).
- `turn`:
  - One request/response loop in the orchestrator.
  - A turn can contain many parts, one part, or zero meaningful parts.
  - Turns are grouping metadata only, not budgeting/accounting units.
- `step`:
  - Forbidden term. Do not use in code/docs/logs/schema/flags/artifacts.
- `cycle`:
  - Forbidden term. Do not use in code/docs/logs/schema/flags/artifacts.
  - Use `turn` for loop iterations and `part` for progress/accounting.

## Why Parts Are The Source Of Truth

- Parts are the highest-fidelity unit we can observe and count consistently across providers.
- A very capable model can do huge work in one turn; turn-count budgets miss this entirely.
- Part-level indexing gives better recovery and replay granularity than turn-only indexing.
- Artifact and replay contracts are keyed to part indices (`checkout-part`, `part_to_commit`).

## Architecture

```
envoi-trace CLI (scripts/trace.py)
  └─ modal run runner.py
       ├─ AgentBackend (agents/base.py)
       │    ├─ CodexAgent (agents/codex.py) ── runs inside sandbox
       │    └─ OpenCodeAgent (agents/opencode.py) ── runs inside sandbox
       ├─ SandboxBackend (sandbox/base.py)
       │    ├─ ModalSandbox (sandbox/modal/backend.py)
       │    └─ E2BSandbox (sandbox/e2b/backend.py)
       ├─ envoi server (localhost:8000) ── test harness from environment/main.py
       └─ MCP server (sandbox/modal/mcp_server.py) ── bridges agent ↔ envoi
```

Key files:
- `runner.py` — Main orchestrator. Runs inside Modal. Manages agent turns, git checkpoints, trace persistence, and session recovery.
- `models.py` — Pydantic models: `PartRecord`, `TurnRecord`, `AgentTrace`, `SessionEnd`, `EnvoiCall`, `TestingState`, etc.
- `agents/base.py` — `AgentBackend` Protocol. Every agent implements this.
- `sandbox/base.py` — `SandboxBackend` Protocol + `CommandResult`. Every sandbox implements this.
- `scripts/trace.py` — CLI entrypoint. Launches `runner.py` via Modal, handles auto-resume.
- `examples/tasks/` — Task prompts (what to tell the agent).
- `examples/environments/` — envoi test harnesses (what to evaluate the agent against).
- `utils/trace_parquet.py` — Parquet serialization: `agent_trace_to_rows()`, `parquet_to_trace_dict()`.
- `utils/storage.py` — S3 upload/download for trace and bundle artifacts.
- `utils/git.py` — Git checkpoint operations inside the sandbox.
- `utils/evaluation.py` — Concurrent commit evaluation against envoi.
- `utils/parsing.py` — Parse agent responses into parts and envoi calls.
- `utils/stream.py` — Real-time stream callback for part events.
- `utils/solve.py` — `SolveTracker`: tracks which test paths have been solved.
- `utils/helpers.py` — Small utilities: timestamps, text, tokens, file upload.

## Task Loading

`runner.py`'s `load_task(task_dir)` loads a task from a directory path. Three tiers:

- **Tier 3**: `task_dir/task.py` with a `generate()` function → returns `(prompt, params)`
- **Tier 2**: `task_dir/prompt.md` (or `en.md`) + `task_dir/params.py` → template substitution
- **Tier 1**: `task_dir/prompt.md` (or `en.md`) only → static prompt

Uses `importlib.util.spec_from_file_location` — task directories don't need to be Python packages.

## Big Technical Decisions

- **Parquet trace format**: One row per part, denormalized. Enables DuckDB cross-trace queries via S3 globbing. Nested objects stored as JSON strings.
- **Git-first state capture**: Checkpoint commits happen only when files changed. Final `repo.bundle` makes full history portable.
- **SDK isolation**: Agent scripts run inside the sandbox. The orchestrator talks to them via a JSON stdio surface, decoupled from agent SDK internals.
- **Two Protocol abstractions**: `AgentBackend` for agents, `SandboxBackend` for sandboxes. Swap implementations without touching the orchestrator.

## Trace Format (`trace.parquet`)

One row per part, trajectory-level fields denormalized into every row.

Each row records:
- Identity: `part`, `timestamp`, `duration_ms`
- Semantics: `role`, `part_type`, `item_type`, `summary`
- Repository: `git_commit`, `repo_checkpoint` (commits, changed files, patch)
- Testing: `envoi_calls` (test invocations), `testing_state` (solve progress)
- Session end (null while in-progress): `session_end_reason`, `session_end_total_parts`

`session_end_reason IS NULL` means in-progress; non-null means completed.

## Artifact Contract (S3)

For trajectory `<id>`:
- `trajectories/<id>/trace.parquet` — Canonical trace (written after every part)
- `trajectories/<id>/repo.bundle` — Full git history (uploaded at end-of-run)

`repo.bundle` is the canonical source for repository reconstruction. `trace.parquet` maps each part to its commit via `git_commit` / `repo_checkpoint`.

## CLI

Run a trajectory:

```bash
envoi-trace --task examples/tasks/c_compiler --env examples/environments/c_compiler
```

Common options:

```bash
envoi-trace --agent codex --max-parts 100 --task <path> --env <path>
envoi-trace --agent opencode --model opencode/gpt-5-nano --task <path> --env <path>
envoi-trace --sandbox e2b --task <path> --env <path>
envoi-trace --preemptible --task <path> --env <path>
envoi-trace --detach --task <path> --env <path>
```

Analyze:

```bash
envoi-trace graph <trajectory_id>
envoi-trace graph <trajectory_id> --part 42 --checkout-dest ./repo_at_42
```

Offline replay:

```bash
replay --mode checkout-part --trajectory-id <id> --part <p> --checkout-dest ./out
replay --mode evaluate --trajectory-id <id> --output eval.json
```

## Where To Edit What

- Task prompt: `examples/tasks/<name>/en.md` (or `prompt.md`)
- Environment harness: `examples/environments/<name>/main.py`
- Test suites: `examples/environments/<name>/tests/*.py`
- Fixture installation: `examples/environments/<name>/setup.sh`
- Trace schema/capture: `runner.py` (main loop, `PartRecord`, `TurnRecord`)
- Trace parquet serialization: `utils/trace_parquet.py`
- Agent integration: `agents/codex.py`, `agents/opencode.py`
- Sandbox runtime: `sandbox/modal/setup.sh`, `sandbox/modal/mcp_server.py`
- CLI launcher: `scripts/trace.py`
- Offline analysis: `scripts/graph_trace.py`, `scripts/offline_replay.py`

## Important Gotchas

- A turn may produce no new commit when files did not change.
- `repo.bundle` is uploaded at end-of-run; if the run dies early, bundle may be missing.
- Full offline evaluation requires heavy fixtures at `/opt/tests/...`.
- `--task` and `--env` are required path arguments — there are no defaults.
- `envoi-repo/` is a local reference; the sandbox uses the installed `envoi` package.
