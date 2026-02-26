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
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import boto3
import pyarrow.parquet as pq

from envoi_code.scripts.graph_trace import async_main as graph_async_main

RETRYABLE_SESSION_END_REASONS = {"agent_error", "timeout", "envoi_error"}


def artifact_uri(bucket: str, trajectory_id: str, filename: str) -> str:
    return f"s3://{bucket}/trajectories/{trajectory_id}/{filename}"


def resolve_positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    if parsed <= 0:
        return default
    return parsed


def normalize_e2b_timeout_for_plan(args: argparse.Namespace) -> None:
    if getattr(args, "sandbox", None) != "e2b":
        return
    requested_timeout = args.timeout_seconds
    if not isinstance(requested_timeout, int) or requested_timeout <= 0:
        return
    shutdown_grace_seconds = resolve_positive_int_env(
        "SHUTDOWN_GRACE_SECONDS",
        300,
    )
    max_session_seconds = resolve_positive_int_env(
        "E2B_MAX_SESSION_SECONDS",
        3600,
    )
    max_run_timeout = max(60, max_session_seconds - shutdown_grace_seconds)
    if requested_timeout <= max_run_timeout:
        return
    args.timeout_seconds = max_run_timeout
    print(
        "[launcher] sandbox=e2b timeout clamped for plan/session cap: "
        f"requested={requested_timeout}s "
        f"grace={shutdown_grace_seconds}s "
        f"max_session={max_session_seconds}s "
        f"applied={max_run_timeout}s",
        flush=True,
    )


def normalize_param_key(flag_name: str) -> str:
    raw = flag_name.removeprefix("--param-").strip()
    if not raw:
        raise SystemExit("Invalid param flag: missing name after --param-")
    return raw.replace("-", "_").lower()


def append_param_value(
    raw_params: dict[str, Any],
    key: str,
    value: str,
) -> None:
    if key not in raw_params:
        raw_params[key] = value
        return
    existing = raw_params[key]
    if isinstance(existing, list):
        existing.append(value)
        return
    raw_params[key] = [existing, value]


def extract_param_flags(argv: list[str]) -> tuple[list[str], dict[str, Any]]:
    passthrough_args: list[str] = []
    raw_params: dict[str, Any] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("--param-"):
            passthrough_args.append(token)
            index += 1
            continue
        if "=" in token:
            flag_name, value = token.split("=", 1)
            key = normalize_param_key(flag_name)
            append_param_value(raw_params, key, value)
            index += 1
            continue
        if index + 1 >= len(argv):
            raise SystemExit(f"Missing value for {token}")
        next_token = argv[index + 1]
        if next_token.startswith("-"):
            raise SystemExit(
                f"Missing value for {token} (use {token}=<value> for values starting with '-')"
            )
        key = normalize_param_key(token)
        append_param_value(raw_params, key, next_token)
        index += 2
    return passthrough_args, raw_params


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
    if getattr(args, "raw_params", None):
        parts.extend([
            "--raw-params-json",
            json.dumps(args.raw_params, ensure_ascii=False),
        ])
    if args.sandbox_cpu is not None:
        parts.extend(["--sandbox-cpu", str(args.sandbox_cpu)])
    if args.sandbox_memory_mb is not None:
        parts.extend(["--sandbox-memory-mb", str(args.sandbox_memory_mb)])
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


def run_child_with_interrupt_handling(command: list[str]) -> int:
    """Run child process and translate Ctrl+C into graceful child shutdown."""
    proc = subprocess.Popen(command)  # noqa: S603
    try:
        return proc.wait()
    except KeyboardInterrupt:
        print(
            "[launcher] interrupt received; stopping active run",
            flush=True,
        )
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            pass

        shutdown_grace_seconds = 30
        try:
            proc.wait(timeout=shutdown_grace_seconds)
        except subprocess.TimeoutExpired:
            print(
                "[launcher] child did not exit after "
                f"{shutdown_grace_seconds}s; terminating",
                flush=True,
            )
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print(
                    "[launcher] child still alive; killing",
                    flush=True,
                )
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
        # User interrupted the launcher: do not auto-resume.
        return 130


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
    parser.add_argument("--sandbox-cpu", type=float, default=None)
    parser.add_argument("--sandbox-memory-mb", type=int, default=None)
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
    normalize_e2b_timeout_for_plan(args)
    trajectory_id = args.trajectory_id or str(uuid.uuid4())
    bucket = os.environ.get("AWS_S3_BUCKET")
    if not bucket:
        raise SystemExit("AWS_S3_BUCKET environment variable is required")
    trace_uri = artifact_uri(bucket, trajectory_id, "trace.parquet")
    bundle_uri = artifact_uri(bucket, trajectory_id, "repo.bundle")
    logs_uri = artifact_uri(bucket, trajectory_id, "logs.parquet")

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
        "[launcher] start "
        f"trajectory_id={trajectory_id} "
        f"sandbox={args.sandbox} "
        f"agent={args.agent} "
        f"part_limit={part_limit_label} "
        f"turn_limit={turn_limit_label} "
        f"timeout={args.timeout_seconds}s "
        f"tests={(','.join(args.test) if args.test else 'all')} "
        f"test_timeout={test_timeout_label}",
        flush=True,
    )
    print(
        "[launcher] io "
        f"task={args.task} env={args.env} "
        f"trace={trace_uri} bundle={bundle_uri} logs={logs_uri}",
        flush=True,
    )
    print(
        "[launcher] mode "
        f"detach={args.detach} non_preemptible={args.non_preemptible}",
        flush=True,
    )
    if getattr(args, "raw_params", None):
        print(
            "[launcher] params="
            + json.dumps(
                args.raw_params,
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

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
        return_code = run_child_with_interrupt_handling(command)
        if return_code in {130, 143}:
            raise SystemExit(return_code)

        should_retry = False
        retry_reason = ""
        if return_code != 0:
            should_retry = args.auto_resume and not args.detach
            retry_reason = f"modal_exit={return_code}"
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
            raise SystemExit(return_code)

        restart_count += 1
        if args.max_restarts > 0 and restart_count > args.max_restarts:
            print(
                "[launcher] maximum restarts reached; stopping",
                flush=True,
            )
            raise SystemExit(return_code if return_code != 0 else 1)
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
    raw_argv = sys.argv[1:]
    if raw_argv and raw_argv[0] == "graph":
        argv_without_params, raw_params = raw_argv, {}
    else:
        argv_without_params, raw_params = extract_param_flags(raw_argv)
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

    args = parser.parse_args(argv_without_params)
    args.raw_params = raw_params

    if args.command == "graph":
        graph_command(args)
    else:
        run_command(args)


if __name__ == "__main__":
    main()
