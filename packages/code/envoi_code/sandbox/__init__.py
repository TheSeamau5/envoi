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
from envoi_code.sandbox.e2b import E2BSandbox
from envoi_code.sandbox.modal import ModalSandbox


async def create_sandbox(
    provider: str, config: SandboxConfig,
) -> Sandbox:
    """Create a sandbox from the named provider."""
    if provider == "modal":
        return await ModalSandbox.create(config)
    if provider == "e2b":
        return await E2BSandbox.create(config)
    raise ValueError(f"Unknown sandbox provider: {provider}")


__all__ = [
    "CommandResult",
    "Sandbox",
    "SandboxConfig",
    "SandboxImageRequirements",
    "create_sandbox",
]
