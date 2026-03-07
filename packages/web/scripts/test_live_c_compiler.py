#!/usr/bin/env python
from __future__ import annotations

import json
import io
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
from playwright.sync_api import sync_playwright

WEB_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("ENVOI_UI_BASE_URL", "http://localhost:3000")
PROJECT = "c-compiler"
S3_PREFIX = os.environ.get("AWS_S3_PREFIX", "envoi-trace-data").strip() or "envoi-trace-data"
SCREENSHOT_DIR = Path("/tmp/envoi-live-c-compiler")
CACHED_REVISIT_MAX_SECONDS = 0.5
DELAYED_API_SECONDS = 2.5
BLOCKED_LOADING_COPY = [
    "Loading cached runs...",
    "Loading cached run...",
    "Loading trajectory...",
]


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
        ready_text="0 selected",
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


def format_percent(passed: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(passed / total) * 100:.1f}%"


def wait_for_server() -> subprocess.Popen[str] | None:
    try:
        response = requests.get(BASE_URL, timeout=1)
        if response.ok:
            return None
    except Exception:
        pass

    process = subprocess.Popen(
        ["pnpm", "run", "dev"],
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
            response = requests.get(BASE_URL, timeout=1)
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


def api_post(path: str, payload: dict[str, Any]) -> requests.Response:
    response = requests.post(
        f"{BASE_URL}{path}",
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    return response


def sql_rows(sql: str) -> list[dict[str, Any]]:
    response = api_post(f"/api/query?project={PROJECT}", {"sql": sql})
    data = response.json()
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected SQL response: {data}")
    return rows


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


def cache_summary_row(trajectory_id: str) -> dict[str, Any]:
    sql = f"""
    SELECT
      trajectory_id,
      started_at,
      ended_at,
      total_parts,
      session_end_reason,
      sandbox_id,
      sandbox_provider,
      COALESCE(
        (
          SELECT SUM(CAST(json_extract(suites::JSON, '$.' || '"' || key || '"' || '.passed') AS INTEGER))
          FROM unnest(json_keys(suites::JSON)) AS key
        ),
        0
      ) AS best_passed,
      COALESCE(
        (
          SELECT SUM(CAST(json_extract(suites::JSON, '$.' || '"' || key || '"' || '.total') AS INTEGER))
          FROM unnest(json_keys(suites::JSON)) AS key
        ),
        0
      ) AS best_total
    FROM trajectories
    WHERE trajectory_id = '{trajectory_id}'
    LIMIT 1
    """
    rows = sql_rows(sql)
    if len(rows) != 1:
        raise RuntimeError(f"Expected 1 cache row for {trajectory_id}, got {rows}")
    return rows[0]


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


def assert_no_blocked_loading_copy(page: Any, context_name: str) -> None:
    body_text = page.locator("body").inner_text()
    for blocked_text in BLOCKED_LOADING_COPY:
        if blocked_text in body_text:
            raise AssertionError(
                f"{context_name}: blocked loading copy visible during cached revisit: {blocked_text}",
            )


def install_delayed_route(context: Any, pattern: Any) -> Any:
    def handler(route: Any) -> None:
        time.sleep(DELAYED_API_SECONDS)
        route.continue_()

    context.route(pattern, handler)
    return handler


def run_cached_navigation_checks(
    context: Any,
    list_page: Any,
    trajectory_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(trajectory_rows) == 0:
        return []

    row = next(
        (candidate for candidate in trajectory_rows if expected_detail_score_text(candidate)),
        None,
    )
    if row is None:
        raise AssertionError(
            "Expected at least one completed evaluation to verify cached detail revisits",
        )
    trajectory_id = str(row["trajectory_id"])
    detail_score = expected_detail_score_text(row)
    if detail_score is None:
        raise AssertionError(f"{trajectory_id}: missing detail score for cached revisit check")

    list_path = f"/project/{PROJECT}/trajectory"
    detail_path = f"/project/{PROJECT}/trajectory/{trajectory_id}"
    list_link = list_page.locator(
        f'a[href="{detail_path}"]'
    ).first

    list_link.wait_for(timeout=120000)
    list_link.click()
    list_page.wait_for_url(f"{BASE_URL}{detail_path}", timeout=120000)
    list_page.get_by_text(detail_score, exact=False).first.wait_for(timeout=120000)
    list_page.wait_for_timeout(750)

    results: list[dict[str, Any]] = []
    list_api_pattern = re.compile(
        rf".*/api/trajectories\?project={re.escape(PROJECT)}(?:&.*)?$",
    )
    detail_api_pattern = re.compile(
        rf".*/api/trajectories/{re.escape(trajectory_id)}\?project={re.escape(PROJECT)}(?:&.*)?$",
    )

    delayed_list_handler = install_delayed_route(context, list_api_pattern)
    try:
        started_at = time.perf_counter()
        list_page.evaluate("window.history.back()")
        list_page.wait_for_url(f"{BASE_URL}{list_path}", timeout=120000)
        list_page.get_by_text("TRAJECTORIES", exact=False).first.wait_for(
            timeout=round(CACHED_REVISIT_MAX_SECONDS * 1000),
        )
        list_page.locator(
            f'a[href="{detail_path}"]'
        ).first.wait_for(timeout=round(CACHED_REVISIT_MAX_SECONDS * 1000))
        duration = time.perf_counter() - started_at
        if duration > CACHED_REVISIT_MAX_SECONDS:
            raise AssertionError(
                f"trajectory_list_revisit: cached revisit too slow ({duration:.3f}s > {CACHED_REVISIT_MAX_SECONDS:.3f}s)",
            )
        assert_no_blocked_loading_copy(list_page, "trajectory_list_revisit")
        results.append({
            "route": "trajectory_list_revisit",
            "path": list_path,
            "duration_s": round(duration, 3),
            "delayed_api_s": DELAYED_API_SECONDS,
        })
        list_page.wait_for_timeout(round(DELAYED_API_SECONDS * 1000) + 250)
    finally:
        context.unroute(list_api_pattern, delayed_list_handler)

    delayed_detail_handler = install_delayed_route(context, detail_api_pattern)
    try:
        started_at = time.perf_counter()
        list_page.locator(f'a[href="{detail_path}"]').first.click()
        list_page.wait_for_url(f"{BASE_URL}{detail_path}", timeout=120000)
        list_page.get_by_text(detail_score, exact=False).first.wait_for(
            timeout=round(CACHED_REVISIT_MAX_SECONDS * 1000),
        )
        duration = time.perf_counter() - started_at
        if duration > CACHED_REVISIT_MAX_SECONDS:
            raise AssertionError(
                f"trajectory_detail_revisit: cached revisit too slow ({duration:.3f}s > {CACHED_REVISIT_MAX_SECONDS:.3f}s)",
            )
        assert_no_blocked_loading_copy(list_page, "trajectory_detail_revisit")
        results.append({
            "route": "trajectory_detail_revisit",
            "path": detail_path,
            "duration_s": round(duration, 3),
            "delayed_api_s": DELAYED_API_SECONDS,
        })
        list_page.wait_for_timeout(round(DELAYED_API_SECONDS * 1000) + 250)
    finally:
        context.unroute(detail_api_pattern, delayed_detail_handler)

    return results


def run_playwright_checks(trajectory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})

        page = context.new_page()
        page.goto(f"{BASE_URL}/project/{PROJECT}", wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(500)

        for route in ROUTE_CHECKS:
            route_page = context.new_page()
            console_messages: list[str] = []
            page_errors: list[str] = []
            route_page.on(
                "console",
                lambda msg, console_messages=console_messages: console_messages.append(f"{msg.type}: {msg.text}"),
            )
            route_page.on(
                "pageerror",
                lambda error, page_errors=page_errors: page_errors.append(str(error)),
            )
            started_at = time.perf_counter()
            route_page.goto(
                f"{BASE_URL}{route.path}",
                wait_until="domcontentloaded",
                timeout=120000,
            )
            route_page.get_by_text(route.ready_text, exact=False).first.wait_for(timeout=120000)
            if route.extra_ready_text:
                route_page.get_by_text(route.extra_ready_text, exact=False).first.wait_for(timeout=120000)
            route_page.wait_for_timeout(750)
            duration = time.perf_counter() - started_at
            body_text = route_page.locator("body").inner_text()
            if "Console Error" in body_text or "Application error" in body_text:
                raise AssertionError(f"{route.name}: runtime error overlay present")
            if duration > route.max_seconds:
                raise AssertionError(
                    f"{route.name}: route load too slow ({duration:.3f}s > {route.max_seconds:.3f}s)",
                )
            severe_console = [
                message
                for message in console_messages
                if "favicon" not in message.lower()
                and "download the react devtools" not in message.lower()
                and not message.startswith("log:")
                and not message.startswith("info:")
            ]
            if severe_console:
                raise AssertionError(f"{route.name}: console issues: {severe_console[0]}")
            if page_errors:
                raise AssertionError(f"{route.name}: page error: {page_errors[0]}")
            route_page.screenshot(path=str(SCREENSHOT_DIR / f"{route.name}.png"), full_page=True)
            results.append({
                "route": route.name,
                "path": route.path,
                "duration_s": round(duration, 3),
            })
            route_page.close()

        list_page = context.new_page()
        list_page.goto(
            f"{BASE_URL}/project/{PROJECT}/trajectory",
            wait_until="domcontentloaded",
            timeout=120000,
        )
        list_page.get_by_text("TRAJECTORIES", exact=False).first.wait_for(timeout=120000)
        list_page.wait_for_timeout(1000)

        for row in trajectory_rows:
            trajectory_id = str(row["trajectory_id"])
            link = list_page.locator(
                f'a[href="/project/{PROJECT}/trajectory/{trajectory_id}"]'
            ).first
            link.wait_for(timeout=120000)
            row_text = link.inner_text()
            duration_label, passed, pct = parse_row_metrics(row_text)
            assert_match(
                f"{trajectory_id} duration label",
                duration_label,
                row["ui_duration"],
            )
            assert_match(f"{trajectory_id} passed", passed, row["best_passed"])
            assert_match(f"{trajectory_id} pct", pct, row["ui_pct"])

        for row in trajectory_rows[:2]:
            trajectory_id = str(row["trajectory_id"])
            detail_page = context.new_page()
            detail_page.goto(
                f"{BASE_URL}/project/{PROJECT}/trajectory/{trajectory_id}",
                wait_until="domcontentloaded",
                timeout=120000,
            )
            detail_ready_text = expected_detail_score_text(row) or trajectory_id
            detail_page.get_by_text(detail_ready_text, exact=False).first.wait_for(timeout=120000)
            detail_page.wait_for_timeout(1000)
            detail_text = detail_page.locator("body").inner_text()
            expected = expected_detail_score_text(row)
            if expected and expected not in detail_text:
                raise AssertionError(
                    f"{trajectory_id}: detail page missing expected score '{expected}'",
                )
            if "Console Error" in detail_text or "Application error" in detail_text:
                raise AssertionError(f"{trajectory_id}: runtime error overlay on detail page")
            detail_page.screenshot(
                path=str(SCREENSHOT_DIR / f"detail-{trajectory_id}.png"),
                full_page=True,
            )
            detail_page.close()

        results.extend(run_cached_navigation_checks(context, list_page, trajectory_rows))
        list_page.close()
        browser.close()
    return results


def main() -> int:
    server_process = wait_for_server()
    try:
        api_rows = api_get(f"/api/trajectories?project={PROJECT}").json()
        if not isinstance(api_rows, list) or len(api_rows) == 0:
            raise RuntimeError(f"Unexpected trajectory payload: {api_rows}")

        trajectory_rows: list[dict[str, Any]] = []
        for api_row in api_rows:
            trajectory_id = str(api_row["id"])
            s3_row = s3_summary_row(trajectory_id)
            cache_row = cache_summary_row(trajectory_id)
            assert_match(f"{trajectory_id} started_at", cache_row["started_at"], s3_row["started_at"])
            assert_match(f"{trajectory_id} ended_at", cache_row["ended_at"], s3_row["ended_at"])
            assert_match(f"{trajectory_id} total_parts", int(cache_row["total_parts"]), int(s3_row["total_parts"]))
            assert_match(
                f"{trajectory_id} session_end_reason",
                cache_row["session_end_reason"],
                s3_row["session_end_reason"],
            )
            assert_match(
                f"{trajectory_id} best_passed",
                int(cache_row["best_passed"]),
                int(s3_row["best_passed"]),
            )
            assert_match(
                f"{trajectory_id} best_total",
                int(cache_row["best_total"]),
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
                "ui_duration": format_duration_label(started_at, ended_at),
                "ui_pct": format_percent(best_passed, best_total),
            })

        route_results = run_playwright_checks(trajectory_rows)
        print(
            json.dumps(
                {
                    "project": PROJECT,
                    "s3_prefix": S3_PREFIX,
                    "trajectory_checks": trajectory_rows,
                    "route_results": route_results,
                    "screenshots": str(SCREENSHOT_DIR),
                },
                indent=2,
            )
        )
        return 0
    finally:
        stop_server(server_process)


if __name__ == "__main__":
    raise SystemExit(main())
