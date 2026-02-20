"""
MCP server exposing test tools.

Each run_tests call evaluates the current /workspace contents against the
test suites so that the latest code is always tested.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import envoi
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tests")

ENVOI_URL = "http://localhost:8000"


@mcp.tool()
async def run_tests(test_path: str) -> str:
    """
    Run C compiler tests against a test suite.

    Args:
        test_path: Test suite path. Options:
            - "basics" (all basics tests)
            - "basics/smoke", "basics/variables", "basics/control_flow", etc.
            - "wacct" (all chapters) or "wacct/chapter_1" through "wacct/chapter_20"
            - "c_testsuite" (all shards) or "c_testsuite/part_<n>"
            - "torture" (all shards) or "torture/part_<n>"

    Returns:
        JSON object with test results including passed/failed counts and details.
    """
    print(f"[mcp] run_tests called: {test_path}")
    start_time = time.monotonic()
    timestamp = datetime.now(UTC).isoformat()

    try:
        docs = envoi.Documents("/workspace")
        async with await envoi.connect_session(
            ENVOI_URL,
            submission=docs,
            session_timeout_seconds=3600,
        ) as session:
            result = await session.test(test_path)

        duration_ms = int((time.monotonic() - start_time) * 1000)

        response = {
            "path": test_path,
            "timestamp": timestamp,
            "duration_ms": duration_ms,
            "status_code": 200,
            "error": None,
            "result": result,
        }
        print(f"[mcp] run_tests success: {test_path} duration_ms={duration_ms}")
    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        response = {
            "path": test_path,
            "timestamp": timestamp,
            "duration_ms": duration_ms,
            "status_code": 500,
            "error": str(e),
            "result": None,
        }
        print(f"[mcp] run_tests error: {test_path} duration_ms={duration_ms} error={e}")

    return json.dumps(response)


if __name__ == "__main__":
    mcp.run(transport="stdio")
