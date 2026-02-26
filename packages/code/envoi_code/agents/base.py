"""Agent Protocol — the interface every agent must implement.

The orchestrator (runner.py) never talks to an LLM directly. It calls methods
on an Agent: setup() to provision the agent inside a sandbox, run_turn()
to execute one prompt/response cycle, and stop() to tear down. Each agent
implementation (Codex, OpenCode) handles the LLM-specific details internally.

Also defines AgentTurnOutcome and Pydantic config models.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from envoi_code.sandbox.base import Sandbox, SandboxImageRequirements

# --- Pydantic config models ---


class AgentCredentials(BaseModel):
    """Base credential container. Agents subclass to add their own fields."""

    api_key: str = ""


class AgentSetupContext(BaseModel):
    """Everything an agent needs for sandbox provisioning.

    The runner builds this once and passes it to agent.setup(). The agent
    reads what it needs and ignores the rest.
    """

    model: str
    credentials: AgentCredentials
    env_files: (
        tuple[dict[str, str], dict[str, str], dict[str, str]] | None
    ) = None
    mcp_server_content: str = ""
    mcp_enabled: bool = False
    workspace_gitignore: str = ""
    runtime_env: dict[str, str] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


# --- Turn outcome ---


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


class AgentFatalError(RuntimeError):
    """Fatal agent-side failure that should stop the run immediately."""

    def __init__(
        self,
        message: str,
        *,
        stop_reason: str = "agent_error",
    ) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason


# --- Protocol ---


@runtime_checkable
class Agent(Protocol):
    """Abstraction over a coding agent running inside a sandbox."""

    @property
    def name(self) -> str:
        """Agent name, e.g. 'opencode' or 'codex'."""
        ...

    @property
    def session_id(self) -> str | None:
        """Current session ID, or None before create_session."""
        ...

    @property
    def log_files(self) -> list[str]:
        """Absolute paths to log files this agent writes inside the sandbox."""
        ...

    @staticmethod
    def resolve_credentials(
        codex_auth_json_b64: str | None = None,
    ) -> AgentCredentials:
        """Resolve credentials from environment variables."""
        ...

    @staticmethod
    def resolve_model(model: str | None) -> str:
        """Return the effective model string, applying agent-specific defaults."""
        ...

    @staticmethod
    def image_requirements() -> SandboxImageRequirements:
        """Declare additional image layers this agent needs beyond
        what the environment Dockerfile provides."""
        ...

    def compute_turn_timeout(
        self,
        *,
        remaining_parts: int,
        remaining_run_seconds: float,
        message_timeout_seconds: int,
    ) -> int:
        """Compute the timeout for the next turn."""
        ...

    async def setup(
        self,
        sandbox: Sandbox,
        ctx: AgentSetupContext,
    ) -> None:
        """Provision this agent inside the sandbox.

        Uploads client scripts, config files, credentials, environment
        files. Runs the environment setup. Installs agent binaries and
        starts agent servers. After this returns, the agent is ready
        for create_session().
        """
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
        current_turn: int,
        remaining_parts_budget: int,
        global_part_count: int,
        global_max_parts: int,
        global_max_turns: int,
        global_elapsed_seconds: int,
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

    async def collect_crash_messages(
        self,
        session_id: str,
    ) -> list[dict[str, Any]] | None:
        """Attempt to recover messages after a crash.

        Returns a list of message dicts if the agent supports post-crash
        message collection, or None if it does not.
        """
        ...

    async def stop(self) -> None:
        """Tear down the agent. Idempotent."""
        ...
