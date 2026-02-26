"""Sandbox abstraction layer.

Provides the Sandbox protocol, SandboxConfig normalization, and
create_sandbox() factory that dispatches to the right backend.
"""

from pydantic import BaseModel

from envoi_code.sandbox.base import (
    CommandResult,
    Sandbox,
    SandboxCapabilities,
    SandboxConfig,
    SandboxImageRequirements,
    SandboxResolution,
)
from envoi_code.sandbox.modal import ModalSandbox

try:
    from envoi_code.sandbox.e2b import E2BSandbox
except ImportError:
    E2BSandbox = None


class SandboxLaunchResult(BaseModel):
    sandbox: Sandbox
    resolution: SandboxResolution

    model_config = {"arbitrary_types_allowed": True}


async def create_sandbox(
    provider: str, config: SandboxConfig,
) -> SandboxLaunchResult:
    """Resolve provider config and create a sandbox."""
    if provider == "modal":
        resolution = ModalSandbox.resolve_config(config)
        sandbox = await ModalSandbox.create(resolution.applied_config)
        return SandboxLaunchResult(sandbox=sandbox, resolution=resolution)
    if provider == "e2b":
        if E2BSandbox is None:
            raise RuntimeError(
                "E2B backend requires optional dependency "
                "e2b-code-interpreter"
            )
        resolution = E2BSandbox.resolve_config(config)
        sandbox = await E2BSandbox.create(resolution.applied_config)
        return SandboxLaunchResult(sandbox=sandbox, resolution=resolution)
    raise ValueError(f"Unknown sandbox provider: {provider}")


__all__ = [
    "CommandResult",
    "Sandbox",
    "SandboxCapabilities",
    "SandboxConfig",
    "SandboxImageRequirements",
    "SandboxLaunchResult",
    "SandboxResolution",
    "create_sandbox",
]
