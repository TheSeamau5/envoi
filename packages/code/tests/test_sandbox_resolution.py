from __future__ import annotations

import os
from pathlib import Path

import envoi_code.orchestrator as orchestrator
from envoi_code.sandbox.base import SandboxConfig, SandboxImageRequirements
from envoi_code.sandbox.e2b.backend import E2BSandbox
from envoi_code.sandbox.modal.backend import ModalSandbox


def test_modal_resolve_config_applies_minimums() -> None:
    config = SandboxConfig(
        timeout=120,
        image_requirements=SandboxImageRequirements(),
        environment_dockerfile="/tmp/Dockerfile",
        environment_docker_context_dir="/tmp",
        cpu=None,
        memory_mb=None,
        min_cpu=2.0,
        min_memory_mb=4096,
    )

    resolution = ModalSandbox.resolve_config(config)

    assert resolution.provider == "modal"
    assert resolution.capabilities.supports_runtime_resources is True
    assert resolution.applied_config.cpu == 2.0
    assert resolution.applied_config.memory_mb == 4096
    assert resolution.warnings == []


def test_modal_resolve_config_rejects_under_minimum() -> None:
    config = SandboxConfig(
        timeout=120,
        image_requirements=SandboxImageRequirements(),
        environment_dockerfile="/tmp/Dockerfile",
        environment_docker_context_dir="/tmp",
        cpu=1.0,
        memory_mb=1024,
        min_cpu=2.0,
        min_memory_mb=2048,
    )

    did_raise = False
    try:
        ModalSandbox.resolve_config(config)
    except ValueError:
        did_raise = True
    assert did_raise


def test_e2b_resolve_config_ignores_unsupported_fields() -> None:
    previous = os.environ.get("E2B_MAX_SESSION_SECONDS")
    os.environ["E2B_MAX_SESSION_SECONDS"] = "3600"
    config = SandboxConfig(
        timeout=7200,
        image_requirements=SandboxImageRequirements(),
        environment_dockerfile="/tmp/CustomDockerfile",
        environment_docker_context_dir="/tmp",
        environment_docker_build_args={"A": "1"},
        cpu=4.0,
        memory_mb=8192,
        min_cpu=2.0,
        min_memory_mb=4096,
    )

    try:
        resolution = E2BSandbox.resolve_config(config)
    finally:
        if previous is None:
            os.environ.pop("E2B_MAX_SESSION_SECONDS", None)
        else:
            os.environ["E2B_MAX_SESSION_SECONDS"] = previous

    assert resolution.provider == "e2b"
    assert resolution.capabilities.supports_runtime_resources is False
    assert resolution.applied_config.timeout == 3600
    assert resolution.applied_config.environment_docker_build_args == {}
    assert resolution.applied_config.cpu is None
    assert resolution.applied_config.memory_mb is None
    assert "environment_docker_build_args" in resolution.ignored
    assert "cpu" in resolution.ignored
    assert "memory_mb" in resolution.ignored
    assert any("reducing sandbox timeout" in warning for warning in resolution.warnings)


def test_orchestrator_has_no_provider_specific_branching() -> None:
    source = Path(orchestrator.__file__).read_text()
    assert 'if sandbox_provider ==' not in source
    assert 'sandbox_provider == "modal"' not in source
    assert 'sandbox_provider == "e2b"' not in source
