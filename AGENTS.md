# AGENTS.md

## What This Repo Does
This repo runs and records long-horizon OpenCode agent sessions against a C-compiler test environment.

Core job:
1. Run an agent in a Modal sandbox with a bounded part budget.
2. Capture what happened (messages, tool calls, sessions/sub-sessions, test calls).
3. Capture repository state at high granularity via git commits when code changed.
4. Persist artifacts to S3 for offline replay and analysis.

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
- `orchestrate.py`: main controller. Starts sandbox services, runs turns, captures trace, checkpoints git, uploads artifacts.
- `sandbox/setup.sh`: boots envoi runtime (`:8000`) and OpenCode server (`:4096`) inside sandbox.
- `sandbox/mcp_server.py`: exposes `run_tests(test_path)` via MCP; runs envoi tests against `/workspace`.
- `sandbox/opencode_client.py`: Python SDK CLI wrapper around OpenCode API (`opencode_ai`), returns stable JSON.
- `environment/main.py`: envoi environment entrypoint (build + test suites).
- `environment/tests/*`: suite implementations (`basics`, `wacct`, `c_testsuite`, `torture`).
- `offline_replay.py`: offline artifact consumer (reconstruct repo at part `p`, replay tests by commit).

## Big Technical Decisions (Intent)
- Single trace object (`agent_trace.json`) instead of append-only JSONL:
  - Easier to store nested turn/part/session/message/checkpoint data.
  - One canonical JSON document per trajectory.
- Git-first state capture:
  - Checkpoint commits happen only when files changed (no duplicate commit noise).
  - Final `repo.bundle` makes full history portable.
- Capture full session family, not only root session:
  - Per turn, `list-sessions` + parent graph traversal captures root + child + deeper sessions.
  - Messages are collected for all discovered session IDs in that family.
- SDK isolation:
  - OpenCode API access is centralized in `sandbox/opencode_client.py`.
  - Orchestrator talks to one JSON CLI surface, decoupled from SDK internals.

## Artifact Contract (S3)
For trajectory `<id>`, artifacts are stored under:
- `trajectories/<id>/agent_trace.json` (canonical trace)
- `trajectories/<id>/repo.bundle` (git history)

Code-state source of truth:
- `repo.bundle` contains full git history and is the canonical source for repository reconstruction.
- `agent_trace.json` maps each part to commit metadata (`git_commit` / `repo_checkpoint`).

`agent_trace.json` shape:
- `turns`: list of turn records
- each turn record includes:
  - `turn` (global turn index)
  - `part_start`, `part_end`
  - `parts`: list of per-part records for that turn

Each part record includes:
- `part` (global part index), `timestamp`, `prompt`, `message_id`
- `sessions`, `session_ids`, `new_messages`
- `envoi_calls`
- `repo_checkpoint`: `commit_before`, `commit_after`, `changed_files`
- `git_commit` (effective commit)

Top-level session summary includes:
- `session_end.reason`
- `session_end.total_parts`
- `session_end.total_turns`
- `session_end.final_git_commit`

## How To Reconstruct Repo At Part `p`
Use `offline_replay.py`:

```bash
uv run python offline_replay.py \
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
```bash
uv run python offline_replay.py \
  --mode evaluate \
  --trajectory-id <trajectory_id> \
  --output output/offline_eval.json
```

Behavior:
- Deduplicates commits across parts.
- Evaluates each unique commit once.
- Maps results back onto each part (`part_to_commit`, `part_evaluations`).

## Where To Edit What
- Trace schema/capture behavior: `orchestrate.py` (`PartRecord`, `TurnRecord`, `collect_turn_messages`, main loop).
- OpenCode API interactions: `sandbox/opencode_client.py`.
- Sandbox boot/runtime services: `sandbox/setup.sh`.
- Tool exposure: `sandbox/opencode.jsonc` and `sandbox/mcp_server.py`.
- Test suite behavior: `environment/tests/*.py`.
- Offline reconstruction/reporting: `offline_replay.py`.

## Operational Notes
- Use `uv` for local Python workflows.
- Main run command:
  ```bash
  modal run orchestrate.py --model opencode/gpt-5-nano --max-parts <n>
  ```
- Lint/check:
  ```bash
  uv run ruff check orchestrate.py sandbox/opencode_client.py offline_replay.py
  ```

## Important Gotchas
- A turn may produce no new commit when files did not change.
- `repo.bundle` is uploaded at end-of-run; if run dies early, bundle may be missing.
- Full offline evaluation requires heavy fixtures at `/opt/tests/...`.
- `envoi-repo/` is a local reference; orchestrator runtime uses installed `envoi` in sandbox image.
