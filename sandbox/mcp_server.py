"""
MCP server that wraps envoi test calls.

This server exposes a single tool `run_tests` that the agent can use
to run C compiler tests. It connects to the envoi runtime running on
localhost:8000 and forwards test requests.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import envoi
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("envoi-tests")

_envoi_session: envoi.Session | None = None
_envoi_client: envoi.Client | None = None


async def get_envoi_session() -> envoi.Session:
    global _envoi_session, _envoi_client
    if _envoi_session is None:
        _envoi_client = await envoi.connect("http://localhost:8000")
        docs = envoi.Documents("/workspace")
        _envoi_session = await _envoi_client.session(timeout_seconds=7200, submission=docs)
    return _envoi_session


@mcp.tool()
async def run_tests(test_path: str) -> str:
    """
    Run C compiler tests against a test suite.

    Args:
        test_path: Test suite path. Options:
            - "basics" (all basics tests)
            - "basics/smoke", "basics/variables", "basics/control_flow", etc.
            - "wacct/chapter_1" through "wacct/chapter_20"
            - "c_testsuite/part_1" through "c_testsuite/part_5"
            - "torture/part_1" through "torture/part_10"

    Returns:
        JSON object with test results including passed/failed counts and details.
    """
    start_time = time.monotonic()
    timestamp = datetime.now(UTC).isoformat()

    try:
        session = await get_envoi_session()
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

    return json.dumps(response)


if __name__ == "__main__":
    mcp.run(transport="stdio")
