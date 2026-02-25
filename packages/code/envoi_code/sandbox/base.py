"""Sandbox Protocol — the interface every sandbox must implement.

A sandbox is a remote Linux environment where agents run in isolation. The
orchestrator uses run() to execute commands, write_file()/read_file() to
transfer files, and terminate() to tear down. Implementations exist for
Modal (sandbox/modal/) and E2B (sandbox/e2b/).

Also defines CommandResult, SandboxConfig, and SandboxImageRequirements.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


@dataclasses.dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of running a command inside a sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int = 0

    def unpack(self) -> tuple[int, str, str]:
        """Return (exit_code, stdout, stderr) for backward-compatible destructuring."""
        return self.exit_code, self.stdout, self.stderr


class SandboxImageRequirements(BaseModel):
    """Declarative image layers needed beyond the base environment Dockerfile."""

    apt_packages: list[str] = Field(default_factory=list)
    pip_packages: list[str] = Field(default_factory=list)
    build_commands: list[str] = Field(default_factory=list)


class SandboxConfig(BaseModel):
    """Everything a sandbox backend needs for creation.

    The runner builds this once. Each backend reads what it needs
    and ignores the rest (same pattern as AgentSetupContext).
    """

    timeout: int = 14400
    image_requirements: SandboxImageRequirements = Field(
        default_factory=SandboxImageRequirements,
    )
    environment_dockerfile: str = ""
    environment_docker_context_dir: str | None = None
    environment_docker_build_args: dict[str, str] = Field(default_factory=dict)
    cpu: float | None = None
    memory_mb: int | None = None
    template: str | None = None
    api_key: str | None = None


@runtime_checkable
class Sandbox(Protocol):
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
        cwd: str | None = None,
        env: dict[str, str] | None = None,
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
