# envoi

Minimal Python project bootstrapped with `uv`.

## Environment Metadata

Environment metadata comes from `pyproject.toml`.

Default resolution:
1. `[tool.envoi.environment]` override values, if present
2. `[project]` values (`name`, `version`, `description`)

Example:

```toml
[project]
name = "polish-notation"
version = "0.1.0"
description = "Evaluate polish notation expressions."

[tool.envoi.environment]
# optional overrides
name = "polish-notation"
```

## Examples Layout

Examples are grouped per environment:

```text
examples/<environment>/
  author/      # environment code and metadata
  client/      # client scripts and submissions
```

## Quickstart

```bash
uv sync
source .venv/bin/activate
uv run envoi --help
```

## Local Runtime

Run an environment directly:

```bash
uv run python -m envoi.runtime --file examples/polish_notation/author/polish_notation.py --port 8000
```

Run it in a local Docker sandbox:

```bash
cd examples/polish_notation/author
uv run envoi --port 8000
```

## Common Commands

```bash
# add a dependency
uv add <package>

# run a module
uv run python -m envoi
```
