"""Agent definitions shared across backends."""

from envoi_code.agents.base import (
    Agent,
    AgentCredentials,
    AgentSetupContext,
    AgentTurnOutcome,
    SandboxImageRequirements,
)

__all__ = [
    "Agent",
    "AgentCredentials",
    "AgentSetupContext",
    "AgentTurnOutcome",
    "SandboxImageRequirements",
]
