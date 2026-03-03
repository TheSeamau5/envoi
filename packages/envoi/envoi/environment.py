from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeGuard, cast, get_type_hints, overload

from pydantic import TypeAdapter

from .utils import Documents, mapping_from_object

type TestFunction = Callable[..., Awaitable[object]]
type TestHandler = TestFunction
type TestDecorator = Callable[[TestFunction], TestFunction]


class ModelValidateProtocol(Protocol):
    @classmethod
    def model_validate(cls, obj: object) -> object: ...


class ModelJsonSchemaProtocol(Protocol):
    @classmethod
    def model_json_schema(cls) -> dict[str, object]: ...


class EnvironmentState:
    test_registry: dict[str, TestFunction]
    global_suites: list[Suite]
    setup_fn: TestFunction | None
    teardown_fn: TestFunction | None

    def __init__(self) -> None:
        self.test_registry = {}
        self.global_suites = []
        self.setup_fn = None
        self.teardown_fn = None


state = EnvironmentState()


def require_async_function(
    function: TestFunction,
    *,
    handler_kind: str,
    handler_name: str | None = None,
) -> None:
    if inspect.iscoroutinefunction(function):
        return
    name_suffix = f" '{handler_name}'" if handler_name else ""
    raise TypeError(f"{handler_kind}{name_suffix} must be defined with async def")


def validate_segment(name: str) -> str:
    if not name:
        raise ValueError("Suite and test names must be non-empty strings")
    if "/" in name:
        raise ValueError("Suite and test names cannot contain '/'")
    return name


def register_test(path: str, function: TestFunction) -> None:
    require_async_function(function, handler_kind="envoi test", handler_name=path)
    state.test_registry[path] = function


def test_path(prefix: str | None, leaf_name: str) -> str:
    if prefix is None or prefix == "":
        return leaf_name
    return f"{prefix}/{leaf_name}"


def register_test_for_prefix(
    prefix: str | None,
    function_or_name: TestFunction | str | None = None,
) -> TestFunction | TestDecorator:
    if callable(function_or_name):
        function = function_or_name
        register_test(test_path(prefix, function.__name__), function)
        return function

    explicit_name = function_or_name

    def decorator(function: TestFunction) -> TestFunction:
        leaf_name = (
            function.__name__
            if explicit_name is None
            else validate_segment(explicit_name)
        )
        register_test(test_path(prefix, leaf_name), function)
        return function

    return decorator


def has_model_validate(value: object) -> TypeGuard[type[ModelValidateProtocol]]:
    if not isinstance(value, type):
        return False
    validate = getattr(value, "model_validate", None)
    return callable(validate)


def has_model_json_schema(value: object) -> TypeGuard[type[ModelJsonSchemaProtocol]]:
    if not isinstance(value, type):
        return False
    schema_method = getattr(value, "model_json_schema", None)
    return callable(schema_method)


def is_documents_annotation(annotation: object) -> bool:
    if annotation is Documents:
        return True
    if isinstance(annotation, str):
        normalized = annotation.replace(" ", "")
        return normalized in {"Documents", "envoi.utils.Documents", "utils.Documents"}
    return False


class Suite:
    _name: str
    _parent: Suite | None
    _children: list[Suite]

    def __init__(self, name: str, parent: Suite | None = None):
        self._name = validate_segment(name)
        self._parent = parent
        self._children = []
        if parent is not None:
            parent._children.append(self)
        else:
            state.global_suites.append(self)

    @property
    def path(self) -> str:
        if self._parent is None:
            return self._name
        return f"{self._parent.path}/{self._name}"

    def suite(self, name: str) -> Suite:
        return Suite(name, parent=self)

    @overload
    def test(self, function_or_name: TestFunction) -> TestFunction: ...

    @overload
    def test(self, function_or_name: str | None = None) -> TestDecorator: ...

    def test(
        self,
        function_or_name: TestFunction | str | None = None,
    ) -> TestFunction | TestDecorator:
        return register_test_for_prefix(self.path, function_or_name)


@overload
def test(function_or_name: TestFunction) -> TestFunction: ...


@overload
def test(function_or_name: str | None = None) -> TestDecorator: ...


