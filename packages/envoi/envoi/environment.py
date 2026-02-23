from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from .utils import Documents

_test_registry: dict[str, Callable[..., Any]] = {}
_global_suites: list[Suite] = []
setup_fn: Callable[..., Any] | None = None
teardown_fn: Callable[..., Any] | None = None


def validate_segment(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("Suite and test names must be non-empty strings")
    if "/" in name:
        raise ValueError("Suite and test names cannot contain '/'")
    return name


def register_test(path: str, function: Callable[..., Any]) -> None:
    _test_registry[path] = function


class Suite:
    def __init__(self, name: str, parent: Suite | None = None):
        self._name = validate_segment(name)
        self._parent = parent
        self._children: list[Suite] = []
        if parent is not None:
            parent._children.append(self)
        else:
            _global_suites.append(self)

    @property
    def path(self) -> str:
        if self._parent is None:
            return self._name
        return f"{self._parent.path}/{self._name}"

    def suite(self, name: str) -> Suite:
        return Suite(name, parent=self)

    def test(
        self,
        function_or_name: Callable[..., Any] | str | None = None,
    ) -> Callable[..., Any]:
        if callable(function_or_name):
            function = function_or_name
            register_test(f"{self.path}/{function.__name__}", function)
            return function

        if function_or_name is not None and not isinstance(function_or_name, str):
            raise TypeError("@suite.test expects a function or optional string name")

        explicit_name = function_or_name

        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            leaf_name = (
                function.__name__
                if explicit_name is None
                else validate_segment(explicit_name)
            )
            register_test(f"{self.path}/{leaf_name}", function)
            return function

        return decorator


def test(
    function_or_name: Callable[..., Any] | str | None = None,
) -> Callable[..., Any]:
    if callable(function_or_name):
        function = function_or_name
        register_test(function.__name__, function)
        return function

    if function_or_name is not None and not isinstance(function_or_name, str):
        raise TypeError("@envoi.test expects a function or optional string name")

    explicit_name = function_or_name

    def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
        leaf_name = function.__name__ if explicit_name is None else validate_segment(explicit_name)
        register_test(leaf_name, function)
        return function

    return decorator


def suite(name: str) -> Suite:
    return Suite(name)


def setup(function: Callable[..., Any]) -> Callable[..., Any]:
    global setup_fn
    if setup_fn is not None:
        raise ValueError("Only one @envoi.setup is allowed")
    setup_fn = function
    return function


def teardown(function: Callable[..., Any]) -> Callable[..., Any]:
    global teardown_fn
    if teardown_fn is not None:
        raise ValueError("Only one @envoi.teardown is allowed")
    teardown_fn = function
    return function


def clear_environment() -> None:
    global setup_fn, teardown_fn
    _test_registry.clear()
    _global_suites.clear()
    setup_fn = None
    teardown_fn = None


def resolve_kwargs(
    function: Callable[..., Any],
    documents: Documents | None,
    raw_kwargs: dict[str, Any],
) -> dict[str, Any]:
    signature = inspect.signature(function)
    hints = get_type_hints(function)
    hints.pop("return", None)

    resolved: dict[str, Any] = {}
    for argument_name in signature.parameters:
        argument_hint = hints.get(argument_name)
        if argument_hint is Documents:
            if documents is not None:
                resolved[argument_name] = documents
            continue

        if argument_name in raw_kwargs:
            argument_value = raw_kwargs[argument_name]
            if argument_hint is not None and hasattr(argument_hint, "model_validate"):
                resolved[argument_name] = argument_hint.model_validate(argument_value)
            else:
                resolved[argument_name] = argument_value

    return resolved


def schema() -> dict[str, Any]:
    return {
        "tests": sorted(_test_registry.keys()),
        "has_setup": setup_fn is not None,
        "has_teardown": teardown_fn is not None,
    }
