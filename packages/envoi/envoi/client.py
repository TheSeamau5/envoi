from __future__ import annotations

import json
from types import TracebackType
from typing import cast

import httpx

from .constants import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_SESSION_TIMEOUT_SECONDS,
)
from .utils import build_request_kwargs, mapping_from_object, to_jsonable


def parse_json_response(response: httpx.Response) -> object | None:
    try:
        return cast(object, response.json())
    except ValueError:
        return None


def object_dict(value: object | None) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return mapping_from_object(cast(object, value))


def response_error_message(response: httpx.Response, payload: object | None) -> str:
    payload_dict = object_dict(payload)
    if payload_dict is not None:
        error_value = payload_dict.get("error")
        if isinstance(error_value, str):
            return error_value

        error_mapping = object_dict(error_value)
        if error_mapping is not None:
            message = error_mapping.get("message")
            if isinstance(message, str):
                return message
            return json.dumps(error_mapping, ensure_ascii=False)

        if error_value is not None:
            return str(error_value)

    raw_text = response.text.strip()
    if raw_text:
        return raw_text

    return response.reason_phrase or "unknown error"


def raise_for_response_error(response: httpx.Response, payload: object | None) -> None:
    payload_dict = object_dict(payload)
    has_payload_error = payload_dict is not None and "error" in payload_dict
    if response.is_error or has_payload_error:
        message = response_error_message(response, payload)
        raise RuntimeError(
            f"Request failed ({response.status_code}): {message}"
        )


def schema_test_names(schema: dict[str, object]) -> list[str]:
    tests = schema.get("tests", [])
    if not isinstance(tests, list):
        return []
    test_items = cast(list[object], tests)

    names: list[str] = []
    for item in test_items:
        if isinstance(item, str):
            names.append(item)
            continue
        item_dict = object_dict(item)
        if item_dict is None:
            continue
        name_value = item_dict.get("name")
        if isinstance(name_value, str):
            names.append(name_value)
    return names


class Client:
    def __init__(
        self,
        url: str,
        schema: dict[str, object],
        http_client: httpx.AsyncClient,
    ) -> None:
        self.base_url: str = url.rstrip("/")
        self.schema: dict[str, object] = schema
        self.http_client: httpx.AsyncClient = http_client

    @property
    def tests(self) -> list[str]:
        return schema_test_names(self.schema)

    @property
    def has_setup(self) -> bool:
        capabilities = object_dict(self.schema.get("capabilities"))
        if capabilities is None:
            raise ValueError("Invalid envoi schema: missing capabilities")
        requires_session = capabilities.get("requires_session")
        if not isinstance(requires_session, bool):
            raise ValueError(
                "Invalid envoi schema: capabilities.requires_session must be a bool"
            )
        return requires_session

    async def test(self, name: str = "", **kwargs: object) -> object:
        if self.has_setup:
            raise RuntimeError("This environment requires a session.")

        endpoint = f"{self.base_url}/test" if not name else f"{self.base_url}/test/{name}"
        request_kwargs = build_request_kwargs(kwargs)
        response = await self.http_client.post(endpoint, **request_kwargs)
        payload = parse_json_response(response)
        raise_for_response_error(response, payload)
        if payload is None:
            raise RuntimeError(
                f"Request failed ({response.status_code}): invalid JSON response body"
            )
        return payload

    async def session(
        self, timeout_seconds: int = DEFAULT_SESSION_TIMEOUT_SECONDS, **kwargs: object
    ) -> Session:
        request_kwargs = build_request_kwargs(kwargs)
        request_kwargs["data"]["timeout"] = str(timeout_seconds)

        response = await self.http_client.post(
            f"{self.base_url}/session", **request_kwargs
        )
        session_payload = parse_json_response(response)
        raise_for_response_error(response, session_payload)
        session_mapping = object_dict(session_payload)
        if session_mapping is None:
            raise RuntimeError(
                f"Request failed ({response.status_code}): invalid JSON response body"
            )

        session_id = session_mapping.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError(
                f"Request failed ({response.status_code}): invalid session id"
            )
        timeout_raw = session_mapping.get("timeout")
        timeout_value = timeout_raw if isinstance(timeout_raw, int) else timeout_seconds

        return Session(
            client=self,
            session_id=session_id,
            timeout_seconds=timeout_value,
        )

    async def close(self) -> None:
        await self.http_client.aclose()

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.close()


class Session:
    def __init__(
        self,
        client: Client,
        session_id: str,
        timeout_seconds: int,
        close_client_on_close: bool = False,
    ) -> None:
        self.client: Client = client
        self.session_id: str = session_id
        self.timeout_seconds: int = timeout_seconds
        self.close_client_on_close: bool = close_client_on_close

    async def test(self, name: str = "", **kwargs: object) -> object:
        endpoint = (
            f"{self.client.base_url}/session/{self.session_id}/test"
            if not name
            else f"{self.client.base_url}/session/{self.session_id}/test/{name}"
        )
        response = await self.client.http_client.post(
            endpoint,
            data={"params": json.dumps(to_jsonable(kwargs))} if kwargs else {},
        )
        payload = parse_json_response(response)
        raise_for_response_error(response, payload)
        if payload is None:
            raise RuntimeError(
                f"Request failed ({response.status_code}): invalid JSON response body"
            )
        return payload

    async def close(self) -> None:
        try:
            response = await self.client.http_client.delete(
                f"{self.client.base_url}/session/{self.session_id}"
            )
            payload = parse_json_response(response)
            if response.status_code != 404:
                raise_for_response_error(response, payload)
        finally:
            if self.close_client_on_close:
                self.close_client_on_close = False
                await self.client.close()

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.close()


async def connect(
    url: str, timeout_seconds: int = DEFAULT_HTTP_TIMEOUT_SECONDS
) -> Client:
    http_client = httpx.AsyncClient(timeout=timeout_seconds)
    try:
        response = await http_client.get(f"{url.rstrip('/')}/schema")
        payload = parse_json_response(response)
        raise_for_response_error(response, payload)
        schema_payload = object_dict(payload)
        if schema_payload is None:
            raise RuntimeError(
                f"Request failed ({response.status_code}): invalid JSON response body"
            )
    except Exception:
        await http_client.aclose()
        raise
    return Client(url=url, schema=schema_payload, http_client=http_client)


async def connect_session(
    url: str,
    *,
    connect_timeout_seconds: int = DEFAULT_HTTP_TIMEOUT_SECONDS,
    session_timeout_seconds: int = DEFAULT_SESSION_TIMEOUT_SECONDS,
    **kwargs: object,
) -> Session:
    envoi_client = await connect(url, timeout_seconds=connect_timeout_seconds)
    try:
        envoi_session = await envoi_client.session(
            timeout_seconds=session_timeout_seconds,
            **kwargs,
        )
    except Exception:
        await envoi_client.close()
        raise

    envoi_session.close_client_on_close = True
    return envoi_session
