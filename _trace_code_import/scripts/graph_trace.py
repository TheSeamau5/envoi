"""Build graph artifacts for a trajectory: slim JSON + PNG charts."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.offline_replay import (
    artifact_uri,
    download_if_needed,
    now_iso,
    reconstruct_repo_at_part,
)
from utils.trace_parquet import parquet_to_trace_dict


def parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def summarize_leaf_results(node: Any) -> tuple[int, int]:
    if isinstance(node, dict):
        passed = parse_int(node.get("passed"))
        total = parse_int(node.get("total"))
        if passed is not None and total is not None and "failed" in node:
            return max(0, passed), max(0, total)
        passed_sum = 0
        total_sum = 0
        for nested in node.values():
            nested_passed, nested_total = summarize_leaf_results(nested)
            passed_sum += nested_passed
            total_sum += nested_total
        return passed_sum, total_sum
    if isinstance(node, list):
        passed_sum = 0
        total_sum = 0
        for nested in node:
            nested_passed, nested_total = summarize_leaf_results(nested)
            passed_sum += nested_passed
            total_sum += nested_total
        return passed_sum, total_sum
    return 0, 0


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def import_matplotlib() -> tuple[Any, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import MaxNLocator
    except Exception as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError(
            "matplotlib is required to generate PNG charts. "
            "Install with `uv add matplotlib`."
        ) from exc
    return plt, MaxNLocator


def collect_part_timestamps(trace: dict[str, Any]) -> dict[int, str]:
    parts = trace.get("parts")
    if not isinstance(parts, list):
        return {}
    mapping: dict[int, str] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_number = parse_int(part.get("part"))
        timestamp = part.get("timestamp")
        if part_number is None:
            continue
        if not isinstance(timestamp, str) or not timestamp:
            continue
        mapping.setdefault(part_number, timestamp)
    return mapping


def build_commit_points(trace: dict[str, Any]) -> list[dict[str, Any]]:
    evaluations = trace.get("evaluations")
    if not isinstance(evaluations, dict):
        return []

    part_timestamps = collect_part_timestamps(trace)
    points: list[dict[str, Any]] = []

    for commit, payload in evaluations.items():
        if not isinstance(commit, str) or not commit:
            continue
        if not isinstance(payload, dict):
            continue

        part = parse_int(payload.get("part"))
        if part is None:
            continue
        status = payload.get("status")
        status_value = status if isinstance(status, str) and status else "unknown"
        timestamp = (
            payload.get("completed_at")
            if isinstance(payload.get("completed_at"), str)
            else payload.get("started_at")
            if isinstance(payload.get("started_at"), str)
            else payload.get("queued_at")
            if isinstance(payload.get("queued_at"), str)
            else part_timestamps.get(part)
        )

        suites: dict[str, dict[str, int]] = {}
        suite_results = payload.get("suite_results")
        suite_results_obj = suite_results if isinstance(suite_results, dict) else {}
        summed_passed = 0
        summed_total = 0
        for suite_name, suite_payload in suite_results_obj.items():
            if not isinstance(suite_name, str) or not suite_name:
                continue
            if not isinstance(suite_payload, dict):
                continue
            suite_passed = parse_int(suite_payload.get("passed")) or 0
            suite_total = parse_int(suite_payload.get("total")) or 0
            if suite_total <= 0:
                nested_passed, nested_total = summarize_leaf_results(suite_payload.get("result"))
                suite_passed = nested_passed
                suite_total = nested_total
            suite_passed = max(0, suite_passed)
            suite_total = max(0, suite_total)
            suites[suite_name] = {"passed": suite_passed, "total": suite_total}
            summed_passed += suite_passed
            summed_total += suite_total

        direct_passed = parse_int(payload.get("passed")) or 0
        direct_total = parse_int(payload.get("total")) or 0
        if summed_total > 0:
            graph_passed: int | None = summed_passed
            graph_total: int | None = summed_total
        elif direct_total > 0:
            graph_passed = direct_passed
            graph_total = direct_total
        else:
            # No usable test totals for this commit evaluation.
            graph_passed = None
            graph_total = None

        error = payload.get("error")
        error_present = isinstance(error, str) and bool(error.strip())
        points.append(
            {
                "part": part,
                "timestamp": timestamp if isinstance(timestamp, str) and timestamp else None,
                "commit": commit,
                "status": status_value,
                "passed": max(0, graph_passed) if isinstance(graph_passed, int) else None,
                "total": max(0, graph_total) if isinstance(graph_total, int) else None,
                "error": error_present,
                "suites": suites,
            }
        )

    points.sort(
        key=lambda point: (
            parse_int(point.get("part")) if parse_int(point.get("part")) is not None else 10**9,
            str(point.get("timestamp") or ""),
            str(point.get("commit") or ""),
        )
    )
    return points


def annotate_elapsed_minutes(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline: datetime | None = None
    for point in points:
        if not isinstance(point, dict):
            continue
        ts = parse_iso_datetime(point.get("timestamp"))
        if ts is None:
            continue
        if baseline is None or ts < baseline:
            baseline = ts

    if baseline is None:
        for point in points:
            if isinstance(point, dict):
                point["elapsed_minutes"] = None
        return points

    for point in points:
        if not isinstance(point, dict):
            continue
        ts = parse_iso_datetime(point.get("timestamp"))
        if ts is None:
            point["elapsed_minutes"] = None
            continue
        elapsed_seconds = (ts - baseline).total_seconds()
        point["elapsed_minutes"] = round(max(0.0, elapsed_seconds) / 60.0, 3)
    return points


def detect_suites(points: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for point in points:
        suites = point.get("suites")
        if not isinstance(suites, dict):
            continue
        for suite_name in suites:
            if isinstance(suite_name, str) and suite_name:
                seen.add(suite_name)
    return sorted(seen)


def build_report_from_trace(trace: dict[str, Any]) -> dict[str, Any]:
    points = annotate_elapsed_minutes(build_commit_points(trace))
    suites = detect_suites(points)
    return {
        "trajectory_id": trace.get("trajectory_id"),
        "agent_model": trace.get("agent_model"),
        "started_at": trace.get("started_at"),
        "generated_at": now_iso(),
        "analysis_source": "graph_png_v1",
        "x_axes": ["part", "elapsed_minutes"],
        "suites": suites,
        "points": points,
        "counts": {"commit_points": len(points)},
    }


def render_chart(
    *,
    points: list[dict[str, Any]],
    output_path: Path,
    title: str,
    x_mode: str,
    y_values: list[int | None],
    y_label: str = "Passed Tests",
    y_max: int | None = None,
) -> None:
    plt, MaxNLocator = import_matplotlib()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    x_values: list[Any] = []
    plot_y: list[float] = []
    for point, y in zip(points, y_values, strict=False):
        if x_mode == "part":
            x = parse_int(point.get("part"))
            if x is None:
                continue
            x_values.append(x)
            if isinstance(y, int):
                plot_y.append(float(max(0, y)))
            else:
                plot_y.append(math.nan)
            continue

        if x_mode == "elapsed_minutes":
            elapsed = point.get("elapsed_minutes")
            if not isinstance(elapsed, (int, float)):
                continue
            x_values.append(float(elapsed))
            if isinstance(y, int):
                plot_y.append(float(max(0, y)))
            else:
                plot_y.append(math.nan)
            continue

        ts = parse_iso_datetime(point.get("timestamp"))
        if ts is None:
            continue
        x_values.append(ts)
        if isinstance(y, int):
            plot_y.append(float(max(0, y)))
        else:
            plot_y.append(math.nan)

    fig, ax = plt.subplots(figsize=(12, 6))
    if x_values and plot_y:
        ax.plot(x_values, plot_y, marker="o", linewidth=2.0, markersize=4.5)
    else:
        ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center", fontsize=14)

    ax.set_title(title)
    ax.set_ylabel(y_label)
    if x_mode == "part":
        ax.set_xlabel("Parts")
    elif x_mode == "elapsed_minutes":
        ax.set_xlabel("Elapsed Time (minutes)")
    else:
        ax.set_xlabel("Time")
    if isinstance(y_max, int) and y_max > 0:
        ax.set_ylim(0, y_max)
    else:
        ax.set_ylim(bottom=0)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(True, linestyle="--", alpha=0.4)

    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def resolve_output_dir(output_arg: str | None, trajectory_id: str) -> Path:
    if not output_arg:
        return Path(f"output/graph_trace_{trajectory_id}").expanduser().resolve()
    candidate = Path(output_arg).expanduser().resolve()
    if candidate.suffix.lower() == ".json":
        return (candidate.parent / candidate.stem).resolve()
    return candidate


def write_graph_artifacts(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    points = report.get("points")
    point_list = points if isinstance(points, list) else []
    suites = report.get("suites")
    suite_list = suites if isinstance(suites, list) else []

    json_path = output_dir / "graph_data.json"
    json_path.write_text(json.dumps(report, indent=2))

    overall_total_candidates = [
        parse_int(point.get("total"))
        for point in point_list
        if isinstance(point, dict)
    ]
    overall_total = max(
        (value for value in overall_total_candidates if isinstance(value, int) and value > 0),
        default=None,
    )

    charts: dict[str, str] = {}
    overall_parts = output_dir / "overall_by_parts.png"
    render_chart(
        points=point_list,
        output_path=overall_parts,
        title="Overall Passed Tests by Parts",
        x_mode="part",
        y_values=[parse_int(p.get("passed")) if isinstance(p, dict) else None for p in point_list],
        y_max=overall_total,
    )
    charts["overall_by_parts"] = str(overall_parts)

    overall_time = output_dir / "overall_by_time.png"
    render_chart(
        points=point_list,
        output_path=overall_time,
        title="Overall Passed Tests by Elapsed Time",
        x_mode="elapsed_minutes",
        y_values=[parse_int(p.get("passed")) if isinstance(p, dict) else None for p in point_list],
        y_max=overall_total,
    )
    charts["overall_by_time"] = str(overall_time)

    suites_dir = output_dir / "suites"
    for suite in suite_list:
        if not isinstance(suite, str) or not suite:
            continue
        key = slugify(suite)
        suite_values: list[int | None] = []
        for point in point_list:
            if not isinstance(point, dict):
                suite_values.append(None)
                continue
            suites_payload = point.get("suites")
            suites_obj = suites_payload if isinstance(suites_payload, dict) else {}
            suite_payload = suites_obj.get(suite)
            suite_obj = suite_payload if isinstance(suite_payload, dict) else {}
            suite_values.append(parse_int(suite_obj.get("passed")))
        suite_total_candidates = [
            parse_int(
                point.get("suites", {}).get(suite, {}).get("total")
                if isinstance(point, dict)
                and isinstance(point.get("suites"), dict)
                and isinstance(point.get("suites", {}).get(suite), dict)
                else None
            )
            for point in point_list
        ]
        suite_total = max(
            (value for value in suite_total_candidates if isinstance(value, int) and value > 0),
            default=None,
        )

        suite_parts = suites_dir / f"{key}_by_parts.png"
        render_chart(
            points=point_list,
            output_path=suite_parts,
            title=f"{suite} Passed Tests by Parts",
            x_mode="part",
            y_values=suite_values,
            y_max=suite_total,
        )
        charts[f"{suite}:by_parts"] = str(suite_parts)

        suite_time = suites_dir / f"{key}_by_time.png"
        render_chart(
            points=point_list,
            output_path=suite_time,
            title=f"{suite} Passed Tests by Elapsed Time",
            x_mode="elapsed_minutes",
            y_values=suite_values,
            y_max=suite_total,
        )
        charts[f"{suite}:by_time"] = str(suite_time)
    charts["graph_data"] = str(json_path)
    return charts


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Build PNG graph artifacts from trace evaluations, or checkout a part.",
    )
    parser.add_argument(
        "trajectory_id",
        help="Trajectory ID under trajectories/<id>/ in S3.",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("AWS_S3_BUCKET"),
        help="S3 bucket (default: AWS_S3_BUCKET env var).",
    )
    parser.add_argument(
        "--part",
        type=int,
        help="If set, checkout this part instead of building graph artifacts.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output directory for graph artifacts. "
            "Default: output/graph_trace_<trajectory_id>/"
        ),
    )
    parser.add_argument(
        "--checkout-dest",
        default=None,
        help="Checkout destination when --part is set.",
    )
    args = parser.parse_args()

    trace_source = artifact_uri(args.bucket, args.trajectory_id, "trace.parquet")

    if args.part is not None:
        bundle_source = artifact_uri(args.bucket, args.trajectory_id, "repo.bundle")
        scratch = Path(tempfile.mkdtemp(prefix="graph-trace-artifacts-")).resolve()
        try:
            trace_path = download_if_needed(trace_source, scratch)
            bundle_path = download_if_needed(bundle_source, scratch)
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
            output_path = (
                Path(args.output).expanduser().resolve()
                if args.output
                else Path(f"output/repo_part_{args.part}.json").expanduser().resolve()
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(metadata, indent=2))
            print(
                f"[done] checked out part {args.part} at commit {metadata['commit']} "
                f"to {checkout_dest}"
            )
            print(f"[done] wrote checkout metadata to {output_path}")
            return
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    output_dir = resolve_output_dir(args.output, args.trajectory_id)
    scratch = Path(tempfile.mkdtemp(prefix="graph-trace-artifacts-")).resolve()
    try:
        trace_path = download_if_needed(trace_source, scratch)
        trace = parquet_to_trace_dict(str(trace_path))
        report = build_report_from_trace(trace)
        charts = write_graph_artifacts(report, output_dir)
        counts = report.get("counts", {})
        print(
            "[graph] "
            f"commit_points={counts.get('commit_points', 0)} "
            f"charts={len(charts) - 1}"
        )
        print(f"[done] wrote graph folder: {output_dir}")
        print(f"[done] json: {charts['graph_data']}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
