"""Build graph artifacts for a trajectory: slim JSON + PNG charts."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from envoi_code.scripts.offline_replay import (
    artifact_uri,
    download_if_needed,
    now_iso,
    reconstruct_repo_at_part,
)
from envoi_code.utils.trace_parquet import parquet_to_trace_dict

matplotlib.use("Agg")


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



# ── Camera-ready chart palette & helpers ─────────────────────────────

BG = "#FAFBFC"
FG = "#1B1F23"
GRID_CLR = "#E1E4E8"
SPINE_CLR = "#D1D5DA"

# Rotating color palette for dynamic suite assignment.
COLOR_PALETTE = [
    "#0366D6", "#2EA44F", "#D73A49", "#6F42C1", "#E36209",
    "#0598BC", "#B08800", "#6A737D", "#22863A", "#005CC5",
    "#8B5CF6", "#EC4899", "#14B8A6", "#F59E0B", "#6366F1",
]


def suite_short_name(suite: str) -> str:
    """Derive a short display name from a suite path like 'all/wacct/run_wacct_all'."""
    parts = suite.strip("/").split("/")
    return parts[-1] if parts else suite


def suite_group_key(suite: str) -> str:
    """Derive the grouping key (second path segment, or the leaf if shallow).

    'all/basics/smoke'              -> 'basics'
    'all/wacct/run_wacct_all'       -> 'wacct'
    'my_suite'                      -> 'my_suite'
    """
    parts = suite.strip("/").split("/")
    if len(parts) >= 2:
        return parts[1]
    return parts[0]


def assign_colors(suites: list[str]) -> dict[str, str]:
    """Assign a color to each suite from the palette, cycling if needed."""
    return {s: COLOR_PALETTE[i % len(COLOR_PALETTE)] for i, s in enumerate(suites)}


def group_suites(suites: list[str]) -> list[tuple[str, list[str]]]:
    """Group suites by their second path segment.

    Returns a list of (group_name, [suite_paths]) in discovery order.
    Groups with multiple members are aggregated; singletons stay as-is.
    """
    from collections import OrderedDict

    groups: OrderedDict[str, list[str]] = OrderedDict()
    for s in suites:
        key = suite_group_key(s)
        groups.setdefault(key, []).append(s)
    return list(groups.items())


def setup_ax(ax: Any) -> None:
    """Apply standard axis styling."""
    ax.set_facecolor(BG)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(SPINE_CLR)
    ax.spines["left"].set_linewidth(0.7)
    ax.spines["bottom"].set_color(SPINE_CLR)
    ax.spines["bottom"].set_linewidth(0.7)
    ax.tick_params(colors=FG, length=3, width=0.6, labelsize=9)
    ax.grid(True, axis="y", color=GRID_CLR, linewidth=0.5, alpha=0.8)
    ax.grid(False, axis="x")


def force_x_ticks(ax: Any, x_max: float) -> None:
    """Ensure x-axis ticks include 0 and x_max."""
    base = ax.get_xticks()
    forced = sorted(set([0, x_max] + [t for t in base if 0 <= t <= x_max]))
    ax.set_xticks(forced)
    ax.set_xticklabels([f"{t:.0f}" for t in forced])


def add_right_pct_axis(
    ax: Any, y_lim_pct: float = 108, fontsize: float = 10, labelsize: float = 9
) -> Any:
    """Add a right-side 0-100% axis."""
    axr = ax.twinx()
    axr.set_ylim(0, y_lim_pct)
    axr.set_ylabel("% of Total", color="#888888", fontsize=fontsize)
    axr.tick_params(colors="#888888", length=3, width=0.5, labelsize=labelsize)
    axr.spines["top"].set_visible(False)
    axr.spines["left"].set_visible(False)
    axr.spines["right"].set_color("#CCCCCC")
    axr.spines["right"].set_linewidth(0.5)
    axr.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    axr.set_facecolor("none")
    return axr


def save_chart(fig: Any, output_path: Path) -> None:
    """Save with standard settings."""
    import warnings

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        fig.tight_layout()
    fig.savefig(
        output_path, dpi=300, bbox_inches="tight",
        facecolor=BG, edgecolor="none", pad_inches=0.15,
    )
    plt.close(fig)


def get_suite_series(
    has_data: list[dict[str, Any]], suite_name: str
) -> tuple[Any, Any, Any]:
    """Extract (times, passed, total) arrays for a single suite."""
    times, passed, total = [], [], []
    for p in has_data:
        s = p["suites"].get(suite_name, {})
        sp = s.get("passed")
        st = s.get("total")
        if sp is not None:
            times.append(p["elapsed_minutes"])
            passed.append(sp)
            total.append(st or 0)
    return np.array(times), np.array(passed), np.array(total)


def get_group_series(
    has_data: list[dict[str, Any]], suite_names: list[str]
) -> tuple[Any, Any, Any]:
    """Aggregate one or more suites into a single (times, passed, total) series."""
    times, passed_agg, total_agg = [], [], []
    for p in has_data:
        bp = sum(p["suites"].get(s, {}).get("passed", 0) or 0 for s in suite_names)
        bt = sum(p["suites"].get(s, {}).get("total", 0) or 0 for s in suite_names)
        times.append(p["elapsed_minutes"])
        passed_agg.append(bp)
        total_agg.append(bt)
    return np.array(times), np.array(passed_agg), np.array(total_agg)


# ── Chart: Small multiples — grid of suite groups ────────────────────

def chart_small_multiples(
    has_data: list[dict[str, Any]],
    suites: list[str],
    model: str,
    output_path: Path,
) -> None:
    groups = group_suites(suites)
    n = len(groups)
    if n == 0:
        return
    ncols = min(n, 2)
    nrows = (n + ncols - 1) // ncols

    fig = plt.figure(figsize=(7 * ncols, 4.25 * nrows))
    fig.patch.set_facecolor(BG)
    gs = gridspec.GridSpec(nrows, ncols, hspace=0.35, wspace=0.3)

    for idx, (group_name, suite_members) in enumerate(groups):
        row, col = divmod(idx, ncols)
        ax = fig.add_subplot(gs[row, col])
        setup_ax(ax)

        color = COLOR_PALETTE[idx % len(COLOR_PALETTE)]
        t, passed, total = get_group_series(has_data, suite_members)

        if len(t) == 0:
            ax.set_title(group_name, fontsize=11, fontweight="600", color=FG, pad=8)
            continue

        max_total = int(total.max()) if len(total) > 0 else 0
        x_max = t[-1] if len(t) > 0 else 1

        plot_t = np.concatenate([[0.0], t])
        plot_y = np.concatenate([[0.0], passed.astype(float)])

        ax.fill_between(plot_t, 0, plot_y, alpha=0.15, color=color, linewidth=0)
        ax.plot(plot_t, plot_y, color=color, linewidth=2.0)
        ax.scatter(
            t, passed, s=14, color=color, edgecolors="white",
            linewidths=0.5, zorder=4,
        )

        if max_total > 0:
            ax.axhline(y=max_total, color=color, linewidth=0.7, linestyle="--", alpha=0.4)
            pct = passed[-1] / max_total * 100
            ax.set_ylim(0, max_total * 1.12)
        else:
            pct = 0
            ax.set_ylim(bottom=0)

        ax.set_title(group_name, fontsize=11, fontweight="600", color=FG, pad=8)
        ax.set_xlabel("Elapsed Time (min)", fontsize=9, color="#6A737D")
        ax.set_ylabel("Passed", fontsize=9, color="#6A737D")
        ax.set_xlim(0, x_max)
        force_x_ticks(ax, x_max)

        if max_total > 0:
            add_right_pct_axis(ax, y_lim_pct=112, fontsize=8, labelsize=7.5)

        ax.annotate(
            f"{int(passed[-1])}/{max_total} ({pct:.0f}%)",
            xy=(t[-1], passed[-1]), xytext=(-8, 10),
            textcoords="offset points", fontsize=9, fontweight="600",
            color=color, ha="right",
        )

    fig.suptitle(
        f"Suite Progression  |  {model}",
        fontsize=14, fontweight="700", color=FG, y=0.98,
    )
    save_chart(fig, output_path)


# ── Chart: Progress + velocity (the "hero" chart) ────────────────────

def chart_velocity(
    has_data: list[dict[str, Any]],
    total_tests: int,
    model: str,
    output_path: Path,
) -> None:
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(13, 7), height_ratios=[2, 1], sharex=True,
    )
    fig.patch.set_facecolor(BG)

    times = np.array([p["elapsed_minutes"] for p in has_data])
    passed = np.array([p["passed"] for p in has_data], dtype=float)

    plot_t = np.concatenate([[0.0], times])
    plot_y = np.concatenate([[0.0], passed])
    x_max = times[-1]

    # Top panel: passed tests
    setup_ax(ax1)
    ax1.fill_between(plot_t, 0, plot_y, alpha=0.12, color="#0366D6")
    ax1.plot(plot_t, plot_y, color="#0366D6", linewidth=2.2)
    ax1.scatter(
        times, passed, s=16, color="#0366D6", edgecolors="white",
        linewidths=0.5, zorder=4,
    )

    ax1.set_ylim(0, total_tests * 1.08)
    ax1.set_xlim(0, x_max)
    ax1.axhline(
        y=total_tests, color="#0366D6", linewidth=0.7, linestyle="--", alpha=0.4,
    )
    ax1.set_ylabel("Passed Tests", fontsize=10.5, color=FG)
    ax1.set_title(
        f"Progress  |  {model}",
        fontsize=13, fontweight="600", color=FG, pad=12,
    )

    add_right_pct_axis(ax1)

    # Auto-detect regression (largest single-step drop > 15% of prior value)
    reg_idx = None
    max_drop = 0.0
    for i in range(1, len(passed)):
        drop = passed[i - 1] - passed[i]
        if passed[i - 1] > 0 and drop / passed[i - 1] > 0.15 and drop > max_drop:
            max_drop = drop
            reg_idx = i
    if reg_idx is not None:
        ax1.annotate(
            f"\u2212{int(max_drop)} regression",
            xy=(times[reg_idx], passed[reg_idx]),
            xytext=(times[reg_idx] + 6, passed[reg_idx] - 80),
            fontsize=8.5, fontweight="500", color="#D73A49",
            arrowprops=dict(
                arrowstyle="-|>", color="#D73A49", lw=1.0,
                connectionstyle="arc3,rad=-0.2",
            ),
            bbox=dict(
                boxstyle="round,pad=0.3", facecolor="#FFF3E0",
                edgecolor="#D73A49", alpha=0.9, linewidth=0.7,
            ),
            zorder=12,
        )

    # Best value annotation (max passed, with arrow to the point)
    best_idx = int(np.argmax(passed))
    best_val = int(passed[best_idx])
    best_pct = best_val / total_tests * 100
    ax1.annotate(
        f"Best: {best_val} / {total_tests}  ({best_pct:.0f}%)",
        xy=(times[best_idx], passed[best_idx]),
        xytext=(-140, 20), textcoords="offset points",
        fontsize=10, fontweight="600", color="#0366D6",
        arrowprops=dict(
            arrowstyle="-|>", color="#0366D6", lw=1.0,
            connectionstyle="arc3,rad=0.2",
        ),
        zorder=12,
    )

    # Bottom panel: delta bars
    setup_ax(ax2)
    deltas = np.diff(passed)
    delta_times = (times[:-1] + times[1:]) / 2
    bar_colors = ["#2EA44F" if d >= 0 else "#D73A49" for d in deltas]
    ax2.bar(
        delta_times, deltas, width=1.8, color=bar_colors, alpha=0.75, edgecolor="none",
    )
    ax2.axhline(y=0, color=SPINE_CLR, linewidth=0.8)
    ax2.set_ylabel("\u0394 Tests", fontsize=10.5, color=FG)
    ax2.set_xlabel("Elapsed Time (minutes)", fontsize=10.5, color=FG)

    force_x_ticks(ax2, x_max)
    save_chart(fig, output_path)


# ── Chart: Heatmap — suite completion % over time ────────────────────

def chart_heatmap(
    has_data: list[dict[str, Any]],
    suites: list[str],
    total_tests: int,
    model: str,
    output_path: Path,
) -> None:
    row_defs = group_suites(suites)
    if not row_defs:
        return

    n_rows = len(row_defs)
    n_times = len(has_data)
    fig_height = max(3, 1.0 + n_rows * 0.9)
    fig, ax = plt.subplots(figsize=(14, fig_height))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    times = [p["elapsed_minutes"] for p in has_data]

    matrix = np.zeros((n_rows, n_times))
    row_totals: list[int] = []
    for ri, (_label, suite_list) in enumerate(row_defs):
        max_total = 0
        for ti, p in enumerate(has_data):
            sp = sum(p["suites"].get(s, {}).get("passed", 0) or 0 for s in suite_list)
            st = sum(p["suites"].get(s, {}).get("total", 1) or 1 for s in suite_list)
            max_total = max(max_total, st)
            matrix[ri, ti] = sp / st * 100 if st > 0 else 0
        row_totals.append(max_total)

    cmap = LinearSegmentedColormap.from_list(
        "envoi", ["#FFFFFF", "#BBD6F7", "#6BAED6", "#2171B5", "#08306B"],
    )

    im = ax.imshow(
        matrix, aspect="auto", cmap=cmap, vmin=0, vmax=100,
        interpolation="nearest",
    )

    tick_step = max(1, n_times // 10)
    ax.set_xticks(range(0, n_times, tick_step))
    ax.set_xticklabels(
        [f"{times[i]:.0f}" for i in range(0, n_times, tick_step)], fontsize=8.5,
    )
    ax.set_xlabel("Elapsed Time (minutes)", fontsize=10.5, color=FG)

    ylabels = [f"{row_defs[i][0]}  ({row_totals[i]})" for i in range(n_rows)]
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(ylabels, fontsize=10)

    if n_times <= 45:
        for ri in range(n_rows):
            for ti in range(n_times):
                val = matrix[ri, ti]
                color = "white" if val > 55 else FG
                ax.text(
                    ti, ri, f"{val:.0f}", ha="center", va="center",
                    fontsize=6.5, color=color, fontweight="400",
                )

    ax.set_title(
        f"Suite Completion Heatmap (%)  |  {model}",
        fontsize=13, fontweight="600", color=FG, pad=12,
    )

    cbar = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02, aspect=20)
    cbar.set_label("% Passed", fontsize=9, color="#6A737D")
    cbar.ax.tick_params(labelsize=8, colors="#6A737D")

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(SPINE_CLR)
    ax.spines["bottom"].set_color(SPINE_CLR)

    save_chart(fig, output_path)


# ── Chart entry point ────────────────────────────────────────────────

def generate_charts(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    """Generate all camera-ready charts from a trace report.

    Returns dict mapping chart name to file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    points = report.get("points", [])
    suites = report.get("suites", [])
    model = report.get("agent_model", "unknown")

    has_data = [p for p in points if p.get("passed") is not None]
    if not has_data:
        print("[charts] No evaluation data found, skipping chart generation.")
        return {}

    total_tests = max(
        (p["total"] for p in has_data if isinstance(p.get("total"), int) and p["total"] > 0),
        default=0,
    )
    if total_tests == 0:
        print("[charts] Could not determine total test count, skipping.")
        return {}

    charts: dict[str, str] = {}

    p = output_dir / "small_multiples.png"
    chart_small_multiples(has_data, suites, model, p)
    charts["small_multiples"] = str(p)

    p = output_dir / "progress.png"
    chart_velocity(has_data, total_tests, model, p)
    charts["progress"] = str(p)

    p = output_dir / "heatmap.png"
    chart_heatmap(has_data, suites, total_tests, model, p)
    charts["heatmap"] = str(p)

    return charts


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


