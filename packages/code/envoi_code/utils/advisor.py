"""External advisor integration for turn-end feedback enrichment."""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from typing import Any

from envoi_code.utils.helpers import tprint

print = tprint

ADVISOR_THINKING_LEVELS = {"low", "medium", "high"}
ADVISOR_RETRY_ATTEMPTS = max(
    1,
    int(os.environ.get("ADVISOR_RETRY_ATTEMPTS", "3")),
)
ADVISOR_RETRY_BASE_DELAY_SECONDS = max(
    0.0,
    float(os.environ.get("ADVISOR_RETRY_BASE_DELAY_SECONDS", "1.0")),
)
ADVISOR_LOG_RESPONSE_PREVIEW_CHARS = max(
    0,
    int(os.environ.get("ADVISOR_LOG_RESPONSE_PREVIEW_CHARS", "240")),
)
ADVISOR_MAX_OUTPUT_TOKENS = max(
    1,
    int(os.environ.get("ADVISOR_MAX_OUTPUT_TOKENS", "128000")),
)


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


def extract_text_blocks(content: Any) -> list[str]:
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
    chunks = extract_text_blocks(content)
    if chunks:
        return "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()

    if isinstance(message, dict):
        chunks = extract_text_blocks(message.get("content"))
        if chunks:
            return "\n\n".join(
                chunk.strip() for chunk in chunks if chunk.strip()
            ).strip()
    return ""


def read_attr_or_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def summarize_content_blocks(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    summaries: list[dict[str, Any]] = []
    for block in content:
        block_type = read_attr_or_key(block, "type")
        text = read_attr_or_key(block, "text")
        item: dict[str, Any] = {"type": block_type}
        if isinstance(text, str):
            item["text_chars"] = len(text)
            if ADVISOR_LOG_RESPONSE_PREVIEW_CHARS > 0:
                item["text_preview"] = text[:ADVISOR_LOG_RESPONSE_PREVIEW_CHARS]
        summaries.append(item)
    return summaries


def summarize_anthropic_response(response: Any) -> dict[str, Any]:
    content = read_attr_or_key(response, "content")
    usage = read_attr_or_key(response, "usage")
    summary: dict[str, Any] = {
        "id": read_attr_or_key(response, "id"),
        "model": read_attr_or_key(response, "model"),
        "type": read_attr_or_key(response, "type"),
        "stop_reason": read_attr_or_key(response, "stop_reason"),
        "stop_sequence": read_attr_or_key(response, "stop_sequence"),
        "role": read_attr_or_key(response, "role"),
        "content_blocks": summarize_content_blocks(content),
    }
    if usage is not None:
        summary["usage"] = {
            "input_tokens": read_attr_or_key(usage, "input_tokens"),
            "output_tokens": read_attr_or_key(usage, "output_tokens"),
            "cache_creation_input_tokens": read_attr_or_key(
                usage,
                "cache_creation_input_tokens",
            ),
            "cache_read_input_tokens": read_attr_or_key(
                usage,
                "cache_read_input_tokens",
            ),
        }
    return summary


def build_payload_for_attempt(
    *,
    base_payload: dict[str, Any],
    attempt_number: int,
) -> tuple[dict[str, Any], str]:
    payload = dict(base_payload)
    if attempt_number <= 1:
        return payload, "thinking+output_config"
    if attempt_number == 2:
        payload.pop("output_config", None)
        return payload, "thinking"
    payload.pop("thinking", None)
    payload.pop("output_config", None)
    return payload, "basic"


async def request_anthropic_advisor(
    *,
    model_spec: str,
    thinking_level: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: int | None = None,
    max_output_tokens: int | None = None,
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
    normalized_effort = normalize_thinking_level(thinking_level)
    effective_max_output_tokens = (
        max_output_tokens
        if isinstance(max_output_tokens, int) and max_output_tokens > 0
        else ADVISOR_MAX_OUTPUT_TOKENS
    )
    request_timeout: float | None = (
        float(timeout_seconds)
        if isinstance(timeout_seconds, int) and timeout_seconds > 0
        else None
    )

    request_payload: dict[str, Any] = {
        "model": normalized_model,
        "max_tokens": effective_max_output_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": normalized_effort},
    }
    print(
        "[advisor] request_setup "
        f"model={normalized_model} thinking={normalized_effort} "
        f"max_tokens={effective_max_output_tokens} "
        f"timeout_seconds={request_timeout if request_timeout is not None else 'none'} "
        f"system_chars={len(system_prompt)} user_chars={len(user_prompt)} "
        f"max_attempts={ADVISOR_RETRY_ATTEMPTS}"
    )

    async with AsyncAnthropic(
        api_key=api_key,
        http_client=DefaultAioHttpClient(),
    ) as client:
        last_error: Exception | None = None
        for attempt in range(1, ADVISOR_RETRY_ATTEMPTS + 1):
            payload_for_attempt, payload_mode = build_payload_for_attempt(
                base_payload=request_payload,
                attempt_number=attempt,
            )
            print(
                "[advisor] request_attempt "
                f"attempt={attempt}/{ADVISOR_RETRY_ATTEMPTS} "
                f"mode={payload_mode} stream=True payload_keys={sorted(payload_for_attempt)}"
            )
            started_at = time.monotonic()
            try:
                async with client.messages.stream(
                    **payload_for_attempt,
                    timeout=request_timeout,
                ) as stream:
                    text_chunks: list[str] = []
                    async for text_delta in stream.text_stream:
                        if text_delta:
                            text_chunks.append(text_delta)
                    response = await stream.get_final_message()
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                response_summary = summarize_anthropic_response(response)
                print(
                    "[advisor] response_received "
                    f"attempt={attempt} elapsed_ms={elapsed_ms} "
                    f"summary={compact_json(response_summary)}"
                )

                text = "".join(text_chunks).strip()
                if not text:
                    text = extract_anthropic_message_text(response)
                if text.strip():
                    print(
                        "[advisor] response_text "
                        f"attempt={attempt} chars={len(text)} "
                        f"preview={text[:ADVISOR_LOG_RESPONSE_PREVIEW_CHARS]}"
                    )
                    return text

                error = RuntimeError("advisor returned an empty response")
                last_error = error
                print(
                    "[advisor] empty_response "
                    f"attempt={attempt} response_blocks="
                    f"{len(response_summary.get('content_blocks', []))}"
                )
            except Exception as error:  # noqa: BLE001
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                last_error = error
                print(
                    "[advisor] request_error "
                    f"attempt={attempt} elapsed_ms={elapsed_ms} "
                    f"error_type={type(error).__name__} "
                    f"error={str(error).strip()}"
                )
                print(
                    "[advisor] request_error_traceback "
                    + traceback.format_exc().strip()
                )

            if attempt < ADVISOR_RETRY_ATTEMPTS:
                delay_seconds = (
                    ADVISOR_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                )
                print(
                    "[advisor] retry_scheduled "
                    f"next_attempt={attempt + 1} "
                    f"sleep_seconds={delay_seconds:.2f}"
                )
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)

    if last_error is None:
        raise RuntimeError(
            "advisor request failed after retries without a captured error",
        )
    raise RuntimeError(
        "advisor request failed after "
        f"{ADVISOR_RETRY_ATTEMPTS} attempts: "
        f"{type(last_error).__name__}: {str(last_error).strip()}",
    ) from last_error
