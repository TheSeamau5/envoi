# examples/

Task prompts and environment harnesses for agent evaluation.

## Structure

```
examples/
  tasks/
    c_compiler/
      en.md              # System prompt sent to the agent
  environments/
    c_compiler/
      main.py            # envoi test harness (suites, setup)
      setup.sh           # Fixture installation (git clones test repos)
      Dockerfile         # (optional) custom sandbox image
      tests/
        basics.py        # Basic compilation tests
        wacct.py         # "Writing a C Compiler" test suite
        c_testsuite.py   # c-testsuite corpus
        torture.py       # GCC torture tests
        utils.py         # Shared test runner utilities
```

## Task Directory

A task directory tells the agent what to do. It needs at least one prompt file. Three tiers of complexity:

**Tier 1 — Static prompt**: Just a markdown file.

```
my_task/
  prompt.md    # or en.md for language-specific prompts
```

**Tier 2 — Prompt with parameters**: A prompt template plus a `params.py` that returns substitution values.

```
my_task/
  prompt.md     # contains {variable} placeholders
  params.py     # must export params() -> dict
```

**Tier 3 — Dynamic generation**: A `task.py` with a `generate()` function that returns `(prompt, params)`.

```
my_task/
  task.py       # must export generate() -> tuple[str, dict]
```

## Environment Directory

An environment directory defines what the agent is evaluated against. It must contain a `main.py` using the [envoi SDK](https://github.com/TheSeamau5/envoi.git).

The `main.py` defines:
- **Test suites** — Groups of tests the agent can run via the `run_tests` MCP tool
- **Setup function** — Called once when the agent submits code, typically runs a build step

Optional files:
- `setup.sh` — Runs at sandbox boot to install fixtures (test data, git repos, etc.)
- `Dockerfile` — Custom sandbox image if your environment needs specific system dependencies
- `tests/` — Python modules implementing individual test suites

### The c_compiler Environment

The included example evaluates an agent building a C compiler in Rust from scratch:

- **4 test suites**: `basics` (simple programs), `wacct` (20 chapters from "Writing a C Compiler"), `c_testsuite` (community C test corpus), `torture` (GCC torture tests)
- **setup.sh** clones three test repositories into `/opt/tests/`
- The agent must produce a `./cc` binary that compiles C to x86_64

## Writing Your Own Environment

1. Create a task directory with a prompt file
2. Create an environment directory with `main.py`
3. Define test suites using `envoi.suite()` and `@suite.test()`
4. Define a `@envoi.setup` function for build/install steps
5. Run with `envoi-trace --task your_task --env your_env`

The envoi server runs on `localhost:8000` inside the sandbox. Agents access it through the MCP `run_tests` tool — they never hit the HTTP API directly.