def test(
    function_or_name: TestFunction | str | None = None,
) -> TestFunction | TestDecorator:
    return register_test_for_prefix(None, function_or_name)


def suite(name: str) -> Suite:
    return Suite(name)


def setup(function: TestFunction) -> TestFunction:
    require_async_function(function, handler_kind="@envoi.setup handler")
    if state.setup_fn is not None:
        raise ValueError("Only one @envoi.setup is allowed")
    state.setup_fn = function
    return function


def teardown(function: TestFunction) -> TestFunction:
    require_async_function(function, handler_kind="@envoi.teardown handler")
    if state.teardown_fn is not None:
        raise ValueError("Only one @envoi.teardown is allowed")
    state.teardown_fn = function
    return function


def clear_environment() -> None:
    state.test_registry.clear()
    state.global_suites.clear()
    state.setup_fn = None
    state.teardown_fn = None


def test_registry_items() -> list[tuple[str, TestFunction]]:
    return list(state.test_registry.items())


def get_setup_fn() -> TestFunction | None:
    return state.setup_fn


def get_teardown_fn() -> TestFunction | None:
    return state.teardown_fn


def resolve_kwargs(
    function: TestFunction,
    documents: Documents | None,
    raw_kwargs: dict[str, object],
) -> dict[str, object]:
    signature = inspect.signature(function)
    hints = safe_type_hints(function)

    resolved: dict[str, object] = {}
    for argument_name, parameter in signature.parameters.items():
        argument_hint: object = (
            hints[argument_name]
            if argument_name in hints
            else cast(object, parameter.annotation)
        )
        if is_documents_annotation(argument_hint):
            if documents is not None:
                resolved[argument_name] = documents
            continue

        if argument_name in raw_kwargs:
            argument_value = raw_kwargs[argument_name]
            if argument_hint is not None and has_model_validate(argument_hint):
                resolved[argument_name] = argument_hint.model_validate(argument_value)
            else:
                resolved[argument_name] = argument_value

    return resolved


def safe_type_hints(function: TestFunction) -> dict[str, object]:
    try:
        hints = get_type_hints(function)
    except Exception:
        raw_annotations = mapping_from_object(getattr(function, "__annotations__", {}))
        _ = raw_annotations.pop("return", None)
        return raw_annotations
    hints.pop("return", None)
    return hints


def property_schema(annotation: object) -> dict[str, object]:
    if annotation is inspect.Signature.empty:
        return {}
    if has_model_json_schema(annotation):
        return mapping_from_object(annotation.model_json_schema())
    try:
        schema = TypeAdapter(annotation).json_schema()
    except Exception:
        return {}
    return mapping_from_object(schema)


def params_schema(function: TestFunction) -> dict[str, object]:
    signature = inspect.signature(function)
    hints = safe_type_hints(function)
    properties: dict[str, object] = {}
    required: list[str] = []
    for argument_name, parameter in signature.parameters.items():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        argument_hint: object = (
            hints[argument_name]
            if argument_name in hints
            else cast(object, parameter.annotation)
        )
        if is_documents_annotation(argument_hint):
            continue
        properties[argument_name] = property_schema(argument_hint)
        parameter_default = cast(object, parameter.default)
        if parameter_default is inspect.Signature.empty:
            required.append(argument_name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def docstring_summary(function: TestFunction) -> str | None:
    raw = inspect.getdoc(function)
    if not raw:
        return None
    first_line = raw.splitlines()[0].strip()
    return first_line or None


def schema() -> dict[str, object]:
    tests = sorted(state.test_registry.keys())
    test_metadata: dict[str, object] = {}
    for path in tests:
        function = state.test_registry[path]
        entry: dict[str, object] = {
            "params_schema": params_schema(function),
        }
        description = docstring_summary(function)
        if description is not None:
            entry["description"] = description
        test_metadata[path] = entry

    result: dict[str, object] = {
        "schema_version": "envoi.schema.v1",
        "capabilities": {
            "requires_session": state.setup_fn is not None,
            "has_teardown": state.teardown_fn is not None,
            "handler_mode": "async_only",
        },
        "tests": tests,
        "test_metadata": test_metadata,
    }
    if state.setup_fn is not None:
        result["setup_params_schema"] = params_schema(state.setup_fn)
    return result
