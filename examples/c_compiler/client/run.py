"""
LLM-driven C compiler development loop.

Calls GPT-5.2 to generate a Rust-based C compiler, submits it to the
envoi C compiler environment, reads structured test results, and iterates.

Usage:
    export OPENAI_API_KEY="sk-..."
    uv run run.py

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
from pathlib import Path
from typing import Any, cast

from openai import OpenAI

import envoi

box: Any
Console: Any
Live: Any
Panel: Any
Spinner: Any
Table: Any
Text: Any

try:
    from rich import box
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.spinner import Spinner
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except Exception:
    box = cast(Any, None)
    Console = cast(Any, None)
    Live = cast(Any, None)
    Panel = cast(Any, None)
    Spinner = cast(Any, None)
    Table = cast(Any, None)
    Text = cast(Any, None)
    RICH_AVAILABLE = False


ENVOI_URL = os.environ.get("ENVOI_URL", "http://localhost:8000")
MAX_ITERATIONS = 4
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.2")

console = Console() if RICH_AVAILABLE else None

C_SUBSET_SPEC = """
You are building a C compiler in Rust. The compiler binary accepts:
    ./cc input.c -o output

It should read a C source file, compile it, and produce a Linux x86_64 ELF executable.
You can (and probably should) invoke the system assembler `as` and linker `gcc`
to handle assembly and linking. Focus on parsing C and generating assembly.

The subset of C to support:
- #include <stdio.h> (just needs to not crash on it; printf is linked from libc)
- int type only (no char, no float, no pointers for now)
- int main() { ... } as the entry point
- Integer literals
- Arithmetic: +, -, *, /
- Local variable declarations: int x = 5;
- Assignment: x = 10;
- if / else statements
- while loops
- Comparison operators: <, >, <=, >=, ==, !=
- Function definitions with int parameters and int return type
- Function calls
- return statements
- printf("%d\\n", expr) for one integer argument
- printf("string\\n") with string literals

Compiler requirements:
1. Parse the C source
2. Generate x86_64 assembly (AT&T or Intel syntax)
3. Call `as` to assemble an object file
4. Call `gcc` to link (needed for libc / printf)
5. Respect CLI: ./cc input.c -o output
""".strip()


def _print(message: str = "") -> None:
    if RICH_AVAILABLE:
        assert console is not None
        console.print(message)
    else:
        print(message)


def build_initial_prompt() -> str:
    return f"""
{C_SUBSET_SPEC}

Generate a complete Rust project. Return a JSON object where keys are file paths
relative to project root and values are full file contents.

