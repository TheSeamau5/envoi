# AGENTS.md

## Repository Overview

Envoi is a monorepo containing an SDK for building API-backed evaluation environments, a coding agent evaluation framework, and a unified CLI. It uses **uv workspaces** for Python package management.

### Packages

| Package | Install name | What it does |
|---------|-------------|--------------|
| `packages/envoi/` | `envoi` | SDK for authoring evaluation environments (`@envoi.suite`, `@envoi.test`, `@envoi.setup`) |
| `packages/code/` | `envoi-code` | Orchestrates coding agents against envoi environments, captures parquet traces |
| `packages/cli/` | `envoi-cli` | Unified `envoi` CLI. Routes subcommands to the right packages |

Dependency graph:

```
envoi-cli  ──→  envoi-code  ──→  envoi
                                    ↑
envoi-cli  ─────────────────────────┘
```

### Examples

Examples live in `examples/<name>/` with colocated `task/` and `environment/` directories:

```
examples/
├── c_compiler/
│   ├── task/en.md
│   └── environment/
│       ├── main.py
│       ├── Dockerfile
│       ├── setup.sh
│       └── tests/
└── polish_notation/
    └── environment/
```

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
envoi CLI (packages/cli/envoi_cli/main.py)
  └─ envoi code
       └─ modal run sandbox/modal/deploy.py
            ├─ Orchestrator (packages/code/envoi_code/orchestrator.py)
            ├─ AgentBackend (packages/code/envoi_code/agents/base.py)
            │    ├─ CodexAgent (agents/codex.py) ── runs inside sandbox
            │    └─ OpenCodeAgent (agents/opencode.py) ── runs inside sandbox
            ├─ SandboxBackend (packages/code/envoi_code/sandbox/base.py)
            │    ├─ ModalSandbox (sandbox/modal/backend.py)
            │    └─ E2BSandbox (sandbox/e2b/backend.py)
            ├─ envoi server (localhost:8000) ── test harness from environment/main.py
            └─ MCP server (sandbox/mcp_server.py) ── bridges agent ↔ envoi
