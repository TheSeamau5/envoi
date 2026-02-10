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
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import envoi
from openai import AsyncOpenAI

from display import (
    console,
    print_build_failure,
    print_final_summary,
    print_iteration_header,
    print_llm_summary,
    print_main_prompt,
    print_summary,
    print_tier_results,
    stream_llm_response,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _parse_ramp_counts(raw: str) -> list[int]:
    values: list[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as error:
            raise RuntimeError(
                f"Invalid TIER_RAMP_COUNTS value: {token!r}. Expected comma-separated integers."
            ) from error
        if value < 0:
            raise RuntimeError("TIER_RAMP_COUNTS values must be >= 0.")
        values.append(value)

    if not values:
        values = [0]
    if values[-1] != 0:
        values.append(0)
    return values


ENVOI_URL = os.environ.get("ENVOI_URL", "http://localhost:8000")
MAX_ITERATIONS = 10
MODEL = os.environ.get("AI_MODEL", "gpt-5.2-codex")
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "low")
VERBOSITY = os.environ.get("OPENAI_VERBOSITY", "medium")
PRINT_MAIN_PROMPT = os.environ.get("PRINT_MAIN_PROMPT", "1") != "0"
PROMPT_PRINT_MAX_CHARS = int(os.environ.get("PROMPT_PRINT_MAX_CHARS", "12000"))
MODEL_MAX_OUTPUT_TOKENS = int(os.environ.get("MODEL_MAX_OUTPUT_TOKENS", "128000"))
TIER_TEST_TIMEOUT_SECONDS = int(os.environ.get("TIER_TEST_TIMEOUT_SECONDS", "30"))
TIER_RAMP_COUNTS = _parse_ramp_counts(os.environ.get("TIER_RAMP_COUNTS", "8,24,0"))
TARGET_TEST_NAME = os.environ.get("TARGET_TEST_NAME", "").strip()
TARGET_TEST_SUITE = os.environ.get("TARGET_TEST_SUITE", "").strip()

TIER_ORDER = ["basics", "wacct", "c_testsuite", "torture_execute"]
SESSION_TIMEOUT_SECONDS = int(
    os.environ.get(
        "SESSION_TIMEOUT_SECONDS",
        str(max(600, TIER_TEST_TIMEOUT_SECONDS * len(TIER_ORDER) + 120)),
    )
)
PROMPT_LOG_DIR = Path(__file__).resolve().parent / "prompt_logs"
BEST_SUBMISSION_DIR = Path(__file__).resolve().parent / "best_submission"

BUILD_SH = """\
#!/bin/bash
set -e
cargo build --release
cp target/release/c_compiler ./cc
"""

SYSTEM_PROMPT = """
You are building and improving a C compiler in Rust. Target CLI:
  ./cc input.c -o output

Goal:
- Progressively improve toward broader C support based on failing tests.
- Make high-leverage fixes based on failing tests.
- Keep package name `c_compiler`.
- Do not emit build.sh (the controller injects it).

Non-negotiable compiler contract:
- `-o <path>` must produce a runnable native executable, not a placeholder file.
- Do not "touch" / write empty output files to satisfy `-o`.
- If compilation fails, return non-zero and emit diagnostics; do not fake success.
- Baseline milestone is end-to-end compile+link that can run tiny programs.

Runtime/diagnostics expectations:
- Capture tool stderr from assembler/linker phases.
- Avoid panic!()/unwrap() in user-facing paths.
- Emit phase-tagged diagnostics where possible.
- Write rich debug internals into ./debug_artifacts/ (AST/IR/ASM/traces/backtraces, etc.).
- The evaluator clears ./debug_artifacts/ per test case and captures files on failures.

Response protocol (JSON only):
- Return one JSON object where keys are file paths relative to project root and values are full file contents.
- Return the complete project each iteration (not only changed files).
- Required files: Cargo.toml and src/main.rs.
- Do NOT include build.sh.
- Do NOT include markdown or commentary.
""".strip()

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def build_initial_prompt() -> str:
    return (
        "Generate the complete Rust project for the compiler. "
        "Return only a JSON object mapping file paths to full file contents. "
        "At minimum include Cargo.toml and src/main.rs."
    )


def format_source_tree_for_prompt(files: dict[str, str]) -> str:
    sections: list[str] = []
    for file_path in sorted(files.keys()):
        content = files[file_path]
        line_count = content.count("\n")
        if content and not content.endswith("\n"):
            line_count += 1
        sections.append(
            "\n".join(
                [
                    f"--- file: {file_path} ({len(content)} chars, {line_count} lines) ---",
                    content,
                ]
            )
        )
    return "\n\n".join(sections)


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f}MB"
    if value >= 1024:
        return f"{value / 1024:.1f}KB"
    return f"{value}B"


