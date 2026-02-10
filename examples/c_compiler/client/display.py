"""Display and streaming UI for the C compiler client loop."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
FILE_KEY_PATTERN = re.compile(r'"((?:[^"\\]|\\.)+)"\s*:')



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


async def stream_llm_response(stream: Any, phase: str) -> str:
    """Handle all streaming display for an LLM response. Returns raw output text."""
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
                break

    if current_loading_file is not None:
        console.print(f"[green]✓[/green] {current_loading_file}")

    flush_reasoning_buffer(force=True)

    if reasoning_seen:
        console.print()
    elif not discovered_files:
        console.print("[dim]No reasoning summary text emitted by model.[/dim]")

    return output_text


def print_llm_summary(elapsed: float, files: dict[str, str]) -> None:
    sample_files = ", ".join(sorted(files.keys())[:6])
    if len(files) > 6:
        sample_files += ", ..."
    if not sample_files:
        sample_files = "none"

    console.print(
        Panel(
            "\n".join(
                [
                    f"Total elapsed: {elapsed:.1f}s",
                    f"Files returned: {len(files)}",
                    f"Sample files: {sample_files}",
                ]
            ),
            title="LLM Generation Summary",
            border_style="cyan",
            expand=False,
        )
    )


def print_iteration_header(iteration: int, max_iterations: int) -> None:
    console.print()
    console.print(
        Panel(
            f"Iteration {iteration}/{max_iterations}",
            style="bold blue",
            expand=False,
        )
    )


def print_main_prompt(
    *,
    phase: str,
    prompt: str,
    system_prompt: str | None = None,
    prompt_path: Path | None = None,
    request_path: Path | None = None,
    max_chars: int | None = None,
) -> None:
    sections: list[str] = []
    if system_prompt is not None:
        sections.append(f"=== SYSTEM INSTRUCTIONS ===\n{system_prompt}")
    sections.append(f"=== USER INPUT ===\n{prompt}")
    full_prompt = "\n\n".join(sections)

    shown_prompt = full_prompt
    truncated = False
    if isinstance(max_chars, int) and max_chars > 0 and len(full_prompt) > max_chars:
        shown_prompt = full_prompt[:max_chars]
        truncated = True

    info = [f"Phase: {phase}", f"Payload chars: {len(full_prompt):,}"]
    if system_prompt is not None:
        info.append(f"System chars: {len(system_prompt):,}")
    info.append(f"User chars: {len(prompt):,}")
    if prompt_path is not None:
        info.append(f"Prompt saved: {prompt_path}")
    if request_path is not None:
        info.append(f"Request JSON: {request_path}")
    if truncated:
        info.append(f"Showing first {len(shown_prompt):,} chars")

    console.print("[magenta]=== Main LLM Prompt ===[/magenta]")
    for line in info:
        console.print(f"[dim]{line}[/dim]")
    console.print()
    console.print(shown_prompt, markup=False)
    if truncated:
        console.print("[dim][truncated in UI; full prompt saved to disk][/dim]")
    console.print("[magenta]=======================[/magenta]")


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


def _to_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _artifact_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _artifact_brief(artifact: dict[str, Any]) -> str:
    path = artifact.get("path", "unknown")
    kind = artifact.get("kind", "unknown")
    size = _fmt_bytes(_to_int(artifact.get("size_bytes")))
    chunks = artifact.get("text_chunks")
    chunk_count = len(chunks) if isinstance(chunks, list) else 0

    preview = ""
    if isinstance(chunks, list) and chunks and isinstance(chunks[0], str):
        first_non_empty = ""
        for line in chunks[0].splitlines():
            stripped = line.strip()
            if stripped:
                first_non_empty = stripped
                break
        if first_non_empty:
            preview = f" | {first_non_empty[:90]}"

    return f"{kind}: {path} ({size}, {chunk_count} chunk(s)){preview}"


def print_tier_results(
    tier_name: str,
    cases: list[dict[str, Any]],
    regressions: list[dict[str, Any]] | None = None,
    is_focus: bool = False,
) -> None:
    if not cases:
        return

    regression_names = {r["test_name"] for r in (regressions or [])}
    focus_label = " [bold red](FOCUS)[/bold red]" if is_focus else ""

    table = Table(
        title=f"Test Tier: {tier_name}{focus_label}",
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
            is_regression = name in regression_names
            result_str = "[red bold]REGR[/red bold]" if is_regression else "[red]FAIL[/red]"
            stderr = (case.get("stderr") or "").strip()
            detail = stderr.split("\n")[0][:50] if stderr else ""
            artifacts = _artifact_dicts(case.get("debug_artifacts"))
            if artifacts:
                detail = f"{detail} [{len(artifacts)} artifacts]".strip()

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
        artifacts = _artifact_dicts(case.get("debug_artifacts"))
        has_artifacts = len(artifacts) > 0
        if not stderr and not has_artifacts:
            continue
        if errors_shown >= 2:
            remaining = sum(
                1
                for c in cases
                if not c.get("passed")
                and (
                    (c.get("stderr") or "").strip()
                    or len(_artifact_dicts(c.get("debug_artifacts"))) > 0
                )
            )
            if remaining > errors_shown:
                console.print(
                    f"[dim]  ... and {remaining - errors_shown} more failures with stderr/artifacts (see table)[/dim]"
                )
            break
        case_name = case.get("name", "unknown")
        if stderr:
            console.print(f"[dim]  stderr ({case_name}):[/dim]")
            for line in stderr.splitlines()[:5]:
                console.print(f"[dim]    {line}[/dim]")
        if has_artifacts:
            total_bytes = sum(
                value
                for value in (
                    _to_int(a.get("size_bytes"))
                    for a in artifacts
                )
                if value is not None
            )
            console.print(
                f"[dim]  debug_artifacts ({case_name}): {len(artifacts)} file(s), {_fmt_bytes(total_bytes)}[/dim]"
            )
            shown = 0
            for artifact in artifacts:
                console.print(f"[dim]    - {_artifact_brief(artifact)}[/dim]")
                shown += 1
                if shown >= 4:
                    break
            if len(artifacts) > shown:
                console.print(f"[dim]    ... and {len(artifacts) - shown} more artifact file(s)[/dim]")
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
