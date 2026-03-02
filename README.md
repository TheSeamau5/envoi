# envoi

Envoi is a Python SDK for building **evaluation environments** — HTTP servers that test whether AI-generated code actually works.

You write async Python functions that define what "correct" means. Envoi turns them into an API that any client can call: a script, a CI pipeline, or an AI coding agent.

```
pip install envoi-ai
```

## The problem

You want an AI agent to write a C compiler for you. How do you know if it's working?

You need a **test harness** — something that takes the agent's code, compiles it, runs test programs through it, and reports what passed and what failed. That harness needs to run in an isolated environment (a Docker container) so the agent's code can't break your machine. And it needs to be an HTTP service so the agent can call it programmatically during its work, not just at the end.

Envoi is a framework for building these harnesses.

## How it works

An envoi environment is a Python file with decorated functions:

```python
import envoi
from solution import square

@envoi.test
async def test_square():
    """Does the function square 5 correctly?"""
    return {"passed": square(5) == 25}

@envoi.test
async def test_negative():
    """Does it handle negative numbers?"""
    return {"passed": square(-3) == 9}
```

When you run `envoi-runtime --file environment.py`, this becomes a FastAPI server. A client connects, uploads source code, and gets back test results as JSON.

The lifecycle is:

1. **Client creates a session** — uploads files, triggers `@envoi.setup`
2. **Client runs tests** — calls individual tests or entire suites, gets structured results
3. **Client closes the session** — triggers `@envoi.teardown`, cleans up

For simpler environments that don't need setup (no compilation phase, no uploaded files), skip `@envoi.setup` and tests run statelessly.

## Writing tests

### Single tests

The simplest environment is a single test:

```python
import envoi

@envoi.test
async def adds_correctly():
    return {"passed": 1 + 1 == 2}
```

Tests must be `async def`. They can return any JSON-serializable value. For tests that need to run shell commands (compiling code, executing binaries), use `envoi.run()`.

### Test suites

Group related tests with `envoi.suite()`. This creates hierarchical paths — requesting `/test/basics` runs all tests in the suite, while `/test/basics/variables` runs just one:

```python
basics = envoi.suite("basics")

@basics.test
async def variables():
    result = await envoi.run("./cc tests/variables.c -o out && ./out")
    return {"passed": result.exit_code == 0}

@basics.test
async def control_flow():
    result = await envoi.run("./cc tests/control_flow.c -o out && ./out")
    return {"passed": result.exit_code == 0}
```

Suites nest:

```python
advanced = basics.suite("advanced")

@advanced.test
async def pointers():
    # path: "basics/advanced/pointers"
    ...
```

### Parameterized tests

Test names can contain template variables. The runtime extracts values from the URL and passes them as keyword arguments:

```python
wacct = envoi.suite("wacct")

@wacct.test("chapter_{chapter}")
async def run_chapter(chapter: int):
    result = await envoi.run(f"./run_tests --chapter {chapter}")
    return {"passed": result.exit_code == 0, "chapter": chapter}
```

A request to `/test/wacct/chapter_5` runs `run_chapter(chapter=5)`.

### Setup and teardown

For environments where the code under test must be built first, use `@envoi.setup`. This makes the environment session-based — clients must create a session before running tests:

```python
@envoi.setup
async def build(submission: envoi.Documents):
    workdir = envoi.session_path()
    await envoi.run(f"cp -r {submission.dir}/. {workdir}")
    result = await envoi.run("make", cwd=str(workdir))
    if result.exit_code != 0:
        raise RuntimeError(f"Build failed:\n{result.stderr}")

@envoi.teardown
async def cleanup():
    workdir = envoi.session_path()
    await envoi.run(f"rm -rf {workdir}")
```

`@envoi.setup` receives uploaded files as a `Documents` argument. `@envoi.teardown` runs when the session closes or times out. Each environment can have at most one of each.

## Running the server

```bash
envoi-runtime --file environment.py --host 0.0.0.0 --port 8000
```

This serves:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/schema` | Discover available tests, capabilities, and parameter types |
| `POST` | `/test/{path}` | Run a test (stateless environments only) |
| `POST` | `/session` | Create a session (calls `@setup` with uploaded files) |
| `POST` | `/session/{id}/test/{path}` | Run a test within a session |
| `DELETE` | `/session/{id}` | Close a session (calls `@teardown`) |

Session-based environments spawn an isolated worker process per session. Each session has an inactivity timeout (default 300 seconds) — if no tests are run within that window, the session is cleaned up automatically.

## Using the client

Connect to a running environment from Python:

```python
import envoi

# Session-based environment
async with await envoi.connect("http://localhost:8000") as client:
    # Upload source code and create a session
    async with await client.session(
        submission=envoi.Documents("./my-compiler-src")
    ) as session:
        # Run all tests
        all_results = await session.test()

        # Run a specific suite
        basics_results = await session.test("basics")

        # Run a specific test
        one_result = await session.test("basics/variables")
```

Shorthand for the common case:

```python
async with await envoi.connect_session(
    "http://localhost:8000",
    submission=envoi.Documents("./src"),
) as session:
    result = await session.test()
```

For stateless environments (no `@setup`):

```python
async with await envoi.connect("http://localhost:8000") as client:
    result = await client.test("adds_correctly")
```

## Deploying with Docker

Build and run an environment as a container:

```bash
python -m envoi.deploy --path ./my_environment --port 9000
```

If the environment directory contains a `Dockerfile`, it is used (this is how you install compilers, test fixtures, and other heavy dependencies). Otherwise, a base `python:3.12-slim` image is used.

```python
from envoi import deploy

result = deploy("./my_environment", port=9000)
# result["url"] == "http://localhost:9000"
```

## Utilities

### `envoi.run(command, cwd=None, timeout_seconds=30)`

Run a shell command. Returns a `RunResult` with `.stdout`, `.stderr`, `.exit_code`, and `.stdout_bytes`. Inside a session, `cwd` defaults to the session's working directory.

### `envoi.Documents(paths)`

A container for files to upload. Wraps file paths, directory paths, or in-memory text:

```python
envoi.Documents("./src")                          # a directory
envoi.Documents(["file1.c", "file2.c"])           # specific files
envoi.Documents.from_text("main.c", "int main(){}")  # from a string
```

### `envoi.session_path()`

Returns the working directory for the current session. Use in `@setup` to know where to extract files, and in tests to find the built artifact.

## Repository structure

This repo is a monorepo. The SDK (`envoi-ai` on PyPI) is the foundation. The other packages build on it:

| Directory | PyPI name | What it does |
|-----------|-----------|--------------|
| `packages/envoi/` | `envoi-ai` | The SDK described above |
| `packages/code/` | `envoi-code` | Orchestrates AI coding agents against envoi environments, captures traces |
| `packages/cli/` | `envoi-cli` | Unified `envoi` CLI |

Dependency chain: `envoi-cli` → `envoi-code` → `envoi-ai`

### Examples

The `examples/` directory contains complete evaluation environments:

```
examples/c_compiler/         # Evaluate a C compiler across 250+ test programs
  task/                      # Task definition (prompt, parameters)
  environment/               # envoi environment (test harness, Dockerfile, test suites)

examples/gameboy_emulator/   # Evaluate a Game Boy emulator across ROM test suites
  task/
  environment/
```

Each example includes a `Dockerfile` that installs the full toolchain (compilers, test fixtures, reference implementations) so the environment is completely self-contained.

## Development

```bash
uv sync
cp .env.example .env  # fill in credentials
envoi --help
```

## License

Apache-2.0