def summarize_debug_artifacts(artifacts: Any, *, max_files: int = 4) -> str:
    if not isinstance(artifacts, list):
        return ""

    valid = [a for a in artifacts if isinstance(a, dict)]
    if not valid:
        return ""

    total_bytes = sum(a["size_bytes"] for a in valid if isinstance(a.get("size_bytes"), int))
    file_parts: list[str] = []
    for artifact in valid[:max_files]:
        path = artifact.get("path", "unknown")
        kind = artifact.get("kind", "unknown")
        size = artifact.get("size_bytes") if isinstance(artifact.get("size_bytes"), int) else None
        file_parts.append(f"{kind}:{path}({_fmt_bytes(size)})")

    if len(valid) > max_files:
        file_parts.append(f"+{len(valid) - max_files} more")

    return (
        f"debug_artifacts: {len(valid)} file(s), total {_fmt_bytes(total_bytes)}"
        + (f" | {'; '.join(file_parts)}" if file_parts else "")
    )


def format_failures_for_prompt(
    failures: list[dict[str, Any]],
    *,
    max_examples: int = 12,
    max_source_lines: int = 30,
    max_stderr_lines: int = 5,
) -> str:
    if not failures:
        return "No failures."

    parts = [f"{len(failures)} failing tests:\n"]
    examples = failures[:max_examples]
    permission_denied_hint_added = False
    for ex in examples:
        name = ex.get("test_name", "unknown")
        phase = ex.get("phase", "unknown")
        parts.append(f"=== {name} ({phase}) ===")

        source = ex.get("c_source", "")
        if source:
            lines = source.strip().splitlines()
            if len(lines) > max_source_lines:
                lines = lines[:max_source_lines] + [f"... ({len(lines) - max_source_lines} more lines)"]
            parts.append("```c")
            parts.append("\n".join(lines))
            parts.append("```")

        expected = ex.get("expected_stdout", "")
        actual = ex.get("actual_stdout", "")
        if expected != actual:
            parts.append(f"expected stdout: {expected!r}")
            parts.append(f"actual stdout:   {actual!r}")

        stderr = (ex.get("stderr") or "").strip()
        if stderr:
            stderr_lines = stderr.splitlines()
            if len(stderr_lines) > max_stderr_lines:
                stderr_lines = stderr_lines[:max_stderr_lines] + ["..."]
            parts.append("stderr: " + "\n  ".join(stderr_lines))
            if (
                not permission_denied_hint_added
                and ex.get("actual_exit_code", 0) == 126
                and "permission denied" in stderr.lower()
            ):
                parts.append(
                    "Likely cause: compiler wrote a non-executable output file. "
                    "Fix codegen+assemble+link so `-o` is a runnable binary."
                )
                permission_denied_hint_added = True

        artifact_summary = summarize_debug_artifacts(ex.get("debug_artifacts", []))
        if artifact_summary:
            parts.append(artifact_summary)
        parts.append("")

    if len(failures) > max_examples:
        parts.append(f"... and {len(failures) - max_examples} more failing tests")

    return "\n".join(parts)


