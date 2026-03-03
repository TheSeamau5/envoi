from __future__ import annotations

import json
from typing import cast

import httpx

from .utils import mapping_from_object


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


def response_has_error(response: httpx.Response, payload: object | None) -> bool:
    payload_dict = object_dict(payload)
    has_payload_error = payload_dict is not None and "error" in payload_dict
    return response.is_error or has_payload_error
