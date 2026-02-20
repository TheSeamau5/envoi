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
    Run task tests against a suite path.

    Args:
        test_path: Suite path understood by the current envoi environment.

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
