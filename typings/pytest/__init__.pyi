from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, overload

class RaisesContext(AbstractContextManager[BaseException]): ...


@overload
def fixture(
    function: Callable[..., Any],
    *,
    scope: str = ...,
    params: Any = ...,
    autouse: bool = ...,
    ids: Any = ...,
    name: str | None = ...,
) -> Callable[..., Any]: ...
@overload
def fixture(
    function: None = ...,
    *,
    scope: str = ...,
    params: Any = ...,
    autouse: bool = ...,
    ids: Any = ...,
    name: str | None = ...,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...


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