```

## Key Files

### SDK (`packages/envoi/envoi/`)
- `environment.py` — `@envoi.suite()`, `@envoi.test()`, `@envoi.setup()` decorators
- `client.py` — `envoi.Client`, `envoi.connect()`, async session API
- `runtime.py` — FastAPI server that hosts an environment
- `deploy.py` — Docker-based local deployment
- `constants.py` — Shared constants (ports, timeouts, image names)

### Runner (`packages/code/envoi_code/`)
- `orchestrator.py` — Main orchestrator. Runs inside Modal. Manages agent turns, git checkpoints, trace persistence, and session recovery.
- `models.py` — Pydantic models: `PartRecord`, `TurnRecord`, `AgentTrace`, `SessionEnd`, `EnvoiCall`, `TestingState`, etc.
- `agents/base.py` — `Agent` Protocol. Every agent implements this.
- `sandbox/base.py` — `Sandbox` Protocol + `CommandResult`. Every sandbox implements this.
- `scripts/trace.py` — CLI entrypoint. Launches orchestrator via Modal, handles auto-resume.
- `utils/trace_parquet.py` — Parquet serialization: `agent_trace_to_rows()`, `parquet_to_trace_dict()`.
- `utils/storage.py` — S3 upload/download for trace and bundle artifacts.
- `utils/git.py` — Git checkpoint operations inside the sandbox.
- `utils/evaluation.py` — Concurrent commit evaluation against envoi.
- `utils/parsing.py` — Parse agent responses into parts and envoi calls.
- `utils/stream.py` — Real-time stream callback for part events.
- `utils/solve.py` — `SolveTracker`: tracks which test paths have been solved.
- `utils/helpers.py` — Small utilities: timestamps, text, tokens, file upload.

### CLI (`packages/cli/envoi_cli/`)
- `main.py` — Unified `envoi` command. `envoi deploy` always available; `envoi code *` available when `envoi-code` is installed.

## Task Loading

`orchestrator.py`'s `load_task(task_dir)` loads a task from a directory path. Three tiers:

- **Tier 3**: `task_dir/task.py` with a `generate()` function → returns `(prompt, params)`
- **Tier 2**: `task_dir/prompt.md` (or `en.md`) + `task_dir/params.py` → template substitution
- **Tier 1**: `task_dir/prompt.md` (or `en.md`) only → static prompt

Uses `importlib.util.spec_from_file_location` — task directories don't need to be Python packages.

## Big Technical Decisions

- **Parquet trace format**: One row per part, denormalized. Enables DuckDB cross-trace queries via S3 globbing. Nested objects stored as JSON strings.
- **Git-first state capture**: Checkpoint commits happen only when files changed. Final `repo.bundle` makes full history portable.
- **SDK isolation**: Agent scripts run inside the sandbox. The orchestrator talks to them via a JSON stdio surface, decoupled from agent SDK internals.
- **Two Protocol abstractions**: `Agent` for agents, `Sandbox` for sandboxes. Swap implementations without touching the orchestrator.

## Hard Requirements

- Persist `trace.parquet` after every recorded part.
- Create a git checkpoint immediately for any part that changed files.

## Schema Policy

- No deprecated fields, aliases, compatibility shims, or dual schemas.
- If a schema or term changes, migrate and delete the old one.
- Rule: fix or delete.

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

Command style rule:
- Never tell this user to run `uv run envoi ...`; always use the direct CLI form `envoi ...`.

Run a trajectory:

```bash
envoi code --task examples/c_compiler/task --env examples/c_compiler/environment
envoi code --example examples/c_compiler
```

Common options:

```bash
envoi code --agent codex --max-parts 100 --task <path> --env <path>
envoi code --agent opencode --model opencode/gpt-5-nano --task <path> --env <path>
envoi code --sandbox e2b --task <path> --env <path>
envoi code --preemptible --task <path> --env <path>
envoi code --detach --task <path> --env <path>
envoi code --timeout-seconds 10800 --task <path> --env <path>
envoi code --test basics --task <path> --env <path>
envoi code --test basics --test wacct/chapter_1 --task <path> --env <path>
envoi code --test-timeout-seconds 10800 --task <path> --env <path>
```

Run defaults:
- `--max-parts` omitted => no part cap.
- `--max-turns` omitted => no turn cap.
- `--timeout-seconds` default is 7200.

Evaluation defaults and selectors:
- If `--test` is omitted, evaluation runs all tests (`session.test()`).
- Repeat `--test` to evaluate multiple test paths.
- `--test-timeout-seconds` applies to both async commit eval and blocking turn-end eval.

Evaluation lifecycle:
- On each file-changing part/commit, commit eval is queued asynchronously.
- At each turn end, a blocking workspace eval runs before the next turn prompt is built.
- If any eval finds a winning commit (`passed == total`), the run latches to the first winner and stops.
- On solved runs, trace/bundle outputs are projected to the winning commit (no post-win history retained).

Deploy an environment locally:

```bash
envoi deploy examples/c_compiler/environment
envoi deploy examples/c_compiler/environment --port 9000
```

Analyze:

```bash
envoi code graph <trajectory_id>
envoi code graph <trajectory_id> --part 42 --checkout-dest ./repo_at_42
```

## How To Run

```bash
uv sync
cp .env.example .env  # fill in credentials
envoi code --example examples/c_compiler
```

## Where To Edit What

- Task prompt: `examples/<name>/task/en.md` (or `prompt.md`)
- Environment harness: `examples/<name>/environment/main.py`
- Test suites: `examples/<name>/environment/tests/*.py`
- Fixture installation: `examples/<name>/environment/setup.sh`
- Trace schema/capture: `packages/code/envoi_code/orchestrator.py` (main loop, `PartRecord`, `TurnRecord`)
- Trace parquet serialization: `packages/code/envoi_code/utils/trace_parquet.py`
- Agent integration: `packages/code/envoi_code/agents/codex.py`, `packages/code/envoi_code/agents/opencode.py`
- Sandbox runtime: `packages/code/envoi_code/sandbox/modal/`, `packages/code/envoi_code/sandbox/mcp_server.py`
- CLI launcher: `packages/code/envoi_code/scripts/trace.py`
- Offline analysis: `packages/code/envoi_code/scripts/graph_trace.py`, `packages/code/envoi_code/scripts/offline_replay.py`

## Important Gotchas

- A turn may produce no new commit when files did not change.
- `repo.bundle` is uploaded at end-of-run; if the run dies early, bundle may be missing.
- On solved runs, `trace.parquet` is projected to the first winning commit part (no post-win parts are kept).
- Full offline evaluation requires heavy fixtures at `/opt/tests/...`.
- `--task` and `--env` are required path arguments — there are no defaults (unless using `--example`).
