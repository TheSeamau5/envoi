#!/usr/bin/env python
from __future__ import annotations

import io
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import pyarrow.parquet as pq
import requests

WEB_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("ENVOI_UI_BASE_URL", "http://localhost:3000")
PROJECT = "c-compiler"
S3_PREFIX = os.environ.get("AWS_S3_PREFIX", "envoi-trace-data").strip() or "envoi-trace-data"
COLD_PAGE_MAX_SECONDS = 0.3
WARM_PAGE_MAX_SECONDS = 0.1


@dataclass
class RouteCheck:
    name: str
    path: str
    ready_text: str
    max_seconds: float
    extra_ready_text: str | None = None


ROUTE_CHECKS = [
    RouteCheck(
        name="trajectory_list",
        path=f"/project/{PROJECT}/trajectory",
        ready_text="TRAJECTORIES",
        extra_ready_text="CODEx/GPT-5.3-CODEX".lower(),
        max_seconds=5.0,
    ),
    RouteCheck(
        name="compare_curves",
        path=f"/project/{PROJECT}/compare/curves",
        ready_text="Curves",
        max_seconds=5.0,
    ),
    RouteCheck(
        name="setups",
        path=f"/project/{PROJECT}/setups",
        ready_text="Setup Compare",
        extra_ready_text="Median Progress Curves",
        max_seconds=5.0,
    ),
    RouteCheck(
        name="difficulty",
        path=f"/project/{PROJECT}/difficulty",
        ready_text="Difficulty Heatmap",
        extra_ready_text="Each cell shows the",
        max_seconds=4.0,
    ),
    RouteCheck(
        name="portfolio",
        path=f"/project/{PROJECT}/portfolio",
        ready_text="Portfolio Dashboard",
        max_seconds=4.0,
    ),
    RouteCheck(
        name="query",
        path=f"/project/{PROJECT}/query",
        ready_text="SQL Console",
        extra_ready_text="Templates",
        max_seconds=4.0,
    ),
]


def format_duration_label(started_at: str, ended_at: str) -> str:
    start_epoch = time.mktime(time.strptime(started_at[:19], "%Y-%m-%dT%H:%M:%S"))
    end_epoch = time.mktime(time.strptime(ended_at[:19], "%Y-%m-%dT%H:%M:%S"))
    minutes_total = round((end_epoch - start_epoch) / 60)
    hours = minutes_total // 60
    minutes = minutes_total % 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def normalize_api_duration_label(label: str) -> str:
    value = label.strip()
    value = value.replace(" hrs ", "h ")
    value = value.replace(" hr ", "h ")
    value = value.replace(" min", "m")
    return value


def format_percent(passed: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(passed / total) * 100:.1f}%"


