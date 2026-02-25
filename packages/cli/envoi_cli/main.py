from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from envoi.deploy import deploy

try:
    from envoi_code.scripts.trace import (
        add_run_args,
        extract_param_flags,
        graph_command,
        run_command,
    )
except ImportError:
    add_run_args = None
    extract_param_flags = None
    graph_command = None
    run_command = None


def normalize_code_argv(argv: list[str]) -> list[str]:
    """Normalize shorthand `envoi code` forms before argparse parsing."""
    if not argv or argv[0] != "code":
        return argv

    if len(argv) == 1:
        return argv

    code_head = argv[1]
    if code_head in {"graph", "-h", "--help"}:
        return argv

    if code_head == "run":
        if len(argv) > 2 and not argv[2].startswith("-"):
            return ["code", "run", "--example", argv[2], *argv[3:]]
        return argv

    if code_head.startswith("-"):
        return argv

    return ["code", "run", "--example", code_head, *argv[2:]]


def main() -> None:
    # Load .env from the repo root (walk up from cwd until we find one)
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()  # fallback: python-dotenv's own search
    parser = argparse.ArgumentParser(prog="envoi")
    subparsers = parser.add_subparsers(dest="command")

    # --- deploy subcommand (always available, comes from envoi SDK) ---
    deploy_parser = subparsers.add_parser("deploy", help="Deploy an environment locally")
    deploy_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to environment folder or Python file (default: .)",
    )
    deploy_parser.add_argument("--port", type=int, default=8000)

    # --- code subcommand (available when envoi-code is installed) ---
    _has_code = add_run_args is not None

    if _has_code:
        code_parser = subparsers.add_parser("code", help="Run coding agent trajectories")
        code_subparsers = code_parser.add_subparsers(dest="code_command")

        # "run" subcommand
        run_parser = code_subparsers.add_parser("run", help="Run an agent trajectory")
        run_parser.add_argument(
            "--example",
            default=None,
            help="Path to example dir (resolves task/ and environment/ within it)",
        )
        assert add_run_args is not None
        add_run_args(run_parser)

        # Also add run args + --example directly on the code parser so
        # `envoi code --example ...` works without the `run` subcommand.
        code_parser.add_argument(
            "--example",
            default=None,
            help="Path to example dir (resolves task/ and environment/ within it)",
        )
        add_run_args(code_parser)

        graph_parser = code_subparsers.add_parser("graph", help="Analyze a trajectory")
        graph_parser.add_argument("trajectory_id", help="Trajectory ID in S3")
        graph_parser.add_argument("--bucket", default=None)
        graph_parser.add_argument("--output", default=None)
        graph_parser.add_argument("--part", type=int, default=None)
        graph_parser.add_argument("--checkout-dest", default=None)

    normalized_argv = normalize_code_argv(sys.argv[1:])
    is_code_command = bool(normalized_argv) and normalized_argv[0] == "code"
    is_code_graph = (
        len(normalized_argv) >= 2 and normalized_argv[0] == "code"
        and normalized_argv[1] == "graph"
    )
    if extract_param_flags is not None and is_code_command and not is_code_graph:
        argv_without_params, raw_params = extract_param_flags(normalized_argv)
    else:
        argv_without_params, raw_params = normalized_argv, {}
    args = parser.parse_args(argv_without_params)
    args.raw_params = raw_params

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "deploy":
        result = deploy(path=args.path, port=args.port)
        if result["container_id"]:
            print(f"Started container: {result['container_id']}")
        print(f"Runtime URL: {result['url']}")

    elif args.command == "code":
        if not _has_code:
            print(
                "envoi-code is not installed. Run: pip install envoi-cli[code]",
                file=sys.stderr,
            )
            sys.exit(1)

        # Default to "run" when no subcommand given (e.g. `envoi code --example ...`)
        code_command = args.code_command or "run"

        if code_command == "run":
            if args.example:
                example = Path(args.example)
                args.task = str(example / "task")
                args.env = str(example / "environment")
            if not args.task or not args.env:
                print(
                    "error: --task and --env are required (or use --example)",
                    file=sys.stderr,
                )
                sys.exit(1)
            assert run_command is not None
            run_command(args)
        elif code_command == "graph":
            assert graph_command is not None
            graph_command(args)
        else:
            parser.parse_args(["code", "--help"])


if __name__ == "__main__":
    main()
