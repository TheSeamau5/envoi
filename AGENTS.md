# AGENTS.md

## What This Repo Does
This repo is an iterative harness to get an agent to build a C compiler against an envoi test environment.

Primary objective:
- Increase compiler correctness over time until the agent passes all required suites.

Operational objective:
- Make each run replayable and diagnosable at part granularity.

Core job:
1. Run an agent in a Modal sandbox with a bounded part budget.
2. Let the agent iteratively edit compiler code and run envoi tests.
3. Capture what happened at part granularity.
4. Capture repository state via git commits when code changed.
5. Persist artifacts to S3 for replay and analysis.

Hard requirement:
- Persist `agent_trace.json` after every recorded part.
- Create a git checkpoint immediately for any part that changed files.

Schema policy:
- No deprecated fields, aliases, compatibility shims, or dual schemas.
- Do not keep old names alive after renames.
- If a schema or term changes, migrate and delete the old one.
- Rule: fix or delete.

## Vocabulary (Canonical)
- `part`:
  - Most granular observable unit in the trace.
  - A meaningful assistant part such as `reasoning`, `text`, `tool`, `tool_use`, `tool_result`, or `patch`.
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

## Architecture At A Glance
- `task.py`: canonical agent definition (shared system prompt).
- `runner.py`: main controller. Starts Modal sandbox runtime, runs agent turns, captures trace, checkpoints git, uploads artifacts.
- `sandbox/modal/setup.sh`: boots envoi runtime (`:8000`) and agent runtime tooling inside Modal sandbox.
- `sandbox/modal/mcp_server.py`: exposes `run_tests(test_path)` via MCP; runs envoi tests against `/workspace`.
- `agents/opencode.py`: OpenCode agent client wrapper (stable JSON surface + inline OpenCode config template).
- `agents/codex.py`: Codex agent client wrapper (stable JSON surface).
- `environment/main.py`: envoi environment entrypoint (build + test suites).
- `environment/tests/*`: suite implementations (`basics`, `wacct`, `c_testsuite`, `torture`).
- `scripts/offline_replay.py`: offline artifact consumer (reconstruct repo at part `p`, replay tests by commit).

## Big Technical Decisions (Intent)
- Single trace object (`agent_trace.json`) instead of append-only JSONL:
  - Easier to store nested turn/part/checkpoint/test-state data.
  - One canonical JSON document per trajectory.
- Git-first state capture:
  - Checkpoint commits happen only when files changed (no duplicate commit noise).
  - Final `repo.bundle` makes full history portable.
- SDK isolation:
  - OpenCode API access is centralized in `agents/opencode.py`.
  - Orchestrator talks to one JSON CLI surface, decoupled from SDK internals.

## Artifact Contract (S3)
For trajectory `<id>`, artifacts are stored under:
- `trajectories/<id>/agent_trace.json` (canonical trace)
- `trajectories/<id>/repo.bundle` (git history)

Code-state source of truth:
- `repo.bundle` contains full git history and is the canonical source for repository reconstruction.
- `agent_trace.json` maps each part to commit metadata (`git_commit` / `repo_checkpoint`).

`agent_trace.json` shape:
- `parts`: canonical list of part records (source of truth)
- `turns`: grouping metadata

Each part record includes:
- identity and timing:
  - `part`, `timestamp`, `duration_ms`
- part semantics:
  - `role` (`assistant` or `user`)
  - `part_type` (`reasoning`, `text`, `tool`, `patch`, ...)
  - `item_type` (provider-specific item kind)
  - `summary` (concise content preview)
- repository state:
  - `git_commit`
  - `repo_checkpoint`: `commit_before`, `commit_after`, `changed_files`
- testing state:
  - `envoi_calls` (new test calls observed on that part)
  - `testing_state` (solved progress + latest test status)

Top-level session summary includes:
- `session_end.reason`
- `session_end.total_parts`
- `session_end.total_turns`
- `session_end.final_git_commit`

## Quick Trace Commands
Run a trajectory:

```bash
uv run trace
```

Common options:

```bash
uv run trace --agent codex --max-parts 100 --detach
```

`trace` prints:
- `TRAJECTORY_ID`
- `TRACE_S3_URI`
- `BUNDLE_S3_URI`

Analyze a trajectory:

```bash
uv run graph_trace <trajectory_id>
```

This auto-downloads trace + bundle from S3 and runs suite-level analysis.

For a specific part checkout:

```bash
uv run graph_trace <trajectory_id> --part <p>
```

## How To Reconstruct Repo At Part `p`
Use the `replay` CLI (implemented in `scripts/offline_replay.py`) when you need full control:

```bash
uv run replay \
  --mode checkout-part \
  --trajectory-id <trajectory_id> \
  --part <p> \
  --checkout-dest output/repo_part_<p> \
  --output output/repo_part_<p>.json
```

What it does:
1. Resolves/downloads `agent_trace.json` and `repo.bundle`.
2. Reads commit for part `p`.
3. Clones bundle and checks out that commit.

## How To Replay Tests Offline
Use the `replay` CLI (implemented in `scripts/offline_replay.py`) when you need full control:
```bash
uv run replay \
  --mode evaluate \
  --trajectory-id <trajectory_id> \
  --output output/offline_eval.json
```

Behavior:
- Deduplicates commits across parts.
- Evaluates each unique commit once.
- Maps results back onto each part (`part_to_commit`, `part_evaluations`).

## Where To Edit What
- Agent definition (shared prompt): `task.py`.
- Trace schema/capture behavior: `runner.py` (`PartRecord`, `TurnRecord`, `make_stream_part_callback`, main loop).
- OpenCode agent integration: `agents/opencode.py`.
- Codex agent integration: `agents/codex.py`.
- Sandbox boot/runtime services: `sandbox/modal/setup.sh`.
- Tool exposure: `agents/opencode.py` (inline config template) and `sandbox/modal/mcp_server.py`.
- Test suite behavior: `environment/tests/*.py`.
- Offline reconstruction/reporting: `scripts/offline_replay.py`.
- Short launchers: `scripts/trace.py`, `scripts/graph_trace.py`.

## Operational Notes
- Use `uv` for local Python workflows.
- Default run behavior:
  - agent: `codex`
  - max parts: `1000`
- Normal workflow:
  - run trajectory (`uv run trace`)
  - capture `TRAJECTORY_ID` from startup logs
  - analyze (`uv run graph_trace <trajectory_id>`)
- Main run command (simple):
  ```bash
  uv run trace
  ```
- With explicit options:
  ```bash
  uv run trace --agent codex --max-parts 100 --detach
  ```
- Direct Modal command (full control):
  ```bash
  modal run runner.py --agent codex --max-parts <n>
  ```
- Lint/check:
  ```bash
  uv run ruff check task.py runner.py agents/opencode.py agents/codex.py scripts/offline_replay.py scripts/trace.py scripts/graph_trace.py
  ```

## Important Gotchas
- A turn may produce no new commit when files did not change.
- `repo.bundle` is uploaded at end-of-run; if run dies early, bundle may be missing.
- Full offline evaluation requires heavy fixtures at `/opt/tests/...`.
- `envoi-repo/` is a local reference; orchestrator runtime uses installed `envoi` in sandbox image.
