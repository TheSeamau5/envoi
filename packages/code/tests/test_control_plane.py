from __future__ import annotations

from envoi_code.control_plane import (
    even_split_counts,
    normalize_param_key,
    options_from_param_space,
    param_sets_from_grid,
    param_sets_from_random,
)
from envoi_code.params_api import ParamSpace, ParamSpaceDimension, ParamSpaceOption


def test_even_split_counts() -> None:
    assert even_split_counts(10, 3) == [4, 3, 3]
    assert even_split_counts(6, 2) == [3, 3]
    assert even_split_counts(0, 2) == [0, 0]


def test_param_sets_from_grid() -> None:
    combinations = param_sets_from_grid(
        {
            "target": ["x86_64-linux", "aarch64-linux"],
            "milestone": ["M0", "M1"],
        }
    )
    assert len(combinations) == 4
    assert {
        "target": "x86_64-linux",
        "milestone": "M0",
    } in combinations


def test_param_sets_from_random_without_replacement() -> None:
    random_sets = param_sets_from_random(
        {
            "target": ["x86_64-linux", "aarch64-linux"],
            "lang": ["en", "fr"],
        },
        run_count=4,
    )
    assert len(random_sets) == 4
    assert len({tuple(sorted(row.items())) for row in random_sets}) == 4


def test_options_from_param_space() -> None:
    param_space = ParamSpace(
        dimensions=[
            ParamSpaceDimension(
                key="target",
                kind="enum",
                options=[
                    ParamSpaceOption(value="x86_64-linux"),
                    ParamSpaceOption(value="aarch64-linux"),
                ],
            ),
            ParamSpaceDimension(
                key="milestone",
                kind="enum",
                allow_random=False,
                options=[
                    ParamSpaceOption(value="M0"),
                    ParamSpaceOption(value="M1"),
                ],
            ),
        ]
    )
    option_map = options_from_param_space(param_space)
    assert option_map == {"target": ["x86_64-linux", "aarch64-linux"]}


def test_normalize_param_key() -> None:
    assert normalize_param_key("Param-Name") == "param_name"
