from __future__ import annotations

import inspect
import json
from typing import Any, Callable, get_type_hints

from pydantic import TypeAdapter

from .utils import Documents


class Environment:
    def __init__(self) -> None:
        self.tests: dict[str, Callable[..., Any]] = {}
        self.actions: dict[str, Callable[..., Any]] = {}
        self.observables: dict[str, Callable[..., Any]] = {}
        self.setup_fn: Callable[..., Any] | None = None
        self.teardown_fn: Callable[..., Any] | None = None

    def test(self, function: Callable[..., Any]) -> Callable[..., Any]:
        self.tests[function.__name__] = function
        return function

    def action(self, function: Callable[..., Any]) -> Callable[..., Any]:
        self.actions[function.__name__] = function
        return function

    def observe(
        self, function: Callable[..., Any] | str | None = None
    ) -> Callable[..., Any]:
        if callable(function):
            self.observables[function.__name__] = function
            return function

        observe_name = function

        def decorator(inner_function: Callable[..., Any]) -> Callable[..., Any]:
            capability_name = (
                observe_name if isinstance(observe_name, str) else inner_function.__name__
            )
            self.observables[capability_name] = inner_function
            return inner_function

        return decorator

    def setup(self, function: Callable[..., Any]) -> Callable[..., Any]:
        if self.setup_fn is not None:
            raise ValueError("Only one @envoi.setup is allowed")
        self.setup_fn = function
        return function

    def teardown(self, function: Callable[..., Any]) -> Callable[..., Any]:
        if self.teardown_fn is not None:
            raise ValueError("Only one @envoi.teardown is allowed")
        self.teardown_fn = function
        return function

    def clear(self) -> None:
        self.tests.clear()
        self.actions.clear()
        self.observables.clear()
        self.setup_fn = None
        self.teardown_fn = None

    def annotation_schema(self, annotation: Any) -> dict[str, Any]:
        if annotation is Any:
            return {}

        if hasattr(annotation, "model_json_schema"):
            try:
                return annotation.model_json_schema()
            except Exception:
                pass

        try:
            return TypeAdapter(annotation).json_schema()
        except Exception:
            return {}

    def json_default(self, value: Any) -> Any | None:
        try:
            json.dumps(value)
            return value
        except TypeError:
            return None

    def function_schema(
        self,
        name: str,
        function: Callable[..., Any],
    ) -> dict[str, Any]:
        signature = inspect.signature(function)
        hints = get_type_hints(function)
        return_hint = hints.pop("return", None)

        argument_properties: dict[str, Any] = {}
        required_arguments: list[str] = []
        documents_argument: str | None = None

        for argument_name, parameter in signature.parameters.items():
            argument_hint = hints.get(argument_name, Any)
            if argument_hint is Documents:
                documents_argument = argument_name
                continue

            argument_schema = self.annotation_schema(argument_hint)
            if parameter.default is inspect.Signature.empty:
                required_arguments.append(argument_name)
            else:
                default_value = self.json_default(parameter.default)
                if default_value is not None:
                    argument_schema = {**argument_schema, "default": default_value}

            argument_properties[argument_name] = argument_schema

        arguments_schema: dict[str, Any] = {
            "type": "object",
            "properties": argument_properties,
            "additionalProperties": False,
        }
        if required_arguments:
            arguments_schema["required"] = required_arguments

        capability: dict[str, Any] = {
            "name": name,
            "description": inspect.getdoc(function),
            "submissionSchema": arguments_schema,
            "targetSchema": self.annotation_schema(return_hint) if return_hint else {},
        }
        if documents_argument is not None:
            capability["x-envoi-documentsArgument"] = documents_argument

        return capability

    def resolve_kwargs(
        self,
        function: Callable[..., Any],
        documents: Documents | None,
        raw_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        hints = get_type_hints(function)
        hints.pop("return", None)

        resolved: dict[str, Any] = {}
        for argument_name, hint in hints.items():
            if hint is Documents:
                if documents is not None:
                    resolved[argument_name] = documents
                continue

            if argument_name in raw_kwargs:
                resolved[argument_name] = raw_kwargs[argument_name]

        return resolved

    def resolve_action_kwargs(
        self,
        action_name: str,
        raw_data: dict[str, Any],
    ) -> dict[str, Any]:
        if action_name not in self.actions:
            return {}

        action_function = self.actions[action_name]
        hints = get_type_hints(action_function)
        hints.pop("return", None)

        resolved: dict[str, Any] = {}
        for argument_name, hint in hints.items():
            if argument_name not in raw_data:
                continue

            argument_value = raw_data[argument_name]
            if hasattr(hint, "model_validate"):
                resolved[argument_name] = hint.model_validate(argument_value)
            else:
                resolved[argument_name] = argument_value

        return resolved

    def capabilities(
        self,
        functions: dict[str, Callable[..., Any]],
    ) -> list[dict[str, Any]]:
        capabilities: list[dict[str, Any]] = []
        for name, function in functions.items():
            capabilities.append(self.function_schema(name, function))
        return capabilities

    def schema(self) -> dict[str, Any]:
        return {
            "tests": self.capabilities(self.tests),
            "actions": self.capabilities(self.actions),
            "observables": self.capabilities(self.observables),
            "setup": self.function_schema("setup", self.setup_fn) if self.setup_fn else None,
            "teardown": (
                self.function_schema("teardown", self.teardown_fn)
                if self.teardown_fn
                else None
            ),
            "has_setup": self.setup_fn is not None,
            "has_teardown": self.teardown_fn is not None,
        }


state = Environment()


def test(function: Callable[..., Any]) -> Callable[..., Any]:
    return state.test(function)


def action(function: Callable[..., Any]) -> Callable[..., Any]:
    return state.action(function)


def observe(
    function: Callable[..., Any] | str | None = None,
) -> Callable[..., Any]:
    return state.observe(function)


def setup(function: Callable[..., Any]) -> Callable[..., Any]:
    return state.setup(function)


def teardown(function: Callable[..., Any]) -> Callable[..., Any]:
    return state.teardown(function)


def clear_environment() -> None:
    state.clear()


def resolve_kwargs(
    function: Callable[..., Any],
    documents: Documents | None,
    raw_kwargs: dict[str, Any],
) -> dict[str, Any]:
    return state.resolve_kwargs(function, documents, raw_kwargs)


def resolve_action_kwargs(action_name: str, raw_data: dict[str, Any]) -> dict[str, Any]:
    return state.resolve_action_kwargs(action_name, raw_data)
