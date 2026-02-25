from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from envoi_code.orchestrator import load_task


def load_task_result(task_dir: Path, env_dir: Path) -> object:
    return asyncio.run(
        load_task(
            task_dir,
            environment_dir=env_dir,
            raw_params={"lang": "fr", "target": "x86_64-linux"},
            selected_test_paths=["basics"],
            agent="codex",
            model="gpt-test",
        ),
    )


def test_load_task_prefers_task_py_resolve_task(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    env_dir = tmp_path / "environment"
    task_dir.mkdir()
    env_dir.mkdir()
    (task_dir / "prompt.md").write_text("fallback prompt")
    (task_dir / "task.py").write_text(
        "from envoi_code.task_api import ResolvedTask\n"
        "async def resolve_task(context):\n"
        "    return ResolvedTask(\n"
        "        prompt='dynamic prompt',\n"
        "        task_params={'x': 1},\n"
        "        metadata={'m': 2},\n"
        "    )\n",
    )

    result = load_task_result(task_dir, env_dir)

    assert result.prompt == "dynamic prompt"
    assert result.task_params == {"x": 1}
    assert result.metadata == {"m": 2}


def test_load_task_rejects_sync_resolve_task(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    env_dir = tmp_path / "environment"
    task_dir.mkdir()
    env_dir.mkdir()
    (task_dir / "task.py").write_text(
        "def resolve_task(context):\n"
        "    return {'prompt': 'x'}\n",
    )

    with pytest.raises(TypeError, match="must be async"):
        load_task_result(task_dir, env_dir)


def test_load_task_falls_back_to_prompt_md_only(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    env_dir = tmp_path / "environment"
    task_dir.mkdir()
    env_dir.mkdir()
    (task_dir / "prompt.md").write_text("hello from prompt")

    result = load_task_result(task_dir, env_dir)

    assert result.prompt == "hello from prompt"
    assert result.task_params == {}
    assert result.metadata == {}


def test_load_task_does_not_use_en_md_fallback(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    env_dir = tmp_path / "environment"
    task_dir.mkdir()
    env_dir.mkdir()
    (task_dir / "en.md").write_text("legacy prompt")

    with pytest.raises(FileNotFoundError, match="task.py resolver or prompt.md"):
        load_task_result(task_dir, env_dir)
