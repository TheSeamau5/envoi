from __future__ import annotations

from collections.abc import Callable
from typing import Any, ContextManager, TypeVar, overload

FixtureFunction = TypeVar("FixtureFunction", bound=Callable[..., Any])


class RaisesContext(ContextManager[BaseException]): ...


@overload
def fixture(
    function: FixtureFunction,
    *,
    scope: str = ...,
    params: Any = ...,
    autouse: bool = ...,
    ids: Any = ...,
    name: str | None = ...,
) -> FixtureFunction: ...
@overload
def fixture(
    function: None = ...,
    *,
    scope: str = ...,
    params: Any = ...,
    autouse: bool = ...,
    ids: Any = ...,
    name: str | None = ...,
) -> Callable[[FixtureFunction], FixtureFunction]: ...


@overload
def raises(
    expected_exception: type[BaseException] | tuple[type[BaseException], ...],
    *,
    match: str | None = ...,
) -> RaisesContext: ...
@overload
def raises(
    expected_exception: type[BaseException] | tuple[type[BaseException], ...],
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> None: ...
