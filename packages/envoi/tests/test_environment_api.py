from __future__ import annotations

import pytest
from envoi import environment
from envoi.utils import Documents
from pydantic import BaseModel


@pytest.fixture(autouse=True)
def clear_registry() -> None:
    environment.clear_environment()
    yield
    environment.clear_environment()


def test_envoi_test_requires_async_function() -> None:
    def sync_test() -> None:
        return None

    with pytest.raises(TypeError, match="async def"):
        environment.test(sync_test)


def test_suite_test_requires_async_function() -> None:
    suite = environment.suite("basics")

    def sync_test() -> None:
        return None

    with pytest.raises(TypeError, match="async def"):
        suite.test(sync_test)


def test_setup_and_teardown_require_async_functions() -> None:
    def sync_setup() -> None:
        return None

    def sync_teardown() -> None:
        return None

    with pytest.raises(TypeError, match="@envoi.setup"):
        environment.setup(sync_setup)

    with pytest.raises(TypeError, match="@envoi.teardown"):
        environment.teardown(sync_teardown)


def test_schema_v1_includes_capabilities_and_param_schemas() -> None:
    class SetupConfig(BaseModel):
        retries: int = 1

    class TestConfig(BaseModel):
        seed: int

    @environment.setup
    async def setup_fn(
        documents: Documents,
        config: SetupConfig,
        count: int = 1,
    ) -> None:
        """Prepare the workspace."""
        del documents, config, count

    @environment.teardown
    async def teardown_fn() -> None:
        return None

    basics = environment.suite("basics")

    @basics.test("smoke")
    async def smoke(
        documents: Documents,
        case_name: str,
        cfg: TestConfig,
        attempts: int = 0,
    ) -> dict[str, bool]:
        """Smoke check for the environment."""
        del documents, case_name, cfg, attempts
        return {"ok": True}

    schema = environment.schema()

    assert schema["schema_version"] == "envoi.schema.v1"
    assert schema["capabilities"] == {
        "requires_session": True,
        "has_teardown": True,
        "handler_mode": "async_only",
    }
    assert schema["tests"] == ["basics/smoke"]

    test_metadata = schema["test_metadata"]["basics/smoke"]
    assert test_metadata["description"] == "Smoke check for the environment."
    test_params = test_metadata["params_schema"]
    assert test_params["type"] == "object"
    assert test_params["additionalProperties"] is False
    assert "documents" not in test_params["properties"]
    assert set(test_params["properties"]) == {"case_name", "cfg", "attempts"}
    assert "case_name" in test_params["required"]
    assert "cfg" in test_params["required"]
    assert "attempts" not in test_params["required"]

    setup_params = schema["setup_params_schema"]
    assert setup_params["type"] == "object"
    assert setup_params["additionalProperties"] is False
    assert "documents" not in setup_params["properties"]
    assert set(setup_params["properties"]) == {"config", "count"}
    assert "config" in setup_params["required"]
    assert "count" not in setup_params["required"]