def build_iteration_prompt(
    previous_files: dict[str, str],
    current_tier: str,
    failures: list[dict[str, Any]],
) -> str:
    return "\n".join(
        [
            f"Previous source tree:\n{format_source_tree_for_prompt(previous_files)}",
            f"\nFailing tier: {current_tier}\n{format_failures_for_prompt(failures)}",
            "\nFix the compiler and return the COMPLETE updated project as JSON {path: content}.",
        ]
    )

# ---------------------------------------------------------------------------
# IO + API helpers
# ---------------------------------------------------------------------------


def normalize_files(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise RuntimeError("Model output must be a JSON object of {path: content}.")

    files: dict[str, str] = {}
    for file_path, content in payload.items():
        if not isinstance(file_path, str):
            continue
        files[file_path] = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

    if not files:
        raise RuntimeError("Model returned no files.")

    required = {"Cargo.toml", "src/main.rs"}
    missing = sorted(required - set(files.keys()))
    if missing:
        raise RuntimeError(f"Model response missing required files: {', '.join(missing)}")

    return files


def _phase_slug(phase: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", phase.strip().lower())
    return slug.strip("-") or "phase"


def save_prompt_snapshot(iteration: int, phase: str, prompt: str) -> Path:
    PROMPT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = PROMPT_LOG_DIR / f"iter_{iteration:02d}_{_phase_slug(phase)}.txt"
    path.write_text(prompt, encoding="utf-8")
    return path


def save_request_snapshot(iteration: int, phase: str, request: dict[str, Any]) -> Path:
    PROMPT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = PROMPT_LOG_DIR / f"iter_{iteration:02d}_{_phase_slug(phase)}.request.json"
    path.write_text(json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def build_llm_request(prompt: str) -> dict[str, Any]:
    return {
        "model": MODEL,
        "instructions": SYSTEM_PROMPT,
        "input": [{"role": "user", "content": prompt}],
        "max_output_tokens": MODEL_MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": REASONING_EFFORT, "summary": "auto"},
        "text": {"format": {"type": "json_object"}, "verbosity": VERBOSITY},
        "stream": True,
    }


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
    if BEST_SUBMISSION_DIR.exists():
        shutil.rmtree(BEST_SUBMISSION_DIR)
    BEST_SUBMISSION_DIR.mkdir(parents=True)
    write_submission(files, BEST_SUBMISSION_DIR)
    console.print(f"[bold cyan]Saved best submission to:[/bold cyan] {BEST_SUBMISSION_DIR}")


async def call_llm(
    client: AsyncOpenAI,
    request: dict[str, Any],
    phase: str,
) -> dict[str, str]:
    started_at = time.monotonic()
    stream = await client.responses.create(**request)
    output_text = await stream_llm_response(stream, phase)
    elapsed = time.monotonic() - started_at

    if not output_text:
        raise RuntimeError("Model returned an empty response.")

    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Model did not return valid JSON: {error}") from error

    files = normalize_files(payload)
    print_llm_summary(elapsed, files)
    return files


async def run_tiers(
    session: Any,
    focus_tier_idx: int,
    tier_attempts: dict[str, int],
) -> tuple[int, int, int | None, list[dict[str, Any]]]:
    total_passed = 0
    total_tests = 0
    target_suite = ""
    if TARGET_TEST_NAME:
        target_suite = TARGET_TEST_SUITE or TIER_ORDER[focus_tier_idx]

    def tier_test_kwargs(tier_name: str, tier_idx: int) -> tuple[dict[str, Any], bool]:
        # Optional direct targeting mode for one specific test case.
        if TARGET_TEST_NAME:
            if tier_name == target_suite:
                return {"test_name": TARGET_TEST_NAME}, False

        # For already-passed lower tiers, always run full to catch regressions.
        if tier_idx < focus_tier_idx:
            return {}, False

        attempt = tier_attempts.get(tier_name, 0)
        ramp_index = min(attempt, len(TIER_RAMP_COUNTS) - 1)
        n_tests = TIER_RAMP_COUNTS[ramp_index]
        if n_tests > 0:
            return {"n_tests": n_tests}, True
        return {}, False

    def kwargs_label(test_kwargs: dict[str, Any]) -> str:
        if not test_kwargs:
            return "full suite"
        return ", ".join(f"{k}={v!r}" for k, v in test_kwargs.items())

    # Run all tiers in-order for this submission. Stop at first failure.
    for idx, tier_name in enumerate(TIER_ORDER):
        if target_suite and tier_name != target_suite:
            continue

        while True:
            test_kwargs, sampled_run = tier_test_kwargs(tier_name, idx)
            tier_attempts[tier_name] = tier_attempts.get(tier_name, 0) + 1

            console.print(
                f"[cyan]Running tier {tier_name}...[/cyan] "
                f"[dim]({kwargs_label(test_kwargs)}, timeout: {TIER_TEST_TIMEOUT_SECONDS}s)[/dim]"
            )
            tier_started_at = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    session.test(tier_name, **test_kwargs),
                    timeout=TIER_TEST_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - tier_started_at
                console.print(
                    f"[red]Tier {tier_name} timed out after {elapsed:.1f}s[/red]"
                )
                return total_passed, total_tests, idx, [
                    {
                        "test_name": f"{tier_name}_timeout",
                        "phase": "test_timeout",
                        "c_source": "",
                        "expected_stdout": "",
                        "actual_stdout": "",
                        "expected_exit_code": 0,
                        "actual_exit_code": 1,
                        "stderr": (
                            f"tier `{tier_name}` timed out after "
                            f"{TIER_TEST_TIMEOUT_SECONDS}s"
                        ),
                        "debug_artifacts": [],
                    }
                ]
            except Exception as error:
                elapsed = time.monotonic() - tier_started_at
                console.print(
                    f"[red]Tier {tier_name} failed after {elapsed:.1f}s:[/red] {error}"
                )
                return total_passed, total_tests, idx, [
                    {
                        "test_name": f"{tier_name}_error",
                        "phase": "test_error",
                        "c_source": "",
                        "expected_stdout": "",
                        "actual_stdout": "",
                        "expected_exit_code": 0,
                        "actual_exit_code": 1,
                        "stderr": f"tier `{tier_name}` raised error: {error}",
                        "debug_artifacts": [],
                    }
                ]

            elapsed = time.monotonic() - tier_started_at
            console.print(
                f"[dim]Tier {tier_name} completed in {elapsed:.1f}s[/dim]"
            )
            cases = result.get("cases", []) if isinstance(result, dict) else []

            passed = sum(1 for case in cases if case.get("passed"))
            total_passed += passed
            total_tests += len(cases)

            print_tier_results(tier_name, cases, is_focus=(idx == focus_tier_idx))

            failures: list[dict[str, Any]] = []
            for case in cases:
                if case.get("passed"):
                    continue
                failures.append(
                    {
                        "test_name": case.get("name", "unknown"),
                        "phase": case.get("phase", "unknown"),
                        "c_source": case.get("c_source", ""),
                        "expected_stdout": case.get("expected_stdout", ""),
                        "actual_stdout": case.get("actual_stdout", ""),
                        "expected_exit_code": case.get("expected_exit_code", 0),
                        "actual_exit_code": case.get("actual_exit_code", 0),
                        "stderr": case.get("stderr", ""),
                        "debug_artifacts": case.get("debug_artifacts", []),
                    }
                )
            if failures:
                return total_passed, total_tests, idx, failures

            if not sampled_run:
                break

            console.print(
                f"[dim]Tier {tier_name} sampled run passed; running larger slice now.[/dim]"
            )

    return total_passed, total_tests, None, []

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
    console.print(
        f"[dim]Tier timeout: {TIER_TEST_TIMEOUT_SECONDS}s | "
        f"Session timeout: {SESSION_TIMEOUT_SECONDS}s[/dim]"
    )

    previous_files: dict[str, str] | None = None
    best_passed = -1
    current_tier_idx = 0
    last_failures: list[dict[str, Any]] = []
    tier_attempts: dict[str, int] = {tier: 0 for tier in TIER_ORDER}

    if TARGET_TEST_SUITE and TARGET_TEST_SUITE not in TIER_ORDER:
        raise RuntimeError(
            f"TARGET_TEST_SUITE must be one of {TIER_ORDER}, got {TARGET_TEST_SUITE!r}"
        )

    if TARGET_TEST_NAME:
        if TARGET_TEST_SUITE:
            console.print(
                f"[yellow]Targeted mode:[/yellow] suite={TARGET_TEST_SUITE}, test_name={TARGET_TEST_NAME}"
            )
        else:
            console.print(
                f"[yellow]Targeted mode:[/yellow] focus-tier test_name={TARGET_TEST_NAME}"
            )
    console.print(
        f"[dim]Tier ramp counts (n_tests): {TIER_RAMP_COUNTS} "
        "(0 means full suite)[/dim]"
    )

    async with AsyncOpenAI(api_key=api_key) as llm_client:
        for iteration in range(1, MAX_ITERATIONS + 1):
            print_iteration_header(iteration, MAX_ITERATIONS)

            if previous_files is None:
                phase = "Generating initial compiler"
                prompt = build_initial_prompt()
            else:
                phase = f"Improving compiler (tier: {TIER_ORDER[current_tier_idx]})"
                prompt = build_iteration_prompt(
                    previous_files,
                    TIER_ORDER[current_tier_idx],
                    last_failures,
                )

            request = build_llm_request(prompt)
            prompt_path = save_prompt_snapshot(iteration, phase, prompt)
            request_path = save_request_snapshot(iteration, phase, request)

            if PRINT_MAIN_PROMPT:
                print_main_prompt(
                    phase=phase,
                    prompt=prompt,
                    system_prompt=SYSTEM_PROMPT,
                    prompt_path=prompt_path,
                    request_path=request_path,
                    max_chars=PROMPT_PRINT_MAX_CHARS,
                )
            else:
                console.print(
                    f"[dim]Prompt saved to {prompt_path} | request saved to {request_path}[/dim]"
                )

            try:
                files = await call_llm(llm_client, request, phase)
            except Exception as error:
                console.print(f"[red]LLM call failed:[/red] {error}")
                break

            previous_files = files
            console.print(f"Generated {len(files)} files")

            total_passed = 0
            total_tests = 0
            failed_tier_idx: int | None = None
            failed_cases: list[dict[str, Any]] = []
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
                        session_timeout_seconds=SESSION_TIMEOUT_SECONDS,
                        submission=envoi.Documents(tmp_path),
                    ) as session:
                        (
                            total_passed,
                            total_tests,
                            failed_tier_idx,
                            failed_cases,
                        ) = await run_tiers(
                            session,
                            current_tier_idx,
                            tier_attempts,
                        )
                except Exception as error:
                    print_build_failure(error)
                    build_failed = True
                    current_tier_idx = 0
                    last_failures = [
                        {
                            "test_name": "build",
                            "phase": "build",
                            "c_source": "",
                            "expected_stdout": "",
                            "actual_stdout": "",
                            "expected_exit_code": 0,
                            "actual_exit_code": 1,
                            "stderr": str(error),
                            "debug_artifacts": [],
                        }
                    ]

            if build_failed:
                continue

            is_new_best = total_passed > best_passed
            if is_new_best:
                best_passed = total_passed
                save_best_submission(files)

            print_summary(total_passed, total_tests, is_new_best)

            if failed_tier_idx is None:
                last_failures = []
                if TARGET_TEST_NAME:
                    console.print("[bold green]Targeted test passed![/bold green]")
                    break
                console.print("[bold green]All tiers passed![/bold green]")
                break

            if failed_tier_idx < current_tier_idx:
                console.print(
                    f"[red bold]Lower-tier regression in {TIER_ORDER[failed_tier_idx]}. "
                    f"Dropping back from {TIER_ORDER[current_tier_idx]}.[/red bold]"
                )

            current_tier_idx = failed_tier_idx
            last_failures = failed_cases

    print_final_summary(max(best_passed, 0))


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
