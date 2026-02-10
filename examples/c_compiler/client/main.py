"""
LLM-driven C compiler development loop.

Calls gpt-5.2-codex via the OpenAI Responses API to generate a Rust-based
C compiler, submits it to the envoi C compiler environment, reads structured
test results, and iterates.

Usage:
    export OPENAI_API_KEY="sk-..."
    uv run python main.py

The environment must be running:
    docker build -t envoi-c-compiler -f examples/c_compiler/author/Dockerfile .
    docker run --rm -p 8000:8000 envoi-c-compiler

Install deps in this directory with:
    uv sync
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import envoi
from display import (
    console,
    print_build_failure,
    print_final_summary,
    print_iteration_header,
    print_llm_summary,
    print_summary,
    print_tier_results,
    stream_llm_response,
)
from openai import AsyncOpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENVOI_URL = os.environ.get("ENVOI_URL", "http://localhost:8000")
MAX_ITERATIONS = 4
MODEL = os.environ.get("AI_MODEL", "gpt-5.2-codex")
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "low")
VERBOSITY = os.environ.get("OPENAI_VERBOSITY", "medium")

TIER_ORDER = ["basics", "wacct", "c_testsuite", "torture"]
REGRESSION_STATE_PATH = Path(__file__).resolve().parent / "regression_state.json"
BUILD_SH = """\
#!/bin/bash
set -e
cargo build --release
cp target/release/c_compiler ./cc
"""

SYSTEM_PROMPT = """
Build a C compiler in Rust. CLI: ./cc input.c -o output
Read C source, compile to x86_64 assembly, invoke `as` and `gcc` to assemble and link.

C subset to support:
- #include <stdio.h> (ignore, printf linked from libc)
- int type only
- int main() entry point
- Integer literals, arithmetic (+, -, *, /)
- Local variables: int x = 5; assignment: x = 10;
- if/else, while loops
- Comparisons: <, >, <=, >=, ==, !=
- Functions with int params and int return
- return statements
- printf("%d\\n", expr) and printf("string\\n")

