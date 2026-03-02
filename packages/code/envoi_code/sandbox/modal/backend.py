"""Modal sandbox backend -- runs commands in a Modal cloud sandbox.

Implements Sandbox using Modal's Sandbox API. Each trajectory gets an
ephemeral sandbox with a pre-built image (Ubuntu 24.04, Python 3.12, Rust, etc.).
Commands run via sandbox.exec(), files transfer via stdin/stdout piping.
"""

from __future__ import annotations

import asyncio
import base64
import shlex
import time
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any

import modal

from envoi_code.sandbox.base import (
    CommandResult,
    SandboxCapabilities,
    SandboxConfig,
    SandboxResolution,
)
from envoi_code.utils.helpers import tprint


class ModalSandbox:
    """Sandbox implementation backed by modal.Sandbox."""

    app: modal.App | None = None

    def __init__(self, inner: modal.Sandbox) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        return "modal"

    @property
    def sandbox_id(self) -> str:
        return self._inner.object_id

    @staticmethod
    def capabilities() -> SandboxCapabilities:
        return SandboxCapabilities(
            supports_runtime_resources=True,
            supports_docker_build_args=True,
            supports_dockerfile_override=True,
            max_timeout_seconds=None,
        )

    @staticmethod
    def resolve_config(config: SandboxConfig) -> SandboxResolution:
        applied = config.model_copy(deep=True)
        if applied.min_cpu is not None:
            if applied.cpu is None:
                applied.cpu = applied.min_cpu
            elif applied.cpu < applied.min_cpu:
                raise ValueError(
                    f"Requested sandbox cpu ({applied.cpu}) is below "
                    f"environment minimum ({applied.min_cpu})",
                )
        if applied.min_memory_mb is not None:
            if applied.memory_mb is None:
                applied.memory_mb = applied.min_memory_mb
            elif applied.memory_mb < applied.min_memory_mb:
                raise ValueError(
                    f"Requested sandbox memory_mb ({applied.memory_mb}) is below "
                    f"environment minimum ({applied.min_memory_mb})",
                )
        return SandboxResolution(
            provider="modal",
            capabilities=ModalSandbox.capabilities(),
            applied_config=applied,
            ignored={},
            warnings=[],
        )

    @staticmethod
    async def get_app() -> modal.App:
        """Return the shared Modal App (must be set by deploy.py before use)."""
        if ModalSandbox.app is None:
            raise RuntimeError(
                "ModalSandbox.app not set — deploy.py must assign the "
                "ephemeral app before calling run_trajectory()"
            )
        return ModalSandbox.app

    @staticmethod
    def build_image(config: SandboxConfig) -> modal.Image:
        """Build a sandbox image from Dockerfile + agent requirements."""
        context_dir = (
            Path(config.environment_docker_context_dir).resolve()
            if config.environment_docker_context_dir
            else None
        )
        dockerfile_path = Path(config.environment_dockerfile)
        if not dockerfile_path.exists() and not dockerfile_path.is_absolute():
            if context_dir is not None:
                context_relative_path = context_dir / dockerfile_path
                if context_relative_path.exists():
                    dockerfile_path = context_relative_path
        dockerfile_path = dockerfile_path.resolve()
        if not dockerfile_path.exists():
            raise FileNotFoundError(
                f"Environment Dockerfile not found: {dockerfile_path}",
            )
        image = modal.Image.from_dockerfile(
            str(dockerfile_path),
            context_dir=str(context_dir) if context_dir is not None else None,
            build_args=config.environment_docker_build_args,
            add_python="3.12",
        )
        reqs = config.image_requirements
        if reqs.apt_packages:
            image = image.apt_install(*reqs.apt_packages)
        if reqs.pip_packages:
            image = image.pip_install(*reqs.pip_packages)
        for cmd in reqs.build_commands:
            image = image.run_commands(cmd)
        return image

    @staticmethod
    async def create(config: SandboxConfig) -> ModalSandbox:
        """Create a new Modal sandbox from config."""
        image = ModalSandbox.build_image(config)
        inner = await modal.Sandbox.create.aio(
            "bash",
            "-c",
            "sleep infinity",
            image=image,
            timeout=config.timeout,
            app=await ModalSandbox.get_app(),
            cpu=config.cpu,
            memory=config.memory_mb,
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
            tprint(f"[run] {cmd[:200]}")

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
                    tprint(chunk, end="")
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
            tprint(f"[run] TIMEOUT after {timeout}s: {cmd[:100]}")
        if exit_code != 0:
            tprint(f"[run] FAILED exit={exit_code} cmd={cmd[:100]}")
            if stderr:
                tprint(f"[run] stderr: {stderr[:500]}")
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
            tprint(f"[setup][upload] {path}")
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
