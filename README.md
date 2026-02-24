# envoi

API-backed evaluation environments for AI coding agents.

## Packages

| Package | Install | Description |
|---------|---------|-------------|
| `packages/envoi/` | `pip install envoi` | SDK for authoring evaluation environments |
| `packages/code/` | `pip install envoi-code` | Coding agent evaluation framework with trace capture |
| `packages/cli/` | `pip install envoi-cli` | Unified `envoi` CLI |

## Quickstart

```bash
uv sync
cp .env.example .env  # fill in credentials
envoi --help
```

## Run an agent trajectory

```bash
envoi code --example examples/c_compiler
```

Default run behavior:
- No max part cap unless `--max-parts` is set.
- No max turn cap unless `--max-turns` is set.
- Total run timeout defaults to `7200` seconds.
- Each run writes `trace.parquet`, `repo.bundle`, and `logs.parquet` to S3.

Set explicit run caps/timeouts:

```bash
envoi code --example examples/c_compiler --max-parts 500 --max-turns 20
envoi code --example examples/c_compiler --timeout-seconds 14400
```

Run selected test paths instead of all tests:

```bash
envoi code --example examples/c_compiler --test basics
envoi code --example examples/c_compiler --test basics --test wacct/chapter_1
```

Override evaluation timeout (commit evals + turn-end evals):

```bash
envoi code --example examples/c_compiler --test-timeout-seconds 10800
```

## Evaluation behavior

- If `--test` is omitted, evaluations run all tests (`session.test()`).
- Repeat `--test` to evaluate one or more specific test paths.
- `--test-timeout-seconds` applies to both async commit eval and blocking turn-end eval.
- If `--test-timeout-seconds` is omitted, the default is `EVALUATION_TIMEOUT_SECONDS` (default `7200` seconds).
- Commit evals run asynchronously on each git checkpoint; turn-end eval runs synchronously before the next turn prompt.
- Turn-end feedback includes the top 50 failed tests (priority: `basics -> c_testsuite -> wacct -> torture`) with full test source and failure message.
- If environment params enable advisor model settings, turn-end feedback also includes an external assessment based on the task prompt, top failed tests, and current commit code snapshot.
- Runs stop at the first winning commit (`passed == total`), and artifacts are projected to that winning commit.
- Runtime/worker/orchestrator logs are captured as structured records in `logs.parquet`.

Environment-level advisor config:
- Create `environment/params.py` with `params()` returning:
  - `advisor_model` (example: `@anthropic/claude-opus-4.6`)
  - `advisor_model_thinking_level` (`low|medium|high`)
  - `failed_tests_feedback_limit` (default: 50)

## Deploy an environment locally

```bash
envoi deploy examples/c_compiler/environment
```

## Examples

Examples live in `examples/<name>/` with colocated `task/` and `environment/` directories:

```
examples/c_compiler/
  task/en.md              # what to tell the agent
  environment/
    main.py               # envoi test harness
    Dockerfile            # sandbox image
    params.py             # optional environment-level run params
    tests/                # test suites
```

## Structured logs

Per trajectory, S3 now includes:
- `trajectories/<id>/trace.parquet`
- `trajectories/<id>/repo.bundle`
- `trajectories/<id>/logs.parquet`

`logs.parquet` stores structured runtime records (`ts`, `component`, `event`, `level`, `message`, `turn`, `part`, `git_commit`, `session_id`, `fields`).

Flush policy:
- Periodic during run (default every `5s` or `50` new records, whichever triggers first).
- Immediate wake-up on warning/error logs.
- Forced flush on turn boundaries and shutdown.

Tuning:
- `LOGS_FLUSH_INTERVAL_SECONDS` (default `5`)
- `LOGS_FLUSH_BATCH_SIZE` (default `50`)

Quick DuckDB query example:

```sql
SELECT ts, component, event, level, message
FROM read_parquet('s3://<bucket>/trajectories/<id>/logs.parquet')
ORDER BY ts;
```

## Development

```bash
uv sync
uv run ruff check .
uv run python -c "import envoi; import envoi_code; from envoi_cli.main import main"
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
