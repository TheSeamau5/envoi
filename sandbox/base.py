"""Base protocol for sandbox backends."""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable


@dataclasses.dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of running a command inside a sandbox."""

    exit_code: int
    stdout: str
    stderr: str

    def unpack(self) -> tuple[int, str, str]:
        """Return (exit_code, stdout, stderr) for backward-compatible destructuring."""
        return self.exit_code, self.stdout, self.stderr


@runtime_checkable
class SandboxBackend(Protocol):
    """Abstraction over a remote sandbox environment."""

    @property
    def name(self) -> str:
        """Provider name, e.g. 'modal' or 'e2b'."""
        ...

    async def run(
        self,
        cmd: str,
        *,
        timeout: int = 60,
        quiet: bool = False,
        stream_output: bool = False,
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
        on_stderr_line: Callable[[str], Awaitable[None]] | None = None,
    ) -> CommandResult:
        """Execute a shell command inside the sandbox."""
        ...

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        ensure_dir: bool = True,
        log_upload: bool = False,
    ) -> None:
        """Write a text file inside the sandbox."""
        ...

    async def read_file(self, path: str) -> str:
        """Read a text file from the sandbox."""
        ...

    async def read_file_bytes(self, path: str) -> bytes:
        """Read a binary file from the sandbox."""
        ...

    async def terminate(self) -> None:
        """Terminate the sandbox. Idempotent."""
        ...
