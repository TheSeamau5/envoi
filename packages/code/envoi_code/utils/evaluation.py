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


def _build_evaluation_python_script(
    *,
    repo_dir_json: str,
    envoi_url_json: str,
    eval_test_path_json: str,
    marker_json: str,
) -> str:
    return (
        "import asyncio\n"
        "import json\n"
        "import time\n"
        "import traceback\n"
        "import envoi\n"
        f"repo_dir = {repo_dir_json}\n"
        f"envoi_url = {envoi_url_json}\n"
        f"eval_test_path = {eval_test_path_json}\n"
        f"marker = {marker_json}\n"
        "MAX_MESSAGE_CHARS = 320\n"
        "MAX_TAIL_CHARS = 1200\n"
        "def _as_str(value):\n"
        "    if isinstance(value, str):\n"
        "        return value\n"
        "    if value is None:\n"
        "        return None\n"
        "    return str(value)\n"
        "def _normalize_suite_path(value):\n"
        "    text = _as_str(value)\n"
        "    if text is None:\n"
        "        return ''\n"
        "    parts = [part for part in text.split('/') if part]\n"
        "    if not parts:\n"
        "        return ''\n"
        "    normalized = [parts[0]]\n"
        "    for part in parts[1:]:\n"
        "        if part == normalized[-1]:\n"
        "            continue\n"
        "        normalized.append(part)\n"
        "    return '/'.join(normalized)\n"
        "def _clip_message(value):\n"
        "    text = _as_str(value)\n"
        "    if text is None:\n"
        "        return None, False\n"
        "    text = text.strip()\n"
        "    if not text:\n"
        "        return None, False\n"
        "    if len(text) <= MAX_MESSAGE_CHARS:\n"
        "        return text, False\n"
        "    return text[:MAX_MESSAGE_CHARS], True\n"
        "def _clip_tail(value):\n"
        "    text = _as_str(value)\n"
        "    if text is None:\n"
        "        return None, False\n"
        "    text = text.strip()\n"
        "    if not text:\n"
        "        return None, False\n"
        "    if len(text) <= MAX_TAIL_CHARS:\n"
        "        return text, False\n"
        "    return text[-MAX_TAIL_CHARS:], True\n"
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
        "def _duration_ms_from_case(case):\n"
        "    total = 0.0\n"
        "    for key in ('compile_time_ms', 'run_time_ms'):\n"
        "        value = case.get(key)\n"
        "        if isinstance(value, (int, float)):\n"
        "            total += max(0.0, float(value))\n"
        "    return int(total) if total > 0 else None\n"
        "def _extract_case_tests(cases, suite):\n"
        "    out = []\n"
        "    for idx, case in enumerate(cases):\n"
        "        if not isinstance(case, dict):\n"
        "            continue\n"
        "        test_id = _as_str(case.get('name')) or f'case_{idx + 1}'\n"
        "        passed_flag = case.get('passed')\n"
        "        passed = bool(passed_flag) if isinstance(passed_flag, bool) else False\n"
        "        status = 'passed' if passed else 'failed'\n"
        "        phase = case.get('phase') if isinstance(case.get('phase'), str) else ''\n"
        "        if passed:\n"
        "            failure_type = None\n"
        "        elif phase == 'compile':\n"
        "            failure_type = 'compile_error'\n"
        "        elif phase in {'run', 'runtime'}:\n"
        "            failure_type = 'runtime_error'\n"
        "        else:\n"
        "            failure_type = 'assertion'\n"
        "        message, message_truncated = _clip_message(\n"
        "            case.get('stderr') or case.get('error') or case.get('message')\n"
        "        )\n"
        "        stdout_tail, stdout_truncated = _clip_tail(\n"
        "            case.get('actual_stdout') or case.get('stdout')\n"
        "        )\n"
        "        stderr_tail, stderr_truncated = _clip_tail(\n"
        "            case.get('stderr') or case.get('error')\n"
        "        )\n"
        "        suite_name = _normalize_suite_path(suite)\n"
        "        out.append({\n"
        "            'suite': suite_name,\n"
        "            'test_id': test_id,\n"
        "            'status': status,\n"
        "            'duration_ms': _duration_ms_from_case(case),\n"
        "            'failure_type': failure_type,\n"
        "            'message': message,\n"
        "            'stdout_tail': stdout_tail,\n"
        "            'stderr_tail': stderr_tail,\n"
        "            'truncated': bool(\n"
        "                message_truncated or stdout_truncated or stderr_truncated\n"
        "            ),\n"
        "        })\n"
        "    return out\n"
        "def _extract_generic_tests(items, suite):\n"
        "    out = []\n"
        "    for idx, item in enumerate(items):\n"
        "        if not isinstance(item, dict):\n"
        "            continue\n"
        "        test_id = (\n"
        "            _as_str(item.get('test_id'))\n"
        "            or _as_str(item.get('name'))\n"
        "            or _as_str(item.get('id'))\n"
        "            or f'item_{idx + 1}'\n"
        "        )\n"
        "        status_value = item.get('status')\n"
        "        if isinstance(status_value, str):\n"
        "            status = status_value.strip().lower()\n"
        "            if status not in {'passed', 'failed', 'error', 'timeout', 'skipped'}:\n"
        "                status = 'failed'\n"
        "        else:\n"
        "            passed_value = item.get('passed')\n"
        "            if isinstance(passed_value, bool):\n"
        "                status = 'passed' if passed_value else 'failed'\n"
        "            elif isinstance(passed_value, int):\n"
        "                status = 'passed' if passed_value > 0 else 'failed'\n"
        "            else:\n"
        "                status = 'failed'\n"
        "        failure_type = item.get('failure_type') if isinstance(item.get('failure_type'), str) else None\n"
        "        if failure_type is None and status in {'error', 'timeout'}:\n"
        "            failure_type = status\n"
        "        if failure_type is None and status == 'failed':\n"
        "            failure_type = 'assertion'\n"
        "        message, message_truncated = _clip_message(\n"
        "            item.get('message') or item.get('error') or item.get('stderr')\n"
        "        )\n"
        "        stdout_tail, stdout_truncated = _clip_tail(\n"
        "            item.get('stdout_tail') or item.get('stdout') or item.get('actual_stdout')\n"
        "        )\n"
        "        stderr_tail, stderr_truncated = _clip_tail(\n"
        "            item.get('stderr_tail') or item.get('stderr') or item.get('error')\n"
        "        )\n"
        "        duration_ms = item.get('duration_ms')\n"
        "        if isinstance(duration_ms, float):\n"
        "            duration_ms = int(duration_ms)\n"
        "        if not isinstance(duration_ms, int):\n"
        "            duration_ms = None\n"
        "        suite_name = _normalize_suite_path(suite)\n"
        "        out.append({\n"
        "            'suite': suite_name,\n"
        "            'test_id': test_id,\n"
        "            'status': status,\n"
        "            'duration_ms': duration_ms,\n"
        "            'failure_type': failure_type,\n"
        "            'message': message,\n"
        "            'stdout_tail': stdout_tail,\n"
        "            'stderr_tail': stderr_tail,\n"
        "            'truncated': bool(\n"
        "                message_truncated or stdout_truncated or stderr_truncated\n"
        "            ),\n"
        "        })\n"
        "    return out\n"
        "def _extract_tests(node, suite):\n"
        "    tests = []\n"
        "    if isinstance(node, dict):\n"
        "        cases = node.get('cases')\n"
        "        if isinstance(cases, list):\n"
        "            tests.extend(_extract_case_tests(cases, suite))\n"
        "        test_items = node.get('tests')\n"
        "        if isinstance(test_items, list):\n"
        "            tests.extend(_extract_generic_tests(test_items, suite))\n"
        "        for key, value in node.items():\n"
        "            if key in {\n"
        "                'passed', 'failed', 'total', 'ok', 'error',\n"
        "                'traceback', 'duration_ms', 'cases', 'tests'\n"
        "            }:\n"
        "                continue\n"
        "            if not isinstance(key, str) or not key:\n"
        "                continue\n"
        "            child_suite = _normalize_suite_path(\n"
        "                f'{suite}/{key}' if suite else key\n"
        "            )\n"
        "            tests.extend(_extract_tests(value, child_suite))\n"
        "        return tests\n"
        "    if isinstance(node, list):\n"
        "        return _extract_generic_tests(node, suite)\n"
        "    return []\n"
        "def _dedupe_tests(tests):\n"
        "    out = []\n"
        "    seen = set()\n"
        "    for test in tests:\n"
        "        if not isinstance(test, dict):\n"
        "            continue\n"
        "        key = (\n"
        "            test.get('suite'),\n"
        "            test.get('test_id'),\n"
        "            test.get('status'),\n"
        "            test.get('failure_type'),\n"
        "            test.get('message'),\n"
        "        )\n"
        "        if key in seen:\n"
        "            continue\n"
        "        seen.add(key)\n"
        "        out.append(test)\n"
        "    return out\n"
        "def _suite_rollup(tests, default_suite, passed, failed, total):\n"
        "    rollup = {}\n"
        "    for test in tests:\n"
        "        suite = _normalize_suite_path(\n"
        "            _as_str(test.get('suite')) or default_suite\n"
        "        )\n"
        "        if suite not in rollup:\n"
        "            rollup[suite] = {\n"
        "                'ok': True,\n"
        "                'passed': 0,\n"
        "                'failed': 0,\n"
        "                'total': 0,\n"
        "                'error': None,\n"
        "            }\n"
        "        row = rollup[suite]\n"
        "        row['total'] += 1\n"
        "        if test.get('status') == 'passed':\n"
        "            row['passed'] += 1\n"
        "        else:\n"
        "            row['failed'] += 1\n"
        "            row['ok'] = False\n"
        "    if rollup:\n"
        "        return rollup\n"
        "    normalized_default_suite = _normalize_suite_path(default_suite)\n"
        "    return {\n"
        "        normalized_default_suite: {\n"
            "            'ok': failed == 0 and total > 0,\n"
            "            'passed': int(passed),\n"
        "            'failed': int(failed),\n"
        "            'total': int(total),\n"
        "            'error': None,\n"
        "        }\n"
        "    }\n"
        "async def _main() -> None:\n"
        "    started_at = time.monotonic()\n"
        "    payload = {\n"
        "        'duration_ms': 0,\n"
        "        'passed': 0,\n"
        "        'failed': 0,\n"
        "        'total': 0,\n"
        "        'suite_results': {},\n"
        "        'tests': [],\n"
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
        "            extracted_tests = _dedupe_tests(_extract_tests(result, suite_key))\n"
        "            payload['tests'] = extracted_tests\n"
        "            payload['suite_results'] = _suite_rollup(\n"
        "                extracted_tests,\n"
        "                suite_key,\n"
        "                int(passed),\n"
        "                int(failed),\n"
        "                int(total),\n"
        "            )\n"
        "    except Exception as error:  # noqa: BLE001\n"
        "        msg = str(error).strip()\n"
        "        payload['error'] = msg if msg else type(error).__name__\n"
        "        payload['traceback'] = traceback.format_exc()\n"
        "    finally:\n"
        "        payload['duration_ms'] = int((time.monotonic() - started_at) * 1000)\n"
        "    print(marker + json.dumps(payload, ensure_ascii=False, default=str))\n"
        "asyncio.run(_main())\n"
    )


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
    python_script = _build_evaluation_python_script(
        repo_dir_json=repo_dir_json,
        envoi_url_json=envoi_url_json,
        eval_test_path_json=eval_test_path_json,
        marker_json=marker_json,
    )
    return (
        "set -euo pipefail\n"
        f"repo_dir={quoted_repo_dir}\n"
        "rm -rf \"$repo_dir\"\n"
        "git clone -q /workspace \"$repo_dir\"\n"
        "cd \"$repo_dir\"\n"
        f"git checkout -q {quoted_commit}\n"
        "python3 - <<'PY'\n"
        f"{python_script}"
        "PY\n"
        "status=$?\n"
        "cd /workspace\n"
        "rm -rf \"$repo_dir\"\n"
        "exit $status\n"
    )


def build_workspace_evaluation_command(
    *,
    repo_dir: str = "/workspace",
) -> str:
    repo_dir_json = json.dumps(repo_dir)
    envoi_url_json = json.dumps(EVALUATION_ENVOI_URL)
    eval_test_path_json = json.dumps(EVALUATION_TEST_PATH)
    marker_json = json.dumps(EVALUATION_JSON_MARKER)
    quoted_repo_dir = shlex.quote(repo_dir)
    python_script = _build_evaluation_python_script(
        repo_dir_json=repo_dir_json,
        envoi_url_json=envoi_url_json,
        eval_test_path_json=eval_test_path_json,
        marker_json=marker_json,
    )
    return (
        "set -euo pipefail\n"
        f"repo_dir={quoted_repo_dir}\n"
        "python3 - <<'PY'\n"
        f"{python_script}"
        "PY\n"
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


async def run_workspace_evaluation(
    *,
    sandbox: Sandbox,
    repo_dir: str = "/workspace",
) -> dict[str, Any]:
    command = build_workspace_evaluation_command(
        repo_dir=repo_dir,
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
