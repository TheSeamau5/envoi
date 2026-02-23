"""Sandbox abstraction layer.

Provides the Sandbox protocol, SandboxConfig for creation, and
create_sandbox() factory that dispatches to the right backend.
"""

from sandbox.base import (
    CommandResult,
    Sandbox,
    SandboxConfig,
    SandboxImageRequirements,
)


async def create_sandbox(
    provider: str, config: SandboxConfig,
) -> Sandbox:
    """Create a sandbox from the named provider. Lazy-imports backends."""
    if provider == "modal":
        from sandbox.modal import ModalSandbox

        return await ModalSandbox.create(config)
    if provider == "e2b":
        from sandbox.e2b import E2BSandbox

        return await E2BSandbox.create(config)
    raise ValueError(f"Unknown sandbox provider: {provider}")


__all__ = [
    "CommandResult",
    "Sandbox",
    "SandboxConfig",
    "SandboxImageRequirements",
    "create_sandbox",
]
