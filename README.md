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
uv run envoi --help
```

## Run an agent trajectory

```bash
uv run envoi code run --example examples/c_compiler
```

## Deploy an environment locally

```bash
uv run envoi deploy examples/c_compiler/environment
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
