"""AgentBackend Protocol — the interface every agent must implement.

The orchestrator (runner.py) never talks to an LLM directly. It calls methods
on an AgentBackend: start() to provision the agent inside a sandbox, run_turn()
to execute one prompt/response cycle, and stop() to tear down. Each agent
implementation (Codex, OpenCode) handles the LLM-specific details internally.

Also defines AgentTurnOutcome, the structured return value from run_turn().
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from sandbox.base import SandboxBackend


class AgentTurnOutcome(BaseModel):
    session_id: str
    response: dict[str, Any]
    session_objects: list[dict[str, Any]] = Field(
        default_factory=list,
    )
    session_ids: list[str] = Field(default_factory=list)
    new_messages: list[dict[str, Any]] = Field(
        default_factory=list,
    )


@runtime_checkable
class AgentBackend(Protocol):
    """Abstraction over a coding agent running inside a sandbox."""

    @property
    def name(self) -> str:
        """Agent name, e.g. 'opencode' or 'codex'."""
        ...

    @property
    def session_id(self) -> str | None:
        """Current session ID, or None before create_session."""
        ...

    async def start(
        self,
        *,
        sb: SandboxBackend,
        model: str,
        api_key: str,
        setup_script: str = "",
        env_files: (
            tuple[dict[str, str], dict[str, str], dict[str, str]]
            | None
        ) = None,
        **kwargs: Any,
    ) -> None:
        """Provision the agent inside the sandbox."""
        ...

    async def create_session(
        self,
        trajectory_id: str,
    ) -> str:
        """Create or return a session ID for this trajectory."""
        ...

    async def run_turn(
        self,
        *,
        prompt_text: str,
        timeout: int,
        remaining_parts_budget: int,
        on_stream_part: (
            Callable[[dict[str, Any]], Awaitable[None]] | None
        ) = None,
    ) -> AgentTurnOutcome | None:
        """Run one agent turn. Returns None on failure."""
        ...

    def on_turn_complete(
        self,
        outcome: AgentTurnOutcome,
    ) -> None:
        """Post-turn bookkeeping (session sync, seen IDs)."""
        ...

    def on_resume(
        self,
        existing_messages: list[dict[str, Any]],
    ) -> None:
        """Restore agent state from a prior trace on resume."""
        ...

    async def recover_session(
        self,
        trajectory_id: str,
        attempt: int,
    ) -> str:
        """Create a recovery session after a turn failure."""
        ...

    async def stop(self) -> None:
        """Tear down the agent. Idempotent."""
        ...
