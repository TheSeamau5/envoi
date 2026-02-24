"""
envoi-trace CLI -- the main entrypoint for running and analyzing trajectories.

Run mode (default, no subcommand): launches runner.py via Modal to execute an
agent trajectory. Handles auto-resume on retryable failures (agent_error,
timeout, envoi_error) and prints trajectory ID + S3 URIs (trace/bundle/logs)
at startup.

Graph mode (subcommand): downloads trace + bundle from S3 and generates
suite-level analysis graphs.

Usage:
    envoi code --task examples/c_compiler/task --env examples/c_compiler/environment
    envoi code --agent codex --max-parts 1000 --task <path> --env <path>
    envoi code graph <trajectory_id>
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import boto3
import pyarrow.parquet as pq

from envoi_code.scripts.graph_trace import async_main as graph_async_main

RETRYABLE_SESSION_END_REASONS = {"agent_error", "timeout", "envoi_error"}


def artifact_uri(bucket: str, trajectory_id: str, filename: str) -> str:
    return f"s3://{bucket}/trajectories/{trajectory_id}/{filename}"


def common_runner_args(
    args: argparse.Namespace, trajectory_id: str, *, modal_mode: bool = False,
) -> list[str]:
    """Build runner.py argument list shared by both modal and direct execution."""
    parts: list[str] = [
        "--agent",
        args.agent,
        "--trajectory-id",
        trajectory_id,
        "--task-dir",
        str(args.task),
        "--environment-dir",
        str(args.env),
    ]
    if args.max_parts is not None:
        parts.extend(["--max-parts", str(args.max_parts)])
    if args.max_turns is not None:
        parts.extend(["--max-turns", str(args.max_turns)])
    if modal_mode:
        if args.test:
            parts.extend(
                [
                    "--test-json",
                    json.dumps(args.test, ensure_ascii=False),
                ],
            )
    else:
        for test_path in (args.test or []):
            parts.extend(["--test", test_path])
    if args.test_timeout_seconds is not None:
        parts.extend(["--test-timeout-seconds", str(args.test_timeout_seconds)])
    if args.timeout_seconds is not None:
        parts.extend(["--timeout-seconds", str(args.timeout_seconds)])
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
    deploy_path = str(Path(__file__).resolve().parent.parent / "sandbox" / "modal" / "deploy.py")
    command.append(deploy_path)
    command.extend(common_runner_args(args, trajectory_id, modal_mode=True))
    if args.non_preemptible:
        command.append("--non-preemptible")
    return command


def build_direct_command(args: argparse.Namespace, trajectory_id: str) -> list[str]:
    """Build a direct python invocation for non-Modal sandbox providers."""
    orchestrator_path = str(Path(__file__).resolve().parent.parent / "orchestrator.py")
    command: list[str] = ["python3", orchestrator_path]
    command.extend(common_runner_args(args, trajectory_id))
    return command


def load_trace_session_end(
    bucket: str, trajectory_id: str,
) -> tuple[str | None, int | None]:
    key = f"trajectories/{trajectory_id}/trace.parquet"
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
        buf = io.BytesIO(body.read())
        table = pq.read_table(
            buf, columns=["session_end_reason", "session_end_total_parts"],
        )
        if table.num_rows == 0:
            return None, None
        reason = table.column("session_end_reason")[0].as_py()
        total_parts = table.column("session_end_total_parts")[0].as_py()
    except Exception:  # noqa: BLE001
        return None, None
    return (
        reason if isinstance(reason, str) and reason else None,
        total_parts if isinstance(total_parts, int) else None,
    )


def add_run_args(parser: argparse.ArgumentParser) -> None:
    """Add run-mode arguments to a parser."""
    parser.set_defaults(non_preemptible=True)
    parser.add_argument("--agent", choices=["codex", "opencode"], default="codex")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--max-parts",
        type=int,
        default=None,
        help="Optional part budget. If omitted, parts are unbounded.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Optional turn budget. Stops after this many turns.",
    )
    parser.add_argument(
        "--test",
        action="append",
        default=None,
        help=(
            "Optional test path to run during evaluation. "
            "Repeat to target multiple paths. "
            "If omitted, all tests run."
        ),
    )
    parser.add_argument(
        "--test-timeout-seconds",
        type=int,
        default=None,
        help=(
            "Timeout for each commit/turn-end evaluation run. "
            "Defaults to EVALUATION_TIMEOUT_SECONDS."
        ),
    )
    parser.add_argument("--task", default=None, help="Path to task directory.")
    parser.add_argument("--env", default=None, help="Path to environment directory.")
    parser.add_argument("--message-timeout-seconds", type=int, default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=7200,
        help="Total run timeout in seconds (default: 7200).",
    )
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
        help="Automatically relaunch on retryable failures (default).",
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
        help="Maximum number of relaunches (0 = unlimited).",
    )
    parser.add_argument(
        "--restart-delay-seconds",
        type=int,
        default=10,
        help="Delay between relaunch attempts.",
    )


def run_command(args: argparse.Namespace) -> None:
    """Execute a trajectory run."""
    trajectory_id = args.trajectory_id or str(uuid.uuid4())
    bucket = os.environ.get("AWS_S3_BUCKET")
    if not bucket:
        raise SystemExit("AWS_S3_BUCKET environment variable is required")
    trace_uri = artifact_uri(bucket, trajectory_id, "trace.parquet")
    bundle_uri = artifact_uri(bucket, trajectory_id, "repo.bundle")
    logs_uri = artifact_uri(bucket, trajectory_id, "logs.parquet")

    banner = "=" * 72
    print(banner, flush=True)
    print(f"TRAJECTORY_ID: {trajectory_id}", flush=True)
    print(f"TRACE_S3_URI: {trace_uri}", flush=True)
    print(f"BUNDLE_S3_URI: {bundle_uri}", flush=True)
    print(f"LOGS_S3_URI: {logs_uri}", flush=True)
    part_limit_label = (
        str(args.max_parts)
        if isinstance(args.max_parts, int) and args.max_parts > 0
        else "none"
    )
    turn_limit_label = (
        str(args.max_turns)
        if isinstance(args.max_turns, int) and args.max_turns > 0
        else "none"
    )
    test_timeout_label = (
        f"{args.test_timeout_seconds}s"
        if args.test_timeout_seconds is not None
        else "default"
    )
    print(
        f"agent={args.agent} part_limit={part_limit_label} "
        f"turn_limit={turn_limit_label} "
        f"timeout={args.timeout_seconds}s "
        f"tests={(','.join(args.test) if args.test else 'all')} "
        f"test_timeout={test_timeout_label} "
        f"detach={args.detach} non_preemptible={args.non_preemptible}",
        flush=True,
    )
    print(f"task={args.task} env={args.env}", flush=True)
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
            under_part_cap = (
                args.max_parts is None
                or total_parts is None
                or total_parts < args.max_parts
            )
            if (
                reason in RETRYABLE_SESSION_END_REASONS
                and under_part_cap
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


def graph_command(args: argparse.Namespace) -> None:
    """Execute graph generation (delegates to graph_trace)."""
    argv_backup = sys.argv
    sys.argv = ["envoi-trace graph", args.trajectory_id]
    if args.bucket:
        sys.argv.extend(["--bucket", args.bucket])
    if args.output:
        sys.argv.extend(["--output", args.output])
    if args.part is not None:
        sys.argv.extend(["--part", str(args.part)])
    if args.checkout_dest:
        sys.argv.extend(["--checkout-dest", args.checkout_dest])
    try:
        asyncio.run(graph_async_main())
    finally:
        sys.argv = argv_backup


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="envoi-trace",
        description="envoi-trace: run agent trajectories and build graphs.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # graph subcommand
    graph_parser = subparsers.add_parser(
        "graph", help="Build graph artifacts from a trajectory.",
    )
    graph_parser.add_argument("trajectory_id", help="Trajectory ID in S3.")
    graph_parser.add_argument(
        "--bucket",
        default=os.environ.get("AWS_S3_BUCKET"),
    )
    graph_parser.add_argument("--output", default=None)
    graph_parser.add_argument("--part", type=int, default=None)
    graph_parser.add_argument("--checkout-dest", default=None)

    # Default (no subcommand) = run mode: add run args to the main parser
    add_run_args(parser)

    args = parser.parse_args()

    if args.command == "graph":
        graph_command(args)
    else:
        run_command(args)


if __name__ == "__main__":
    main()
