"""Sandbox abstraction layer.

Provides the Sandbox protocol, SandboxConfig for creation, and
create_sandbox() factory that dispatches to the right backend.
"""

from envoi_code.sandbox.base import (
    CommandResult,
    Sandbox,
    SandboxConfig,
    SandboxImageRequirements,
)
from envoi_code.sandbox.modal import ModalSandbox

try:
    from envoi_code.sandbox.e2b import E2BSandbox
except ImportError:
    E2BSandbox = None


async def create_sandbox(
    provider: str, config: SandboxConfig,
) -> Sandbox:
    """Create a sandbox from the named provider."""
    if provider == "modal":
        return await ModalSandbox.create(config)
    if provider == "e2b":
        if E2BSandbox is None:
            raise RuntimeError(
                "E2B backend requires optional dependency "
                "e2b-code-interpreter"
            )
        return await E2BSandbox.create(config)
    raise ValueError(f"Unknown sandbox provider: {provider}")


__all__ = [
    "CommandResult",
    "Sandbox",
    "SandboxConfig",
    "SandboxImageRequirements",
    "create_sandbox",
]
