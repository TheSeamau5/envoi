"""
Short launcher for trajectory runs.

Primary usage:
    uv run trace
    uv run trace --agent codex --max-parts 100 --detach
"""

from __future__ import annotations

import argparse
import os
import subprocess
import uuid


def artifact_uri(bucket: str, trajectory_id: str, filename: str) -> str:
    return f"s3://{bucket}/trajectories/{trajectory_id}/{filename}"


def build_modal_command(args: argparse.Namespace, trajectory_id: str) -> list[str]:
    command: list[str] = ["modal", "run"]
    if args.detach:
        command.append("--detach")
    command.extend(
        [
            "runner.py",
            "--agent",
            args.agent,
            "--max-parts",
            str(args.max_parts),
            "--trajectory-id",
            trajectory_id,
        ]
    )
    if args.model:
        command.extend(["--model", args.model])
    if args.message_timeout_seconds is not None:
        command.extend(["--message-timeout-seconds", str(args.message_timeout_seconds)])
    if args.non_preemptible:
        command.append("--non-preemptible")
    if args.agent == "codex" and args.codex_auth_file:
        command.extend(["--codex-auth-file", args.codex_auth_file])
    return command


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch runner.py with short defaults.",
    )
    parser.add_argument("--agent", choices=["codex", "opencode"], default="codex")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-parts", type=int, default=1000)
    parser.add_argument("--message-timeout-seconds", type=int, default=None)
    parser.add_argument("--non-preemptible", action="store_true")
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--trajectory-id", default=None)
    parser.add_argument("--codex-auth-file", default="~/.codex/auth.json")
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
        f"agent={args.agent} max_parts={args.max_parts} detach={args.detach}",
        flush=True,
    )
    print(banner, flush=True)

    command = build_modal_command(args, trajectory_id)
    result = subprocess.run(command, check=False)  # noqa: S603
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
