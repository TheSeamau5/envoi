from __future__ import annotations

import argparse
import sys

from .constants import DEFAULT_IMAGE_NAME, DEFAULT_PORT


def main() -> None:
    parser = argparse.ArgumentParser(prog="envoi")
    subparsers = parser.add_subparsers(dest="command")

    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Build and run an environment in Docker",
    )
    deploy_parser.add_argument(
        "--path",
        default=".",
        help="Path to environment folder or Python file (default: .)",
    )
    deploy_parser.add_argument(
        "--module",
        default=None,
        help="Python module filename when --path is a folder",
    )
    deploy_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    deploy_parser.add_argument("--image", default=DEFAULT_IMAGE_NAME)
    deploy_parser.add_argument("--name", default=None)
    deploy_parser.add_argument("--no-build", action="store_true")
    deploy_parser.add_argument("--foreground", action="store_true")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "deploy":
        _run_deploy(args)


def _run_deploy(args: argparse.Namespace) -> None:
    from .deploy import deploy

    result = deploy(
        path=args.path,
        module=args.module,
        port=args.port,
        image_name=args.image,
        container_name=args.name,
        build=not args.no_build,
        detach=not args.foreground,
    )
    if result["container_id"]:
        print(f"Started container: {result['container_id']}")
    print(f"Runtime URL: {result['url']}")
