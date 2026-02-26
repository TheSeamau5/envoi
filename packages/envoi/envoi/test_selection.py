from __future__ import annotations

import re
from collections.abc import Iterable


def coerce_path_value(value: str) -> str | int:
    try:
        return int(value)
    except ValueError:
        return value


def extract_template_params(
    template_path: str,
    request_path: str,
) -> dict[str, object] | None:
    if "{" not in template_path or "}" not in template_path:
        return None
    if "{" in request_path or "}" in request_path:
        return None

    pattern_parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template_path):
        pattern_parts.append(re.escape(template_path[cursor:match.start()]))
        parameter_name = match.group(1)
        pattern_parts.append(f"(?P<{parameter_name}>[^/]+)")
        cursor = match.end()
    pattern_parts.append(re.escape(template_path[cursor:]))

    pattern = "^" + "".join(pattern_parts) + "$"
    path_match = re.match(pattern, request_path)
    if path_match is None:
        return None

    return {
        key: coerce_path_value(value)
        for key, value in path_match.groupdict().items()
    }


def matched_tests[HandlerT](
    path: str,
    registry_items: Iterable[tuple[str, HandlerT]],
) -> dict[str, tuple[HandlerT, dict[str, object]]]:
    matched: dict[str, tuple[HandlerT, dict[str, object]]] = {}
    for test_path, function in registry_items:
        is_template = "{" in test_path and "}" in test_path

        if is_template:
            if path == test_path:
                matched[test_path] = (function, {})
                continue
            if not path:
                continue
            template_params = extract_template_params(test_path, path)
            if template_params is not None:
                matched[test_path] = (function, template_params)
            continue

        if not path:
            matched[test_path] = (function, {})
            continue

        if test_path == path or test_path.startswith(path + "/"):
            matched[test_path] = (function, {})

    return matched
