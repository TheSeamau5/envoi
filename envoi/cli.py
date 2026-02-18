from __future__ import annotations

import argparse

from .constants import DEFAULT_PORT
from .deploy import deploy


def main() -> None:
    parser = argparse.ArgumentParser(prog="envoi")
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to environment folder or Python file (default: .)",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    args = parser.parse_args()

    result = deploy(
        path=args.path,
        port=args.port,
    )
    if result["container_id"]:
        print(f"Started container: {result['container_id']}")
    print(f"Runtime URL: {result['url']}")
