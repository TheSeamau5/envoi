"""
Claude Code agent backend -- wraps the claude-agent-sdk for non-interactive turns.

This module has two roles:
1. As a script running inside the sandbox: uses ``ClaudeSDKClient`` from
   claude-agent-sdk to send prompts and stream responses, emitting TRACE_EVENT
   lines on stderr for each meaningful part (text, tool use, file changes).
   StreamEvent messages provide real-time progress during generation.
2. As the ClaudeCodeAgent class implementing Agent: uploads itself into the
   sandbox, manages sessions, and translates turn results for runner.py.

The TRACE_EVENT protocol is how parts flow from the agent to the orchestrator
in real time. Each event is a JSON line prefixed with "TRACE_EVENT " on stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MEANINGFUL_PART_TYPES: set[str] = {
    "reasoning",
    "text",
    "tool",
    "tool_use",
    "tool_result",
    "patch",
}

TRACE_EVENT_PREFIX = "TRACE_EVENT "

def load_agent_shared_module() -> Any:
    try:
        return importlib.import_module("envoi_code.agents.shared")
    except Exception:
        return importlib.import_module("agent_shared")


agent_shared = load_agent_shared_module()


# ---------------------------------------------------------------------------
# Sandbox-side helpers
# ---------------------------------------------------------------------------


def truncate_for_trace(value: str, limit: int = 240) -> str:
    return agent_shared.truncate_for_trace(value, limit=limit)


def ts() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


def emit_trace_event(event: dict[str, Any]) -> None:
    """Write a TRACE_EVENT line to stderr."""
    agent_shared.emit_trace_event(
        event,
        prefix=TRACE_EVENT_PREFIX,
    )


def tool_summary(name: str, inp: Any) -> str:
    """Generate a human-readable summary for a tool call."""
    if not isinstance(inp, dict):
        return name
    if name == "Bash":
        return truncate_for_trace(inp.get("command", ""), 240)
    if name in ("Read", "Write", "Edit"):
        return inp.get("file_path", name)
    if name == "Grep":
        return f"grep {inp.get('pattern', '')}"
    if name == "Glob":
        return f"glob {inp.get('pattern', '')}"
    if name == "Task":
        desc = inp.get("description", "")
        agent = inp.get("subagent_type", "")
        if desc:
            return f"Task({agent}): {desc}" if agent else f"Task: {desc}"
        return name
    if name == "TodoWrite":
        todos = inp.get("todos", [])
        active = [
            t.get("activeForm") or t.get("content", "?")
            for t in todos
            if isinstance(t, dict) and t.get("status") == "in_progress"
        ]
        if active:
            return f"TodoWrite: {', '.join(active)}"
        return f"TodoWrite: {len(todos)} items"
    if name == "WebFetch":
        return f"fetch {inp.get('url', '')}"
    if name == "WebSearch":
        return f"search: {inp.get('query', '')}"
    if name == "NotebookEdit":
        return inp.get("notebook_path", name)
    return name


def format_elapsed(total_seconds: int) -> str:
    return agent_shared.format_elapsed(total_seconds)


# ---------------------------------------------------------------------------
# Sandbox-side: run a turn via claude-agent-sdk
# ---------------------------------------------------------------------------


async def run_claude_code_turn(
    *,
    prompt_text: str,
    model: str,
    session_id: str,
    max_parts: int,
    max_turns: int = 200,
    cwd: str = "/workspace",
    mcp_config_path: str | None = None,
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    """Execute one agent turn using ClaudeSDKClient.

    Uses the session-based ClaudeSDKClient for proper streaming support.
    Emits TRACE_EVENT lines on stderr for each meaningful part, and
    logs real-time progress via StreamEvent handling.
    Returns a JSON-serializable result dict on stdout.
    """
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )
        from claude_agent_sdk.types import StreamEvent
    except ImportError as exc:
        return {
            "ok": False,
            "error": f"claude-agent-sdk not available: {exc}",
        }

    sdk_env: dict[str, str] = {
        "IS_SANDBOX": "1",
        "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1",
    }
    api_key_file = Path("/tmp/anthropic_api_key.txt")
    if api_key_file.exists():
        sdk_env["ANTHROPIC_API_KEY"] = api_key_file.read_text().strip()
    elif os.environ.get("ANTHROPIC_API_KEY"):
        sdk_env["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]

    def forward_cli_stderr(line: str) -> None:
        stripped = line.rstrip("\n")
        if stripped:
            print(f"[claude-cli] {stripped}", file=sys.stderr, flush=True)

    options = ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        permission_mode="bypassPermissions",
        max_turns=max_turns if max_turns > 0 else 200,
        env=sdk_env,
        stderr=forward_cli_stderr,
        include_partial_messages=True,
        resume=resume_session_id,
    )

    if mcp_config_path and Path(mcp_config_path).exists():
        try:
            mcp_config = json.loads(Path(mcp_config_path).read_text())
            if isinstance(mcp_config, dict):
                options.mcp_servers = mcp_config
        except Exception:
            pass

    parts_seen = 0
    sdk_session_id: str | None = None
    result_text = ""
    messages: list[dict[str, Any]] = []

    # Track which blocks we've already emitted trace events for.
    # With include_partial_messages=True, the SDK yields repeated
    # AssistantMessages with growing content. We deduplicate by
    # tracking block identifiers we've already processed.
    emitted_block_ids: set[str] = set()

    # Accumulators for real-time streaming progress via StreamEvent.
    # These give immediate feedback while the model is generating.
    stream_text_buffer = ""
    stream_thinking_buffer = ""
    stream_thinking_chars_printed = 0  # track how many chars we've shown so far
    stream_text_chars_printed = 0

    resume_label = f" resume={resume_session_id}" if resume_session_id else ""
    print(
        f"[{ts()}] [claude-code] connecting model={model} "
        f"max_turns={max_turns} prompt_len={len(prompt_text)}{resume_label}",
        file=sys.stderr,
        flush=True,
    )

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt_text)

            print(
                f"[{ts()}] prompt sent, waiting for response...",
                file=sys.stderr, flush=True,
            )

            msg_count = 0
            async for message in client.receive_response():
                msg_count += 1

                if isinstance(message, SystemMessage):
                    print(
                        f"[{ts()}] system: {message.subtype}",
                        file=sys.stderr, flush=True,
                    )
                    if (
                        message.subtype == "init"
                        and "session_id" in message.data
                    ):
                        sdk_session_id = message.data["session_id"]

                elif isinstance(message, StreamEvent):
                    event_data = message.event
                    event_type = event_data.get("type", "")

                    if event_type == "content_block_start":
                        block_info = event_data.get("content_block", {})
                        btype = block_info.get("type", "")
                        if btype == "tool_use":
                            tool_name = block_info.get("name", "?")
                            print(
                                f"[{ts()}] >> tool: {tool_name}",
                                file=sys.stderr, flush=True,
                            )
                        elif btype == "text":
                            stream_text_buffer = ""
                            stream_text_chars_printed = 0
                        elif btype == "thinking":
                            stream_thinking_buffer = ""
                            stream_thinking_chars_printed = 0
                            print(
                                f"[{ts()}] >> thinking...",
                                file=sys.stderr, flush=True,
                            )

                    elif event_type == "content_block_delta":
                        delta = event_data.get("delta", {})
                        dtype = delta.get("type", "")
                        if dtype == "text_delta":
                            stream_text_buffer += delta.get("text", "")
                            # Print text as it arrives
                            new_text = stream_text_buffer[stream_text_chars_printed:]
                            if new_text:
                                sys.stderr.write(new_text)
                                sys.stderr.flush()
                                stream_text_chars_printed = len(stream_text_buffer)
                        elif dtype == "thinking_delta":
                            stream_thinking_buffer += delta.get(
                                "thinking", ""
                            )
                            # Print thinking as it arrives
                            new_thinking = stream_thinking_buffer[stream_thinking_chars_printed:]
                            if new_thinking:
                                sys.stderr.write(new_thinking)
                                sys.stderr.flush()
                                stream_thinking_chars_printed = len(stream_thinking_buffer)

                    elif event_type == "content_block_stop":
                        if stream_thinking_buffer.strip():
                            # Newline to end the streamed thinking, then summary
                            print(
                                f"\n[{ts()}] << thinking done "
                                f"({len(stream_thinking_buffer)} chars)",
                                file=sys.stderr, flush=True,
                            )
                            stream_thinking_buffer = ""
                            stream_thinking_chars_printed = 0
                        if stream_text_buffer.strip():
                            # Newline to end the streamed text, then summary
                            print(
                                f"\n[{ts()}] << text done "
                                f"({len(stream_text_buffer)} chars)",
                                file=sys.stderr, flush=True,
                            )
                            stream_text_buffer = ""
                            stream_text_chars_printed = 0

                    elif event_type == "message_start":
                        model_name = event_data.get(
                            "message", {}
                        ).get("model", "?")
                        print(
                            f"[{ts()}] >> response from {model_name}",
                            file=sys.stderr, flush=True,
                        )

                elif isinstance(message, AssistantMessage):
                    block_types = [
                        type(b).__name__ for b in message.content
                    ]
                    print(
                        f"[{ts()}] assistant: {block_types}"
                        f"{' ERROR=' + message.error if message.error else ''}",
                        file=sys.stderr, flush=True,
                    )
                    message_dict: dict[str, Any] = {
                        "role": "assistant",
                        "content": [],
                    }
                    for idx, block in enumerate(message.content):
                        if isinstance(block, TextBlock):
                            block_id = f"text:{idx}"
                            already_emitted = block_id in emitted_block_ids
                            text = block.text
                            message_dict["content"].append(
                                {"type": "text", "text": text}
                            )
                            if text.strip() and not already_emitted:
                                emitted_block_ids.add(block_id)
                                parts_seen += 1
                                emit_trace_event({
                                    "event": "part.completed",
                                    "role": "assistant",
                                    "part_type": "text",
                                    "item_type": "text",
                                    "summary": truncate_for_trace(text),
                                    "content": text,
                                    "has_file_change": False,
                                    "files": [],
                                    "tool_name": None,
                                    "tool_status": None,
                                    "tool_input": None,
                                    "tool_output": None,
                                    "tool_error": None,
                                    "tool_exit_code": None,
                                    "token_usage": None,
                                    "timestamp_ms": int(
                                        time.time() * 1000
                                    ),
                                })
                                budget = (
                                    f"[{parts_seen}/{max_parts}]"
                                    if max_parts > 0
                                    else f"[{parts_seen}]"
                                )
                                print(
                                    f"[{ts()}] {budget} [text] "
                                    f"{truncate_for_trace(text, 80)}",
                                    file=sys.stderr,
                                    flush=True,
                                )

                        elif isinstance(block, ToolUseBlock):
                            block_id = f"tool_use:{block.id}"
                            already_emitted = block_id in emitted_block_ids
                            message_dict["content"].append({
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            })
                            if not already_emitted:
                                emitted_block_ids.add(block_id)
                                parts_seen += 1

                                has_file_change = block.name in (
                                    "Write",
                                    "Edit",
                                )
                                files: list[str] = []
                                if has_file_change and isinstance(
                                    block.input, dict
                                ):
                                    fp = block.input.get("file_path", "")
                                    if fp:
                                        files.append(fp)

                                summary = tool_summary(
                                    block.name, block.input
                                )

                                emit_trace_event({
                                    "event": "part.completed",
                                    "role": "assistant",
                                    "part_type": "tool",
                                    "item_type": "tool_use",
                                    "summary": summary,
                                    "content": None,
                                    "has_file_change": has_file_change,
                                    "files": files,
                                    "tool_name": block.name,
                                    "tool_status": "running",
                                    "tool_input": block.input,
                                    "tool_output": None,
                                    "tool_error": None,
                                    "tool_exit_code": None,
                                    "token_usage": None,
                                    "timestamp_ms": int(
                                        time.time() * 1000
                                    ),
                                })
                                budget = (
                                    f"[{parts_seen}/{max_parts}]"
                                    if max_parts > 0
                                    else f"[{parts_seen}]"
                                )
                                detail = summary or block.name
                                print(
                                    f"[{ts()}] {budget} [tool] "
                                    f"{block.name}: {detail}",
                                    file=sys.stderr,
                                    flush=True,
                                )

                        elif isinstance(block, ToolResultBlock):
                            block_id = f"tool_result:{block.tool_use_id}"
                            already_emitted = block_id in emitted_block_ids
                            result_content = str(block.content or "")
                            is_error = block.is_error or False
                            message_dict["content"].append({
                                "type": "tool_result",
                                "tool_use_id": block.tool_use_id,
                                "content": result_content[:500],
                                "is_error": is_error,
                            })
                            if not already_emitted:
                                emitted_block_ids.add(block_id)
                                parts_seen += 1
                                emit_trace_event({
                                    "event": "part.completed",
                                    "role": "assistant",
                                    "part_type": "tool_result",
                                    "item_type": "tool_result",
                                    "summary": (
                                        f"result for {block.tool_use_id}"
                                    ),
                                    "content": result_content[:500],
                                    "has_file_change": False,
                                    "files": [],
                                    "tool_name": None,
                                    "tool_status": (
                                        "error" if is_error else "completed"
                                    ),
                                    "tool_input": None,
                                    "tool_output": result_content[:2000],
                                    "tool_error": (
                                        result_content[:500]
                                        if is_error
                                        else None
                                    ),
                                    "tool_exit_code": (
                                        1 if is_error else 0
                                    ),
                                    "token_usage": None,
                                    "timestamp_ms": int(
                                        time.time() * 1000
                                    ),
                                })

                        elif isinstance(block, ThinkingBlock):
                            block_id = f"thinking:{idx}"
                            already_emitted = block_id in emitted_block_ids
                            if (
                                block.thinking.strip()
                                and not already_emitted
                            ):
                                emitted_block_ids.add(block_id)
                                parts_seen += 1
                                emit_trace_event({
                                    "event": "part.completed",
                                    "role": "assistant",
                                    "part_type": "reasoning",
                                    "item_type": "reasoning",
                                    "summary": truncate_for_trace(
                                        block.thinking
                                    ),
                                    "content": block.thinking,
                                    "has_file_change": False,
                                    "files": [],
                                    "tool_name": None,
                                    "tool_status": None,
                                    "tool_input": None,
                                    "tool_output": None,
                                    "tool_error": None,
                                    "tool_exit_code": None,
                                    "token_usage": None,
                                    "timestamp_ms": int(
                                        time.time() * 1000
                                    ),
                                })

                    # Partial messages update in-place; new turns append.
                    if (
                        messages
                        and messages[-1].get("role") == "assistant"
                    ):
                        messages[-1] = message_dict
                    else:
                        messages.append(message_dict)

                elif isinstance(message, ResultMessage):
                    result_text = message.result or ""
                    duration_str = format_elapsed(
                        message.duration_ms // 1000
                    )
                    cost_str = (
                        f" cost=${message.total_cost_usd:.4f}"
                        if message.total_cost_usd
                        else ""
                    )
                    print(
                        f"[{ts()}] done: {message.num_turns} turns, "
                        f"{duration_str}{cost_str}, "
                        f"{len(result_text)} chars result, "
                        f"{msg_count} messages streamed",
                        file=sys.stderr, flush=True,
                    )

                else:
                    print(
                        f"[{ts()}] ?? {type(message).__name__}: "
                        f"{repr(message)[:200]}",
                        file=sys.stderr, flush=True,
                    )

    except Exception as exc:
        import traceback
        print(
            f"[{ts()}] EXCEPTION: {type(exc).__name__}: {exc}",
            file=sys.stderr, flush=True,
        )
        traceback.print_exc(file=sys.stderr)
    return {
        "ok": True,
        "session_id": sdk_session_id or session_id,
        "result": result_text,
        "parts_seen": parts_seen,
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# Sandbox-side: CLI entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat_parser = subparsers.add_parser("chat")
    chat_parser.add_argument("--session-id", required=True)
    chat_parser.add_argument("--text-file", required=True)
    chat_parser.add_argument("--model", required=True)
    chat_parser.add_argument("--max-parts", type=int, default=0)
    chat_parser.add_argument("--max-turns", type=int, default=200)
    chat_parser.add_argument("--mcp-config", default=None)
    chat_parser.add_argument("--resume", default=None)

    args = parser.parse_args()

    if args.command == "chat":
        text = Path(args.text_file).read_text()
        result = await run_claude_code_turn(
            prompt_text=text,
            model=args.model,
            session_id=args.session_id,
            max_parts=max(0, args.max_parts),
            max_turns=max(0, args.max_turns),
            mcp_config_path=args.mcp_config,
            resume_session_id=args.resume,
        )
        print(json.dumps(result))
        return


if __name__ == "__main__":
    asyncio.run(main())


# -------------------------------------------------------------------
# ClaudeCodeAgent: Agent implementation (runner-side only)
# -------------------------------------------------------------------
# The code below is only executed when imported by runner.py, never
# when this file runs as a standalone sandbox script.

try:
    import builtins

    from envoi_code.agents import agent
    from envoi_code.agents import shared as agent_shared_module
    from envoi_code.agents.base import (
        AgentCredentials,
        AgentSetupContext,
        AgentTurnOutcome,
        SandboxImageRequirements,
    )
    from envoi_code.agents.setup import run_workspace_init
    from envoi_code.sandbox.base import Sandbox
    from envoi_code.utils.helpers import (
        compute_turn_timeout_seconds,
        environment_upload_items,
        run_sandbox_client,
        tprint,
        truncate_text,
        upload_files_parallel,
    )
    from envoi_code.utils.parsing import agent_message_id, parse_trace_event_line

    CLAUDE_CODE_SCRIPT = "/sandbox/claude_code_client.py"
    AGENT_SHARED_SCRIPT = "/sandbox/agent_shared.py"
    CLAUDE_CODE_LABEL = "claude-code-sdk"
    DEFAULT_CLAUDE_CODE_MODEL = "claude-sonnet-4-6"
    AGENT_SHARED_CONTENT = Path(agent_shared_module.__file__).read_text()

    @agent("claude_code")
    class ClaudeCodeAgent:
        """Agent implementation for Claude Code via claude-agent-sdk."""

        @property
        def name(self) -> str:
            return "claude_code"

        @property
        def session_id(self) -> str | None:
            return self.current_session_id

        @property
        def log_files(self) -> list[str]:
            return ["/tmp/envoi.log"]

        def __init__(self) -> None:
            self.sandbox: Sandbox | None = None
            self.agent_model: str = ""
            self.api_key: str = ""
            self.current_session_id: str | None = None
            self.sdk_session_id: str | None = None  # Claude Code CLI session ID for --resume
            self.seen_message_ids: set[str] = set()

        # -- static methods -----------------------------------------

        @staticmethod
        def resolve_credentials(
            auth_json_b64: str | None = None,
        ) -> AgentCredentials:
            """Resolve Claude Code credentials from env vars."""
            del auth_json_b64
            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            return AgentCredentials(api_key=api_key)

        @staticmethod
        def resolve_model(model: str | None) -> str:
            return model or DEFAULT_CLAUDE_CODE_MODEL

        @staticmethod
        def image_requirements() -> SandboxImageRequirements:
            return SandboxImageRequirements(
                pip_packages=["claude-agent-sdk"],
            )

        # -- instance methods ----------------------------------------

        def compute_turn_timeout(
            self,
            *,
            remaining_parts: int,
            remaining_run_seconds: float,
            message_timeout_seconds: int,
        ) -> int:
            return compute_turn_timeout_seconds(
                remaining_parts=remaining_parts,
                remaining_run_seconds=remaining_run_seconds,
                message_timeout_seconds=message_timeout_seconds,
            )

        async def run_client(
            self,
            args: list[str],
            *,
            timeout: int = 60,
            quiet: bool = False,
            stream_output: bool = False,
            on_stderr_line=None,
        ) -> dict[str, Any] | None:
            assert self.sandbox is not None
            return await run_sandbox_client(
                self.sandbox,
                CLAUDE_CODE_SCRIPT,
                CLAUDE_CODE_LABEL,
                args,
                timeout=timeout,
                quiet=quiet,
                stream_output=stream_output,
                on_stderr_line=on_stderr_line,
            )

        # -- protocol methods ---------------------------------------

        async def setup(
            self,
            sandbox: Sandbox,
            ctx: AgentSetupContext,
        ) -> None:
            self.sandbox = sandbox
            self.agent_model = ctx.model
            self.api_key = ctx.credentials.api_key

            builtins.print(
                f"[setup] agent=claude_code model={ctx.model}",
                flush=True,
            )

            setup_uploads: list[tuple[str, str]] = [
                (
                    AGENT_SHARED_SCRIPT,
                    AGENT_SHARED_CONTENT,
                ),
                (
                    "/sandbox/claude_code_client.py",
                    CLAUDE_CODE_CLIENT_CONTENT,
                ),
                (
                    "/workspace/.gitignore",
                    ctx.workspace_gitignore,
                ),
            ]
            if ctx.mcp_enabled and ctx.mcp_server_content.strip():
                setup_uploads.append(
                    ("/sandbox/mcp_server.py", ctx.mcp_server_content),
                )
                setup_uploads.append(
                    (
                        "/sandbox/mcp_config.json",
                        json.dumps(
                            {
                                "tests": {
                                    "command": "python3",
                                    "args": ["/sandbox/mcp_server.py"],
                                }
                            }
                        ),
                    ),
                )

            await upload_files_parallel(
                sandbox, setup_uploads, log_upload=True,
            )

            if ctx.env_files:
                py, c, txt, sh = ctx.env_files
                await upload_files_parallel(
                    sandbox,
                    environment_upload_items(py, c, txt, sh),
                    log_upload=True,
                )
                builtins.print(
                    f"[setup] uploaded {len(py)} py, "
                    f"{len(c)} c, {len(txt)} txt, {len(sh)} sh files",
                    flush=True,
                )

            await run_workspace_init(
                sandbox,
                runtime_env=ctx.runtime_env,
            )

            # Store API key for the sandbox-side script to read
            await sandbox.run(
                f"echo {json.dumps(self.api_key)} > /tmp/anthropic_api_key.txt",
                quiet=True,
            )

        async def create_session(
            self,
            trajectory_id: str,
        ) -> str:
            self.current_session_id = f"claude-code-{trajectory_id}"
            return self.current_session_id

        async def run_turn(
            self,
            *,
            prompt_text: str,
            timeout: int,
            current_turn: int,
            remaining_parts_budget: int,
            global_part_count: int,
            global_max_parts: int,
            global_max_turns: int,
            global_elapsed_seconds: int,
            on_stream_part=None,
        ) -> AgentTurnOutcome | None:
            assert self.sandbox is not None
            prompt_path = "/tmp/prompt.txt"
            await self.sandbox.write_file(
                prompt_path,
                prompt_text,
                ensure_dir=False,
            )
            builtins.print(
                f"[prompt] sending message ({len(prompt_text)} "
                f"chars), waiting up to {timeout}s...",
                flush=True,
            )

            args = [
                "chat",
                "--session-id",
                self.current_session_id or "",
                "--text-file",
                prompt_path,
                "--model",
                self.agent_model,
                "--max-parts",
                str(remaining_parts_budget),
                "--max-turns",
                str(max(0, global_max_turns)) if global_max_turns > 0 else "200",
            ]
            if self.sdk_session_id:
                args.extend(["--resume", self.sdk_session_id])

            async def handle_stderr_line(line: str) -> None:
                handled = await parse_trace_event_line(
                    line, on_stream_part,
                )
                if handled:
                    return
                stripped = line.strip()
                if stripped:
                    tprint(
                        "[claude-code][stderr] "
                        + truncate_text(stripped, limit=500)
                    )

            response = await self.run_client(
                args,
                timeout=timeout,
                stream_output=False,
                on_stderr_line=handle_stderr_line,
            )

            if response is None:
                return None

            if not response.get("ok"):
                error_text = str(response.get("error", ""))
                if len(error_text) > 800:
                    error_text = error_text[:800] + "...[truncated]"
                builtins.print(
                    f"[claude-code] turn failed: {error_text}",
                    flush=True,
                )
                return None

            effective_session_id = (
                response.get("session_id")
                or self.current_session_id
                or ""
            )

            new_messages: list[dict[str, Any]] = []
            raw_messages = response.get("messages", [])
            for msg in raw_messages:
                if not isinstance(msg, dict):
                    continue
                mid = agent_message_id(msg)
                if mid and mid in self.seen_message_ids:
                    continue
                if mid:
                    self.seen_message_ids.add(mid)
                new_messages.append(msg)

            if not new_messages:
                fallback_msg = {
                    "info": {
                        "id": (
                            f"{effective_session_id}:"
                            f"{int(time.time() * 1000)}"
                        ),
                        "role": "assistant",
                        "sessionID": effective_session_id,
                        "time": {
                            "created": int(time.time() * 1000),
                        },
                    },
                    "parts": [],
                    "result": response.get("result", ""),
                }
                fallback_mid = agent_message_id(fallback_msg)
                if fallback_mid:
                    self.seen_message_ids.add(fallback_mid)
                new_messages.append(fallback_msg)

            session_obj = {
                "id": effective_session_id,
                "provider": "claude_code",
            }
            return AgentTurnOutcome(
                session_id=effective_session_id,
                response=response,
                session_objects=[session_obj],
                session_ids=[effective_session_id],
                new_messages=new_messages,
            )

        def on_turn_complete(
            self,
            outcome: AgentTurnOutcome,
        ) -> None:
            self.current_session_id = outcome.session_id
            # Capture the SDK session ID for --resume on subsequent turns
            resp = outcome.response
            if isinstance(resp, dict):
                sdk_sid = resp.get("session_id")
                if isinstance(sdk_sid, str) and sdk_sid:
                    self.sdk_session_id = sdk_sid

        def on_resume(
            self,
            existing_messages: list[dict[str, Any]],
        ) -> None:
            for msg in existing_messages:
                mid = agent_message_id(msg)
                if mid:
                    self.seen_message_ids.add(mid)

        async def recover_session(
            self,
            trajectory_id: str,
            attempt: int,
        ) -> str:
            sid = f"recovery-claude-code-{trajectory_id}-{attempt}"
            self.current_session_id = sid
            return sid

        async def collect_crash_messages(
            self,
            session_id: str,
        ) -> list[dict[str, Any]] | None:
            return None

        async def stop(self) -> None:
            pass

    # The content of this file (agents/claude_code.py) for uploading into
    # the sandbox as the client script.
    CLAUDE_CODE_CLIENT_CONTENT = Path(__file__).read_text()

except ImportError:
    pass  # Running as standalone sandbox script
