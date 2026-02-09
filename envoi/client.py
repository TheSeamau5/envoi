from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from .constants import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_SESSION_TIMEOUT_SECONDS,
)
from .utils import (
    build_request_kwargs,
    schema_item_values,
    to_jsonable,
    to_websocket_url,
)


class Client:
    def __init__(
        self,
        url: str,
        schema: dict[str, Any],
        http_client: httpx.AsyncClient,
    ) -> None:
        self.base_url = url.rstrip("/")
        self.websocket_base_url = to_websocket_url(self.base_url)
        self.schema = schema
        self.http_client = http_client

    @property
    def tests(self) -> list[str]:
        return schema_item_values(self.schema.get("tests", []), "name", str)

    @property
    def actions(self) -> list[str]:
        return schema_item_values(self.schema.get("actions", []), "name", str)

    @property
    def observables(self) -> list[str]:
        return schema_item_values(self.schema.get("observables", []), "name", str)

    @property
    def has_setup(self) -> bool:
        if "has_setup" in self.schema:
            return bool(self.schema.get("has_setup"))
        return self.schema.get("setup") is not None

    async def test(self, name: str, **kwargs: Any) -> Any:
        if self.has_setup:
            raise RuntimeError("This environment requires a session.")

        request_kwargs = build_request_kwargs(kwargs)
        response = await self.http_client.post(
            f"{self.base_url}/test/{name}", **request_kwargs
        )
        response.raise_for_status()
        return response.json()

    async def session(
        self, timeout_seconds: int = DEFAULT_SESSION_TIMEOUT_SECONDS, **kwargs: Any
    ) -> Session:
        request_kwargs = build_request_kwargs(kwargs)
        request_kwargs["data"]["timeout"] = str(timeout_seconds)

        response = await self.http_client.post(
            f"{self.base_url}/session", **request_kwargs
        )
        response.raise_for_status()

        session_payload = response.json()
        if "error" in session_payload:
            raise RuntimeError(f"Session setup failed: {session_payload['error']}")

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
        self.stream_connection: ClientConnection | None = None

    async def test(self, name: str, **kwargs: Any) -> Any:
        response = await self.client.http_client.post(
            f"{self.client.base_url}/session/{self.session_id}/test/{name}",
            data={"params": json.dumps(to_jsonable(kwargs))} if kwargs else {},
        )
        response.raise_for_status()
        return response.json()

    async def observe(self, name: str | None = None) -> AsyncIterator[Any]:
        if name is not None and name not in self.client.observables:
            raise ValueError(f"Unknown observable: {name}")

        stream_connection = await self._get_stream_connection()
        try:
            async for raw_message in stream_connection:
                decoded_message = json.loads(raw_message)
                message_type = decoded_message.get("type")

                if message_type == "observe":
                    observe_name = decoded_message.get("name")
                    if name is None or observe_name == name:
                        yield decoded_message.get("data")
                elif message_type == "error":
                    raise RuntimeError(decoded_message.get("message", "Unknown error"))
        except ConnectionClosed:
            return

    async def action(self, name: str, **kwargs: Any) -> None:
        stream_connection = await self._get_stream_connection()
        payload = {
            "type": "action",
            "name": name,
            "data": to_jsonable(kwargs),
        }
        await stream_connection.send(json.dumps(payload))

    async def close(self) -> None:
        try:
            if self.stream_connection is not None:
                await self.stream_connection.close()
                self.stream_connection = None

            response = await self.client.http_client.delete(
                f"{self.client.base_url}/session/{self.session_id}"
            )

            if response.status_code not in (200, 404):
                response.raise_for_status()
        finally:
            if self.close_client_on_close:
                self.close_client_on_close = False
                await self.client.close()

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    async def _get_stream_connection(self) -> ClientConnection:
        if self.stream_connection is not None:
            return self.stream_connection

        stream_url = f"{self.client.websocket_base_url}/session/{self.session_id}/stream"
        stream_connection = await websockets.connect(stream_url)
        self.stream_connection = stream_connection

        await stream_connection.send(json.dumps({"type": "configure"}))
        return stream_connection


async def connect(
    url: str, timeout_seconds: int = DEFAULT_HTTP_TIMEOUT_SECONDS
) -> Client:
    http_client = httpx.AsyncClient(timeout=timeout_seconds)
    try:
        response = await http_client.get(f"{url.rstrip('/')}/schema")
        response.raise_for_status()
    except Exception:
        await http_client.aclose()
        raise
    return Client(url=url, schema=response.json(), http_client=http_client)


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
