from __future__ import annotations

from argparse import Namespace

import pytest
from envoi_code.scripts.trace import common_runner_args, extract_param_flags


def test_extract_param_flags_parses_values_and_duplicates() -> None:
    argv, raw_params = extract_param_flags([
        "--agent",
        "codex",
        "--param-target",
        "x86_64-linux",
        "--param-impl-lang=zig",
        "--param-seed",
        "M0",
        "--param-seed",
        "M1",
        "--max-parts",
        "5",
    ])

    assert argv == [
        "--agent",
        "codex",
        "--max-parts",
        "5",
    ]
    assert raw_params == {
        "target": "x86_64-linux",
        "impl_lang": "zig",
        "seed": ["M0", "M1"],
    }


def test_extract_param_flags_requires_value() -> None:
    with pytest.raises(SystemExit, match="Missing value"):
        extract_param_flags(["--param-target"])


def make_args(**overrides) -> Namespace:
    values = {
        "agent": "codex",
        "trajectory_id": "traj-001",
        "task": "task-dir",
        "env": "env-dir",
        "max_parts": None,
        "max_turns": None,
        "project": "c-compiler",
        "test": None,
        "test_timeout_seconds": None,
        "timeout_seconds": 28_800,
        "model": None,
        "message_timeout_seconds": None,
        "raw_params": {},
        "sandbox_cpu": None,
        "sandbox_memory_mb": None,
        "sandbox": "modal",
        "codex_auth_file": "",
    }
    values.update(overrides)
    return Namespace(**values)


def test_common_runner_args_omits_message_timeout_when_unset() -> None:
    args = make_args()

    result = common_runner_args(args, "traj-001", modal_mode=True)

    assert "--message-timeout-seconds" not in result


def test_common_runner_args_includes_message_timeout_when_set() -> None:
    args = make_args(message_timeout_seconds=900)

    result = common_runner_args(args, "traj-001", modal_mode=True)

    assert "--message-timeout-seconds" in result
    index = result.index("--message-timeout-seconds")
    assert result[index + 1] == "900"