def collect_eval_event_map(
    trace: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    parts = trace.get("parts")
    if not isinstance(parts, list):
        return {}
    by_commit: dict[str, dict[str, Any]] = {}
    status_rank = {
        "completed": 3,
        "failed": 2,
        "running": 1,
        "queued": 0,
    }
    for part in parts:
        if not isinstance(part, dict):
            continue
        events = part.get("eval_events_delta")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("kind") != "commit_async":
                continue
            commit = event.get("target_commit")
            if not isinstance(commit, str) or not commit:
                continue
            current = by_commit.get(commit)
            current_status = (
                current.get("status")
                if isinstance(current, dict)
                else None
            )
            event_status = event.get("status")
            current_rank = status_rank.get(
                current_status if isinstance(current_status, str) else "",
                -1,
            )
            event_rank = status_rank.get(
                event_status if isinstance(event_status, str) else "",
                -1,
            )
            if current is None or event_rank >= current_rank:
                by_commit[commit] = event
    return by_commit


def build_commit_points(trace: dict[str, Any]) -> list[dict[str, Any]]:
    evaluations = trace.get("evaluations")
    if not isinstance(evaluations, dict):
        return []

    part_timestamps = collect_part_timestamps(trace)
    eval_event_map = collect_eval_event_map(trace)
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
        response_payload = (
            payload.get("payload")
            if isinstance(payload.get("payload"), dict)
            else payload
        )
        payload_clusters = (
            response_payload.get("diagnostic_clusters")
            if isinstance(response_payload, dict)
            else None
        )
        if isinstance(payload_clusters, list):
            diagnostic_clusters = payload_clusters
        else:
            event = eval_event_map.get(commit)
            event_payload = (
                event.get("payload")
                if isinstance(event, dict)
                and isinstance(event.get("payload"), dict)
                else None
            )
            event_clusters = (
                event_payload.get("diagnostic_clusters")
                if isinstance(event_payload, dict)
                else None
            )
            diagnostic_clusters = (
                event_clusters
                if isinstance(event_clusters, list)
                else []
            )

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
                "diagnostic_clusters": diagnostic_clusters,
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


def detect_cluster_keys(points: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for point in points:
        clusters = point.get("diagnostic_clusters")
        if not isinstance(clusters, list):
            continue
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            key = cluster.get("key")
            if isinstance(key, str) and key:
                seen.add(key)
    # Sort by stable key for deterministic ordering.
    return sorted(seen)


def top_cluster_keys(
    points: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[str]:
    counts: dict[str, int] = {}
    for point in points:
        clusters = point.get("diagnostic_clusters")
        if not isinstance(clusters, list):
            continue
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            key = cluster.get("key")
            count = parse_int(cluster.get("count"))
            if not isinstance(key, str) or not key:
                continue
            counts[key] = max(count or 0, counts.get(key, 0))
    ranked = sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0]),
    )
    return [key for key, _ in ranked[: max(1, limit)]]



def build_report_from_trace(trace: dict[str, Any]) -> dict[str, Any]:
    points = annotate_elapsed_minutes(build_commit_points(trace))
    suites = detect_suites(points)
    cluster_keys = detect_cluster_keys(points)
    top_clusters = top_cluster_keys(points)
    return {
        "trajectory_id": trace.get("trajectory_id"),
        "agent_model": trace.get("agent_model"),
        "started_at": trace.get("started_at"),
        "generated_at": now_iso(),
        "analysis_source": "graph_png_v1",
        "x_axes": ["part", "elapsed_minutes"],
        "suites": suites,
        "diagnostic_cluster_keys": cluster_keys,
        "diagnostic_top_clusters": top_clusters,
        "points": points,
        "counts": {
            "commit_points": len(points),
            "diagnostic_clusters": len(cluster_keys),
        },
    }




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

    json_path = output_dir / "graph_data.json"
    json_path.write_text(json.dumps(report, indent=2))

    charts = generate_charts(report, output_dir)
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
