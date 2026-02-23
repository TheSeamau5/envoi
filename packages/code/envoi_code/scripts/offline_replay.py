"""
Offline trajectory replay and evaluation.

Given:
- trace.parquet (local path or s3:// URI)
- repo.bundle (local path or s3:// URI)

This script reconstructs repository state per part, evaluates each unique
commit in the current task environment, and writes a machine-readable report.
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

SUITE_PATHS: list[str] = ["basics", "wacct", "c_testsuite", "torture"]
REQUIRED_PATHS: list[str] = [
    "basics",
    *[f"wacct/chapter_{i}" for i in range(1, 21)],
    *[f"c_testsuite/part_{i}" for i in range(1, 6)],
    *[f"torture/part_{i}" for i in range(1, 11)],
]
HEAVY_TEST_ROOTS: dict[str, str] = {
    "wacct": "/opt/tests/wacct/tests",
    "c_testsuite": "/opt/tests/c-testsuite/tests/single-exec",
    "torture": (
        "/opt/tests/llvm-test-suite/SingleSource/Regression/C/"
        "gcc-c-torture/execute"
    ),
}
DEFAULT_TASK_FIXTURES_ROOT = Path("/opt/tests")


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


def load_trace(path: Path) -> dict[str, Any]:
    if path.suffix == ".parquet":
        from utils.trace_parquet import parquet_to_trace_dict

        return parquet_to_trace_dict(str(path))
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Trace file must contain a JSON object")
    if not isinstance(data.get("parts"), list) and not isinstance(data.get("turns"), list):
        raise ValueError("Trace file missing both 'parts' and 'turns' lists")
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


async def start_runtime(environment_file: Path, port: int, fixtures_root: Path) -> RuntimeHandle:
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
    runtime_env = os.environ.copy()
    runtime_env["ENVOI_TESTS_ROOT"] = str(fixtures_root)

    process = subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=runtime_env,
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


def default_fixtures_root() -> Path:
    env_root = os.environ.get("ENVOI_TESTS_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    if DEFAULT_TASK_FIXTURES_ROOT.exists():
        return DEFAULT_TASK_FIXTURES_ROOT

    if os.access(DEFAULT_TASK_FIXTURES_ROOT.parent, os.W_OK):
        return DEFAULT_TASK_FIXTURES_ROOT

    return (Path.home() / ".cache" / "envoi-tests").resolve()


def resolve_fixture_roots(fixtures_root: Path) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for suite, configured in HEAVY_TEST_ROOTS.items():
        configured_path = Path(configured)
        if configured_path.is_absolute():
            try:
                rel = configured_path.relative_to(DEFAULT_TASK_FIXTURES_ROOT)
                roots[suite] = fixtures_root / rel
            except ValueError:
                roots[suite] = configured_path
        else:
            roots[suite] = fixtures_root / configured_path
    return roots


def has_required_test_fixtures(
    test_paths: list[str],
    fixtures_root: Path,
) -> tuple[bool, list[str]]:
    heavy_roots = resolve_fixture_roots(fixtures_root)
    missing: list[str] = []
    for key, root in heavy_roots.items():
        needed = any(path == key or path.startswith(f"{key}/") for path in test_paths)
        if not needed:
            continue
        if key == "wacct":
            expected = root.parent / "expected_results.json"
            if not root.is_dir() or not expected.is_file():
                missing.append(str(root))
        elif key in {"c_testsuite", "torture"}:
            if not root.is_dir() or not any(root.glob("*.c")):
                missing.append(str(root))
        elif not root.exists():
            missing.append(str(root))
    return len(missing) == 0, missing


def run_cmd(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(  # noqa: S603
        command,
        check=True,
        cwd=str(cwd) if cwd else None,
    )


def ensure_required_test_fixtures(test_paths: list[str], fixtures_root: Path) -> None:
    heavy_roots = resolve_fixture_roots(fixtures_root)
    needed = {
        key
        for key in heavy_roots
        if any(path == key or path.startswith(f"{key}/") for path in test_paths)
    }
    if not needed:
        return

    fixtures_root.mkdir(parents=True, exist_ok=True)

    if "c_testsuite" in needed:
        tests_dir = heavy_roots["c_testsuite"]
        if not tests_dir.is_dir() or not any(tests_dir.glob("*.c")):
            clone_dir = tests_dir.parents[1]
            shutil.rmtree(clone_dir, ignore_errors=True)
            clone_dir.parent.mkdir(parents=True, exist_ok=True)
            print(f"[fixtures] syncing c-testsuite into {clone_dir}")
            run_cmd(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "https://github.com/c-testsuite/c-testsuite.git",
                    str(clone_dir),
                ]
            )

    if "wacct" in needed:
        tests_dir = heavy_roots["wacct"]
        wacct_root = tests_dir.parent
        expected = wacct_root / "expected_results.json"
        if not tests_dir.is_dir() or not expected.is_file():
            shutil.rmtree(wacct_root, ignore_errors=True)
            wacct_root.parent.mkdir(parents=True, exist_ok=True)
            print(f"[fixtures] syncing writing-a-c-compiler-tests into {wacct_root}")
            run_cmd(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "https://github.com/nlsandler/writing-a-c-compiler-tests.git",
                    str(wacct_root),
                ]
            )

    if "torture" in needed:
        tests_dir = heavy_roots["torture"]
        llvm_root = tests_dir.parents[4]
        if not tests_dir.is_dir() or not any(tests_dir.glob("*.c")):
            shutil.rmtree(llvm_root, ignore_errors=True)
            llvm_root.mkdir(parents=True, exist_ok=True)
            print(f"[fixtures] syncing llvm-test-suite torture shard into {llvm_root}")
            run_cmd(["git", "init"], cwd=llvm_root)
            run_cmd(
                [
                    "git",
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/llvm/llvm-test-suite.git",
                ],
                cwd=llvm_root,
            )
            run_cmd(["git", "config", "core.sparseCheckout", "true"], cwd=llvm_root)
            sparse_file = llvm_root / ".git" / "info" / "sparse-checkout"
            sparse_file.parent.mkdir(parents=True, exist_ok=True)
            sparse_file.write_text("SingleSource/Regression/C/gcc-c-torture/execute/\n")
            run_cmd(["git", "pull", "--depth", "1", "origin", "main"], cwd=llvm_root)

    fixtures_ok, missing = has_required_test_fixtures(test_paths, fixtures_root)
    if not fixtures_ok:
        joined = "\n".join(f"- {p}" for p in missing)
        raise RuntimeError(f"Missing required test fixtures after sync attempt:\n{joined}")


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


async def evaluate_commit_by_suite(
    *,
    envoi_url: str,
    repo_path: Path,
) -> dict[str, Any]:
    """Evaluate all tests grouped by suite. Returns per-suite and total counts."""
    started_at = time.monotonic()
    suite_results: dict[str, Any] = {}
    total_passed = 0
    total_failed = 0
    total_tests = 0

    docs = envoi.Documents(repo_path)
    async with await envoi.connect_session(
        envoi_url,
        submission=docs,
        session_timeout_seconds=7200,
    ) as session:
        for suite_path in SUITE_PATHS:
            try:
                result = await session.test(suite_path)
            except Exception as error:  # noqa: BLE001
                suite_results[suite_path] = {
                    "ok": False,
                    "error": str(error),
                    "passed": 0,
                    "failed": 0,
                    "total": 0,
                }
                continue

            if not isinstance(result, dict):
                suite_results[suite_path] = {
                    "ok": False,
                    "error": f"Unexpected result type: {type(result).__name__}",
                    "passed": 0,
                    "failed": 0,
                    "total": 0,
                }
                continue

            passed = int(result.get("passed", 0))
            failed = int(result.get("failed", 0))
            total = int(result.get("total", 0))
            suite_results[suite_path] = {
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
        "suite_results": suite_results,
    }


def reconstruct_repo_at_part(
    *,
    trace_path: Path,
    bundle_path: Path,
    part: int,
    destination: Path,
) -> dict[str, Any]:
    trace = load_trace(trace_path)
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
    fixtures_root: Path | None,
) -> dict[str, Any]:
    fixtures_root = fixtures_root or default_fixtures_root()
    trace = load_trace(trace_path)
    parts = extract_part_rows(trace)
    commit_order = get_unique_commits(parts)

    if not commit_order:
        raise ValueError("No commits found in trace parts")

    ensure_required_test_fixtures(test_paths, fixtures_root)

    workspace_root = Path(tempfile.mkdtemp(prefix="envoi-replay-")).resolve()
    repo_path = workspace_root / "repo"
    clone_bundle(bundle_path, repo_path)

    runtime_port = find_free_port()
    runtime = await start_runtime(
        environment_file=environment_file,
        port=runtime_port,
        fixtures_root=fixtures_root,
    )

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
            "fixtures_root": str(fixtures_root),
        },
        "commits_evaluated": commit_order,
        "commit_evaluations": commit_evals,
        "part_to_commit": {str(s["part"]): s.get("commit") for s in parts},
        "part_evaluations": part_evals,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return report


# ---------------------------------------------------------------------------
# Analyze helpers: token/timing extraction and table formatting
# ---------------------------------------------------------------------------


def iso_to_epoch_ms(iso_str: str) -> int | None:
    try:
        dt = datetime.fromisoformat(iso_str)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def format_elapsed(ms: int | None) -> str:
    if ms is None or ms < 0:
        return "?"
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    remaining = seconds % 60
    if minutes < 60:
        return f"{minutes}m{remaining:02d}s"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    return f"{hours}h{remaining_minutes:02d}m"


def format_token_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def extract_turn_stats(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk turns in the trace and extract token/timing data per turn."""
    started_at = trace.get("started_at")
    start_epoch_ms = iso_to_epoch_ms(started_at) if isinstance(started_at, str) else None

    turns = trace.get("turns", [])
    stats: list[dict[str, Any]] = []
    cumulative_input = 0
    cumulative_output = 0

    for turn in turns:
        if not isinstance(turn, dict):
            continue

        commit = parse_commit_from_part(turn)
        timestamp = turn.get("timestamp")

        turn_input = 0
        turn_output = 0
        turn_reasoning = 0
        turn_total = 0

        for msg in turn.get("new_messages", []):
            if not isinstance(msg, dict):
                continue
            info = msg.get("info", {})
            if not isinstance(info, dict):
                continue
            if info.get("role") != "assistant":
                continue
            tokens = info.get("tokens", {})
            if not isinstance(tokens, dict):
                continue
            turn_input += int(tokens.get("input", 0) or 0)
            turn_output += int(tokens.get("output", 0) or 0)
            turn_reasoning += int(tokens.get("reasoning", 0) or 0)
            turn_total += int(tokens.get("total", 0) or 0)

        cumulative_input += turn_input
        cumulative_output += turn_output

        elapsed_ms = None
        if start_epoch_ms and isinstance(timestamp, str):
            turn_epoch_ms = iso_to_epoch_ms(timestamp)
            if turn_epoch_ms:
                elapsed_ms = turn_epoch_ms - start_epoch_ms

        stats.append({
            "turn": turn.get("turn"),
            "part_start": turn.get("part_start"),
            "part_end": turn.get("part_end"),
            "commit": commit,
            "timestamp": timestamp,
            "elapsed_ms": elapsed_ms,
            "tokens": {
                "input": turn_input,
                "output": turn_output,
                "reasoning": turn_reasoning,
                "total": turn_total,
                "cumulative_input": cumulative_input,
                "cumulative_output": cumulative_output,
            },
        })

    return stats


