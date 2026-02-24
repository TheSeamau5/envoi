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
- Runs stop at the first winning commit (`passed == total`), and artifacts are projected to that winning commit.

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
    setup.sh              # fixture installation
    tests/                # test suites
```

## Development

```bash
uv sync
uv run ruff check .
uv run python -c "import envoi; import envoi_code; from envoi_cli.main import main"
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
