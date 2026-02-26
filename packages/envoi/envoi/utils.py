from __future__ import annotations

import asyncio
import io
import json
import tarfile
import tempfile
import time
import tomllib
from collections.abc import Iterable
from contextvars import ContextVar
from pathlib import Path
from typing import NotRequired, TypedDict, cast, override

from pydantic import BaseModel, Field

from .logging import log_event

JsonPrimitive = str | int | float | bool | None
JsonValue = JsonPrimitive | dict[str, "JsonValue"] | list["JsonValue"]
type RequestFiles = dict[str, tuple[str, bytes, str]]


class RequestKwargs(TypedDict):
    data: dict[str, str]
    files: NotRequired[RequestFiles]


class Documents:
    def __init__(self, paths: str | Path | Iterable[str | Path] | None = None):
        if paths is None:
            self.paths: list[Path] = []
        elif isinstance(paths, str | Path):
            self.paths = [Path(paths)]
        else:
            self.paths = [Path(path) for path in paths]

        self._dir: str | None = None

    @property
    def dir(self) -> str:
        if self._dir is None:
            raise ValueError("Documents are not placed in a sandbox yet")
        return self._dir

    @classmethod
    def from_text(cls, filename: str, content: str) -> Documents:
        temp_dir = Path(tempfile.mkdtemp(prefix="envoi-"))
        file_path = temp_dir / filename
        _ = file_path.write_text(content, encoding="utf-8")
        return cls(file_path)

    @classmethod
    def from_dir(cls, directory: str | Path) -> Documents:
        instance = cls()
        instance._dir = str(directory)
        return instance

    def to_tar(self) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in self.paths:
                if path.is_file():
                    archive.add(str(path), arcname=path.name)
                    continue

                if path.is_dir():
                    for child in path.rglob("*"):
                        if child.is_file():
                            archive.add(
                                str(child),
                                arcname=str(child.relative_to(path)),
                            )

        _ = buffer.seek(0)
        return buffer.read()

    @override
    def __repr__(self) -> str:
        if self._dir is not None:
            return f"Documents(dir={self._dir!r})"
        return f"Documents(paths={self.paths!r})"


class RunResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    stdout_bytes: bytes = Field(default=b"", repr=False)


working_dir: ContextVar[str] = ContextVar("envoi_working_dir")


def emit_environment_log(
    event: str,
    *,
    message: str = "",
    level: str = "info",
    **fields: object,
) -> None:
    _ = log_event(
        component="environment",
        event=event,
        message=message,
        level=level,
        **fields,
    )


def session_path() -> Path:
    """
    Return the working directory for the current session or test.
    Write files here in setup, read them in tests.
    Raises LookupError outside a session or test context.
    """
    return Path(working_dir.get())