def wait_for_server() -> subprocess.Popen[str] | None:
    health_url = f"{BASE_URL}/api/revision?project={PROJECT}"
    try:
        response = requests.get(health_url, timeout=1)
        if response.ok:
            return None
    except Exception:
        pass

    process = subprocess.Popen(
        ["pnpm", "start"],
        cwd=WEB_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    started_at = time.time()
    while time.time() - started_at < 60:
        line = process.stdout.readline() if process.stdout else ""
        if line:
            print(line.rstrip())
        try:
            response = requests.get(health_url, timeout=1)
            if response.ok:
                return process
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Timed out waiting for web server to start")


def stop_server(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def api_get(path: str) -> requests.Response:
    response = requests.get(f"{BASE_URL}{path}", timeout=120)
    response.raise_for_status()
    return response


def timed_get(path: str) -> tuple[requests.Response, float]:
    started_at = time.perf_counter()
    response = api_get(path)
    return response, time.perf_counter() - started_at


def load_parquet_rows(key: str, columns: list[str]) -> list[dict[str, Any]]:
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=S3_PREFIX, Key=key)
    raw = response["Body"].read()
    table = pq.read_table(io.BytesIO(raw), columns=columns)
    return table.to_pylist()


def s3_summary_row(trajectory_id: str) -> dict[str, Any]:
    trace_key = (
        f"project/{PROJECT}/trajectories/{trajectory_id}/trace.parquet"
    )
    log_key = (
        f"project/{PROJECT}/trajectories/{trajectory_id}/logs.parquet"
    )
    trace_rows = load_parquet_rows(
        trace_key,
        [
            "trajectory_id",
            "started_at",
            "timestamp",
            "part",
            "session_end_reason",
            "eval_events_delta",
        ],
    )
    if len(trace_rows) == 0:
        raise RuntimeError(f"Expected trace rows for {trajectory_id}")

    started_at = str(trace_rows[0]["started_at"])
    ended_at = str(trace_rows[-1]["timestamp"])
    total_parts = int(trace_rows[-1]["part"]) + 1
    session_end_reason = trace_rows[-1].get("session_end_reason")
    best_passed = 0
    best_total = 0

    for row in trace_rows:
        raw_events = row.get("eval_events_delta")
        if not isinstance(raw_events, str) or len(raw_events) <= 2:
            continue
        try:
            events = json.loads(raw_events)
        except Exception:
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("status") != "completed":
                continue
            passed = int(event.get("passed", 0) or 0)
            total = int(event.get("total", 0) or 0)
            if passed > best_passed or total > best_total:
                best_passed = passed
                best_total = total

    try:
        log_rows = load_parquet_rows(log_key, ["ts"])
        if len(log_rows) > 0:
            max_log_ts = str(log_rows[-1]["ts"])
            if max_log_ts > ended_at:
                ended_at = max_log_ts
    except Exception:
        pass

    return {
        "started_at": started_at,
        "ended_at": ended_at,
        "total_parts": total_parts,
        "session_end_reason": session_end_reason,
        "best_passed": best_passed,
        "best_total": best_total,
    }


def assert_match(name: str, left: Any, right: Any) -> None:
    if left != right:
        raise AssertionError(f"{name} mismatch: {left!r} != {right!r}")


def parse_row_metrics(row_text: str) -> tuple[str, int, str]:
    match = re.search(
        r"(?P<duration>\d+h \d+m|\d+m)\s+(?P<passed>\d+)\s+(?P<pct>\d+\.\d+%)",
        row_text,
    )
    if not match:
        raise AssertionError(f"Could not parse row metrics from: {row_text}")
    return (
        match.group("duration"),
        int(match.group("passed")),
        match.group("pct"),
    )


def expected_detail_score_text(row: dict[str, Any]) -> str | None:
    best_total = int(row["best_total"])
    if best_total <= 0:
        return None
    return f"{row['best_passed']} passed / {best_total - row['best_passed']} failed"


def serving_rows_match_s3(api_rows: list[dict[str, Any]]) -> bool:
    for api_row in api_rows:
        trajectory_id = str(api_row["id"])
        s3_row = s3_summary_row(trajectory_id)
        if str(api_row["startedAt"]) != s3_row["started_at"]:
            return False
        if int(api_row["totalParts"]) != int(s3_row["total_parts"]):
            return False
        if api_row.get("sessionEndReason") != s3_row["session_end_reason"]:
            return False
        if int(api_row["finalPassed"]) != int(s3_row["best_passed"]):
            return False
        if int(api_row["totalTests"]) != int(s3_row["best_total"]):
            return False
    return True


def wait_for_serving_snapshot_ready() -> list[dict[str, Any]]:
    started_at = time.time()
    last_api_rows: list[dict[str, Any]] = []
    while time.time() - started_at < 60:
        api_rows = api_get(f"/api/trajectories?project={PROJECT}").json()
        if isinstance(api_rows, list) and len(api_rows) > 0:
            last_api_rows = api_rows
            if serving_rows_match_s3(api_rows):
                return api_rows
        time.sleep(1)
    raise RuntimeError(
        f"Timed out waiting for serving snapshot readiness. Last rows: {last_api_rows}",
    )

def run_http_route_checks(trajectory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for route in ROUTE_CHECKS:
        response, duration = timed_get(route.path)
        body_text = response.text
        body_text_lower = body_text.lower()
        if route.ready_text.lower() not in body_text_lower:
            raise AssertionError(f"{route.name}: missing ready text '{route.ready_text}'")
        if route.extra_ready_text and route.extra_ready_text.lower() not in body_text_lower:
            raise AssertionError(
                f"{route.name}: missing extra ready text '{route.extra_ready_text}'",
            )
        if duration > route.max_seconds:
            raise AssertionError(
                f"{route.name}: route load too slow ({duration:.3f}s > {route.max_seconds:.3f}s)",
            )
        results.append({
            "route": route.name,
            "path": route.path,
            "duration_s": round(duration, 3),
        })

    list_path = f"/project/{PROJECT}/trajectory"
    first_list_response, first_list_duration = timed_get(list_path)
    second_list_response, second_list_duration = timed_get(list_path)
    if "Trajectories" not in first_list_response.text:
        raise AssertionError("trajectory_list: missing Trajectories heading")
    if first_list_duration > COLD_PAGE_MAX_SECONDS:
        raise AssertionError(
            f"trajectory_list_cold: too slow ({first_list_duration:.3f}s > {COLD_PAGE_MAX_SECONDS:.3f}s)",
        )
    if second_list_duration > WARM_PAGE_MAX_SECONDS:
        raise AssertionError(
            f"trajectory_list_warm: too slow ({second_list_duration:.3f}s > {WARM_PAGE_MAX_SECONDS:.3f}s)",
        )
    results.append({
        "route": "trajectory_list_cold",
        "path": list_path,
        "duration_s": round(first_list_duration, 3),
    })
    results.append({
        "route": "trajectory_list_warm",
        "path": list_path,
        "duration_s": round(second_list_duration, 3),
    })

    list_html = first_list_response.text
    for row in trajectory_rows:
        trajectory_id = str(row["trajectory_id"])
        if trajectory_id not in list_html:
            raise AssertionError(f"{trajectory_id}: list page missing trajectory id")

    row = next(
        (candidate for candidate in trajectory_rows if expected_detail_score_text(candidate)),
        None,
    )
    if row is None:
        raise AssertionError("Expected at least one completed evaluation for detail verification")
    trajectory_id = str(row["trajectory_id"])
    detail_score = expected_detail_score_text(row)
    detail_path = f"/project/{PROJECT}/trajectory/{trajectory_id}"
    detail_first_response, detail_first_duration = timed_get(detail_path)
    detail_second_response, detail_second_duration = timed_get(detail_path)
    if detail_score and detail_score not in detail_first_response.text:
        raise AssertionError(
            f"{trajectory_id}: detail page missing expected score '{detail_score}'",
        )
    if detail_first_duration > COLD_PAGE_MAX_SECONDS:
        raise AssertionError(
            f"trajectory_detail_cold: too slow ({detail_first_duration:.3f}s > {COLD_PAGE_MAX_SECONDS:.3f}s)",
        )
    if detail_second_duration > WARM_PAGE_MAX_SECONDS:
        raise AssertionError(
            f"trajectory_detail_warm: too slow ({detail_second_duration:.3f}s > {WARM_PAGE_MAX_SECONDS:.3f}s)",
        )
    results.append({
        "route": "trajectory_detail_cold",
        "path": detail_path,
        "duration_s": round(detail_first_duration, 3),
    })
    results.append({
        "route": "trajectory_detail_warm",
        "path": detail_path,
        "duration_s": round(detail_second_duration, 3),
    })

    return results


def main() -> int:
    server_process = wait_for_server()
    try:
        api_rows = wait_for_serving_snapshot_ready()
        if not isinstance(api_rows, list) or len(api_rows) == 0:
            raise RuntimeError(f"Unexpected trajectory payload: {api_rows}")

        trajectory_rows: list[dict[str, Any]] = []
        for api_row in api_rows:
            trajectory_id = str(api_row["id"])
            s3_row = s3_summary_row(trajectory_id)
            assert_match(f"{trajectory_id} started_at", str(api_row["startedAt"]), s3_row["started_at"])
            assert_match(f"{trajectory_id} total_parts", int(api_row["totalParts"]), int(s3_row["total_parts"]))
            assert_match(
                f"{trajectory_id} session_end_reason",
                api_row.get("sessionEndReason"),
                s3_row["session_end_reason"],
            )
            assert_match(
                f"{trajectory_id} best_passed",
                int(api_row["finalPassed"]),
                int(s3_row["best_passed"]),
            )
            assert_match(
                f"{trajectory_id} best_total",
                int(api_row["totalTests"]),
                int(s3_row["best_total"]),
            )

            started_at = str(s3_row["started_at"])
            ended_at = str(s3_row["ended_at"])
            best_passed = int(s3_row["best_passed"])
            best_total = int(s3_row["best_total"])
            trajectory_rows.append({
                "trajectory_id": trajectory_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "total_parts": int(s3_row["total_parts"]),
                "best_passed": best_passed,
                "best_total": best_total,
                "ui_duration": normalize_api_duration_label(str(api_row["duration"])),
                "ui_pct": format_percent(best_passed, best_total),
            })

        route_results = run_http_route_checks(trajectory_rows)
        print(
            json.dumps(
                {
                    "project": PROJECT,
                    "s3_prefix": S3_PREFIX,
                    "trajectory_checks": trajectory_rows,
                    "route_results": route_results,
                },
                indent=2,
            )
        )
        return 0
    finally:
        stop_server(server_process)


if __name__ == "__main__":
    raise SystemExit(main())
