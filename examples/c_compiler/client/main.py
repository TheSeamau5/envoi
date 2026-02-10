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
from collections import defaultdict
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

ENVOI_URL = os.environ.get("ENVOI_URL", "http://localhost:8000")
MAX_ITERATIONS = 10
MODEL = os.environ.get("AI_MODEL", "gpt-5.2-codex")
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "low")
VERBOSITY = os.environ.get("OPENAI_VERBOSITY", "medium")
DEBUG_SUMMARY_ENABLED = os.environ.get("DEBUG_SUMMARY_ENABLED", "1") != "0"
DEBUG_SUMMARY_MODEL = os.environ.get("DEBUG_SUMMARY_MODEL", MODEL)
DEBUG_SUMMARY_REASONING_EFFORT = os.environ.get(
    "DEBUG_SUMMARY_REASONING_EFFORT", "low"
)
DEBUG_SUMMARY_CHUNK_CHARS = int(os.environ.get("DEBUG_SUMMARY_CHUNK_CHARS", "12000"))
DEBUG_SUMMARY_REDUCE_CHARS = int(os.environ.get("DEBUG_SUMMARY_REDUCE_CHARS", "24000"))
PRINT_MAIN_PROMPT = os.environ.get("PRINT_MAIN_PROMPT", "1") != "0"
PROMPT_PRINT_MAX_CHARS = int(os.environ.get("PROMPT_PRINT_MAX_CHARS", "12000"))

TIER_ORDER = ["basics", "wacct", "c_testsuite", "torture"]
REGRESSION_STATE_PATH = Path(__file__).resolve().parent / "regression_state.json"
PROMPT_LOG_DIR = Path(__file__).resolve().parent / "prompt_logs"
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

Error handling:
- Never use panic!() or unwrap() for user-facing errors. Use Result types.
- On invalid input, print a structured error to stderr and exit with code 1:
    error: unexpected token `while` at line 5, col 3
- On unsupported features, emit a clear message and exit 1, do not crash.

Diagnostics on failure:
- Use Command::output() (not status()) for `as` and `gcc` so you capture their stderr.
- If the assembler fails, print its stderr. Do NOT dump the full assembly — it can be huge.
- Tag each error with the compilation phase: "error[parse]:", "error[codegen]:", "error[assemble]:", "error[link]:".
- Also write rich debugging artifacts into ./debug_artifacts/ with no extra CLI flags.
- You may create any filenames in ./debug_artifacts/, but include useful internals
  such as AST, IR, emitted assembly, command traces, or panic/backtrace details.
- The evaluator clears ./debug_artifacts/ before each test case, then captures what you write.

