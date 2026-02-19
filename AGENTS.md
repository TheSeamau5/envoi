# AGENTS.md

## What This Repo Does
This repo runs and records long-horizon OpenCode agent sessions against a C-compiler test environment.

Core job:
1. Run an agent turn-by-turn in a Modal sandbox.
2. Capture what happened (messages, tool calls, sessions/sub-sessions, test calls).
3. Capture repository state per turn via git commits.
4. Persist artifacts to S3 for offline replay and analysis.

## Architecture At A Glance
- `orchestrate.py`: main controller. Starts sandbox services, runs turns, captures trace, checkpoints git, uploads artifacts.
- `sandbox/setup.sh`: boots envoi runtime (`:8000`) and OpenCode server (`:4096`) inside sandbox.
- `sandbox/mcp_server.py`: exposes `run_tests(test_path)` tool via MCP; runs envoi tests against `/workspace`.
- `sandbox/opencode_client.py`: thin Python SDK CLI wrapper around OpenCode API (`opencode_ai`), returns stable JSON.
- `environment/main.py`: envoi environment entrypoint (build + test suites).
- `environment/tests/*`: test suite implementations (`basics`, `wacct`, `c_testsuite`, `torture`).
- `offline_replay.py`: offline artifact consumer (reconstruct repo at turn `t`, replay tests by commit).

## Big Technical Decisions (Intent)
- Single trace object (`agent_trace.json`) instead of append-only `trajectory.jsonl`:
  - Easier to store structured nested data per turn (sessions, messages, checkpoints).
  - One canonical JSON document for an individual trajectory.
- Git-first state capture:
  - Each turn attempts a checkpoint commit (`turn N checkpoint`).
  - This yields stable commit hashes for "repo at turn `t`".
  - Final `repo.bundle` makes full history portable without storing full repo every turn.
- Capture session family, not only root session:
  - Per turn, `list-sessions` + parent graph traversal captures root + child + deeper sessions.
  - Messages are collected for all discovered session IDs in that family.
- SDK isolation:
  - OpenCode API access is centralized in `sandbox/opencode_client.py`.
  - Orchestrator interacts with one JSON-CLI surface, independent from SDK object model details.

## Artifact Contract (S3)
For trajectory `<id>`, artifacts are stored under:
- `trajectories/<id>/agent_trace.json` (canonical trace)
- `trajectories/<id>/repo.bundle` (git history)
- `trajectories/<id>/artifacts.json` (manifest of key artifact URIs)
- `trajectories/<id>/turns/####.patch` (per-turn patch snapshot when a commit was made)

`agent_trace.json` per turn includes:
- `turn`, `timestamp`, `prompt`, `message_id`
- `sessions`: session objects for root/sub-sessions seen that turn
- `session_ids`
- `new_messages`: newly observed messages that turn
- `envoi_calls`: parsed `run_tests` outputs
- `repo_checkpoint`: `commit_before`, `commit_after`, `changed_files`, optional `patch_s3_uri`
- `git_commit`: effective commit for turn

## How To Reconstruct Repo At Turn `t`
Use `offline_replay.py` (do not do this manually):

```bash
uv run python offline_replay.py \
  --mode checkout-turn \
  --trajectory-id <trajectory_id> \
  --turn <t> \
  --checkout-dest output/repo_turn_<t> \
  --output output/repo_turn_<t>.json
```

What it does:
1. Resolves/downloads `agent_trace.json` and `repo.bundle`.
2. Reads commit for turn `t`.
3. Clones bundle and checks out that commit.

## How To Replay Tests Offline
```bash
uv run python offline_replay.py \
  --mode evaluate \
  --trajectory-id <trajectory_id> \
  --output output/offline_eval.json
```

Behavior:
- Deduplicates commits across turns.
- Evaluates each unique commit once.
- Maps results back onto each turn (`turn_to_commit`, `turn_evaluations`).

## Where To Edit What
- Change trace schema/capture behavior: `orchestrate.py` (`TurnRecord`, `collect_turn_messages`, turn loop).
- Change OpenCode API interactions: `sandbox/opencode_client.py`.
- Change sandbox boot/runtime services: `sandbox/setup.sh`.
- Change available tools/exposure to agent: `sandbox/opencode.jsonc` and `sandbox/mcp_server.py`.
- Change test behavior/suite partitioning: `environment/tests/*.py`.
- Change offline reconstruction/reporting logic: `offline_replay.py`.

## Operational Notes
- Use `uv` for local Python workflows.
- Main run command:
  ```bash
  modal run orchestrate.py --model opencode/<model> --max-turns <n>
  ```
- Lint/check:
  ```bash
  uv run ruff check orchestrate.py sandbox/opencode_client.py offline_replay.py
  ```

## Important Gotchas
- `trajectory.jsonl` is not authoritative in current design; use `agent_trace.json`.
- A turn may have no new commit if no files changed.
- `repo.bundle` is uploaded at end-of-run; if run dies early, bundle may be missing.
- Full offline evaluation requires heavy test fixtures present at expected `/opt/tests/...` paths.
- `envoi-repo/` is a local copy/reference; orchestrator runtime uses installed `envoi` in sandbox image.
