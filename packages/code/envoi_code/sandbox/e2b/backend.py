"""E2B sandbox backend -- runs commands in an E2B cloud sandbox.

Implements Sandbox using E2B's Code Interpreter API. Alternative to
Modal for environments that need a different cloud provider. Use with
--sandbox e2b on the CLI.
"""

from __future__ import annotations

import builtins
import importlib
import os
import shlex
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from envoi_code.sandbox.base import (
    CommandResult,
    SandboxCapabilities,
    SandboxConfig,
    SandboxResolution,
)


def resolve_e2b_max_session_seconds() -> int:
    raw_value = os.environ.get("E2B_MAX_SESSION_SECONDS", "").strip()
    if not raw_value:
        return 3600
    try:
        parsed = int(raw_value)
    except ValueError:
        return 3600
    if parsed <= 0:
        return 3600
    return parsed


class E2BSandbox:
    """Sandbox implementation backed by E2B.

    Requires ``e2b-code-interpreter`` (install via
    ``pip install e2b-code-interpreter``).
    The sandbox image must be pre-built as an E2B template — see
    ``sandbox/e2b/e2b.Dockerfile``.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        return "e2b"

    @property
    def sandbox_id(self) -> str:
        return self._inner.sandbox_id

    @staticmethod
    def capabilities() -> SandboxCapabilities:
        provider_max = 86400
        plan_max = resolve_e2b_max_session_seconds()
        return SandboxCapabilities(
            supports_runtime_resources=False,
            supports_docker_build_args=False,
            supports_dockerfile_override=False,
            max_timeout_seconds=min(provider_max, plan_max),
        )

    @staticmethod
    def resolve_config(config: SandboxConfig) -> SandboxResolution:
        applied = config.model_copy(deep=True)
        warnings: list[str] = []
        ignored: dict[str, Any] = {}
        capabilities = E2BSandbox.capabilities()

        if applied.environment_docker_build_args:
            ignored["environment_docker_build_args"] = dict(
                applied.environment_docker_build_args,
            )
            arg_keys = ", ".join(
                sorted(applied.environment_docker_build_args.keys()),
            )
            warnings.append(
                "ignoring environment Docker build args "
                f"(template is pre-built): {arg_keys}",
            )
            applied.environment_docker_build_args = {}

        dockerfile_name = Path(applied.environment_dockerfile).name
        if dockerfile_name != "Dockerfile":
            ignored["environment_dockerfile"] = applied.environment_dockerfile
            warnings.append(
                "ignoring environment Dockerfile override "
                f"(template is pre-built): {dockerfile_name}",
            )

        if applied.cpu is not None or applied.min_cpu is not None:
            ignored["cpu"] = {
                "requested": applied.cpu,
                "minimum": applied.min_cpu,
            }
            warnings.append(
                "ignoring cpu request/minimum "
                "(configure resources in the sandbox template)",
            )
            applied.cpu = None
            applied.min_cpu = None

        if applied.memory_mb is not None or applied.min_memory_mb is not None:
            ignored["memory_mb"] = {
                "requested": applied.memory_mb,
                "minimum": applied.min_memory_mb,
            }
            warnings.append(
                "ignoring memory request/minimum "
                "(configure resources in the sandbox template)",
            )
            applied.memory_mb = None
            applied.min_memory_mb = None

        timeout_cap = capabilities.max_timeout_seconds
        if (
            isinstance(timeout_cap, int)
            and timeout_cap > 0
            and applied.timeout > timeout_cap
        ):
            ignored["timeout"] = {
                "requested": applied.timeout,
                "applied": timeout_cap,
            }
            warnings.append(
                "reducing sandbox timeout to fit provider cap: "
                f"requested={applied.timeout}s cap={timeout_cap}s "
                f"applied={timeout_cap}s",
            )
            applied.timeout = timeout_cap

        return SandboxResolution(
            provider="e2b",
            capabilities=capabilities,
            applied_config=applied,
            ignored=ignored,
            warnings=warnings,
        )

    @staticmethod
    async def create(config: SandboxConfig) -> E2BSandbox:
        """Create a new E2B sandbox from a pre-built template.

        Reads ``config.template`` (falls back to ``E2B_TEMPLATE`` env var
        when set). Ignores ``config.image_requirements`` and
        ``config.environment_dockerfile`` — E2B templates are pre-built.
        """
        try:
            e2b_module = importlib.import_module(
                "e2b_code_interpreter",
            )
        except ImportError as error:
            raise RuntimeError(
                "E2B backend requires optional dependency "
                "e2b-code-interpreter. "
                "Install with: pip install envoi-code[e2b]"
            ) from error
        async_sandbox = getattr(
            e2b_module, "AsyncSandbox", None,
        )
        if async_sandbox is None:
            raise RuntimeError(
                "e2b_code_interpreter does not export "
                "AsyncSandbox"
            )

        resolved_template = config.template or os.environ.get("E2B_TEMPLATE")
        kwargs: dict[str, Any] = {"timeout": config.timeout}
        if resolved_template:
            kwargs["template"] = resolved_template
        if config.api_key:
            kwargs["api_key"] = config.api_key
        inner = await async_sandbox.create(**kwargs)
        template_label = resolved_template or "<provider-default>"
        builtins.print(
            f"[e2b] sandbox created: "
            f"id={inner.sandbox_id} "
            f"template={template_label}"
        )
        return E2BSandbox(inner)

    async def run(
        self,
        cmd: str,
        *,
        timeout: int = 60,
        quiet: bool = False,
        stream_output: bool = False,
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
        on_stderr_line: Callable[[str], Awaitable[None]] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute a shell command inside the E2B sandbox.

        Uses E2B's native command callbacks for live stdout/stderr delivery.
        Callers that register ``on_stdout_line`` / ``on_stderr_line`` receive
        line callbacks as output arrives.
        """
        if not quiet:
            builtins.print(f"[run] {cmd[:200]}")

        # Build shell prefix for cwd/env.
        prefix = ""
        if env:
            exports = " ".join(
                f"{k}={shlex.quote(v)}" for k, v in env.items()
            )
            prefix += f"export {exports} && "
        if cwd:
            prefix += f"cd {shlex.quote(cwd)} && "
        full_cmd = prefix + cmd if prefix else cmd

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stdout_line_buffer = ""
        stderr_line_buffer = ""

        async def emit_chunk(
            chunk: str,
            *,
            sink: list[str],
            live: bool = False,
            line_callback: Callable[[str], Awaitable[None]] | None = None,
            is_stdout: bool,
        ) -> None:
            nonlocal stdout_line_buffer, stderr_line_buffer
            if not chunk:
                return
            sink.append(chunk)
            if live:
                builtins.print(chunk, end="", flush=True)
            if line_callback is None:
                return
            if is_stdout:
                stdout_line_buffer += chunk
                while "\n" in stdout_line_buffer:
                    line, stdout_line_buffer = stdout_line_buffer.split("\n", 1)
                    await line_callback(line.rstrip("\r"))
            else:
                stderr_line_buffer += chunk
                while "\n" in stderr_line_buffer:
                    line, stderr_line_buffer = stderr_line_buffer.split("\n", 1)
                    await line_callback(line.rstrip("\r"))

        async def handle_stdout_chunk(chunk: str) -> None:
            await emit_chunk(
                chunk,
                sink=stdout_chunks,
                line_callback=on_stdout_line,
                is_stdout=True,
            )

        async def handle_stderr_chunk(chunk: str) -> None:
            await emit_chunk(
                chunk,
                sink=stderr_chunks,
                live=stream_output,
                line_callback=on_stderr_line,
                is_stdout=False,
            )

        t0 = time.monotonic()
        result: Any
        try:
            result = await self._inner.commands.run(
                full_cmd,
                timeout=timeout,
                user="root",
                on_stdout=handle_stdout_chunk,
                on_stderr=handle_stderr_chunk,
            )
        except Exception as error:
            # e2b raises CommandExitException on non-zero command exits.
            if (
                hasattr(error, "exit_code")
                and hasattr(error, "stdout")
                and hasattr(error, "stderr")
            ):
                result = error
            else:
                raise

        if on_stdout_line is not None and stdout_line_buffer:
            await on_stdout_line(stdout_line_buffer.rstrip("\r"))
        if on_stderr_line is not None and stderr_line_buffer:
            await on_stderr_line(stderr_line_buffer.rstrip("\r"))

        stdout = getattr(result, "stdout", "") or "".join(stdout_chunks)
        stderr = getattr(result, "stderr", "") or "".join(stderr_chunks)
        exit_code_raw = getattr(result, "exit_code", None)
        exit_code = exit_code_raw if isinstance(exit_code_raw, int) else 0

        duration_ms = int((time.monotonic() - t0) * 1000)

        if exit_code in {124, -1}:
            builtins.print(f"[run] TIMEOUT after {timeout}s: {cmd[:100]}")
        if exit_code != 0:
            builtins.print(f"[run] FAILED exit={exit_code} cmd={cmd[:100]}")
            if stderr:
                builtins.print(f"[run] stderr: {stderr[:500]}")
        return CommandResult(
            exit_code=exit_code, stdout=stdout, stderr=stderr, duration_ms=duration_ms,
        )

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        ensure_dir: bool = True,
        log_upload: bool = False,
    ) -> None:
        """Write a text file inside the E2B sandbox.

        E2B creates parent directories automatically.
        """
        if log_upload:
            builtins.print(f"[setup][upload] {path}")
        await self._inner.files.write(path, content)

    async def read_file(self, path: str) -> str:
        """Read a text file from the E2B sandbox."""
        return await self._inner.files.read(path)

    async def read_file_bytes(self, path: str) -> bytes:
        """Read a binary file from the E2B sandbox (native, no base64 shell-out)."""
        return await self._inner.files.read(path, format="bytes")

    async def terminate(self) -> None:
        """Terminate the E2B sandbox."""
        try:
            await self._inner.kill()
        except Exception:
            pass
