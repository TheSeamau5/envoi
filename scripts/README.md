# scripts/

CLI entrypoints for running trajectories and analyzing results.

## Commands

### `envoi-trace` (main CLI, `trace.py`)

The primary entrypoint. Two modes:

**Run mode** (default, no subcommand):

```bash
envoi-trace --task <path> --env <path> [--agent codex|opencode] [--max-parts N]
```

Launches `runner.py` via Modal (or directly for E2B). Handles auto-resume on retryable failures, prints trajectory ID and S3 URIs at startup.

**Graph mode** (subcommand):

```bash
envoi-trace graph <trajectory_id> [--part N] [--checkout-dest <dir>]
```

Downloads trace + bundle from S3 and generates suite-level analysis graphs.

### `graph_trace.py`

The graph generation implementation. Called by `envoi-trace graph`. Downloads the trace parquet and repo bundle, then produces matplotlib visualizations of test progress over time.

### `offline_replay.py`

Offline artifact consumer for advanced analysis:

```bash
replay --mode checkout-part --trajectory-id <id> --part 42 --checkout-dest ./out
replay --mode evaluate --trajectory-id <id> --output eval.json
```

- `checkout-part`: Reconstruct the repository at any part by cloning the bundle and checking out the right commit
- `evaluate`: Re-run test evaluations offline against each unique commit in the trace
