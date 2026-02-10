"""
LLM-driven C compiler development loop.

Calls gpt-5.2-codex via the OpenAI Responses API to generate a Rust-based
C compiler, submits it to the envoi C compiler environment, reads structured
test results, and iterates.

Usage:
    export OPENAI_API_KEY="sk-..."
    uv run python run.py

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
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

ENVOI_URL = os.environ.get("ENVOI_URL", "http://localhost:8000")
MAX_ITERATIONS = 4
MODEL = os.environ.get("AI_MODEL", "gpt-5.2-codex")
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "low")
VERBOSITY = os.environ.get("OPENAI_VERBOSITY", "medium")

console = Console()
FILE_KEY_PATTERN = re.compile(r'"((?:[^"\\]|\\.)+)"\s*:')

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


def _print(message: str = "") -> None:
    console.print(message)


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


def build_iteration_prompt(previous_files: dict[str, str], test_results: dict[str, Any]) -> str:
    failures = format_failures(test_results)
    return f"""Previous source tree:
{json.dumps(previous_files, indent=2)}

Failed test cases:
{json.dumps(failures, indent=2)}

Fix the compiler. Return the COMPLETE updated project as JSON (all files, not only changed ones). ONLY JSON, no markdown, no commentary."""


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


def looks_like_project_file(path: str) -> bool:
    if path == "files":
        return False
    if path in {"Cargo.toml", "build.sh", "README.md"}:
        return True
    if path.startswith("src/"):
        return True

    known_suffixes = (".rs", ".toml", ".sh", ".md", ".txt", ".json", ".lock")
    if path.endswith(known_suffixes):
        return True

    return "/" in path and "." in Path(path).name


def discover_stream_file_keys(raw_text: str, seen: set[str]) -> list[str]:
    discovered: list[str] = []
    for match in FILE_KEY_PATTERN.finditer(raw_text):
        raw_key = match.group(1)
        try:
            key = json.loads(f'"{raw_key}"')
        except json.JSONDecodeError:
            continue

        if not isinstance(key, str):
            continue
        if key in seen:
            continue
        if not looks_like_project_file(key):
            continue

        seen.add(key)
        discovered.append(key)

    return discovered


class LLMResult:
    """Holds parsed files and the response ID for conversation chaining."""

    def __init__(self, files: dict[str, str], response_id: str) -> None:
        self.files = files
        self.response_id = response_id


async def call_llm(
    client: AsyncOpenAI,
    prompt: str,
    phase: str = "Generating",
    previous_response_id: str | None = None,
) -> LLMResult:
    """Call gpt-5.2-codex via Responses API and stream reasoning + output progress."""
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
    if previous_response_id is not None:
        request["previous_response_id"] = previous_response_id

    stream = await client.responses.create(**request)

    output_text = ""
    seen_files: set[str] = set()
    discovered_files: list[str] = []
    current_loading_file: str | None = None
    started_at = time.monotonic()
    reasoning_seen = False
    reasoning_chunk_buffer = ""
    output_started = False
    last_status_update_at = started_at

    def maybe_update_status(status: Any, label: str, *, force: bool = False) -> None:
        nonlocal last_status_update_at
        now = time.monotonic()
        if force or (now - last_status_update_at) >= 0.25:
            status.update(f"[cyan]{label}[/cyan] [dim]{now - started_at:.1f}s[/dim]")
            last_status_update_at = now

    def flush_reasoning_buffer(*, force: bool = False) -> None:
        nonlocal reasoning_chunk_buffer
        if force and reasoning_chunk_buffer:
            console.print(reasoning_chunk_buffer, style="dim", markup=False)
            reasoning_chunk_buffer = ""
            return

        while "\n" in reasoning_chunk_buffer:
            line, reasoning_chunk_buffer = reasoning_chunk_buffer.split("\n", 1)
            console.print(line, style="dim", markup=False)

    response_obj = None

    console.print(f"[bold cyan]{phase}[/bold cyan]")
    console.print("[dim]Thinking summary (streaming):[/dim]")
    with console.status("[cyan]Thinking...[/cyan] [dim]0.0s[/dim]", spinner="dots") as status:
        async for event in stream:
            event_type = getattr(event, "type", "")

            if event_type == "response.reasoning_summary_text.delta":
                delta = getattr(event, "delta", "")
                if delta:
                    reasoning_seen = True
                    reasoning_chunk_buffer += delta
                    flush_reasoning_buffer()

                    if len(reasoning_chunk_buffer) >= 100 and reasoning_chunk_buffer[-1] in {" ", ".", "!", "?"}:
                        console.print(reasoning_chunk_buffer, style="dim", markup=False)
                        reasoning_chunk_buffer = ""
                maybe_update_status(status, "Thinking...")
                continue

            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                if not delta:
                    maybe_update_status(status, "Generating project files...")
                    continue

                if reasoning_chunk_buffer:
                    flush_reasoning_buffer(force=True)

                if not output_started:
                    output_started = True
                    if reasoning_seen:
                        console.print()
                    console.print("[cyan]Generating project files...[/cyan]")

                output_text += delta

                new_files = discover_stream_file_keys(output_text, seen_files)
                for file_path in new_files:
                    discovered_files.append(file_path)
                    if current_loading_file is not None:
                        console.print(f"[green]✓[/green] {current_loading_file}")
                    current_loading_file = file_path
                    console.print(f"[cyan]generating {file_path}[/cyan]")
                    maybe_update_status(status, f"Generating {file_path}...", force=True)

                if current_loading_file is not None:
                    maybe_update_status(status, f"Generating {current_loading_file}...")
                else:
                    maybe_update_status(status, "Generating project files...")
                continue

            if event_type == "response.completed":
                response_obj = getattr(event, "response", None)
                break

    if current_loading_file is not None:
        console.print(f"[green]✓[/green] {current_loading_file}")

    flush_reasoning_buffer(force=True)

    if reasoning_seen:
        console.print()
    elif not discovered_files:
        console.print("[dim]No reasoning summary text emitted by model.[/dim]")

    total_elapsed = time.monotonic() - started_at

    if not output_text:
        raise RuntimeError("Model returned an empty response.")

    response_id = getattr(response_obj, "id", "") if response_obj else ""

    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Model did not return valid JSON: {error}") from error

    files = normalize_files(parsed)

    sample_files = ", ".join(sorted(files.keys())[:6])
    if len(files) > 6:
        sample_files += ", ..."
    if not sample_files:
        sample_files = "none"

    console.print(
        Panel(
            "\n".join(
                [
                    f"Total elapsed: {total_elapsed:.1f}s",
                    f"Files returned: {len(files)}",
                    f"Sample files: {sample_files}",
                ]
            ),
            title="LLM Generation Summary",
            border_style="cyan",
            expand=False,
        )
    )

    return LLMResult(files=files, response_id=response_id)


def write_submission(files: dict[str, str], output_dir: Path) -> None:
    for relative_path, content in files.items():
        path_obj = Path(relative_path)
        if path_obj.is_absolute() or ".." in path_obj.parts:
            raise RuntimeError(f"Invalid file path from model: {relative_path}")

        full_path = output_dir / path_obj
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    scaffold_dir = Path(__file__).resolve().parent / "scaffold"
    build_sh_src = scaffold_dir / "build.sh"
    if not build_sh_src.is_file():
        raise RuntimeError(f"Missing scaffold build script: {build_sh_src}")

    build_sh_dest = output_dir / "build.sh"
    build_sh_dest.write_text(build_sh_src.read_text(encoding="utf-8"), encoding="utf-8")
    build_sh_dest.chmod(0o755)


def build_setup_failure(error: Exception) -> dict[str, Any]:
    return {
        "passed": 0,
        "failed": 1,
        "total": 1,
        "cases": [
            {
                "name": "build",
                "phase": "build",
                "passed": False,
                "expected_stdout": "",
                "actual_stdout": "",
                "expected_exit_code": 0,
                "actual_exit_code": 1,
                "stderr": str(error),
            }
        ],
    }


def print_iteration_header(iteration: int, max_iterations: int) -> None:
    console.print()
    console.print(
        Panel(
            f"Iteration {iteration}/{max_iterations}",
            style="bold blue",
            expand=False,
        )
    )


def _fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "-"
    return f"{ms:.0f}ms"


def _fmt_bytes(b: int | None) -> str:
    if b is None:
        return "-"
    if b >= 1024 * 1024:
        return f"{b / (1024 * 1024):.1f}MB"
    if b >= 1024:
        return f"{b / 1024:.1f}KB"
    return f"{b}B"


def print_tier_results(tier_name: str, cases: list[dict[str, Any]]) -> None:
    if not cases:
        return

    table = Table(
        title=f"Test Tier: {tier_name}",
        box=box.SIMPLE,
        show_lines=False,
    )
    table.add_column("Test", style="bold")
    table.add_column("Result", justify="center")
    table.add_column("Compile", justify="right")
    table.add_column("vs gcc", justify="right", style="dim")
    table.add_column("Run", justify="right")
    table.add_column("vs gcc", justify="right", style="dim")
    table.add_column("Size", justify="right")
    table.add_column("vs gcc", justify="right", style="dim")
    table.add_column("Details", style="dim", max_width=50)

    for case in cases:
        name = case.get("name", "unknown")
        passed = case.get("passed", False)

        if passed:
            result_str = "[green]PASS[/green]"
            detail = ""
        else:
            result_str = "[red]FAIL[/red]"
            stderr = (case.get("stderr") or "").strip()
            detail = stderr.split("\n")[0][:50] if stderr else ""

        table.add_row(
            str(name),
            result_str,
            _fmt_ms(case.get("compile_time_ms")),
            _fmt_ms(case.get("gcc_compile_time_ms")),
            _fmt_ms(case.get("run_time_ms")),
            _fmt_ms(case.get("gcc_run_time_ms")),
            _fmt_bytes(case.get("binary_size_bytes")),
            _fmt_bytes(case.get("gcc_binary_size_bytes")),
            detail,
        )

    console.print(table)

    # Show at most 2 full error dumps to avoid noise
    errors_shown = 0
    for case in cases:
        if case.get("passed"):
            continue
        stderr = (case.get("stderr") or "").strip()
        if not stderr:
            continue
        if errors_shown >= 2:
            remaining = sum(1 for c in cases if not c.get("passed") and (c.get("stderr") or "").strip())
            if remaining > errors_shown:
                console.print(f"[dim]  ... and {remaining - errors_shown} more errors (see table)[/dim]")
            break
        case_name = case.get("name", "unknown")
        console.print(f"[dim]  stderr ({case_name}):[/dim]")
        for line in stderr.splitlines()[:5]:
            console.print(f"[dim]    {line}[/dim]")
        errors_shown += 1


def print_summary(passed: int, total: int, best: bool) -> None:
    color = "green" if passed == total else ("yellow" if passed > 0 else "red")
    message = f"[{color}]{passed}/{total} tests passed[/{color}]"
    if best:
        message += " [bold cyan]★ New best![/bold cyan]"
    console.print(message)


def print_final_summary(best_passed: int) -> None:
    style = "bold green" if best_passed > 0 else "bold yellow"
    console.print(Panel(f"Final best: {best_passed} tests passed", style=style, expand=False))


def print_build_failure(error: Exception) -> None:
    console.print(f"[red]Build/setup failed:[/red] {error}")


def save_best_submission(files: dict[str, str]) -> None:
    best_dir = Path(__file__).resolve().parent / "best_submission"
    if best_dir.exists():
        shutil.rmtree(best_dir)
    best_dir.mkdir(parents=True)
    write_submission(files, best_dir)
    console.print(f"[bold cyan]Saved best submission to:[/bold cyan] {best_dir}")


async def fetch_test_names() -> list[str]:
    client = await envoi.connect(ENVOI_URL)
    try:
        return client.tests
    finally:
        await client.close()


async def run_loop() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set. "
            "Run: export OPENAI_API_KEY='sk-...'"
        )

    _print(f"Connecting to envoi environment at {ENVOI_URL}...")
    test_names = await fetch_test_names()
    if not test_names:
        raise RuntimeError("Environment has no tests.")
    _print(f"Available tests: {test_names}")
    _print(
        f"Model: {MODEL} | Reasoning effort: {REASONING_EFFORT} | "
        f"Verbosity: {VERBOSITY}"
    )

    previous_files: dict[str, str] | None = None
    last_results: dict[str, Any] = {"passed": 0, "failed": 0, "total": 0, "cases": []}
    best_passed = -1
    previous_response_id: str | None = None

    async with AsyncOpenAI(api_key=api_key) as llm_client:
        for iteration in range(1, MAX_ITERATIONS + 1):
            print_iteration_header(iteration, MAX_ITERATIONS)

            if previous_files is None:
                phase = "Generating initial compiler"
                prompt = build_initial_prompt()
            else:
                phase = "Improving compiler"
                prompt = build_iteration_prompt(previous_files, last_results)

            try:
                llm_result = await call_llm(
                    llm_client,
                    prompt,
                    phase=phase,
                    previous_response_id=previous_response_id,
                )
            except Exception as error:
                console.print(f"[red]LLM call failed:[/red] {error}")
                break

            files = llm_result.files
            previous_response_id = llm_result.response_id
            previous_files = files
            _print(f"Generated {len(files)} files")

            with tempfile.TemporaryDirectory(prefix="envoi-cc-") as tmp_dir:
                tmp_path = Path(tmp_dir)
                try:
                    write_submission(files, tmp_path)
                except Exception as error:
                    console.print(f"[red]Could not write submission:[/red] {error}")
                    break

                all_results: dict[str, Any] = {"cases": [], "passed": 0, "failed": 0, "total": 0}
                _print("Submitting project and creating session...")

                try:
                    async with await envoi.connect_session(
                        ENVOI_URL,
                        session_timeout_seconds=600,
                        submission=envoi.Documents(tmp_path),
                    ) as session:
                        for test_name in test_names:
                            result = await session.test(test_name)

                            tier_cases = result.get("cases", []) if isinstance(result, dict) else []
                            for case in tier_cases:
                                all_results["cases"].append(case)
                                all_results["total"] += 1
                                if case.get("passed"):
                                    all_results["passed"] += 1
                                else:
                                    all_results["failed"] += 1

                            print_tier_results(test_name, tier_cases)
                except Exception as error:
                    print_build_failure(error)
                    last_results = build_setup_failure(error)
                    continue

            last_results = all_results

            is_new_best = all_results["passed"] > best_passed
            if is_new_best:
                best_passed = all_results["passed"]
                save_best_submission(files)

            print_summary(all_results["passed"], all_results["total"], is_new_best)

            if all_results["failed"] == 0 and all_results["total"] > 0:
                console.print("[bold green]All tests passed.[/bold green]")
                break

    print_final_summary(max(best_passed, 0))


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
