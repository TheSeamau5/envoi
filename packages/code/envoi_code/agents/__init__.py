"""Agent definitions shared across backends.

Also provides a dynamic agent registry so the orchestrator never needs to
import individual agent modules directly.  Each agent module decorates its
class with ``@agent("name")`` to self-register.
"""

from __future__ import annotations

from collections.abc import Callable

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
    "agent",
    "get_agent_backends",
]

# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------

_AGENT_BACKENDS: dict[str, type] = {}


def agent(name: str) -> Callable[[type], type]:
    """Class decorator that registers an agent backend by name.

    Usage inside each agent module's runner-side try/except block::

        @agent("codex")
        class CodexAgent:
            ...
    """

    def decorator(cls: type) -> type:
        _AGENT_BACKENDS[name] = cls
        return cls

    return decorator


def get_agent_backends() -> dict[str, type]:
    """Return the agent backend registry, importing all agent modules first.

    Lazy-imports every known agent module so their ``@agent()`` decorators
    execute.  Modules that fail to import (e.g. missing SDK dependencies
    when running sandbox-side) are silently skipped.
    """
    if not _AGENT_BACKENDS:
        _import_all_agents()
    return dict(_AGENT_BACKENDS)


def _import_all_agents() -> None:
    """Import all agent modules so they self-register."""
    import importlib

    for module_name in ("codex", "opencode", "claude_code"):
        try:
            importlib.import_module(f"envoi_code.agents.{module_name}")
        except Exception:
            pass