def build_summary_rows(
    trace: dict[str, Any],
    turn_stats: list[dict[str, Any]],
    commit_evals: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join turn stats with per-commit evaluation results."""
    rows: list[dict[str, Any]] = []
    last_eval: dict[str, Any] = {}

    for ts in turn_stats:
        commit = ts.get("commit")
        if commit and commit in commit_evals:
            last_eval = commit_evals[commit]
        eval_data = last_eval

        suite_results = eval_data.get("suite_results", {})
        rows.append({
            "turn": ts["turn"],
            "part_start": ts.get("part_start"),
            "part_end": ts.get("part_end"),
            "commit": commit,
            "elapsed_ms": ts.get("elapsed_ms"),
            "elapsed_human": format_elapsed(ts.get("elapsed_ms")),
            "tokens": ts.get("tokens", {}),
            "suites": {
                suite: {
                    "passed": suite_results.get(suite, {}).get("passed", 0),
                    "total": suite_results.get(suite, {}).get("total", 0),
                }
                for suite in SUITE_PATHS
            },
            "total_passed": eval_data.get("passed", 0),
            "total_tests": eval_data.get("total", 0),
        })

    return rows


def format_summary_table(rows: list[dict[str, Any]]) -> str:
    """Render summary rows as a fixed-width ASCII table."""
    suite_headers = [suite.replace("_", "-")[:10] for suite in SUITE_PATHS]
    headers = ["Turn", "Parts", "Commit", "Elapsed", "Tokens(in/out)", *suite_headers, "Total"]
    widths = [5, 9, 10, 8, 15, *[10 for _ in SUITE_PATHS], 10]

    def fmt_suite(suites: dict, name: str) -> str:
        s = suites.get(name, {})
        return f"{s.get('passed', 0)}/{s.get('total', 0)}"

    lines: list[str] = []
    sep = "  ".join("-" * w for w in widths)
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=False))
    lines.append(header_line)
    lines.append(sep)

    for row in rows:
        parts_str = ""
        ps, pe = row.get("part_start"), row.get("part_end")
        if ps is not None and pe is not None:
            parts_str = f"{ps}-{pe}" if ps != pe else str(ps)
        elif ps is not None:
            parts_str = str(ps)

        commit = (row.get("commit") or "")[:8]

        tokens = row.get("tokens", {})
        tok_str = (
            f"{format_token_count(tokens.get('cumulative_input', 0))}"
            f"/{format_token_count(tokens.get('cumulative_output', 0))}"
        )

        suites = row.get("suites", {})
        cols = [
            str(row.get("turn", "")),
            parts_str,
            commit,
            row.get("elapsed_human", "?"),
            tok_str,
            *[fmt_suite(suites, suite) for suite in SUITE_PATHS],
            f"{row.get('total_passed', 0)}/{row.get('total_tests', 0)}",
        ]
        lines.append("  ".join(c.ljust(w) for c, w in zip(cols, widths, strict=False)))

    return "\n".join(lines)


async def analyze_trace(
    *,
    trace_path: Path,
    bundle_path: Path,
    output_path: Path,
    environment_file: Path,
    fixtures_root: Path | None,
) -> dict[str, Any]:
    fixtures_root = fixtures_root or default_fixtures_root()
    """Pull trace + bundle, evaluate every commit by suite, produce summary table."""
    trace = load_trace(trace_path)
    turn_stats = extract_turn_stats(trace)

    # Get unique commits from turns (in order)
    turn_commits: list[dict[str, Any]] = [
        {"commit": ts["commit"]} for ts in turn_stats if ts.get("commit")
    ]
    commit_order = get_unique_commits(turn_commits)

    if not commit_order:
        raise ValueError("No commits found in trace turns")

    ensure_required_test_fixtures(SUITE_PATHS, fixtures_root)

    workspace_root = Path(tempfile.mkdtemp(prefix="envoi-analyze-")).resolve()
    repo_path = workspace_root / "repo"
    clone_bundle(bundle_path, repo_path)

    runtime_port = find_free_port()
    runtime = await start_runtime(
        environment_file=environment_file,
        port=runtime_port,
        fixtures_root=fixtures_root,
    )

    commit_evals = {}
    try:
        for index, commit in enumerate(commit_order, start=1):
            print(f"[analyze] evaluating commit {index}/{len(commit_order)}: {commit[:10]}")
            checkout_commit(repo_path, commit)
            commit_evals[commit] = await evaluate_commit_by_suite(
                envoi_url=runtime.url,
                repo_path=repo_path,
            )
            suite_results = commit_evals[commit].get("suite_results", {})
            passed = commit_evals[commit].get("passed", 0)
            total = commit_evals[commit].get("total", 0)
            dur = commit_evals[commit].get("duration_ms", 0)
            suites_summary = "  ".join(
                f"{s}={suite_results.get(s, {}).get('passed', 0)}"
                f"/{suite_results.get(s, {}).get('total', 0)}"
                for s in SUITE_PATHS
            )
            print(
                f"[analyze]   {passed}/{total} passed  ({dur}ms)  {suites_summary}"
            )
    finally:
        stop_runtime(runtime)
        shutil.rmtree(workspace_root, ignore_errors=True)

    summary_rows = build_summary_rows(trace, turn_stats, commit_evals)

    table_str = format_summary_table(summary_rows)
    print()
    print(table_str)
    print()

    report = {
        "trajectory_id": trace.get("trajectory_id"),
        "agent_model": trace.get("agent_model"),
        "started_at": trace.get("started_at"),
        "generated_at": now_iso(),
        "fixtures_root": str(fixtures_root),
        "summary": summary_rows,
        "commits_evaluated": commit_order,
        "commit_evaluations": commit_evals,
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
        choices=["evaluate", "checkout-part", "analyze"],
        default="evaluate",
        help=(
            "evaluate: run tests for all unique commits; "
            "checkout-part: materialize repo at a specific part; "
            "analyze: evaluate by suite and print summary table with tokens/timing."
        ),
    )
    parser.add_argument(
        "--trace",
        help="Local path or s3:// URI to trace.parquet",
    )
    parser.add_argument(
        "--bundle",
        help="Local path or s3:// URI to repo.bundle",
    )
    parser.add_argument(
        "--trajectory-id",
        help=(
            "If provided, trace and bundle are resolved as "
            "s3://<bucket>/trajectories/<trajectory-id>/trace.parquet and repo.bundle"
        ),
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("AWS_S3_BUCKET"),
        help="S3 bucket used with --trajectory-id (default: AWS_S3_BUCKET env var).",
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
        "--fixtures-root",
        default=None,
        help=(
            "Fixtures root for heavy suites. Defaults to ENVOI_TESTS_ROOT if set, "
            "otherwise /opt/tests when writable/present, else ~/.cache/envoi-tests."
        ),
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
        trace_source = artifact_uri(args.bucket, args.trajectory_id, "trace.parquet")
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
        fixtures_root = (
            Path(args.fixtures_root).expanduser().resolve()
            if args.fixtures_root
            else default_fixtures_root()
        )

        if args.mode == "analyze":
            report = await analyze_trace(
                trace_path=trace_path,
                bundle_path=bundle_path,
                output_path=output_path,
                environment_file=environment_file,
                fixtures_root=fixtures_root,
            )
        else:
            test_paths = args.test_paths if args.test_paths else list(REQUIRED_PATHS)
            report = await replay_trace(
                trace_path=trace_path,
                bundle_path=bundle_path,
                output_path=output_path,
                environment_file=environment_file,
                test_paths=test_paths,
                fixtures_root=fixtures_root,
            )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if args.mode == "analyze":
        print(f"[done] wrote analysis to {Path(args.output).expanduser().resolve()}")
    else:
        print(
            f"[done] wrote {len(report['part_evaluations'])} part evaluations "
            f"to {Path(args.output).expanduser().resolve()}"
        )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
