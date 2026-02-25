from __future__ import annotations

import pytest
from envoi_code.scripts.trace import extract_param_flags


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

