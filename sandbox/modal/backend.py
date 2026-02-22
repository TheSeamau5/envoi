"""Modal sandbox backend -- runs commands in a Modal cloud sandbox.

Implements SandboxBackend using Modal's Sandbox API. Each trajectory gets an
ephemeral sandbox with a pre-built image (Ubuntu 24.04, Python 3.12, Rust, etc.).
Commands run via sandbox.exec(), files transfer via stdin/stdout piping.
"""

from __future__ import annotations

import asyncio
import base64
import builtins
import shlex
import time
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any

import modal

from sandbox.base import CommandResult


class ModalSandbox:
    """SandboxBackend implementation backed by modal.Sandbox."""

    def __init__(self, inner: modal.Sandbox) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        return "modal"

    @staticmethod
    async def create(
        *,
        image: modal.Image,
        timeout: int,
        app: modal.App,
    ) -> ModalSandbox:
        """Create a new Modal sandbox."""
        inner = await modal.Sandbox.create.aio(
            "bash",
            "-c",
            "sleep infinity",
            image=image,
            timeout=timeout,
            app=app,
        )
        return ModalSandbox(inner)

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
        """Execute a command inside the Modal sandbox."""
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
        proc = await self._inner.exec.aio("bash", "-c", full_cmd, timeout=timeout)
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        async def drain_stream(
            stream: Any,
            sink: list[str],
            live: bool = False,
            line_callback: Callable[[str], Awaitable[None]] | None = None,
        ) -> None:
            line_buffer = ""
            async for chunk in stream:
                sink.append(chunk)
                if live and chunk:
                    builtins.print(chunk, end="", flush=True)
                if line_callback is None:
                    continue
                line_buffer += chunk
                while "\n" in line_buffer:
                    line, line_buffer = line_buffer.split("\n", 1)
                    await line_callback(line.rstrip("\r"))
            if line_callback is not None and line_buffer:
                await line_callback(line_buffer.rstrip("\r"))

        await asyncio.gather(
            drain_stream(
                proc.stdout,
                stdout_chunks,
                line_callback=on_stdout_line,
            ),
            drain_stream(
                proc.stderr,
                stderr_chunks,
                live=stream_output,
                line_callback=on_stderr_line,
            ),
        )

        await proc.wait.aio()
        duration_ms = int((time.monotonic() - t0) * 1000)
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        exit_code = proc.returncode or 0
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
        """Write a text file inside the Modal sandbox."""
        if log_upload:
            builtins.print(f"[setup][upload] {path}")
        if ensure_dir:
            await self.run(f"mkdir -p '{PurePosixPath(path).parent}'", quiet=True)
        async with await self._inner.open.aio(path, "w") as f:
            await f.write.aio(content)

    async def read_file(self, path: str) -> str:
        """Read a text file from the Modal sandbox."""
        result = await self.run(f"cat {path}", quiet=True)
        if result.exit_code != 0:
            raise FileNotFoundError(f"Failed to read {path}: {result.stderr}")
        return result.stdout

    async def read_file_bytes(self, path: str) -> bytes:
        """Read a binary file from the Modal sandbox."""
        result = await self.run(f"base64 {path}", quiet=True)
        if result.exit_code != 0:
            raise FileNotFoundError(f"Failed to read {path}: {result.stderr}")
        return base64.b64decode(result.stdout.strip())

    async def terminate(self) -> None:
        """Terminate the Modal sandbox."""
        try:
            await self._inner.terminate.aio()
        except Exception:
            pass
