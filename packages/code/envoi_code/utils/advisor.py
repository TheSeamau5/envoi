"""External advisor integration for turn-end feedback enrichment."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from envoi_code.utils.helpers import tprint

print = tprint

ADVISOR_THINKING_LEVELS = {"low", "medium", "high"}
THINKING_BUDGET_BY_LEVEL = {
    "low": 2048,
    "medium": 8192,
    "high": 16384,
}


def normalize_advisor_model(model_spec: str) -> str:
    value = model_spec.strip()
    if not value:
        raise ValueError("advisor model cannot be empty")

    if value.startswith("@"):
        value = value[1:]

    provider: str | None = None
    model_name = value
    if "/" in value:
        provider, model_name = value.split("/", 1)
        provider = provider.strip().lower()

    if provider not in {None, "anthropic"}:
        raise ValueError(
            "Only anthropic advisor models are currently supported. "
            f"Received provider={provider!r}."
        )

    model_name = model_name.strip()
    if not model_name:
        raise ValueError("advisor model name cannot be empty")

    return model_name.replace(".", "-")


def normalize_thinking_level(level: str | None) -> str:
    value = (level or "high").strip().lower()
    if value not in ADVISOR_THINKING_LEVELS:
        raise ValueError(
            "advisor thinking level must be one of: "
            + ", ".join(sorted(ADVISOR_THINKING_LEVELS))
        )
    return value


def _extract_text_blocks(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []

    chunks: list[str] = []
    for block in content:
        text: str | None = None
        block_type: str | None = None
        if isinstance(block, dict):
            raw_type = block.get("type")
            block_type = raw_type if isinstance(raw_type, str) else None
            raw_text = block.get("text")
            text = raw_text if isinstance(raw_text, str) else None
        else:
            raw_type = getattr(block, "type", None)
            block_type = raw_type if isinstance(raw_type, str) else None
            raw_text = getattr(block, "text", None)
            text = raw_text if isinstance(raw_text, str) else None
        if block_type == "text" and text:
            chunks.append(text)
    return chunks


def extract_anthropic_message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    chunks = _extract_text_blocks(content)
    if chunks:
        return "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()

    if isinstance(message, dict):
        chunks = _extract_text_blocks(message.get("content"))
        if chunks:
            return "\n\n".join(
                chunk.strip() for chunk in chunks if chunk.strip()
            ).strip()
    return ""


async def request_anthropic_advisor(
    *,
    model_spec: str,
    thinking_level: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: int = 180,
    max_output_tokens: int = 2200,
) -> str:
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is missing")

    try:
        from anthropic import AsyncAnthropic, DefaultAioHttpClient
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(
            "anthropic package is not installed in the runner environment"
        ) from error

    normalized_model = normalize_advisor_model(model_spec)
    normalized_thinking = normalize_thinking_level(thinking_level)
    thinking_payload = {
        "type": "enabled",
        "budget_tokens": THINKING_BUDGET_BY_LEVEL[normalized_thinking],
    }

    request_payload: dict[str, Any] = {
        "model": normalized_model,
        "max_tokens": max_output_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "thinking": thinking_payload,
    }

    async with AsyncAnthropic(
        api_key=api_key,
        http_client=DefaultAioHttpClient(),
    ) as client:
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await client.messages.create(**request_payload)
        except Exception as error:  # noqa: BLE001
            error_text = str(error).strip().lower()
            if "thinking" not in error_text:
                raise
            # Fallback for SDK/API variants that do not accept `thinking`.
            request_payload.pop("thinking", None)
            async with asyncio.timeout(timeout_seconds):
                response = await client.messages.create(**request_payload)

    text = extract_anthropic_message_text(response)
    if not text:
        raise RuntimeError("advisor returned an empty response")
    return text
