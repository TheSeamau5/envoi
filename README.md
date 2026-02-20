# envoi-trace

Environment for iterative agent runs to build a C compiler against an envoi test harness.

## North Star
Get the agent to pass all C-compiler suites by iterating on code:
`edit -> run_tests -> inspect failures -> fix -> repeat`.

## Purpose
This repo is for running an agent repeatedly in a controlled sandbox so it can:
- write compiler code,
- run envoi test suites,
- fix failures,
- and progress toward passing all suites.

Tracing and git checkpoints exist to make that iterative process observable and reproducible.

This is not a generic tracing project. The tracing exists to support compiler progress and replay.

## Core Loop
1. Start a trajectory (`uv run trace`).
2. Agent edits compiler code in `/workspace`.
3. Agent calls `run_tests` via MCP against envoi suites.
4. Every meaningful assistant part is recorded.
5. Any workspace file changes are checkpointed in git immediately.
6. Trace + repo history are uploaded to S3 for replay/analysis.

## Daily Commands
Run trajectory (defaults: `--agent codex --max-parts 1000`):

```bash
uv run trace
```

Common run:

```bash
uv run trace --agent codex --max-parts 100 --detach
```

At startup, `trace` prints:
- `TRAJECTORY_ID`
- `TRACE_S3_URI`
- `BUNDLE_S3_URI`

Typical loop:
1. `uv run trace --detach`
2. Copy `TRAJECTORY_ID` from startup logs
3. `uv run graph_trace <trajectory_id>`
4. Use results to tune prompt/config and run again

Analyze trajectory by ID:

```bash
uv run graph_trace <trajectory_id>
```

Checkout repo at a specific part:

```bash
uv run graph_trace <trajectory_id> --part <p>
```

## Stored Artifacts
For trajectory `<id>`:
- `s3://<bucket>/trajectories/<id>/agent_trace.json`
- `s3://<bucket>/trajectories/<id>/repo.bundle`

`agent_trace.json` is part-centric:
- top-level `parts` is source of truth,
- each part captures role/type/content summary, timing, repo state, and testing state at that part.

`repo.bundle` is the source of truth for repository reconstruction.

## Full-Control Mode
For advanced/offline workflows:

```bash
uv run python offline_replay.py --help
```

## Dev Check

```bash
uv run ruff check orchestrate.py sandbox/opencode_client.py sandbox/codex_client.py offline_replay.py run_trace.py graph_trace.py
```
