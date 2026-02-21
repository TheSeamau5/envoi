"""
Short launcher for trajectory runs.

Primary usage:
    uv run trace
    uv run trace --agent codex --max-parts 100 --detach
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from typing import Any

import boto3

RETRYABLE_SESSION_END_REASONS = {"agent_error", "timeout", "envoi_error"}


def artifact_uri(bucket: str, trajectory_id: str, filename: str) -> str:
    return f"s3://{bucket}/trajectories/{trajectory_id}/{filename}"


def _common_runner_args(args: argparse.Namespace, trajectory_id: str) -> list[str]:
    """Build runner.py argument list shared by both modal and direct execution."""
    parts: list[str] = [
        "--agent",
        args.agent,
        "--max-parts",
        str(args.max_parts),
        "--trajectory-id",
        trajectory_id,
    ]
    if args.model:
        parts.extend(["--model", args.model])
    if args.message_timeout_seconds is not None:
        parts.extend(["--message-timeout-seconds", str(args.message_timeout_seconds)])
    if args.sandbox != "modal":
        parts.extend(["--sandbox-provider", args.sandbox])
    if args.agent == "codex" and args.codex_auth_file:
        parts.extend(["--codex-auth-file", args.codex_auth_file])
    return parts


def build_modal_command(args: argparse.Namespace, trajectory_id: str) -> list[str]:
    command: list[str] = ["modal", "run"]
    if args.detach:
        command.append("--detach")
    command.append("runner.py")
    command.extend(_common_runner_args(args, trajectory_id))
    if args.non_preemptible:
        command.append("--non-preemptible")
    return command


def build_direct_command(args: argparse.Namespace, trajectory_id: str) -> list[str]:
    """Build a direct python invocation for non-Modal sandbox providers."""
    command: list[str] = ["python3", "runner.py"]
    command.extend(_common_runner_args(args, trajectory_id))
    return command


def load_trace_session_end(bucket: str, trajectory_id: str) -> tuple[str | None, int | None]:
    key = f"trajectories/{trajectory_id}/agent_trace.json"
    client = boto3.client(
        "s3",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception:  # noqa: BLE001
        return None, None
    body = response.get("Body")
    if body is None:
        return None, None
    try:
        payload: Any = json.loads(body.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None, None
    if not isinstance(payload, dict):
        return None, None
    session_end = payload.get("session_end")
    if not isinstance(session_end, dict):
        return None, None
    reason = session_end.get("reason")
    total_parts = session_end.get("total_parts")
    return (
        reason if isinstance(reason, str) and reason else None,
        total_parts if isinstance(total_parts, int) else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch runner.py with short defaults.",
    )
    parser.set_defaults(non_preemptible=True)
    parser.add_argument("--agent", choices=["codex", "opencode"], default="codex")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-parts", type=int, default=1000)
    parser.add_argument("--message-timeout-seconds", type=int, default=None)
    parser.add_argument(
        "--non-preemptible",
        dest="non_preemptible",
        action="store_true",
        help="Run with Modal non-preemptible execution (default).",
    )
    parser.add_argument(
        "--preemptible",
        dest="non_preemptible",
        action="store_false",
        help="Opt into preemptible execution.",
    )
    parser.add_argument("--detach", action="store_true")
    parser.add_argument(
        "--sandbox",
        choices=["modal", "e2b"],
        default="modal",
        help="Sandbox provider to use (default: modal).",
    )
    parser.add_argument("--trajectory-id", default=None)
    parser.add_argument("--codex-auth-file", default="~/.codex/auth.json")
    parser.add_argument(
        "--auto-resume",
        dest="auto_resume",
        action="store_true",
        default=True,
        help="Automatically relaunch the same trajectory ID on retryable failures (default).",
    )
    parser.add_argument(
        "--no-auto-resume",
        dest="auto_resume",
        action="store_false",
        help="Disable automatic relaunch.",
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=20,
        help="Maximum number of relaunches for a trajectory (0 means unlimited).",
    )
    parser.add_argument(
        "--restart-delay-seconds",
        type=int,
        default=10,
        help="Delay between relaunch attempts.",
    )
    args = parser.parse_args()

    trajectory_id = args.trajectory_id or str(uuid.uuid4())
    bucket = os.environ.get("AWS_S3_BUCKET", "envoi-trace-data")
    trace_uri = artifact_uri(bucket, trajectory_id, "agent_trace.json")
    bundle_uri = artifact_uri(bucket, trajectory_id, "repo.bundle")

    banner = "=" * 72
    print(banner, flush=True)
    print(f"TRAJECTORY_ID: {trajectory_id}", flush=True)
    print(f"TRACE_S3_URI: {trace_uri}", flush=True)
    print(f"BUNDLE_S3_URI: {bundle_uri}", flush=True)
    print(
        "agent="
        f"{args.agent} max_parts={args.max_parts} detach={args.detach} "
        f"non_preemptible={args.non_preemptible}",
        flush=True,
    )
    print(banner, flush=True)

    print(f"sandbox={args.sandbox}", flush=True)

    if args.detach and args.auto_resume:
        print("[launcher] detach mode disables auto-resume checks", flush=True)

    if args.sandbox == "modal":
        command = build_modal_command(args, trajectory_id)
    else:
        command = build_direct_command(args, trajectory_id)
    restart_count = 0
    while True:
        print(
            f"[launcher] attempt={restart_count + 1} trajectory_id={trajectory_id}",
            flush=True,
        )
        result = subprocess.run(command, check=False)  # noqa: S603
        if result.returncode in {130, 143}:
            raise SystemExit(result.returncode)

        should_retry = False
        retry_reason = ""
        if result.returncode != 0:
            should_retry = args.auto_resume and not args.detach
            retry_reason = f"modal_exit={result.returncode}"
        elif args.auto_resume and not args.detach:
            reason, total_parts = load_trace_session_end(bucket, trajectory_id)
            if (
                reason in RETRYABLE_SESSION_END_REASONS
                and (total_parts is None or total_parts < args.max_parts)
            ):
                should_retry = True
                retry_reason = f"session_end={reason} parts={total_parts}"

        if not should_retry:
            raise SystemExit(result.returncode)

        restart_count += 1
        if args.max_restarts > 0 and restart_count > args.max_restarts:
            print(
                "[launcher] maximum restarts reached; stopping",
                flush=True,
            )
            raise SystemExit(result.returncode if result.returncode != 0 else 1)
        print(
            f"[launcher] restarting in {args.restart_delay_seconds}s "
            f"({retry_reason})",
            flush=True,
        )
        time.sleep(max(0, args.restart_delay_seconds))


if __name__ == "__main__":
    main()