Output format: JSON object where keys are file paths relative to project root and values are full file contents.
Required files: Cargo.toml (package name "c_compiler"), src/main.rs, and any additional src/*.rs files.
Do NOT include build.sh. Do NOT explain or plan. Produce ONLY the JSON object.
""".strip()

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def build_initial_prompt() -> str:
    return "Generate the complete Rust project. Return ONLY JSON, no markdown, no commentary."


def format_failures(test_results: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for case in test_results.get("cases", []):
        if case.get("passed"):
            continue
        failures.append(
            {
                "test_name": case.get("name", "unknown"),
                "phase": case.get("phase", "unknown"),
                "expected_stdout": case.get("expected_stdout", ""),
                "actual_stdout": case.get("actual_stdout", ""),
                "expected_exit_code": case.get("expected_exit_code", 0),
                "actual_exit_code": case.get("actual_exit_code", 0),
                "stderr": case.get("stderr", ""),
            }
        )
    return failures


def build_iteration_prompt(
    previous_files: dict[str, str],
    current_tier: str,
    current_tier_failures: list[dict[str, Any]],
    regressions: list[dict[str, Any]],
) -> str:
    parts = [
        f"Previous source tree:\n{json.dumps(previous_files, indent=2)}",
        f"\nFailing tier: {current_tier}\nFailed test cases ({len(current_tier_failures)}):\n{json.dumps(current_tier_failures, indent=2)}",
    ]
    if regressions:
        parts.append(
            f"\nREGRESSIONS (previously passing tests that now FAIL — fix these first):\n{json.dumps(regressions, indent=2)}"
        )
    parts.append(
        "\nFix the compiler. Return the COMPLETE updated project as JSON (all files, not only changed ones). ONLY JSON, no markdown, no commentary."
    )
    return "\n".join(parts)

# ---------------------------------------------------------------------------
# File handling
# ---------------------------------------------------------------------------


def normalize_files(payload: Any) -> dict[str, str]:
    if isinstance(payload, dict) and isinstance(payload.get("files"), dict):
        payload = payload["files"]

    if not isinstance(payload, dict):
        raise RuntimeError("Model output must be a JSON object of {path: content}.")

    normalized: dict[str, str] = {}
    for file_path, content in payload.items():
        if not isinstance(file_path, str):
            continue
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        normalized[file_path] = content

    if not normalized:
        raise RuntimeError("Model returned no files.")

    required = {"Cargo.toml", "src/main.rs"}
    missing = sorted(required - set(normalized.keys()))
    if missing:
        raise RuntimeError(f"Model response missing required files: {', '.join(missing)}")

    return normalized


def write_submission(files: dict[str, str], output_dir: Path) -> None:
    for relative_path, content in files.items():
        path_obj = Path(relative_path)
        if path_obj.is_absolute() or ".." in path_obj.parts:
            raise RuntimeError(f"Invalid file path from model: {relative_path}")

        full_path = output_dir / path_obj
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    build_sh_dest = output_dir / "build.sh"
    build_sh_dest.write_text(BUILD_SH, encoding="utf-8")
    build_sh_dest.chmod(0o755)


def save_best_submission(files: dict[str, str]) -> None:
    best_dir = Path(__file__).resolve().parent / "best_submission"
    if best_dir.exists():
        shutil.rmtree(best_dir)
    best_dir.mkdir(parents=True)
    write_submission(files, best_dir)
    console.print(f"[bold cyan]Saved best submission to:[/bold cyan] {best_dir}")

# ---------------------------------------------------------------------------
# Regression tracker
# ---------------------------------------------------------------------------


def load_regression_state() -> dict[str, Any]:
    """Load the persistent regression tracker from disk."""
    if REGRESSION_STATE_PATH.exists():
        try:
            return json.loads(REGRESSION_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_regression_state(state: dict[str, Any]) -> None:
    """Persist the regression tracker to disk."""
    REGRESSION_STATE_PATH.write_text(json.dumps(state, indent=2))


def detect_regressions(
    state: dict[str, Any], tier: str, cases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return cases that regressed (were passing, now failing) in this tier."""
    regressions: list[dict[str, Any]] = []
    for case in cases:
        name = case.get("name", "unknown")
        key = f"{tier}::{name}"
        prev = state.get(key)
        if prev and prev.get("status") == "pass" and not case.get("passed"):
            regressions.append({
                "tier": tier,
                "test_name": name,
                "phase": case.get("phase", "unknown"),
                "expected_stdout": case.get("expected_stdout", ""),
                "actual_stdout": case.get("actual_stdout", ""),
                "expected_exit_code": case.get("expected_exit_code", 0),
                "actual_exit_code": case.get("actual_exit_code", 0),
                "stderr": case.get("stderr", ""),
            })
    return regressions


def update_regression_state(
    state: dict[str, Any], tier: str, cases: list[dict[str, Any]], iteration: int
) -> None:
    """Update the regression state with results from a tier run."""
    for case in cases:
        name = case.get("name", "unknown")
        key = f"{tier}::{name}"
        new_status = "pass" if case.get("passed") else "fail"
        prev = state.get(key)
        if prev is None or prev.get("status") != new_status:
            state[key] = {"status": new_status, "tier": tier, "iteration": iteration}

# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


async def call_llm(
    client: AsyncOpenAI,
    prompt: str,
    phase: str = "Generating",
) -> dict[str, str]:
    """Call the model and return parsed project files."""
    request: dict[str, Any] = {
        "model": MODEL,
        "instructions": SYSTEM_PROMPT,
        "input": [{"role": "user", "content": prompt}],
        "reasoning": {
            "effort": REASONING_EFFORT,
            "summary": "auto",
        },
        "text": {
            "format": {"type": "json_object"},
            "verbosity": VERBOSITY,
        },
        "stream": True,
    }

    started_at = time.monotonic()
    stream = await client.responses.create(**request)
    output_text = await stream_llm_response(stream, phase)
    elapsed = time.monotonic() - started_at

    if not output_text:
        raise RuntimeError("Model returned an empty response.")

    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Model did not return valid JSON: {error}") from error

    files = normalize_files(parsed)
    print_llm_summary(elapsed, files)
    return files

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def run_loop() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set. "
            "Run: export OPENAI_API_KEY='sk-...'"
        )

    console.print(f"Connecting to envoi environment at {ENVOI_URL}...")
    console.print(f"Tier order: {' → '.join(TIER_ORDER)}")
    console.print(
        f"Model: {MODEL} | Reasoning effort: {REASONING_EFFORT} | "
        f"Verbosity: {VERBOSITY}"
    )

    regression_state = load_regression_state()
    previous_files: dict[str, str] | None = None
    best_passed = -1

    # Track the current tier we're trying to pass (index into TIER_ORDER).
    current_tier_idx = 0
    # Failures + regressions from the last test run, used to build the next prompt.
    last_tier_failures: list[dict[str, Any]] = []
    last_regressions: list[dict[str, Any]] = []

    async with AsyncOpenAI(api_key=api_key) as llm_client:
        for iteration in range(1, MAX_ITERATIONS + 1):
            print_iteration_header(iteration, MAX_ITERATIONS)

            # --- LLM call ---
            if previous_files is None:
                phase = "Generating initial compiler"
                prompt = build_initial_prompt()
            else:
                phase = f"Improving compiler (tier: {TIER_ORDER[current_tier_idx]})"
                prompt = build_iteration_prompt(
                    previous_files,
                    TIER_ORDER[current_tier_idx],
                    last_tier_failures,
                    last_regressions,
                )

            try:
                files = await call_llm(llm_client, prompt, phase=phase)
            except Exception as error:
                console.print(f"[red]LLM call failed:[/red] {error}")
                break

            previous_files = files
            console.print(f"Generated {len(files)} files")

            # --- Submit and run tiers ---
            total_passed = 0
            total_tests = 0
            last_tier_failures = []
            last_regressions = []
            build_failed = False

            with tempfile.TemporaryDirectory(prefix="envoi-cc-") as tmp_dir:
                tmp_path = Path(tmp_dir)
                try:
                    write_submission(files, tmp_path)
                except Exception as error:
                    console.print(f"[red]Could not write submission:[/red] {error}")
                    break

                console.print("Submitting project and creating session...")

                try:
                    async with await envoi.connect_session(
                        ENVOI_URL,
                        session_timeout_seconds=600,
                        submission=envoi.Documents(tmp_path),
                    ) as session:
                        for tier_idx, tier_name in enumerate(TIER_ORDER):
                            # Only run up to and including current focus tier.
                            if tier_idx > current_tier_idx:
                                break

                            result = await session.test(tier_name)
                            tier_cases = result.get("cases", []) if isinstance(result, dict) else []

                            # Detect regressions before updating state.
                            tier_regressions = detect_regressions(
                                regression_state, tier_name, tier_cases
                            )

                            # Update persistent state.
                            update_regression_state(
                                regression_state, tier_name, tier_cases, iteration
                            )

                            tier_passed = sum(1 for c in tier_cases if c.get("passed"))
                            tier_failed = len(tier_cases) - tier_passed
                            total_passed += tier_passed
                            total_tests += len(tier_cases)

                            is_focus = tier_idx == current_tier_idx
                            print_tier_results(
                                tier_name, tier_cases,
                                regressions=tier_regressions,
                                is_focus=is_focus,
                            )

                            # If a previously-passing lower tier regressed, drop back.
                            if tier_regressions and tier_idx < current_tier_idx:
                                console.print(
                                    f"[red bold]Regression in tier {tier_name}! "
                                    f"Dropping back from {TIER_ORDER[current_tier_idx]}.[/red bold]"
                                )
                                current_tier_idx = tier_idx
                                last_regressions = tier_regressions
                                last_tier_failures = format_failures(result)
                                break

                            # If current tier has failures, stay here.
                            if tier_failed > 0:
                                last_tier_failures = format_failures(result)
                                last_regressions.extend(tier_regressions)
                                break

                            # Tier fully passed — collect any regressions for
                            # bookkeeping (shouldn't happen if tier passed, but
                            # just in case) and continue to next.
                            last_regressions.extend(tier_regressions)

                        else:
                            # All tiers up to current_tier_idx passed.
                            # Advance to next tier if available.
                            if current_tier_idx < len(TIER_ORDER) - 1:
                                current_tier_idx += 1
                                console.print(
                                    f"[bold green]Tier {TIER_ORDER[current_tier_idx - 1]} passed! "
                                    f"Advancing to {TIER_ORDER[current_tier_idx]}.[/bold green]"
                                )

                except Exception as error:
                    print_build_failure(error)
                    # Build failure counts as basics failure.
                    last_tier_failures = [{
                        "test_name": "build",
                        "phase": "build",
                        "expected_stdout": "",
                        "actual_stdout": "",
                        "expected_exit_code": 0,
                        "actual_exit_code": 1,
                        "stderr": str(error),
                    }]
                    current_tier_idx = 0
                    build_failed = True

            save_regression_state(regression_state)

            if not build_failed:
                is_new_best = total_passed > best_passed
                if is_new_best:
                    best_passed = total_passed
                    save_best_submission(files)

                print_summary(total_passed, total_tests, is_new_best)

                if not last_tier_failures and current_tier_idx == len(TIER_ORDER) - 1:
                    console.print("[bold green]All tiers passed![/bold green]")
                    break

    print_final_summary(max(best_passed, 0))


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