Output format: JSON object where keys are file paths relative to project root and values are full file contents.
Required files: Cargo.toml (package name "c_compiler"), src/main.rs, and any additional src/*.rs files.
Do NOT include build.sh. Do NOT explain or plan. Produce ONLY the JSON object.

When fixing failures:
- Failures are grouped by root cause. Fix the largest group first.
- Each group shows representative C source code that triggers the bug.
- REGRESSIONS are tests that previously passed but now fail. Fix these first.
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
                "c_source": case.get("c_source", ""),
                "expected_stdout": case.get("expected_stdout", ""),
                "actual_stdout": case.get("actual_stdout", ""),
                "expected_exit_code": case.get("expected_exit_code", 0),
                "actual_exit_code": case.get("actual_exit_code", 0),
                "stderr": case.get("stderr", ""),
                "debug_artifacts": case.get("debug_artifacts", []),
            }
        )
    return failures


def extract_failure_signature(case: dict[str, Any]) -> str:
    """Derive a clustering key from a failing test case."""
    phase = case.get("phase", "unknown")
    stderr = (case.get("stderr") or "").strip()

    if phase == "compile":
        first_line = stderr.split("\n")[0] if stderr else "compilation failed"
        # Normalize file paths and line numbers so identical panics cluster.
        first_line = re.sub(r"['\"](/?[\w./]+\.rs):(\d+)(:\d+)?['\"]?", "<location>", first_line)
        first_line = re.sub(r"at .*?:\d+(:\d+)?", "at <location>", first_line)
        return f"compile: {first_line}"

    if phase == "verify":
        actual_exit = case.get("actual_exit_code", 0)
        expected_exit = case.get("expected_exit_code", 0)
        if actual_exit != expected_exit:
            if actual_exit in (139, -11):
                return "verify: segfault (exit 139)"
            if actual_exit in (134, -6):
                return "verify: abort (exit 134)"
            return f"verify: wrong exit code (expected {expected_exit}, got {actual_exit})"
        return "verify: stdout mismatch"

    return f"{phase}: unknown"


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f}MB"
    if value >= 1024:
        return f"{value / 1024:.1f}KB"
    return f"{value}B"


def _debug_artifacts_summary_for_prompt(
    debug_artifacts: Any,
    *,
    max_files: int = 4,
    max_snippet_lines: int = 8,
) -> str:
    if not isinstance(debug_artifacts, list) or not debug_artifacts:
        return ""

    valid_artifacts = [a for a in debug_artifacts if isinstance(a, dict)]
    if not valid_artifacts:
        return ""

    total_bytes = sum(
        a["size_bytes"]
        for a in valid_artifacts
        if isinstance(a.get("size_bytes"), int)
    )
    lines = [
        f"debug_artifacts: {len(valid_artifacts)} file(s), total {_fmt_bytes(total_bytes)}"
    ]

    snippets: list[str] = []
    interesting_pattern = re.compile(
        r"(error|panic|trace|assert|unexpected|expected|token|stack|backtrace|phase|ast|ir|asm)",
        re.IGNORECASE,
    )

    for artifact in valid_artifacts[:max_files]:
        path = artifact.get("path", "unknown")
        kind = artifact.get("kind", "unknown")
        size_bytes = artifact.get("size_bytes")
        sha256 = artifact.get("sha256")
        chunks = artifact.get("text_chunks")
        chunk_count = len(chunks) if isinstance(chunks, list) else 0
        sha_short = sha256[:12] if isinstance(sha256, str) else "unknown"
        lines.append(
            f"- {kind}: {path} ({_fmt_bytes(size_bytes if isinstance(size_bytes, int) else None)}, chunks={chunk_count}, sha256={sha_short})"
        )

        if not isinstance(chunks, list):
            continue

        for chunk in chunks:
            if not isinstance(chunk, str):
                continue
            for raw_line in chunk.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if interesting_pattern.search(line):
                    snippets.append(line[:220])
                if len(snippets) >= max_snippet_lines:
                    break
            if len(snippets) >= max_snippet_lines:
                break
        if len(snippets) >= max_snippet_lines:
            break

    if snippets:
        lines.append("artifact snippets:")
        for snippet in snippets:
            lines.append(f"  {snippet}")

    return "\n".join(lines)


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


def cluster_failures(
    failures: list[dict[str, Any]],
    max_examples: int = 2,
    max_source_lines: int = 30,
    max_clusters: int = 15,
    max_stderr_lines: int = 5,
) -> str:
    """Group failures by root cause and format a compact summary for the LLM."""
    if not failures:
        return "No failures."

    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in failures:
        sig = extract_failure_signature(f)
        clusters[sig].append(f)

    # Sort largest clusters first.
    sorted_clusters = sorted(clusters.items(), key=lambda kv: len(kv[1]), reverse=True)

    parts = [f"{len(failures)} failures in {len(sorted_clusters)} categories:\n"]

    for i, (sig, cases) in enumerate(sorted_clusters):
        if i >= max_clusters:
            remaining = sum(len(c) for _, c in sorted_clusters[i:])
            parts.append(f"... and {len(sorted_clusters) - i} more categories ({remaining} failures)")
            break

        parts.append(f"=== {sig} ({len(cases)} failures) ===")

        # Pick shortest source as representative examples.
        by_length = sorted(cases, key=lambda c: len(c.get("c_source", "")))
        examples = by_length[:max_examples]

        for ex in examples:
            name = ex.get("test_name", "unknown")
            source = ex.get("c_source", "")
            stderr = (ex.get("stderr") or "").strip()

            parts.append(f"-- {name} --")
            if source:
                lines = source.strip().splitlines()
                if len(lines) > max_source_lines:
                    lines = lines[:max_source_lines] + [f"... ({len(lines) - max_source_lines} more lines)"]
                parts.append("```c")
                parts.append("\n".join(lines))
                parts.append("```")

            if ex.get("phase") == "verify":
                expected = ex.get("expected_stdout", "")
                actual = ex.get("actual_stdout", "")
                if expected != actual:
                    parts.append(f"expected stdout: {expected!r}")
                    parts.append(f"actual stdout:   {actual!r}")

            if stderr:
                stderr_lines = stderr.splitlines()
                if len(stderr_lines) > max_stderr_lines:
                    stderr_lines = stderr_lines[:max_stderr_lines] + ["..."]
                parts.append("stderr: " + "\n  ".join(stderr_lines))

            artifact_summary = _debug_artifacts_summary_for_prompt(
                ex.get("debug_artifacts", [])
            )
            if artifact_summary:
                parts.append(artifact_summary)

        # List remaining test names.
        remaining = by_length[max_examples:]
        if remaining:
            names = [c.get("test_name", "?") for c in remaining]
            if len(names) > 10:
                shown = ", ".join(names[:10])
                parts.append(f"Other failing tests: {shown}, ... ({len(names) - 10} more)")
            else:
                parts.append(f"Other failing tests: {', '.join(names)}")

        parts.append("")  # blank line between clusters

    return "\n".join(parts)


def build_iteration_prompt(
    previous_files: dict[str, str],
    current_tier: str,
    current_tier_failures: list[dict[str, Any]],
    regressions: list[dict[str, Any]],
) -> str:
    parts = [
        f"Previous source tree:\n{format_source_tree_for_prompt(previous_files)}",
        f"\nFailing tier: {current_tier}\n{cluster_failures(current_tier_failures)}",
    ]
    if regressions:
        parts.append(
            f"\nREGRESSIONS (previously passing tests that now FAIL — fix these first):\n{cluster_failures(regressions, max_examples=3)}"
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


def _phase_slug(phase: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", phase.strip().lower())
    slug = slug.strip("-")
    return slug or "phase"


def save_prompt_snapshot(iteration: int, phase: str, prompt: str) -> Path:
    PROMPT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    prompt_path = PROMPT_LOG_DIR / f"iter_{iteration:02d}_{_phase_slug(phase)}.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def save_request_snapshot(iteration: int, phase: str, request: dict[str, Any]) -> Path:
    PROMPT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    request_path = PROMPT_LOG_DIR / f"iter_{iteration:02d}_{_phase_slug(phase)}.request.json"
    request_path.write_text(
        json.dumps(request, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return request_path


def build_llm_request(prompt: str) -> dict[str, Any]:
    return {
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
                "c_source": case.get("c_source", ""),
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
    request: dict[str, Any],
    phase: str = "Generating",
) -> dict[str, str]:
    """Call the model and return parsed project files."""
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

    regression_state: dict[str, Any] = {}
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
                files = await call_llm(llm_client, request, phase=phase)
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
                        tier_idx = 0
                        while tier_idx <= current_tier_idx:
                            tier_name = TIER_ORDER[tier_idx]
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

                            # Tier passed. Advance if this was the focus tier.
                            last_regressions.extend(tier_regressions)
                            if tier_idx == current_tier_idx and current_tier_idx < len(TIER_ORDER) - 1:
                                current_tier_idx += 1
                                console.print(
                                    f"[bold green]Tier {tier_name} passed! "
                                    f"Advancing to {TIER_ORDER[current_tier_idx]}.[/bold green]"
                                )

                            tier_idx += 1

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
