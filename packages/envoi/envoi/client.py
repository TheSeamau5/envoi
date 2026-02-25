from __future__ import annotations

import json
from typing import Any

import httpx

from .constants import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_SESSION_TIMEOUT_SECONDS,
)
from .utils import (
    build_request_kwargs,
    to_jsonable,
)


def parse_json_response(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except ValueError:
        return None


def response_error_message(response: httpx.Response, payload: Any | None) -> str:
    if isinstance(payload, dict):
        error_value = payload.get("error")
        if isinstance(error_value, str):
            return error_value

        if isinstance(error_value, dict):
            message = error_value.get("message")
            if isinstance(message, str):
                return message
            return json.dumps(error_value, ensure_ascii=False)

        if error_value is not None:
            return str(error_value)

    raw_text = response.text.strip()
    if raw_text:
        return raw_text

    return response.reason_phrase or "unknown error"


def raise_for_response_error(response: httpx.Response, payload: Any | None) -> None:
    has_payload_error = isinstance(payload, dict) and "error" in payload
    if response.is_error or has_payload_error:
        message = response_error_message(response, payload)
        raise RuntimeError(
            f"Request failed ({response.status_code}): {message}"
        )


def schema_test_names(schema: dict[str, Any]) -> list[str]:
    tests = schema.get("tests", [])
    if not isinstance(tests, list):
        return []

    names: list[str] = []
    for item in tests:
        if isinstance(item, str):
            names.append(item)
            continue
        if isinstance(item, dict):
            name_value = item.get("name")
            if isinstance(name_value, str):
                names.append(name_value)
    return names


class Client:
    def __init__(
        self,
        url: str,
        schema: dict[str, Any],
        http_client: httpx.AsyncClient,
    ) -> None:
        self.base_url = url.rstrip("/")
        self.schema = schema
        self.http_client = http_client

    @property
    def tests(self) -> list[str]:
        return schema_test_names(self.schema)

    @property
    def has_setup(self) -> bool:
        capabilities = self.schema.get("capabilities")
        if not isinstance(capabilities, dict):
            raise ValueError("Invalid envoi schema: missing capabilities")
        requires_session = capabilities.get("requires_session")
        if not isinstance(requires_session, bool):
            raise ValueError(
                "Invalid envoi schema: capabilities.requires_session must be a bool"
            )
        return requires_session

    async def test(self, name: str = "", **kwargs: Any) -> Any:
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
        self, timeout_seconds: int = DEFAULT_SESSION_TIMEOUT_SECONDS, **kwargs: Any
    ) -> Session:
        request_kwargs = build_request_kwargs(kwargs)
        request_kwargs["data"]["timeout"] = str(timeout_seconds)

        response = await self.http_client.post(
            f"{self.base_url}/session", **request_kwargs
        )
        session_payload = parse_json_response(response)
        raise_for_response_error(response, session_payload)
        if not isinstance(session_payload, dict):
            raise RuntimeError(
                f"Request failed ({response.status_code}): invalid JSON response body"
            )

        return Session(
            client=self,
            session_id=session_payload["session_id"],
            timeout_seconds=session_payload.get("timeout", timeout_seconds),
        )

    async def close(self) -> None:
        await self.http_client.aclose()

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()


class Session:
    def __init__(
        self,
        client: Client,
        session_id: str,
        timeout_seconds: int,
        close_client_on_close: bool = False,
    ) -> None:
        self.client = client
        self.session_id = session_id
        self.timeout_seconds = timeout_seconds
        self.close_client_on_close = close_client_on_close

    async def test(self, name: str = "", **kwargs: Any) -> Any:
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

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()


async def connect(
    url: str, timeout_seconds: int = DEFAULT_HTTP_TIMEOUT_SECONDS
) -> Client:
    http_client = httpx.AsyncClient(timeout=timeout_seconds)
    try:
        response = await http_client.get(f"{url.rstrip('/')}/schema")
        payload = parse_json_response(response)
        raise_for_response_error(response, payload)
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Request failed ({response.status_code}): invalid JSON response body"
            )
    except Exception:
        await http_client.aclose()
        raise
    return Client(url=url, schema=payload, http_client=http_client)


async def connect_session(
    url: str,
    *,
    connect_timeout_seconds: int = DEFAULT_HTTP_TIMEOUT_SECONDS,
    session_timeout_seconds: int = DEFAULT_SESSION_TIMEOUT_SECONDS,
    **kwargs: Any,
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
