"""
Short CLI for inspecting a trajectory from S3.

Primary usage:
    uv run graph_trace <trajectory_id>

This defaults to running suite-level analysis and writing:
    output/graph_trace_<trajectory_id>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from scripts.offline_replay import (
    analyze_trace,
    artifact_uri,
    download_if_needed,
    reconstruct_repo_at_part,
)


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze or checkout a trajectory with short defaults.",
    )
    parser.add_argument(
        "trajectory_id",
        help="Trajectory ID under trajectories/<id>/ in S3.",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("AWS_S3_BUCKET", "envoi-trace-data"),
        help="S3 bucket (default: AWS_S3_BUCKET or envoi-trace-data).",
    )
    parser.add_argument(
        "--part",
        type=int,
        help="If set, checkout this part instead of running analysis.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output JSON path.",
    )
    parser.add_argument(
        "--checkout-dest",
        default=None,
        help="Checkout destination when --part is set.",
    )
    parser.add_argument(
        "--environment-file",
        default="environment/main.py",
        help="Environment file used for analysis mode.",
    )
    args = parser.parse_args()

    trace_source = artifact_uri(args.bucket, args.trajectory_id, "agent_trace.json")
    bundle_source = artifact_uri(args.bucket, args.trajectory_id, "repo.bundle")

    scratch = Path(tempfile.mkdtemp(prefix="graph-trace-artifacts-")).resolve()
    try:
        trace_path = download_if_needed(trace_source, scratch)
        bundle_path = download_if_needed(bundle_source, scratch)

        if args.part is not None:
            checkout_dest = (
                Path(args.checkout_dest).expanduser().resolve()
                if args.checkout_dest
                else Path(f"output/repo_part_{args.part}").expanduser().resolve()
            )
            output_path = (
                Path(args.output).expanduser().resolve()
                if args.output
                else Path(f"output/repo_part_{args.part}.json").expanduser().resolve()
            )
            metadata = reconstruct_repo_at_part(
                trace_path=trace_path,
                bundle_path=bundle_path,
                part=args.part,
                destination=checkout_dest,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(metadata, indent=2))
            print(
                f"[done] checked out part {args.part} at commit {metadata['commit']} "
                f"to {checkout_dest}"
            )
            print(f"[done] wrote checkout metadata to {output_path}")
            return

        output_path = (
            Path(args.output).expanduser().resolve()
            if args.output
            else Path(f"output/graph_trace_{args.trajectory_id}.json").expanduser().resolve()
        )
        environment_file = Path(args.environment_file).expanduser().resolve()
        if not environment_file.exists():
            raise FileNotFoundError(f"Environment file not found: {environment_file}")

        await analyze_trace(
            trace_path=trace_path,
            bundle_path=bundle_path,
            output_path=output_path,
            environment_file=environment_file,
        )
        print(f"[done] wrote analysis to {output_path}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
