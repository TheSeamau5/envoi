from __future__ import annotations

import asyncio
import io
import json
import tarfile
import tempfile
import tomllib
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Documents:
    def __init__(self, paths: str | Path | Iterable[str | Path] | None = None):
        if paths is None:
            self.paths: list[Path] = []
        elif isinstance(paths, (str, Path)):
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
        file_path.write_text(content, encoding="utf-8")
        return cls(file_path)

    @classmethod
    def _from_dir(cls, directory: str | Path) -> Documents:
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

        buffer.seek(0)
        return buffer.read()

    def __repr__(self) -> str:
        if self._dir is not None:
            return f"Documents(dir={self._dir!r})"
        return f"Documents(paths={self.paths!r})"


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    stdout_bytes: bytes = field(default=b"", repr=False)


working_dir: ContextVar[str] = ContextVar("envoi_working_dir")


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
    except asyncio.TimeoutError:
        if process is not None:
            process.kill()
            await process.wait()
        return RunResult(
            stdout="",
            stderr="timeout",
            exit_code=-1,
            stdout_bytes=b"",
        )

    return RunResult(
        stdout=stdout_bytes.decode(errors="replace").strip(),
        stderr=stderr_bytes.decode(errors="replace").strip(),
        exit_code=process.returncode or 0,
        stdout_bytes=stdout_bytes,
    )


def serialize_object(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    return value


def parse_params(raw_params: str) -> dict[str, Any]:
    parsed = json.loads(raw_params) if raw_params else {}
    if not isinstance(parsed, dict):
        raise ValueError("params must decode to a JSON object")
    return parsed


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {key: to_jsonable(inner_value) for key, inner_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def schema_item_values(items: Any, field: str, value_type: type[T]) -> list[T]:
    if not isinstance(items, list):
        return []

    values: list[T] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        value = item.get(field)
        if isinstance(value, value_type):
            values.append(value)

    return values


def build_request_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    request_data: dict[str, str] = {}
    serialized_kwargs: dict[str, Any] = {}
    document_argument: tuple[str, Any] | None = None

    for argument_name, argument_value in kwargs.items():
        if isinstance(argument_value, Documents):
            if document_argument is not None:
                raise ValueError(
                    "Only one document argument is supported per request. "
                    f"Found both '{document_argument[0]}' and '{argument_name}'."
                )
            document_argument = (argument_name, argument_value)
        else:
            serialized_kwargs[argument_name] = to_jsonable(argument_value)

    if serialized_kwargs:
        request_data["params"] = json.dumps(serialized_kwargs)

    request_kwargs: dict[str, Any] = {"data": request_data}

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


def read_environment_metadata(project_dir: str | Path = ".") -> dict[str, str]:
    project_path = Path(project_dir)
    pyproject_path = project_path / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError(f"Missing pyproject.toml at {pyproject_path}")

    pyproject_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project_raw = pyproject_data.get("project", {})
    project_table = project_raw if isinstance(project_raw, dict) else {}
    tool_raw = pyproject_data.get("tool", {})
    tool_table = tool_raw if isinstance(tool_raw, dict) else {}
    envoi_raw = tool_table.get("envoi", {})
    envoi_table = envoi_raw if isinstance(envoi_raw, dict) else {}
    environment_raw = envoi_table.get("environment", {})
    environment_table = environment_raw if isinstance(environment_raw, dict) else {}

    name_value = first_non_empty_string(
        environment_table.get("name"),
        project_table.get("name"),
    )
    if name_value is None:
        raise ValueError(
            "Missing environment name. Set [project].name or "
            "[tool.envoi.environment].name in pyproject.toml."
        )
    name: str = name_value

    version_value = first_non_empty_string(
        environment_table.get("version"),
        project_table.get("version"),
        "0.1.0",
    )
    version: str = version_value if version_value is not None else "0.1.0"

    description_value = first_non_empty_string(
        environment_table.get("description"),
        project_table.get("description"),
        "",
    )
    description: str = description_value if description_value is not None else ""

    return {
        "name": name,
        "version": version,
        "description": description,
    }


def first_non_empty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
