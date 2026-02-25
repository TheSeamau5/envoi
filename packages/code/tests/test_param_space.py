from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from envoi_code.param_space import resolve_environment_param_space


def resolve_param_space_result(environment_dir: Path) -> object:
    return asyncio.run(
        resolve_environment_param_space(
            environment_dir=environment_dir,
            task_dir=None,
            selected_test_paths=[],
        ),
    )


def test_resolve_environment_param_space_with_async_resolver(
    tmp_path: Path,
) -> None:
    environment_dir = tmp_path / "environment"
    environment_dir.mkdir()
    (environment_dir / "params.py").write_text(
        "from envoi_code.params_api import ParamSpace, ParamSpaceDimension, ParamSpaceOption\n"
        "async def resolve_param_space(context):\n"
        "    return ParamSpace(dimensions=[\n"
        "        ParamSpaceDimension(\n"
        "            key='target',\n"
        "            options=[ParamSpaceOption(value='x86_64-linux')],\n"
        "        )\n"
        "    ])\n",
    )

    result = resolve_param_space_result(environment_dir)
    assert result.dimensions[0].key == "target"
    assert result.dimensions[0].options[0].value == "x86_64-linux"


def test_resolve_environment_param_space_with_static_param_space(
    tmp_path: Path,
) -> None:
    environment_dir = tmp_path / "environment"
    environment_dir.mkdir()
    (environment_dir / "params.py").write_text(
        "PARAM_SPACE = {\n"
        "  'dimensions': [\n"
        "    {\n"
        "      'key': 'lang',\n"
        "      'kind': 'enum',\n"
        "      'options': [{'value': 'en'}]\n"
        "    }\n"
        "  ]\n"
        "}\n",
    )

    result = resolve_param_space_result(environment_dir)
    assert result.dimensions[0].key == "lang"
    assert result.dimensions[0].options[0].value == "en"


def test_resolve_environment_param_space_requires_async_resolver(
    tmp_path: Path,
) -> None:
    environment_dir = tmp_path / "environment"
    environment_dir.mkdir()
    (environment_dir / "params.py").write_text(
        "def resolve_param_space(context):\n"
        "    return {'dimensions': []}\n",
    )

    with pytest.raises(TypeError, match="must be async"):
        resolve_param_space_result(environment_dir)
