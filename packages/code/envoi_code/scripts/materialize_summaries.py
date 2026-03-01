"""Materialize summary parquet files from raw trace data.

Reads all trace.parquet files from a source directory and produces two
summary parquet files for fast dashboard queries:

  trajectory_summary.parquet — one row per trajectory (metadata + final scores)
  evaluation_summary.parquet — one row per completed evaluation per trajectory

Optionally extracts code snapshots from git bundles (--extract-code) and writes
per-trajectory code_snapshots.parquet files alongside the traces.

Usage (via CLI):
    envoi code materialize --source <path> --dest <path>
    envoi code materialize --source <path> --dest <path> --extract-code
    envoi code materialize --source <path> --dest <path> --incremental
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from envoi_code.utils.trace_parquet import (
    build_evaluations_from_parts,
    parse_json_field,
    read_trace_parquet,
)


# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------

TRAJECTORY_SUMMARY_SCHEMA = pa.schema([
    ("trajectory_id", pa.string()),
    ("environment", pa.string()),
    ("agent", pa.string()),
    ("agent_model", pa.string()),
    ("started_at", pa.string()),
    ("ended_at", pa.string()),
    ("total_parts", pa.int32()),
    ("total_turns", pa.int32()),
    ("total_tokens", pa.int64()),
    ("session_end_reason", pa.string()),
    ("task_params", pa.string()),
    ("suites", pa.string()),
    ("final_passed", pa.int32()),
    ("final_failed", pa.int32()),
    ("final_total", pa.int32()),
    ("final_suite_results", pa.string()),
    ("bundle_uri", pa.string()),
])

EVALUATION_SUMMARY_SCHEMA = pa.schema([
    ("trajectory_id", pa.string()),
    ("environment", pa.string()),
    ("agent_model", pa.string()),
    ("eval_id", pa.string()),
    ("target_commit", pa.string()),
    ("trigger_part", pa.int32()),
    ("trigger_turn", pa.int32()),
    ("status", pa.string()),
    ("passed", pa.int32()),
    ("failed", pa.int32()),
    ("total", pa.int32()),
    ("suite_results", pa.string()),
    ("queued_at", pa.string()),
    ("started_at", pa.string()),
    ("finished_at", pa.string()),
])

CODE_SNAPSHOTS_SCHEMA = pa.schema([
    ("commit_hash", pa.string()),
    ("commit_index", pa.int32()),
    ("file_path", pa.string()),
    ("status", pa.string()),
    ("content", pa.string()),
    ("added_lines", pa.string()),
])


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_trace_files(source: str) -> list[Path]:
    """Find all trace.parquet files under the source directory."""
    source_path = Path(source)
    if not source_path.is_dir():
        print(f"[materialize] source is not a directory: {source}")
        return []

    traces: list[Path] = []
    for child in sorted(source_path.iterdir()):
        if not child.is_dir():
            continue
        # Skip the summaries directory itself
        if child.name == "summaries":
            continue
        trace_file = child / "trace.parquet"
        if trace_file.is_file():
            traces.append(trace_file)
    return traces


# ---------------------------------------------------------------------------
# Trajectory processing
# ---------------------------------------------------------------------------

def process_trajectory(
    trace_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Process a single trace.parquet into a summary row and evaluation rows.

    Returns (trajectory_summary_row, evaluation_summary_rows).
    """
    rows = read_trace_parquet(str(trace_path))
    if not rows:
        raise ValueError(f"Empty trace file: {trace_path}")

    first = rows[0]

    # Parse JSON fields in rows for evaluation extraction
    parsed_rows: list[dict[str, Any]] = []
    for row in rows:
        parsed = dict(row)
        raw_delta = row.get("eval_events_delta")
        parsed["eval_events_delta"] = parse_json_field(raw_delta)
        parsed_rows.append(parsed)

    # Trajectory-level fields (denormalized, same on every row)
    trajectory_id = first.get("trajectory_id") or ""
    environment = first.get("environment") or ""
    agent = first.get("agent") or ""
    agent_model = first.get("agent_model") or ""
    started_at = first.get("started_at") or ""
    session_end_reason = first.get("session_end_reason")
    task_params = first.get("task_params")
    suites = first.get("suites")
    bundle_uri = first.get("bundle_uri")

    # Aggregate fields
    max_part = 0
    max_turn = 0
    total_tokens = 0
    max_timestamp = ""
    has_turn = False

    for row in rows:
        part = row.get("part")
        if isinstance(part, int) and part > max_part:
            max_part = part

        turn = row.get("turn")
        if isinstance(turn, int):
            has_turn = True
            if turn > max_turn:
                max_turn = turn

        token_est = row.get("content_token_estimate")
        if isinstance(token_est, int):
            total_tokens += token_est

        ts = row.get("timestamp")
        if isinstance(ts, str) and ts > max_timestamp:
            max_timestamp = ts

    total_parts = max_part + 1

    # Build evaluations
    evaluations = build_evaluations_from_parts(parsed_rows)

    # Find the last completed evaluation
    completed_evals = [
        evaluation
        for evaluation in evaluations.values()
        if isinstance(evaluation, dict)
        and evaluation.get("status") == "completed"
        and isinstance(evaluation.get("total"), int)
        and evaluation["total"] > 0
    ]
    completed_evals.sort(
        key=lambda evaluation: (
            evaluation.get("part", 0)
            if isinstance(evaluation.get("part"), int)
            else 0
        ),
    )

    final_passed = 0
    final_failed = 0
    final_total = 0
    final_suite_results: str | None = None

    if completed_evals:
        last_eval = completed_evals[-1]
        final_passed = last_eval.get("passed", 0)
        final_failed = last_eval.get("failed", 0)
        final_total = last_eval.get("total", 0)
        sr = last_eval.get("suite_results")
        if isinstance(sr, dict):
            final_suite_results = json.dumps(
                sr, separators=(",", ":"), ensure_ascii=False,
            )

    trajectory_summary: dict[str, Any] = {
        "trajectory_id": trajectory_id,
        "environment": environment,
        "agent": agent,
        "agent_model": agent_model,
        "started_at": started_at,
        "ended_at": max_timestamp or started_at,
        "total_parts": total_parts,
        "total_turns": max_turn if has_turn else None,
        "total_tokens": total_tokens,
        "session_end_reason": session_end_reason,
        "task_params": task_params,
        "suites": suites,
        "final_passed": final_passed,
        "final_failed": final_failed,
        "final_total": final_total,
        "final_suite_results": final_suite_results,
        "bundle_uri": bundle_uri,
    }

    # Build evaluation summary rows (only completed evaluations)
    eval_rows: list[dict[str, Any]] = []
    for evaluation in completed_evals:
        sr_value = evaluation.get("suite_results")
        suite_results_json: str | None = None
        if isinstance(sr_value, dict):
            suite_results_json = json.dumps(
                sr_value, separators=(",", ":"), ensure_ascii=False,
            )

        trigger_turn = evaluation.get("trigger_turn")

        eval_rows.append({
            "trajectory_id": trajectory_id,
            "environment": environment,
            "agent_model": agent_model,
            "eval_id": evaluation.get("eval_id", ""),
            "target_commit": evaluation.get("commit", ""),
            "trigger_part": evaluation.get("part", 0),
            "trigger_turn": trigger_turn if isinstance(trigger_turn, int) else None,
            "status": evaluation.get("status", ""),
            "passed": evaluation.get("passed", 0),
            "failed": evaluation.get("failed", 0),
            "total": evaluation.get("total", 0),
            "suite_results": suite_results_json,
            "queued_at": evaluation.get("queued_at"),
            "started_at": evaluation.get("started_at"),
            "finished_at": evaluation.get("completed_at"),
        })

    return trajectory_summary, eval_rows


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------