async def run(
    command: str,
    cwd: str | None = None,
    timeout_seconds: int = 30,
) -> RunResult:
    started = time.monotonic()
    emit_environment_log(
        "command.start",
        message=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    effective_cwd = cwd
    if effective_cwd is None:
        try:
            effective_cwd = working_dir.get()
        except LookupError:
            effective_cwd = None

    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=effective_cwd,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        if process is not None:
            process.kill()
            _ = await process.wait()
        emit_environment_log(
            "command.timeout",
            level="error",
            message=command,
            cwd=effective_cwd,
            timeout_seconds=timeout_seconds,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return RunResult(
            stdout="",
            stderr="timeout",
            exit_code=-1,
            stdout_bytes=b"",
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    stderr_text = stderr_bytes.decode(errors="replace").strip()
    stdout_text = stdout_bytes.decode(errors="replace").strip()
    exit_code = process.returncode if process.returncode is not None else 0
    emit_environment_log(
        "command.complete",
        level="error" if exit_code != 0 else "info",
        message=command,
        cwd=effective_cwd,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_tail=stdout_text[-800:] if stdout_text else None,
        stderr_tail=stderr_text[-800:] if stderr_text else None,
    )
    return RunResult(
        stdout=stdout_text,
        stderr=stderr_text,
        exit_code=exit_code,
        stdout_bytes=stdout_bytes,
    )


def serialize_object(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump()
    return value


def parse_json_text(text: str) -> object:
    return cast(object, json.loads(text))


def parse_params(raw_params: str) -> dict[str, object]:
    parsed_obj: object = parse_json_text(raw_params) if raw_params else cast(object, {})
    parsed_mapping = mapping_from_object(parsed_obj)
    if not parsed_mapping and raw_params and raw_params.strip() != "{}":
        raise ValueError("params must decode to a JSON object")
    return parsed_mapping


def to_jsonable(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump())
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {
            str(key): to_jsonable(inner_value)
            for key, inner_value in mapping.items()
        }
    if isinstance(value, list | tuple):
        sequence = cast(list[object] | tuple[object, ...], value)
        return [to_jsonable(item) for item in sequence]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def schema_item_values[T](
    items: object,
    field: str,
    value_type: type[T],
) -> list[T]:
    if not isinstance(items, list):
        return []
    item_list = cast(list[object], items)

    values: list[T] = []
    for item in item_list:
        if not isinstance(item, dict):
            continue

        item_mapping = cast(dict[object, object], item)
        value = item_mapping.get(field)
        if isinstance(value, value_type):
            values.append(value)

    return values


def build_request_kwargs(kwargs: dict[str, object]) -> RequestKwargs:
    request_data: dict[str, str] = {}
    serialized_kwargs: dict[str, JsonValue] = {}
    document_argument: tuple[str, Documents] | None = None

    for argument_name, argument_value in kwargs.items():
        if isinstance(argument_value, Documents):
            if document_argument is not None:
                conflicting_args = (
                    f"'{document_argument[0]}' and '{argument_name}'"
                )
                raise ValueError(
                    "Only one document argument is supported per request. "
                    + f"Found both {conflicting_args}."
                )
            document_argument = (argument_name, argument_value)
            continue

        serialized_kwargs[argument_name] = to_jsonable(argument_value)

    if serialized_kwargs:
        request_data["params"] = json.dumps(serialized_kwargs)

    request_kwargs: RequestKwargs = {"data": request_data}

    if document_argument is not None:
        argument_name, document_value = document_argument
        request_kwargs["files"] = {
            "file": (
                f"{argument_name}.tar.gz",
                document_value.to_tar(),
                "application/gzip",
            )
        }

    return request_kwargs


def mapping_from_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    mapping = cast(dict[object, object], value)
    return {
        str(key): inner_value
        for key, inner_value in mapping.items()
    }


def read_environment_metadata(project_dir: str | Path = ".") -> dict[str, str]:
    project_path = Path(project_dir)
    pyproject_path = project_path / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError(f"Missing pyproject.toml at {pyproject_path}")

    pyproject_raw = cast(
        object,
        tomllib.loads(pyproject_path.read_text(encoding="utf-8")),
    )
    pyproject_data = mapping_from_object(pyproject_raw)
    project_table = mapping_from_object(pyproject_data.get("project"))
    tool_table = mapping_from_object(pyproject_data.get("tool"))
    envoi_table = mapping_from_object(tool_table.get("envoi"))
    environment_table = mapping_from_object(envoi_table.get("environment"))

    name_value = first_non_empty_string(
        environment_table.get("name"),
        project_table.get("name"),
    )
    if name_value is None:
        raise ValueError(
            "Missing environment name. Set [project].name or "
            + "[tool.envoi.environment].name in pyproject.toml."
        )

    version_value = first_non_empty_string(
        environment_table.get("version"),
        project_table.get("version"),
        "0.1.0",
    )
    description_value = first_non_empty_string(
        environment_table.get("description"),
        project_table.get("description"),
        "",
    )

    return {
        "name": name_value,
        "version": version_value if version_value is not None else "0.1.0",
        "description": description_value if description_value is not None else "",
    }


def first_non_empty_string(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
