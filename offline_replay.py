"""
Offline trajectory replay and evaluation.

Given:
- agent_trace.json (local path or s3:// URI)
- repo.bundle (local path or s3:// URI)

This script reconstructs repository state per part, evaluates the compiler
for each unique commit, and writes a machine-readable JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
import envoi
import httpx

REQUIRED_PATHS: list[str] = [
    "basics",
    *[f"wacct/chapter_{i}" for i in range(1, 21)],
    *[f"c_testsuite/part_{i}" for i in range(1, 6)],
    *[f"torture/part_{i}" for i in range(1, 11)],
]

HEAVY_TEST_ROOTS: dict[str, Path] = {
    "wacct": Path("/opt/tests/wacct/tests"),
    "c_testsuite": Path("/opt/tests/c-testsuite/tests/single-exec"),
    "torture": Path("/opt/tests/llvm-test-suite/SingleSource/Regression/C/gcc-c-torture/execute"),
}


@dataclass
class RuntimeHandle:
    process: subprocess.Popen[str]
    url: str


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URI: {uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def is_s3_uri(value: str) -> bool:
    return value.startswith("s3://")


def download_if_needed(source: str, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    if not is_s3_uri(source):
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Missing file: {path}")
        return path

    bucket, key = parse_s3_uri(source)
    local_path = destination_dir / Path(key).name
    boto3.client("s3").download_file(bucket, key, str(local_path))
    return local_path


def load_agent_trace(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("agent_trace.json must contain a JSON object")
    if not isinstance(data.get("parts"), list) and not isinstance(data.get("turns"), list):
        raise ValueError("agent_trace.json missing both 'parts' and 'turns' lists")
    return data


def artifact_uri(bucket: str, trajectory_id: str, filename: str) -> str:
    return f"s3://{bucket}/trajectories/{trajectory_id}/{filename}"


def find_free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def wait_for_runtime(url: str, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"{url}/schema", timeout=2.0)
                if response.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.3)
    raise TimeoutError(f"Timed out waiting for runtime at {url}")


async def start_runtime(environment_file: Path, port: int) -> RuntimeHandle:
    command = [
        "python",
        "-m",
        "envoi.runtime",
        "--file",
        str(environment_file),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    process = subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        await wait_for_runtime(url, timeout_seconds=90)
    except Exception:
        process.terminate()
        process.wait(timeout=5)
        raise
    return RuntimeHandle(process=process, url=url)


def stop_runtime(handle: RuntimeHandle) -> None:
    if handle.process.poll() is None:
        handle.process.terminate()
        try:
            handle.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            handle.process.kill()
            handle.process.wait(timeout=5)


def clone_bundle(bundle_path: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    subprocess.run(  # noqa: S603
        ["git", "clone", str(bundle_path), str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination


def checkout_commit(repo_path: Path, commit: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo_path), "checkout", "--force", commit],
        check=True,
        capture_output=True,
        text=True,
    )


def has_required_test_fixtures(test_paths: list[str]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    needs_wacct = any(path.startswith("wacct/") for path in test_paths)
    needs_c_testsuite = any(path.startswith("c_testsuite/") for path in test_paths)
    needs_torture = any(path.startswith("torture/") for path in test_paths)

    checks = [
        ("wacct", needs_wacct),
        ("c_testsuite", needs_c_testsuite),
        ("torture", needs_torture),
    ]
    for key, needed in checks:
        if needed and not HEAVY_TEST_ROOTS[key].exists():
            missing.append(str(HEAVY_TEST_ROOTS[key]))
    return len(missing) == 0, missing


def parse_commit_from_part(part: dict[str, Any]) -> str | None:
    for key in ("git_commit",):
        value = part.get(key)
        if isinstance(value, str) and value:
            return value
    repo_checkpoint = part.get("repo_checkpoint")
    if isinstance(repo_checkpoint, dict):
        for key in ("commit_after", "commit_before"):
            value = repo_checkpoint.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def get_part_records(trace: dict[str, Any]) -> list[dict[str, Any]]:
    raw_parts = trace.get("parts")
    if isinstance(raw_parts, list):
        return [part for part in raw_parts if isinstance(part, dict)]

    raw_turns = trace.get("turns")
    if isinstance(raw_turns, list):
        flattened_parts: list[dict[str, Any]] = []
        for turn in raw_turns:
            if not isinstance(turn, dict):
                continue
            turn_number = turn.get("turn")
            turn_parts = turn.get("parts")
            if isinstance(turn_parts, list) and turn_parts:
                for part in turn_parts:
                    if not isinstance(part, dict):
                        continue
                    merged = dict(part)
                    if merged.get("turn") is None and isinstance(turn_number, int):
                        merged["turn"] = turn_number
                    flattened_parts.append(merged)
            else:
                # Legacy traces where `turns` held part-like rows directly.
                flattened_parts.append(turn)
        return flattened_parts
    return []


def extract_part_rows(trace: dict[str, Any]) -> list[dict[str, Any]]:
    parts_raw = [
        part
        for part in get_part_records(trace)
        if part.get("part") is not None or part.get("turn") is not None
    ]
    parts_raw.sort(key=lambda part: int(part.get("part") or part.get("turn") or 0))

    rows: list[dict[str, Any]] = []
    for part in parts_raw:
        part_number = int(part.get("part") or part.get("turn") or 0)
        rows.append(
            {
                "part": part_number,
                "commit": parse_commit_from_part(part),
                "timestamp": part.get("timestamp"),
            }
        )
    return rows


def get_unique_commits(rows: list[dict[str, Any]]) -> list[str]:
    commit_order: list[str] = []
    for row in rows:
        commit = row.get("commit")
        if isinstance(commit, str) and commit and commit not in commit_order:
            commit_order.append(commit)
    return commit_order


def resolve_part_commit(trace: dict[str, Any], part_number: int) -> tuple[str, dict[str, Any]]:
    for row in extract_part_rows(trace):
        if row["part"] != part_number:
            continue
        commit = row.get("commit")
        if not isinstance(commit, str) or not commit:
            raise ValueError(f"Part {part_number} has no commit recorded")
        return commit, row
    raise ValueError(f"Part {part_number} not found in trace")


async def evaluate_commit(
    *,
    envoi_url: str,
    repo_path: Path,
    test_paths: list[str],
) -> dict[str, Any]:
    started_at = time.monotonic()
    path_results: dict[str, Any] = {}
    total_passed = 0
    total_failed = 0
    total_tests = 0

    docs = envoi.Documents(repo_path)
    async with await envoi.connect_session(
        envoi_url,
        submission=docs,
        session_timeout_seconds=7200,
    ) as session:
        for path in test_paths:
            try:
                result = await session.test(path)
            except Exception as error:  # noqa: BLE001
                path_results[path] = {
                    "ok": False,
                    "error": str(error),
                    "passed": 0,
                    "failed": 0,
                    "total": 0,
                }
                total_failed += 1
                continue

            if not isinstance(result, dict):
                path_results[path] = {
                    "ok": False,
                    "error": f"Unexpected result type: {type(result).__name__}",
                    "passed": 0,
                    "failed": 0,
                    "total": 0,
                }
                total_failed += 1
                continue

            passed = int(result.get("passed", 0))
            failed = int(result.get("failed", 0))
            total = int(result.get("total", 0))
            path_results[path] = {
                "ok": failed == 0 and total > 0,
                "passed": passed,
                "failed": failed,
                "total": total,
            }
            total_passed += passed
            total_failed += failed
            total_tests += total

    duration_ms = int((time.monotonic() - started_at) * 1000)
    return {
        "duration_ms": duration_ms,
        "passed": total_passed,
        "failed": total_failed,
        "total": total_tests,
        "path_results": path_results,
    }


def reconstruct_repo_at_part(
    *,
    trace_path: Path,
    bundle_path: Path,
    part: int,
    destination: Path,
) -> dict[str, Any]:
    trace = load_agent_trace(trace_path)
    commit, row = resolve_part_commit(trace, part)
    destination.parent.mkdir(parents=True, exist_ok=True)
    clone_bundle(bundle_path, destination)
    checkout_commit(destination, commit)
    return {
        "trajectory_id": trace.get("trajectory_id"),
        "session_id": trace.get("session_id"),
        "part": part,
        "timestamp": row.get("timestamp"),
        "commit": commit,
        "repo_path": str(destination),
    }


async def replay_trace(
    *,
    trace_path: Path,
    bundle_path: Path,
    output_path: Path,
    environment_file: Path,
    test_paths: list[str],
) -> dict[str, Any]:
    trace = load_agent_trace(trace_path)
    parts = extract_part_rows(trace)
    commit_order = get_unique_commits(parts)

    if not commit_order:
        raise ValueError("No commits found in trace parts")

    fixtures_ok, missing_fixtures = has_required_test_fixtures(test_paths)
    if not fixtures_ok:
        missing = "\n".join(f"- {p}" for p in missing_fixtures)
        raise RuntimeError(f"Missing required test fixtures. Expected paths:\n{missing}")

    workspace_root = Path(tempfile.mkdtemp(prefix="envoi-replay-")).resolve()
    repo_path = workspace_root / "repo"
    clone_bundle(bundle_path, repo_path)

    runtime_port = find_free_port()
    runtime = await start_runtime(environment_file=environment_file, port=runtime_port)

    commit_evals: dict[str, Any] = {}
    try:
        for index, commit in enumerate(commit_order, start=1):
            print(f"[replay] evaluating commit {index}/{len(commit_order)}: {commit}")
            checkout_commit(repo_path, commit)
            commit_evals[commit] = await evaluate_commit(
                envoi_url=runtime.url,
                repo_path=repo_path,
                test_paths=test_paths,
            )
    finally:
        stop_runtime(runtime)
        shutil.rmtree(workspace_root, ignore_errors=True)

    part_evals: list[dict[str, Any]] = []
    for part in parts:
        commit = part["commit"]
        part_result = {
            "part": part["part"],
            "timestamp": part["timestamp"],
            "commit": commit,
            "evaluation": commit_evals.get(commit),
        }
        part_evals.append(part_result)

    report = {
        "trajectory_id": trace.get("trajectory_id"),
        "session_id": trace.get("session_id"),
        "generated_at": now_iso(),
        "input": {
            "trace_path": str(trace_path),
            "bundle_path": str(bundle_path),
            "environment_file": str(environment_file),
            "test_paths": test_paths,
        },
        "commits_evaluated": commit_order,
        "commit_evaluations": commit_evals,
        "part_to_commit": {str(s["part"]): s.get("commit") for s in parts},
        "part_evaluations": part_evals,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return report


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay trajectory artifacts, evaluate tests, or reconstruct repo at part t.",
    )
    parser.add_argument(
        "--mode",
        choices=["evaluate", "checkout-part"],
        default="evaluate",
        help=(
            "evaluate: run tests for all unique commits; "
            "checkout-part: materialize repo at a specific part."
        ),
    )
    parser.add_argument(
        "--trace",
        help="Local path or s3:// URI to agent_trace.json",
    )
    parser.add_argument(
        "--bundle",
        help="Local path or s3:// URI to repo.bundle",
    )
    parser.add_argument(
        "--trajectory-id",
        help=(
            "If provided, trace and bundle are resolved as "
            "s3://<bucket>/trajectories/<trajectory-id>/agent_trace.json and repo.bundle"
        ),
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("AWS_S3_BUCKET", "envoi-trace-data"),
        help="S3 bucket used with --trajectory-id (default: AWS_S3_BUCKET or envoi-trace-data)",
    )
    parser.add_argument(
        "--output",
        default="output/offline_eval.json",
        help=(
            "Where to write JSON output. For --mode evaluate this is the evaluation report; "
            "for --mode checkout-part this is checkout metadata."
        ),
    )
    parser.add_argument(
        "--part",
        type=int,
        help="Part number (required for --mode checkout-part)",
    )
    parser.add_argument(
        "--checkout-dest",
        default=None,
        help="Destination directory for --mode checkout-part (default: output/repo_part_<part>)",
    )
    parser.add_argument(
        "--environment-file",
        default="environment/main.py",
        help="Path to the envoi environment module (used in --mode evaluate)",
    )
    parser.add_argument(
        "--test-path",
        action="append",
        dest="test_paths",
        default=[],
        help="Specific test path(s) to run. If omitted, runs all required paths.",
    )
    args = parser.parse_args()

    if args.trajectory_id:
        trace_source = artifact_uri(args.bucket, args.trajectory_id, "agent_trace.json")
        bundle_source = artifact_uri(args.bucket, args.trajectory_id, "repo.bundle")
    else:
        trace_source = args.trace
        bundle_source = args.bundle

    if not trace_source or not bundle_source:
        parser.error(
            "Provide --trajectory-id (and optional --bucket), or provide both --trace and --bundle."
        )

    scratch = Path(tempfile.mkdtemp(prefix="envoi-artifacts-")).resolve()
    try:
        trace_path = download_if_needed(trace_source, scratch)
        bundle_path = download_if_needed(bundle_source, scratch)

        output_path = Path(args.output).expanduser().resolve()

        if args.mode == "checkout-part":
            if args.part is None:
                parser.error("--part is required when --mode checkout-part")

            checkout_dest = (
                Path(args.checkout_dest).expanduser().resolve()
                if args.checkout_dest
                else Path(f"output/repo_part_{args.part}").expanduser().resolve()
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

        environment_file = Path(args.environment_file).expanduser().resolve()
        if not environment_file.exists():
            raise FileNotFoundError(f"Environment file not found: {environment_file}")
        test_paths = args.test_paths if args.test_paths else list(REQUIRED_PATHS)

        report = await replay_trace(
            trace_path=trace_path,
            bundle_path=bundle_path,
            output_path=output_path,
            environment_file=environment_file,
            test_paths=test_paths,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(
        f"[done] wrote {len(report['part_evaluations'])} part evaluations "
        f"to {Path(args.output).expanduser().resolve()}"
    )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
