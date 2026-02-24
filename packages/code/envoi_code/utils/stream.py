"""Real-time stream callback for agent part events.

Creates the on_stream_part callback that runner.py passes to agent.run_turn().
When the agent emits a part event (via TRACE_EVENT on stderr), this callback
records it as a PartRecord, triggers git checkpoints for file changes, extracts
envoi test calls, and persists the updated trace to S3.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from envoi_code.models import (
    AgentTrace,
    EnvoiCall,
    PartRecord,
    RepoCheckpoint,
    TurnRecord,
)
from envoi_code.sandbox.base import Sandbox
from envoi_code.utils.git import create_part_checkpoint, get_changed_files
from envoi_code.utils.helpers import (
    iso_from_epoch_ms,
    redact_secrets,
    token_estimate,
    tprint,
    word_count,
)
from envoi_code.utils.solve import SolveTracker
from envoi_code.utils.storage import save_trace_parquet

print = tprint


def make_stream_part_callback(
    *,
    sandbox: Sandbox,
    trajectory_id: str,
    agent_trace: AgentTrace,
    tracker: SolveTracker,
    environment: str,
    task_params: dict[str, Any] | None,
    agent_name: str,
    resolved_model: str,
    effective_max_parts: int,
    part_counter: list[int],
    git_commit_ref: list[str | None],
    last_part_timestamp_ms_ref: list[int | None],
    turn_record: TurnRecord | None,
    session_id: str,
    schedule_commit_evaluation: (
        Callable[[str, int, int], None] | None
    ) = None,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    async def on_stream_part(
        stream_event: dict[str, Any],
    ) -> None:
        if turn_record is None:
            return
        if stream_event.get("event") != "part.completed":
            return
        if part_counter[0] >= effective_max_parts:
            return

        try:
            part_counter[0] += 1
            absolute_part = part_counter[0]
            raw_files = stream_event.get("files")
            files = (
                [
                    f
                    for f in raw_files
                    if isinstance(f, str) and f
                ]
                if isinstance(raw_files, list)
                else []
            )
            part_type_value = stream_event.get("part_type")
            part_type = (
                part_type_value
                if isinstance(part_type_value, str)
                else None
            )
            item_type_value = stream_event.get("item_type")
            item_type = (
                item_type_value
                if isinstance(item_type_value, str)
                else None
            )
            summary_value = stream_event.get("summary")
            summary = (
                summary_value
                if isinstance(summary_value, str) and summary_value
                else None
            )
            content_value = stream_event.get("content")
            content = (
                content_value
                if isinstance(content_value, str)
                else None
            )
            tool_name_value = stream_event.get("tool_name")
            tool_name = (
                tool_name_value
                if isinstance(tool_name_value, str)
                else None
            )
            tool_status_value = stream_event.get("tool_status")
            tool_status = (
                tool_status_value
                if isinstance(tool_status_value, str)
                else None
            )
            tool_exit_code_value = stream_event.get(
                "tool_exit_code",
            )
            tool_exit_code = (
                tool_exit_code_value
                if isinstance(tool_exit_code_value, int)
                else None
            )
            token_usage_value = stream_event.get("token_usage")
            token_usage = (
                redact_secrets(token_usage_value)
                if isinstance(token_usage_value, dict)
                else None
            )
            provider_part_value = stream_event.get(
                "provider_part",
            )
            provider_part = (
                redact_secrets(provider_part_value)
                if isinstance(provider_part_value, dict)
                else None
            )
            provider_item_value = stream_event.get(
                "provider_item",
            )
            provider_item = (
                redact_secrets(provider_item_value)
                if isinstance(provider_item_value, dict)
                else None
            )
            provider_event_value = stream_event.get(
                "provider_event",
            )
            provider_event = (
                redact_secrets(provider_event_value)
                if isinstance(provider_event_value, dict)
                else None
            )
            tool_input = redact_secrets(
                stream_event.get("tool_input"),
            )
            tool_output = redact_secrets(
                stream_event.get("tool_output"),
            )
            tool_error = redact_secrets(
                stream_event.get("tool_error"),
            )
            role_value = stream_event.get("role")
            role: Literal["assistant", "user"] = (
                role_value
                if role_value in {"assistant", "user"}
                else "assistant"
            )
            event_timestamp_ms_value = stream_event.get(
                "timestamp_ms",
            )
            event_timestamp_ms = (
                event_timestamp_ms_value
                if isinstance(event_timestamp_ms_value, int)
                else int(time.time() * 1000)
            )
            prev_ts = last_part_timestamp_ms_ref[0]
            duration_ms = (
                event_timestamp_ms - prev_ts
                if isinstance(prev_ts, int)
                and event_timestamp_ms >= prev_ts
                else None
            )
            last_part_timestamp_ms_ref[0] = event_timestamp_ms
            has_file_change = bool(
                stream_event.get("has_file_change"),
            )
            changed_files = await get_changed_files(sandbox)
            detected_file_change = bool(changed_files)
            checkpoint: RepoCheckpoint | None = None
            commit_for_async_eval: str | None = None
            should_checkpoint = (
                has_file_change or detected_file_change
            )
            if should_checkpoint:
                if not has_file_change:
                    print(
                        "[stream] corrected has_file_change"
                        "=false with git dirty "
                        f"state on part {absolute_part}"
                    )
                checkpoint = await create_part_checkpoint(
                    sandbox=sandbox,
                    trajectory_id=trajectory_id,
                    part=absolute_part,
                    changed_files_hint=(
                        changed_files or files
                    ),
                    commit_before_hint=git_commit_ref[0],
                )
                if (
                    has_file_change
                    and not checkpoint.committed
                    and not detected_file_change
                ):
                    print(
                        "[git] part signaled file change "
                        "but git was clean; "
                        "recorded metadata only on "
                        f"part {absolute_part}"
                    )
                if files and not checkpoint.changed_files:
                    checkpoint.changed_files = files
                if (
                    checkpoint.changed_files and not files
                ):
                    files = list(checkpoint.changed_files)
                git_commit_ref[0] = (
                    checkpoint.commit_after
                    or checkpoint.commit_before
                    or git_commit_ref[0]
                )
                if (
                    checkpoint.committed
                    and isinstance(
                        checkpoint.commit_after, str
                    )
                    and checkpoint.commit_after
                ):
                    commit_for_async_eval = (
                        checkpoint.commit_after
                    )

            part_envoi_calls: list[EnvoiCall] = []
            raw_test_call = stream_event.get("test_call")
            if isinstance(raw_test_call, dict):
                try:
                    part_envoi_calls.append(
                        EnvoiCall(**raw_test_call),
                    )
                except Exception:
                    pass
            if part_envoi_calls:
                tracker.update(part_envoi_calls)

            part_record = PartRecord(
                trajectory_id=trajectory_id,
                session_id=session_id,
                agent=agent_name,
                part=absolute_part,
                timestamp=iso_from_epoch_ms(
                    event_timestamp_ms,
                ),
                role=role,
                part_type=part_type,
                item_type=item_type,
                summary=summary,
                duration_ms=duration_ms,
                agent_model=resolved_model,
                git_commit=git_commit_ref[0],
                files=files,
                content=content,
                summary_word_count=word_count(summary),
                summary_token_estimate=token_estimate(
                    summary,
                ),
                content_word_count=word_count(content),
                content_token_estimate=token_estimate(
                    content,
                ),
                tool_name=tool_name,
                tool_status=tool_status,
                tool_input=tool_input,
                tool_output=tool_output,
                tool_error=tool_error,
                tool_exit_code=tool_exit_code,
                token_usage=token_usage,
                provider_part=provider_part,
                provider_item=provider_item,
                provider_event=provider_event,
                patch=(
                    checkpoint.patch if checkpoint else None
                ),
                envoi_calls=part_envoi_calls,
                testing_state=tracker.snapshot(),
                repo_checkpoint=checkpoint,
            )
            turn_record.parts.append(part_record)
            agent_trace.parts.append(part_record)
            if turn_record.part_start is None:
                turn_record.part_start = absolute_part
            turn_record.part_end = absolute_part
            turn_record.git_commit = git_commit_ref[0]
            if checkpoint is not None:
                turn_record.repo_checkpoint = checkpoint
            if (
                commit_for_async_eval is not None
                and schedule_commit_evaluation is not None
            ):
                schedule_commit_evaluation(
                    commit_for_async_eval,
                    absolute_part,
                    turn_record.turn,
                )
            save_trace_parquet(
                trajectory_id, agent_trace,
                environment=environment,
                task_params=task_params,
            )
        except Exception as callback_err:
            print(
                "[stream] failed to process live part "
                f"event: {callback_err}"
            )

    return on_stream_part
