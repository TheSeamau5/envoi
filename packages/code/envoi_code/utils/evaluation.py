"""Concurrent commit evaluation against envoi.

After a part changes files and creates a git checkpoint, this module evaluates
the commit by running the full environment test suite via the envoi server.
Uses bounded concurrency to evaluate multiple commits without overwhelming
the sandbox.
"""

from __future__ import annotations

import json
import os
import shlex
import uuid
from typing import Any

from envoi_code.sandbox.base import Sandbox
from envoi_code.utils.helpers import tprint

print = tprint

EVALUATION_CONCURRENCY = max(
    1, int(os.environ.get("EVALUATION_CONCURRENCY", "1"))
)
EVALUATION_TIMEOUT_SECONDS = max(
    60, int(os.environ.get("EVALUATION_TIMEOUT_SECONDS", "7200"))
)
EVALUATION_ENVOI_URL = (
    os.environ.get("EVALUATION_ENVOI_URL", "http://localhost:8000").strip()
    or "http://localhost:8000"
)
# Temporary debug default: run smoke suite instead of full-run.
# Set EVALUATION_TEST_PATH="" to force full-run session.test().
EVALUATION_TEST_PATH = os.environ.get("EVALUATION_TEST_PATH", "basics").strip()
EVALUATION_JSON_MARKER = "__ENVOI_EVAL_JSON__"


def extract_leaf_paths(schema: Any) -> list[str]:
    """Walk an envoi /schema tree and collect all leaf test paths."""
    # Handle the flat envoi format: {"tests": ["basics", "wacct", ...]}
    if isinstance(schema, dict):
        tests = schema.get("tests")
        if isinstance(tests, list):
            return sorted(t for t in tests if isinstance(t, str) and t)

    # Fallback: walk nested children/suites dicts
    leaves: list[str] = []

    def _walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            children = node.get("children") or node.get("suites")
            if isinstance(children, dict):
                for key, child in children.items():
                    _walk(child, f"{prefix}/{key}" if prefix else key)
                return
        # Leaf node
        if prefix:
            leaves.append(prefix)

    _walk(schema, "")
    return sorted(leaves) if leaves else []


def build_commit_evaluation_command(
    *,
    commit: str,
    eval_repo_dir: str,
) -> str:
    repo_dir_json = json.dumps(eval_repo_dir)
    envoi_url_json = json.dumps(EVALUATION_ENVOI_URL)
    eval_test_path_json = json.dumps(EVALUATION_TEST_PATH)
    marker_json = json.dumps(EVALUATION_JSON_MARKER)
    quoted_commit = shlex.quote(commit)
    quoted_repo_dir = shlex.quote(eval_repo_dir)
    return (
        "set -euo pipefail\n"
        f"repo_dir={quoted_repo_dir}\n"
        "rm -rf \"$repo_dir\"\n"
        "git clone -q /workspace \"$repo_dir\"\n"
        "cd \"$repo_dir\"\n"
        f"git checkout -q {quoted_commit}\n"
        "python3 - <<'PY'\n"
        "import asyncio\n"
        "import json\n"
        "import time\n"
        "import traceback\n"
        "import envoi\n"
        f"repo_dir = {repo_dir_json}\n"
        f"envoi_url = {envoi_url_json}\n"
        f"eval_test_path = {eval_test_path_json}\n"
        f"marker = {marker_json}\n"
        "def _collect_totals(node):\n"
        "    if isinstance(node, dict):\n"
        "        passed = node.get('passed')\n"
        "        failed = node.get('failed')\n"
        "        total = node.get('total')\n"
        "        if (\n"
        "            isinstance(passed, int)\n"
        "            and isinstance(failed, int)\n"
        "            and isinstance(total, int)\n"
        "        ):\n"
        "            return max(0, passed), max(0, failed), max(0, total)\n"
        "        p = f = t = 0\n"
        "        for value in node.values():\n"
        "            cp, cf, ct = _collect_totals(value)\n"
        "            p += cp\n"
        "            f += cf\n"
        "            t += ct\n"
        "        return p, f, t\n"
        "    if isinstance(node, list):\n"
        "        p = f = t = 0\n"
        "        for value in node:\n"
        "            cp, cf, ct = _collect_totals(value)\n"
        "            p += cp\n"
        "            f += cf\n"
        "            t += ct\n"
        "        return p, f, t\n"
        "    return 0, 0, 0\n"
        "async def _main() -> None:\n"
        "    started_at = time.monotonic()\n"
        "    payload = {\n"
        "        'duration_ms': 0,\n"
        "        'passed': 0,\n"
        "        'failed': 0,\n"
        "        'total': 0,\n"
        "        'suite_results': {},\n"
        "        'error': None,\n"
        "    }\n"
        "    try:\n"
        "        docs = envoi.Documents(repo_dir)\n"
        "        async with await envoi.connect_session(\n"
        "            envoi_url,\n"
        "            connect_timeout_seconds=7200,\n"
        "            submission=docs,\n"
        "            session_timeout_seconds=7200,\n"
        "        ) as session:\n"
        "            result = (\n"
        "                await session.test(eval_test_path)\n"
        "                if eval_test_path\n"
        "                else await session.test()\n"
        "            )\n"
        "            passed, failed, total = _collect_totals(result)\n"
        "            payload['passed'] = int(passed)\n"
        "            payload['failed'] = int(failed)\n"
        "            payload['total'] = int(total)\n"
        "            suite_key = eval_test_path if eval_test_path else 'all'\n"
        "            payload['suite_results'] = {\n"
        "                suite_key: {\n"
        "                    'ok': failed == 0 and total > 0,\n"
        "                    'passed': int(passed),\n"
        "                    'failed': int(failed),\n"
        "                    'total': int(total),\n"
        "                    'error': None,\n"
        "                }\n"
        "            }\n"
        "    except Exception as error:  # noqa: BLE001\n"
        "        msg = str(error).strip()\n"
        "        payload['error'] = msg if msg else type(error).__name__\n"
        "        payload['traceback'] = traceback.format_exc()\n"
        "    finally:\n"
        "        payload['duration_ms'] = "
        "int((time.monotonic() - started_at) * 1000)\n"
        "    print(marker + json.dumps(payload, ensure_ascii=False))\n"
        "asyncio.run(_main())\n"
        "PY\n"
        "status=$?\n"
        "cd /workspace\n"
        "rm -rf \"$repo_dir\"\n"
        "exit $status\n"
    )


def parse_commit_evaluation_payload(
    stdout: str,
) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if not line.startswith(EVALUATION_JSON_MARKER):
            continue
        raw_json = line[len(EVALUATION_JSON_MARKER):].strip()
        if not raw_json:
            continue
        try:
            parsed = json.loads(raw_json)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


async def run_commit_evaluation(
    *,
    sandbox: Sandbox,
    commit: str,
) -> dict[str, Any]:
    eval_repo_dir = (
        f"/tmp/envoi-eval-{commit[:12]}-{uuid.uuid4().hex[:8]}"
    )
    command = build_commit_evaluation_command(
        commit=commit,
        eval_repo_dir=eval_repo_dir,
    )
    exit_code, stdout, stderr = (
        await sandbox.run(
            command,
            timeout=EVALUATION_TIMEOUT_SECONDS,
            quiet=True,
        )
    ).unpack()
    payload = parse_commit_evaluation_payload(stdout)
    return {
        "command": command,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "payload": payload,
    }