def compute_added_lines(diff_text: str) -> list[int]:
    """Parse unified diff and return 0-indexed line numbers of added lines."""
    added: list[int] = []
    current_line = 0
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            # Parse new-file line number: @@ -old,len +new,len @@
            match = re.search(r"\+(\d+)", line)
            if match:
                current_line = int(match.group(1)) - 1  # convert to 0-indexed
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(current_line)
            current_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            pass  # deleted line, don't advance new-file line counter
        else:
            current_line += 1  # context line
    return added


def is_binary_file(repo_dir: str, commit: str, path: str) -> bool:
    """Check if a file is binary by using git diff --numstat."""
    try:
        result = subprocess.run(
            ["git", "show", "--no-patch", "--format=", f"{commit}:{path}"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        # Check for binary indicator
        if result.returncode != 0:
            return True
        # A rough heuristic: if content contains null bytes, it's binary
        return b"\x00" in result.stdout[:8192]
    except Exception:
        return True


def read_file_content(repo_dir: str, commit: str, path: str) -> str | None:
    """Read file content at a specific commit, returning None on failure."""
    try:
        result = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=repo_dir,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace")
    except Exception:
        return None


def extract_code_snapshots(
    trace_path: Path,
    repo_bundle_path: Path,
) -> list[dict[str, Any]]:
    """Extract code snapshots from a git bundle for a trajectory.

    Returns rows for the CODE_SNAPSHOTS_SCHEMA.
    """
    rows = read_trace_parquet(str(trace_path))
    if not rows:
        return []

    # Get ordered unique commits from the trace
    seen_commits: set[str] = set()
    ordered_commits: list[str] = []
    for row in sorted(rows, key=lambda row: row.get("part", 0)):
        commit = row.get("git_commit")
        if isinstance(commit, str) and commit and commit not in seen_commits:
            seen_commits.add(commit)
            ordered_commits.append(commit)

    if not ordered_commits:
        print(f"  [code] No commits found in trace")
        return []

    # Clone bundle to temp directory
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = os.path.join(tmp_dir, "repo")
        try:
            subprocess.run(
                ["git", "clone", str(repo_bundle_path), repo_dir],
                capture_output=True,
                timeout=120,
            )
        except Exception as err:
            print(f"  [code] Failed to clone bundle: {err}")
            return []

        if not os.path.isdir(repo_dir):
            print(f"  [code] Clone produced no repo directory")
            return []

        # Fetch all refs to ensure all commits are available
        try:
            subprocess.run(
                ["git", "fetch", "--all"],
                cwd=repo_dir,
                capture_output=True,
                timeout=60,
            )
        except Exception:
            pass

        snapshot_rows: list[dict[str, Any]] = []
        prev_commit: str | None = None

        for commit_index, commit in enumerate(ordered_commits):
            # Verify the commit exists
            check = subprocess.run(
                ["git", "cat-file", "-t", commit],
                cwd=repo_dir,
                capture_output=True,
                timeout=10,
            )
            if check.returncode != 0:
                print(f"  [code] Commit {commit[:12]} not found, skipping")
                continue

            if prev_commit is None:
                # First commit: all files are "added"
                try:
                    result = subprocess.run(
                        ["git", "ls-tree", "-r", "--name-only", commit],
                        cwd=repo_dir,
                        capture_output=True,
                        timeout=30,
                    )
                    if result.returncode != 0:
                        prev_commit = commit
                        continue
                    file_list = result.stdout.decode(
                        "utf-8", errors="replace",
                    ).strip().splitlines()
                except Exception as err:
                    print(f"  [code] Failed to list files for first commit: {err}")
                    prev_commit = commit
                    continue

                for file_path in file_list:
                    if not file_path.strip():
                        continue
                    if is_binary_file(repo_dir, commit, file_path):
                        continue
                    content = read_file_content(repo_dir, commit, file_path)
                    if content is None:
                        continue
                    if len(content) > 500_000:
                        continue
                    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                    added_lines = list(range(line_count))
                    snapshot_rows.append({
                        "commit_hash": commit,
                        "commit_index": commit_index,
                        "file_path": file_path,
                        "status": "A",
                        "content": content,
                        "added_lines": json.dumps(added_lines),
                    })
            else:
                # Subsequent commit: only changed files
                try:
                    result = subprocess.run(
                        ["git", "diff", "--name-status", prev_commit, commit],
                        cwd=repo_dir,
                        capture_output=True,
                        timeout=30,
                    )
                    if result.returncode != 0:
                        prev_commit = commit
                        continue
                    diff_lines = result.stdout.decode(
                        "utf-8", errors="replace",
                    ).strip().splitlines()
                except Exception as err:
                    print(f"  [code] Failed to diff {prev_commit[:12]}..{commit[:12]}: {err}")
                    prev_commit = commit
                    continue

                for diff_line in diff_lines:
                    if not diff_line.strip():
                        continue
                    parts = diff_line.split("\t", maxsplit=2)
                    if len(parts) < 2:
                        continue

                    status_code = parts[0].strip()
                    file_path = parts[-1].strip()

                    # Handle renames: R100\told\tnew
                    if status_code.startswith("R"):
                        if len(parts) >= 3:
                            old_path = parts[1].strip()
                            file_path = parts[2].strip()
                            # Emit delete for old path
                            snapshot_rows.append({
                                "commit_hash": commit,
                                "commit_index": commit_index,
                                "file_path": old_path,
                                "status": "D",
                                "content": None,
                                "added_lines": "[]",
                            })
                            status_code = "A"
                        else:
                            continue

                    if status_code == "D":
                        snapshot_rows.append({
                            "commit_hash": commit,
                            "commit_index": commit_index,
                            "file_path": file_path,
                            "status": "D",
                            "content": None,
                            "added_lines": "[]",
                        })
                        continue

                    # A or M (or the A from a renamed file)
                    normalized_status = "A" if status_code.startswith("A") else "M"
                    if is_binary_file(repo_dir, commit, file_path):
                        continue
                    content = read_file_content(repo_dir, commit, file_path)
                    if content is None:
                        continue
                    if len(content) > 500_000:
                        continue

                    # Compute added lines
                    if normalized_status == "A":
                        line_count = content.count("\n") + (
                            1 if content and not content.endswith("\n") else 0
                        )
                        added_lines = list(range(line_count))
                    else:
                        try:
                            diff_result = subprocess.run(
                                [
                                    "git", "diff",
                                    prev_commit, commit, "--", file_path,
                                ],
                                cwd=repo_dir,
                                capture_output=True,
                                timeout=30,
                            )
                            diff_text = diff_result.stdout.decode(
                                "utf-8", errors="replace",
                            )
                            added_lines = compute_added_lines(diff_text)
                        except Exception:
                            added_lines = []

                    snapshot_rows.append({
                        "commit_hash": commit,
                        "commit_index": commit_index,
                        "file_path": file_path,
                        "status": normalized_status,
                        "content": content,
                        "added_lines": json.dumps(added_lines),
                    })

            prev_commit = commit

        return snapshot_rows


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

def materialize_command(args: argparse.Namespace) -> None:
    """Run the materialization pipeline."""
    source = args.source
    dest = args.dest
    extract_code = getattr(args, "extract_code", False)
    incremental = getattr(args, "incremental", False)

    start_time = time.monotonic()

    print(f"[materialize] source={source} dest={dest}")
    print(f"[materialize] extract_code={extract_code} incremental={incremental}")

    # Ensure destination directory exists
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    # Discover trace files
    trace_files = discover_trace_files(source)
    print(f"[materialize] found {len(trace_files)} trace files")

    if not trace_files and not incremental:
        # Write empty summary files
        empty_traj = pa.Table.from_pylist([], schema=TRAJECTORY_SUMMARY_SCHEMA)
        pq.write_table(empty_traj, str(dest_path / "trajectory_summary.parquet"))
        empty_eval = pa.Table.from_pylist([], schema=EVALUATION_SUMMARY_SCHEMA)
        pq.write_table(empty_eval, str(dest_path / "evaluation_summary.parquet"))
        print("[materialize] wrote empty summary files (no traces found)")
        return

    # Load existing summaries for incremental mode
    known_ids: set[str] = set()
    existing_traj_rows: list[dict[str, Any]] = []
    existing_eval_rows: list[dict[str, Any]] = []

    if incremental:
        traj_summary_path = dest_path / "trajectory_summary.parquet"
        eval_summary_path = dest_path / "evaluation_summary.parquet"

        if traj_summary_path.is_file():
            existing_traj_table = pq.read_table(str(traj_summary_path))
            existing_traj_rows = existing_traj_table.to_pylist()
            for row in existing_traj_rows:
                tid = row.get("trajectory_id")
                if isinstance(tid, str) and tid:
                    known_ids.add(tid)
            print(f"[materialize] incremental: {len(known_ids)} existing trajectories")

        if eval_summary_path.is_file():
            existing_eval_table = pq.read_table(str(eval_summary_path))
            existing_eval_rows = existing_eval_table.to_pylist()

    # Process each trace file
    all_traj_rows: list[dict[str, Any]] = list(existing_traj_rows)
    all_eval_rows: list[dict[str, Any]] = list(existing_eval_rows)
    processed = 0
    skipped = 0
    errors = 0

    for trace_file in trace_files:
        trajectory_id = trace_file.parent.name

        if incremental and trajectory_id in known_ids:
            skipped += 1
            continue

        try:
            traj_row, eval_rows = process_trajectory(trace_file)
            all_traj_rows.append(traj_row)
            all_eval_rows.extend(eval_rows)
            processed += 1

            if processed % 10 == 0:
                print(f"[materialize] processed {processed} trajectories...")

        except Exception as err:
            print(f"[materialize] error processing {trajectory_id}: {err}")
            errors += 1
            continue

    # Write trajectory summary
    traj_table = pa.Table.from_pylist(all_traj_rows, schema=TRAJECTORY_SUMMARY_SCHEMA)
    traj_out = str(dest_path / "trajectory_summary.parquet")
    pq.write_table(traj_table, traj_out)

    # Write evaluation summary
    eval_table = pa.Table.from_pylist(all_eval_rows, schema=EVALUATION_SUMMARY_SCHEMA)
    eval_out = str(dest_path / "evaluation_summary.parquet")
    pq.write_table(eval_table, eval_out)

    elapsed = time.monotonic() - start_time
    print(
        f"[materialize] done in {elapsed:.1f}s — "
        f"processed={processed} skipped={skipped} errors={errors} "
        f"trajectory_rows={len(all_traj_rows)} eval_rows={len(all_eval_rows)}",
    )

    # Code extraction
    if extract_code:
        code_start = time.monotonic()
        code_extracted = 0
        code_skipped = 0
        code_errors = 0

        for trace_file in trace_files:
            trajectory_id = trace_file.parent.name
            bundle_path = trace_file.parent / "repo.bundle"
            code_out_path = trace_file.parent / "code_snapshots.parquet"

            if not bundle_path.is_file():
                code_skipped += 1
                continue

            if incremental and code_out_path.is_file():
                code_skipped += 1
                continue

            print(f"  [code] extracting {trajectory_id}...")
            try:
                snapshot_rows = extract_code_snapshots(trace_file, bundle_path)
                if snapshot_rows:
                    table = pa.Table.from_pylist(
                        snapshot_rows, schema=CODE_SNAPSHOTS_SCHEMA,
                    )
                    pq.write_table(table, str(code_out_path))
                    code_extracted += 1
                    print(
                        f"  [code] wrote {len(snapshot_rows)} rows for {trajectory_id}",
                    )
                else:
                    print(f"  [code] no snapshots produced for {trajectory_id}")
                    code_skipped += 1
            except Exception as err:
                print(f"  [code] error extracting {trajectory_id}: {err}")
                code_errors += 1

        code_elapsed = time.monotonic() - code_start
        print(
            f"[materialize] code extraction done in {code_elapsed:.1f}s — "
            f"extracted={code_extracted} skipped={code_skipped} errors={code_errors}",
        )
