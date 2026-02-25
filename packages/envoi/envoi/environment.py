from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import TypeAdapter

from .utils import Documents

_test_registry: dict[str, Callable[..., Any]] = {}
_global_suites: list[Suite] = []
setup_fn: Callable[..., Any] | None = None
teardown_fn: Callable[..., Any] | None = None


def require_async_function(
    function: Callable[..., Any],
    *,
    handler_kind: str,
    handler_name: str | None = None,
) -> None:
    if inspect.iscoroutinefunction(function):
        return
    name_suffix = f" '{handler_name}'" if handler_name else ""
    raise TypeError(f"{handler_kind}{name_suffix} must be defined with async def")


def validate_segment(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("Suite and test names must be non-empty strings")
    if "/" in name:
        raise ValueError("Suite and test names cannot contain '/'")
    return name


def register_test(path: str, function: Callable[..., Any]) -> None:
    require_async_function(function, handler_kind="envoi test", handler_name=path)
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
    require_async_function(function, handler_kind="@envoi.setup handler")
    if setup_fn is not None:
        raise ValueError("Only one @envoi.setup is allowed")
    setup_fn = function
    return function


def teardown(function: Callable[..., Any]) -> Callable[..., Any]:
    global teardown_fn
    require_async_function(function, handler_kind="@envoi.teardown handler")
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


def safe_type_hints(function: Callable[..., Any]) -> dict[str, Any]:
    try:
        hints = get_type_hints(function)
    except Exception:
        return {}
    hints.pop("return", None)
    return hints


def property_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Signature.empty:
        return {}
    if hasattr(annotation, "model_json_schema") and callable(annotation.model_json_schema):
        schema = annotation.model_json_schema()
        return schema if isinstance(schema, dict) else {}
    try:
        schema = TypeAdapter(annotation).json_schema()
    except Exception:
        return {}
    return schema if isinstance(schema, dict) else {}


def params_schema(function: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(function)
    hints = safe_type_hints(function)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for argument_name, parameter in signature.parameters.items():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        argument_hint = hints.get(argument_name, parameter.annotation)
        if argument_hint is Documents:
            continue
        properties[argument_name] = property_schema(argument_hint)
        if parameter.default is inspect.Signature.empty:
            required.append(argument_name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def docstring_summary(function: Callable[..., Any]) -> str | None:
    raw = inspect.getdoc(function)
    if not raw:
        return None
    first_line = raw.splitlines()[0].strip()
    return first_line or None


def schema() -> dict[str, Any]:
    tests = sorted(_test_registry.keys())
    test_metadata: dict[str, Any] = {}
    for path in tests:
        function = _test_registry[path]
        entry: dict[str, Any] = {
            "params_schema": params_schema(function),
        }
        description = docstring_summary(function)
        if description is not None:
            entry["description"] = description
        test_metadata[path] = entry

    result: dict[str, Any] = {
        "schema_version": "envoi.schema.v1",
        "capabilities": {
            "requires_session": setup_fn is not None,
            "has_teardown": teardown_fn is not None,
            "handler_mode": "async_only",
        },
        "tests": tests,
        "test_metadata": test_metadata,
    }
    if setup_fn is not None:
        result["setup_params_schema"] = params_schema(setup_fn)
    return {
        **result,
    }
