"""E2B sandbox backend -- runs commands in an E2B cloud sandbox.

Implements Sandbox using E2B's Code Interpreter API. Alternative to
Modal for environments that need a different cloud provider. Use with
--sandbox e2b on the CLI.
"""

from __future__ import annotations

import builtins
import shlex
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sandbox.base import CommandResult, SandboxConfig


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

    @staticmethod
    async def create(config: SandboxConfig) -> E2BSandbox:
        """Create a new E2B sandbox from a pre-built template.

        Reads ``config.template`` (falls back to ``E2B_TEMPLATE`` env var
        or ``"envoi-trace"``). Ignores ``config.image_requirements`` and
        ``config.environment_dockerfile`` — E2B templates are pre-built.
        """
        import os

        from e2b_code_interpreter import AsyncSandbox

        resolved_template = (
            config.template
            or os.environ.get("E2B_TEMPLATE", "envoi-trace")
        )
        # E2B Pro caps at 24 h; hobby at 1 h.
        capped_timeout = min(config.timeout, 86400)
        kwargs: dict[str, Any] = {
            "template": resolved_template,
            "timeout": capped_timeout,
        }
        if config.api_key:
            kwargs["api_key"] = config.api_key
        inner = await AsyncSandbox.create(**kwargs)
        builtins.print(
            f"[e2b] sandbox created: "
            f"id={inner.sandbox_id} "
            f"template={resolved_template}"
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

        E2B's ``commands.run()`` provides synchronous callbacks, so async
        ``on_stdout_line`` / ``on_stderr_line`` are dispatched post-hoc after
        the command completes.  This means there is no live streaming of output
        during long-running commands — all output is delivered at once when the
        command finishes.
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

        t0 = time.monotonic()
        result = await self._inner.commands.run(full_cmd, timeout=timeout, user="root")
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        exit_code = result.exit_code if result.exit_code is not None else 0

        # Post-hoc async callback dispatch.
        if on_stdout_line is not None:
            for line in stdout.splitlines():
                await on_stdout_line(line)
        if on_stderr_line is not None:
            for line in stderr.splitlines():
                await on_stderr_line(line)

        if stream_output and stderr:
            builtins.print(stderr, end="", flush=True)

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