You MUST include:
- Cargo.toml (package name must be "c_compiler")
- src/main.rs
- Any additional src/*.rs files needed

Do NOT include build.sh (it is provided externally).
Return ONLY JSON, no markdown, no commentary.
""".strip()


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
    return f"""
{C_SUBSET_SPEC}

Here is your previous Rust source tree:
{json.dumps(previous_files, indent=2)}

The following test cases failed:
{json.dumps(failures, indent=2)}

Fix the compiler and return the COMPLETE updated project as JSON.
Return all files, not only changed files.
Return ONLY JSON, no markdown, no commentary.
""".strip()


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


def _extract_stream_content(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return ""

    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""

    content = getattr(delta, "content", None)
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
                continue
            if isinstance(item, dict):
                dict_text = item.get("text")
                if isinstance(dict_text, str):
                    parts.append(dict_text)
        return "".join(parts)

    return ""


def call_llm(prompt: str, phase: str = "Generating") -> dict[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set. "
            "Run: export OPENAI_API_KEY='sk-...'"
        )

    client = OpenAI(api_key=api_key)
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
        stream=True,
    )

    collected: list[str] = []
    char_count = 0

    if RICH_AVAILABLE:
        assert console is not None
        with Live(Spinner("dots", text=f"{phase}..."), console=console, refresh_per_second=10) as live:
            for chunk in stream:
                delta = _extract_stream_content(chunk)
                if not delta:
                    continue
                collected.append(delta)
                char_count += len(delta)
                live.update(
                    Text.assemble(
                        ("⟳ ", "bold cyan"),
                        (f"{phase}... ", ""),
                        (f"{char_count:,} chars received", "dim"),
                    )
                )
        console.print(f"[dim]{phase} complete ({char_count:,} chars).[/dim]")
    else:
        _print(f"{phase}...")
        next_report = 500
        for chunk in stream:
            delta = _extract_stream_content(chunk)
            if not delta:
                continue
            collected.append(delta)
            char_count += len(delta)
            if char_count >= next_report:
                _print(f"  {char_count:,} chars received...")
                next_report += 500
        _print(f"{phase} complete ({char_count:,} chars).")

    content = "".join(collected)
    if not content:
        raise RuntimeError("Model returned an empty response.")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Model did not return valid JSON: {error}") from error

    return normalize_files(parsed)


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
    if RICH_AVAILABLE:
        assert console is not None
        console.print()
        console.print(
            Panel(
                f"Iteration {iteration}/{max_iterations}",
                style="bold blue",
                expand=False,
            )
        )
        return

    _print()
    _print("=" * 60)
    _print(f"Iteration {iteration}/{max_iterations}")
    _print("=" * 60)


def print_tier_results(tier_name: str, cases: list[dict[str, Any]]) -> None:
    if RICH_AVAILABLE:
        assert console is not None
        table = Table(
            title=f"Test Tier: {tier_name}",
            box=box.SIMPLE,
            show_lines=False,
        )
        table.add_column("Test", style="bold")
        table.add_column("Result", justify="center")
        table.add_column("Phase", style="yellow")
        table.add_column("Details", style="dim", max_width=70)

        for case in cases:
            name = case.get("name", "unknown")
            passed = case.get("passed", False)
            phase = case.get("phase", "")

            if passed:
                result_str = "[green]PASS[/green]"
                detail = ""
            else:
                result_str = "[red]FAIL[/red]"
                stderr = (case.get("stderr") or "").strip()
                detail = stderr.split("\n")[0][:70] if stderr else ""

            table.add_row(str(name), result_str, str(phase), detail)

        console.print(table)

        for case in cases:
            if case.get("passed"):
                continue
            stderr = (case.get("stderr") or "").strip()
            if not stderr:
                continue
            case_name = case.get("name", "unknown")
            console.print(f"[dim]  stderr ({case_name}):[/dim]")
            for line in stderr.splitlines()[:3]:
                console.print(f"[dim]    {line}[/dim]")
        return

    _print(f"Test tier: {tier_name}")
    for case in cases:
        name = case.get("name", "unknown")
        phase = case.get("phase", "")
        if case.get("passed"):
            _print(f"  [PASS] {name}")
            continue

        _print(f"  [FAIL] {name} (phase: {phase})")
        stderr = (case.get("stderr") or "").strip()
        if stderr:
            for line in stderr.splitlines()[:3]:
                _print(f"    stderr: {line}")


def print_summary(passed: int, total: int, best: bool) -> None:
    if RICH_AVAILABLE:
        assert console is not None
        color = "green" if passed == total else ("yellow" if passed > 0 else "red")
        message = f"[{color}]{passed}/{total} tests passed[/{color}]"
        if best:
            message += " [bold cyan]★ New best![/bold cyan]"
        console.print(message)
        return

    suffix = " * New best!" if best else ""
    _print(f"{passed}/{total} tests passed{suffix}")


def print_final_summary(best_passed: int) -> None:
    if RICH_AVAILABLE:
        assert console is not None
        style = "bold green" if best_passed > 0 else "bold yellow"
        console.print(Panel(f"Final best: {best_passed} tests passed", style=style, expand=False))
        return

    _print(f"Final best: {best_passed} tests passed")


def print_build_failure(error: Exception) -> None:
    if RICH_AVAILABLE:
        assert console is not None
        console.print(f"[red]Build/setup failed:[/red] {error}")
        return

    _print(f"Build/setup failed: {error}")


def save_best_submission(files: dict[str, str]) -> None:
    best_dir = Path(__file__).resolve().parent / "best_submission"
    if best_dir.exists():
        shutil.rmtree(best_dir)
    best_dir.mkdir(parents=True)
    write_submission(files, best_dir)

    if RICH_AVAILABLE:
        assert console is not None
        console.print(f"[bold cyan]Saved best submission to:[/bold cyan] {best_dir}")
    else:
        _print(f"Saved best submission to: {best_dir}")


async def fetch_test_names() -> list[str]:
    client = await envoi.connect(ENVOI_URL)
    try:
        return client.tests
    finally:
        await client.close()


async def run_loop() -> None:
    _print(f"Connecting to envoi environment at {ENVOI_URL}...")
    test_names = await fetch_test_names()
    if not test_names:
        raise RuntimeError("Environment has no tests.")
    _print(f"Available tests: {test_names}")

    previous_files: dict[str, str] | None = None
    last_results: dict[str, Any] = {"passed": 0, "failed": 0, "total": 0, "cases": []}
    best_passed = -1

    for iteration in range(1, MAX_ITERATIONS + 1):
        print_iteration_header(iteration, MAX_ITERATIONS)

        if previous_files is None:
            phase = "Generating initial compiler"
            prompt = build_initial_prompt()
        else:
            phase = "Improving compiler"
            prompt = build_iteration_prompt(previous_files, last_results)

        try:
            files = call_llm(prompt, phase=phase)
        except Exception as error:
            if RICH_AVAILABLE:
                assert console is not None
                console.print(f"[red]LLM call failed:[/red] {error}")
            else:
                _print(f"LLM call failed: {error}")
            break

        previous_files = files
        _print(f"Generated {len(files)} files")

        with tempfile.TemporaryDirectory(prefix="envoi-cc-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            try:
                write_submission(files, tmp_path)
            except Exception as error:
                if RICH_AVAILABLE:
                    assert console is not None
                    console.print(f"[red]Could not write submission:[/red] {error}")
                else:
                    _print(f"Could not write submission: {error}")
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
            if RICH_AVAILABLE:
                assert console is not None
                console.print("[bold green]All tests passed.[/bold green]")
            else:
                _print("All tests passed.")
            break

    print_final_summary(max(best_passed, 0))


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
