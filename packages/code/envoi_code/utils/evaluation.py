"""Concurrent commit evaluation against envoi.

After a part changes files and creates a git checkpoint, this module evaluates
that commit by running environment tests via the envoi server.
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
EVALUATION_DEFAULT_TIMEOUT_SECONDS = max(
    60, int(os.environ.get("EVALUATION_TIMEOUT_SECONDS", "7200"))
)
EVALUATION_ENVOI_URL = (
    os.environ.get("EVALUATION_ENVOI_URL", "http://localhost:8000").strip()
    or "http://localhost:8000"
)
EVALUATION_JSON_MARKER = "__ENVOI_EVAL_JSON__"


def normalize_test_paths(
    test_paths: list[str] | None,
) -> list[str]:
    if not test_paths:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in test_paths:
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def resolve_evaluation_timeout(
    timeout_seconds: int | None,
) -> int:
    if isinstance(timeout_seconds, int) and timeout_seconds > 0:
        return timeout_seconds
    return EVALUATION_DEFAULT_TIMEOUT_SECONDS


def extract_leaf_paths(schema: Any) -> list[str]:
    """Read leaf test paths from the canonical envoi /schema v1 format."""
    if not isinstance(schema, dict):
        return []
    tests = schema.get("tests")
    if not isinstance(tests, list):
        return []
    return sorted(t for t in tests if isinstance(t, str) and t)


def build_evaluation_python_script(
    *,
    repo_dir_json: str,
    envoi_url_json: str,
    eval_test_paths_json: str,
    eval_timeout_seconds_json: str,
    marker_json: str,
) -> str:
    return (
        "import asyncio\n"
        "import importlib.util\n"
        "import inspect\n"
        "import json\n"
        "import sys\n"
        "import time\n"
        "import traceback\n"
        "from pathlib import Path\n"
        "import envoi\n"
        f"repo_dir = {repo_dir_json}\n"
        f"envoi_url = {envoi_url_json}\n"
        f"eval_test_paths = {eval_test_paths_json}\n"
        f"eval_timeout_seconds = int({eval_timeout_seconds_json})\n"
        f"marker = {marker_json}\n"
        "MAX_MESSAGE_CHARS = 320\n"
        "MAX_TAIL_CHARS = 1200\n"
        "def as_str(value):\n"
        "    if isinstance(value, str):\n"
        "        return value\n"
        "    if value is None:\n"
        "        return None\n"
        "    return str(value)\n"
        "def normalize_suite_path(value):\n"
        "    text = as_str(value)\n"
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
        "def load_environment_test_sources():\n"
        "    mapping = {}\n"
        "    environment_main = Path('/environment/main.py')\n"
        "    def read_text(path):\n"
        "        try:\n"
        "            text = path.read_text(encoding='utf-8', errors='replace')\n"
        "        except Exception:\n"
        "            return None\n"
        "        text = text.strip()\n"
        "        if not text:\n"
        "            return None\n"
        "        max_chars = 12000\n"
        "        if len(text) > max_chars:\n"
        "            return text[:max_chars]\n"
        "        return text\n"
        "    if not environment_main.exists():\n"
        "        tests_dir = Path('/environment/tests')\n"
        "        if tests_dir.is_dir():\n"
        "            for test_file in sorted(tests_dir.glob('*.py')):\n"
        "                file_source = read_text(test_file)\n"
        "                if file_source is not None:\n"
        "                    mapping[test_file.stem] = file_source\n"
        "        return mapping\n"
        "    try:\n"
        "        from envoi import environment as environment_state\n"
        "    except Exception:\n"
        "        return mapping\n"
        "    try:\n"
        "        environment_state.clear_environment()\n"
        "        environment_dir = str(environment_main.parent)\n"
        "        if environment_dir not in sys.path:\n"
        "            sys.path.insert(0, environment_dir)\n"
        "        spec = importlib.util.spec_from_file_location(\n"
        "            '_envoi_eval_environment',\n"
        "            str(environment_main),\n"
        "        )\n"
        "        if spec is None or spec.loader is None:\n"
        "            return mapping\n"
        "        module = importlib.util.module_from_spec(spec)\n"
        "        spec.loader.exec_module(module)\n"
        "        registry = getattr(environment_state, '_test_registry', {})\n"
        "        if not isinstance(registry, dict):\n"
        "            return mapping\n"
        "        for test_path, test_fn in registry.items():\n"
        "            if not isinstance(test_path, str) or not callable(test_fn):\n"
        "                continue\n"
        "            try:\n"
        "                source = inspect.getsource(test_fn)\n"
        "            except Exception:\n"
        "                continue\n"
        "            if not isinstance(source, str):\n"
        "                continue\n"
        "            source = source.strip()\n"
        "            if not source:\n"
        "                continue\n"
        "            normalized = normalize_suite_path(test_path)\n"
        "            if normalized:\n"
        "                mapping[normalized] = source\n"
        "        tests_dir = Path('/environment/tests')\n"
        "        if tests_dir.is_dir():\n"
        "            for test_file in sorted(tests_dir.glob('*.py')):\n"
        "                file_source = read_text(test_file)\n"
        "                if file_source is not None:\n"
        "                    mapping.setdefault(test_file.stem, file_source)\n"
        "        main_source = read_text(environment_main)\n"
        "        if main_source is not None:\n"
        "            mapping.setdefault('main', main_source)\n"
        "        return mapping\n"
        "    except Exception:\n"
        "        return mapping\n"
        "    finally:\n"
        "        try:\n"
        "            environment_state.clear_environment()\n"
        "        except Exception:\n"
        "            pass\n"
        "def suite_source_candidates(suite):\n"
        "    normalized = normalize_suite_path(suite)\n"
        "    if not normalized:\n"
        "        return []\n"
        "    candidates = [normalized]\n"
        "    if normalized.startswith('all/'):\n"
        "        candidates.append(normalized[len('all/'):])\n"
        "    parts = normalized.split('/')\n"
        "    for size in range(len(parts) - 1, 0, -1):\n"
        "        candidates.append('/'.join(parts[:size]))\n"
        "    if parts and parts[0] == 'all':\n"
        "        tail = parts[1:]\n"
        "        for size in range(len(tail) - 1, 0, -1):\n"
        "            candidates.append('/'.join(tail[:size]))\n"
        "    seen = set()\n"
        "    deduped = []\n"
        "    for candidate in candidates:\n"
        "        if not candidate or candidate in seen:\n"
        "            continue\n"
        "        seen.add(candidate)\n"
        "        deduped.append(candidate)\n"
        "    return deduped\n"
        "def attach_test_sources(tests, source_map):\n"
        "    if not isinstance(source_map, dict) or not source_map:\n"
        "        return tests\n"
        "    for test in tests:\n"
        "        if not isinstance(test, dict):\n"
        "            continue\n"
        "        current_source = as_str(test.get('source'))\n"
        "        if current_source and current_source.strip():\n"
        "            continue\n"
        "        suite = as_str(test.get('suite')) or ''\n"
        "        resolved_source = None\n"
        "        for candidate in suite_source_candidates(suite):\n"
        "            value = source_map.get(candidate)\n"
        "            if isinstance(value, str) and value.strip():\n"
        "                resolved_source = value.strip()\n"
        "                break\n"
        "        if resolved_source is None:\n"
        "            normalized_suite = normalize_suite_path(suite)\n"
        "            suite_root = normalized_suite.split('/')[0] if normalized_suite else ''\n"
        "            best_key = None\n"
        "            for key, value in source_map.items():\n"
        "                if not isinstance(key, str) or not isinstance(value, str):\n"
        "                    continue\n"
        "                if '/' in key:\n"
        "                    continue\n"
        "                if not suite_root:\n"
        "                    continue\n"
        "                if (\n"
        "                    suite_root == key\n"
        "                    or suite_root.startswith(key + '_')\n"
        "                    or key.startswith(suite_root + '_')\n"
        "                ):\n"
        "                    if best_key is None or len(key) > len(best_key):\n"
        "                        best_key = key\n"
        "            if isinstance(best_key, str):\n"
        "                value = source_map.get(best_key)\n"
        "                if isinstance(value, str) and value.strip():\n"
        "                    resolved_source = value.strip()\n"
        "        if resolved_source is not None:\n"
        "            test['source'] = resolved_source\n"
        "    return tests\n"
        "def clip_message(value):\n"
        "    text = as_str(value)\n"
        "    if text is None:\n"
        "        return None, False\n"
        "    text = text.strip()\n"
        "    if not text:\n"
        "        return None, False\n"
        "    if len(text) <= MAX_MESSAGE_CHARS:\n"
        "        return text, False\n"
        "    return text[:MAX_MESSAGE_CHARS], True\n"
        "def clip_tail(value):\n"
        "    text = as_str(value)\n"
        "    if text is None:\n"
        "        return None, False\n"
        "    text = text.strip()\n"
        "    if not text:\n"
        "        return None, False\n"
        "    if len(text) <= MAX_TAIL_CHARS:\n"
        "        return text, False\n"
        "    return text[-MAX_TAIL_CHARS:], True\n"
        "def collect_totals(node):\n"
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
        "            cp, cf, ct = collect_totals(value)\n"
        "            p += cp\n"
        "            f += cf\n"
        "            t += ct\n"
        "        return p, f, t\n"
        "    if isinstance(node, list):\n"
        "        p = f = t = 0\n"
        "        for value in node:\n"
        "            cp, cf, ct = collect_totals(value)\n"
        "            p += cp\n"
        "            f += cf\n"
        "            t += ct\n"
        "        return p, f, t\n"
        "    return 0, 0, 0\n"
        "def duration_ms_from_case(case):\n"
        "    total = 0.0\n"
        "    for key in ('compile_time_ms', 'run_time_ms'):\n"
        "        value = case.get(key)\n"
        "        if isinstance(value, (int, float)):\n"
        "            total += max(0.0, float(value))\n"
        "    return int(total) if total > 0 else None\n"
        "def extract_case_tests(cases, suite):\n"
        "    out = []\n"
        "    for idx, case in enumerate(cases):\n"
        "        if not isinstance(case, dict):\n"
        "            continue\n"
        "        test_id = as_str(case.get('name')) or f'case_{idx + 1}'\n"
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
        "        message, message_truncated = clip_message(\n"
        "            case.get('stderr') or case.get('error') or case.get('message')\n"
        "        )\n"
        "        stdout_tail, stdout_truncated = clip_tail(\n"
        "            case.get('actual_stdout') or case.get('stdout')\n"
        "        )\n"
        "        stderr_tail, stderr_truncated = clip_tail(\n"
        "            case.get('stderr') or case.get('error')\n"
        "        )\n"
        "        suite_name = normalize_suite_path(suite)\n"
        "        source = as_str(case.get('c_source') or case.get('source'))\n"
        "        out.append({\n"
        "            'suite': suite_name,\n"
        "            'test_id': test_id,\n"
        "            'status': status,\n"
        "            'duration_ms': duration_ms_from_case(case),\n"
        "            'failure_type': failure_type,\n"
        "            'message': message,\n"
        "            'source': source,\n"
        "            'stdout_tail': stdout_tail,\n"
        "            'stderr_tail': stderr_tail,\n"
        "            'truncated': bool(\n"
        "                message_truncated or stdout_truncated or stderr_truncated\n"
        "            ),\n"
        "        })\n"
        "    return out\n"
        "def extract_generic_tests(items, suite):\n"
        "    out = []\n"
        "    for idx, item in enumerate(items):\n"
        "        if not isinstance(item, dict):\n"
        "            continue\n"
        "        test_id = (\n"
        "            as_str(item.get('test_id'))\n"
        "            or as_str(item.get('name'))\n"
        "            or as_str(item.get('id'))\n"
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
        "        failure_type = item.get('failure_type')\n"
        "        if not isinstance(failure_type, str):\n"
        "            failure_type = None\n"
        "        if failure_type is None and status in {'error', 'timeout'}:\n"
        "            failure_type = status\n"
        "        if failure_type is None and status == 'failed':\n"
        "            failure_type = 'assertion'\n"
        "        message, message_truncated = clip_message(\n"
        "            item.get('message') or item.get('error') or item.get('stderr')\n"
        "        )\n"
        "        stdout_tail, stdout_truncated = clip_tail(\n"
        "            item.get('stdout_tail') or item.get('stdout') or item.get('actual_stdout')\n"
        "        )\n"
        "        stderr_tail, stderr_truncated = clip_tail(\n"
        "            item.get('stderr_tail') or item.get('stderr') or item.get('error')\n"
        "        )\n"
        "        duration_ms = item.get('duration_ms')\n"
        "        if isinstance(duration_ms, float):\n"
        "            duration_ms = int(duration_ms)\n"
        "        if not isinstance(duration_ms, int):\n"
        "            duration_ms = None\n"
        "        suite_name = normalize_suite_path(suite)\n"
        "        source = as_str(item.get('source') or item.get('c_source'))\n"
        "        out.append({\n"
        "            'suite': suite_name,\n"
        "            'test_id': test_id,\n"
        "            'status': status,\n"
        "            'duration_ms': duration_ms,\n"
        "            'failure_type': failure_type,\n"
        "            'message': message,\n"
        "            'source': source,\n"
        "            'stdout_tail': stdout_tail,\n"
        "            'stderr_tail': stderr_tail,\n"
        "            'truncated': bool(\n"
        "                message_truncated or stdout_truncated or stderr_truncated\n"
        "            ),\n"
        "        })\n"
        "    return out\n"
        "def extract_tests(node, suite):\n"
        "    tests = []\n"
        "    if isinstance(node, dict):\n"
        "        cases = node.get('cases')\n"
        "        if isinstance(cases, list):\n"
        "            tests.extend(extract_case_tests(cases, suite))\n"
        "        test_items = node.get('tests')\n"
        "        if isinstance(test_items, list):\n"
        "            tests.extend(extract_generic_tests(test_items, suite))\n"
        "        for key, value in node.items():\n"
        "            if key in {\n"
        "                'passed', 'failed', 'total', 'ok', 'error',\n"
        "                'traceback', 'duration_ms', 'cases', 'tests'\n"
        "            }:\n"
        "                continue\n"
        "            if not isinstance(key, str) or not key:\n"
        "                continue\n"
        "            child_suite = normalize_suite_path(\n"
        "                f'{suite}/{key}' if suite else key\n"
        "            )\n"
        "            tests.extend(extract_tests(value, child_suite))\n"
        "        return tests\n"
        "    if isinstance(node, list):\n"
        "        return extract_generic_tests(node, suite)\n"
        "    return []\n"
        "def dedupe_tests(tests):\n"
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
        "            test.get('source'),\n"
        "        )\n"
        "        if key in seen:\n"
        "            continue\n"
        "        seen.add(key)\n"
        "        out.append(test)\n"
        "    return out\n"
        "def suite_rollup(tests, default_suite, passed, failed, total):\n"
        "    rollup = {}\n"
        "    for test in tests:\n"
        "        suite = normalize_suite_path(\n"
        "            as_str(test.get('suite')) or default_suite\n"
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
        "    normalized_default_suite = normalize_suite_path(default_suite)\n"
        "    return {\n"
        "        normalized_default_suite: {\n"
        "            'ok': failed == 0 and total > 0,\n"
        "            'passed': int(passed),\n"
        "            'failed': int(failed),\n"
        "            'total': int(total),\n"
        "            'error': None,\n"
        "        }\n"
        "    }\n"
        "def merge_suite_results(dst, src):\n"
        "    for suite, row in src.items():\n"
        "        if not isinstance(suite, str) or not isinstance(row, dict):\n"
        "            continue\n"
        "        if suite not in dst:\n"
        "            dst[suite] = {\n"
        "                'ok': bool(row.get('ok', True)),\n"
        "                'passed': int(row.get('passed', 0) or 0),\n"
        "                'failed': int(row.get('failed', 0) or 0),\n"
        "                'total': int(row.get('total', 0) or 0),\n"
        "                'error': (\n"
        "                    row.get('error')\n"
        "                    if isinstance(row.get('error'), str)\n"
        "                    else None\n"
        "                ),\n"
        "            }\n"
        "            continue\n"
        "        cur = dst[suite]\n"
        "        cur['passed'] = int(cur.get('passed', 0) or 0) + int(row.get('passed', 0) or 0)\n"
        "        cur['failed'] = int(cur.get('failed', 0) or 0) + int(row.get('failed', 0) or 0)\n"
        "        cur['total'] = int(cur.get('total', 0) or 0) + int(row.get('total', 0) or 0)\n"
        "        cur['ok'] = bool(cur.get('ok', True)) and bool(row.get('ok', True))\n"
        "        if not isinstance(cur.get('error'), str):\n"
        "            err = row.get('error')\n"
        "            cur['error'] = err if isinstance(err, str) else None\n"
        "async def main() -> None:\n"
        "    started_at = time.monotonic()\n"
        "    selected_paths = [\n"
        "        path.strip()\n"
        "        for path in eval_test_paths\n"
        "        if isinstance(path, str) and path.strip()\n"
        "    ]\n"
        "    test_source_map = load_environment_test_sources()\n"
        "    payload = {\n"
        "        'duration_ms': 0,\n"
        "        'passed': 0,\n"
        "        'failed': 0,\n"
        "        'total': 0,\n"
        "        'suite_results': {},\n"
        "        'tests': [],\n"
        "        'selected_test_paths': selected_paths,\n"
        "        'error': None,\n"
        "    }\n"
        "    try:\n"
        "        docs = envoi.Documents(repo_dir)\n"
        "        async with await envoi.connect_session(\n"
        "            envoi_url,\n"
        "            connect_timeout_seconds=eval_timeout_seconds,\n"
        "            submission=docs,\n"
        "            session_timeout_seconds=eval_timeout_seconds,\n"
        "        ) as session:\n"
        "            if selected_paths:\n"
        "                for test_path in selected_paths:\n"
        "                    result = await session.test(test_path)\n"
        "                    passed, failed, total = collect_totals(result)\n"
        "                    payload['passed'] += int(passed)\n"
        "                    payload['failed'] += int(failed)\n"
        "                    payload['total'] += int(total)\n"
        "                    suite_key = normalize_suite_path(test_path)\n"
        "                    extracted_tests = extract_tests(result, suite_key)\n"
        "                    extracted_tests = attach_test_sources(\n"
        "                        extracted_tests,\n"
        "                        test_source_map,\n"
        "                    )\n"
        "                    extracted_tests = dedupe_tests(extracted_tests)\n"
        "                    payload['tests'].extend(extracted_tests)\n"
        "                    merge_suite_results(\n"
        "                        payload['suite_results'],\n"
        "                        suite_rollup(\n"
        "                            extracted_tests,\n"
        "                            suite_key,\n"
        "                            int(passed),\n"
        "                            int(failed),\n"
        "                            int(total),\n"
        "                        ),\n"
        "                    )\n"
        "                payload['tests'] = dedupe_tests(payload['tests'])\n"
        "            else:\n"
        "                result = await session.test()\n"
        "                passed, failed, total = collect_totals(result)\n"
        "                payload['passed'] = int(passed)\n"
        "                payload['failed'] = int(failed)\n"
        "                payload['total'] = int(total)\n"
        "                suite_key = 'all'\n"
        "                extracted_tests = extract_tests(result, suite_key)\n"
        "                extracted_tests = attach_test_sources(\n"
        "                    extracted_tests,\n"
        "                    test_source_map,\n"
        "                )\n"
        "                extracted_tests = dedupe_tests(extracted_tests)\n"
        "                payload['tests'] = extracted_tests\n"
        "                payload['suite_results'] = suite_rollup(\n"
        "                    extracted_tests,\n"
        "                    suite_key,\n"
        "                    int(passed),\n"
        "                    int(failed),\n"
        "                    int(total),\n"
        "                )\n"
        "    except Exception as error:\n"
        "        msg = str(error).strip()\n"
        "        payload['error'] = msg if msg else type(error).__name__\n"
        "        payload['traceback'] = traceback.format_exc()\n"
        "    finally:\n"
        "        payload['duration_ms'] = int((time.monotonic() - started_at) * 1000)\n"
        "    print(marker + json.dumps(payload, ensure_ascii=False, default=str))\n"
        "asyncio.run(main())\n"
    )


def build_commit_evaluation_command(
    *,
    commit: str,
    eval_repo_dir: str,
    test_paths: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> str:
    repo_dir_json = json.dumps(eval_repo_dir)
    envoi_url_json = json.dumps(EVALUATION_ENVOI_URL)
    eval_test_paths_json = json.dumps(
        normalize_test_paths(test_paths),
        ensure_ascii=False,
    )
    eval_timeout_seconds_json = json.dumps(
        resolve_evaluation_timeout(timeout_seconds)
    )
    marker_json = json.dumps(EVALUATION_JSON_MARKER)
    quoted_commit = shlex.quote(commit)
    quoted_repo_dir = shlex.quote(eval_repo_dir)
    python_script = build_evaluation_python_script(
        repo_dir_json=repo_dir_json,
        envoi_url_json=envoi_url_json,
        eval_test_paths_json=eval_test_paths_json,
        eval_timeout_seconds_json=eval_timeout_seconds_json,
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
    test_paths: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> str:
    repo_dir_json = json.dumps(repo_dir)
    envoi_url_json = json.dumps(EVALUATION_ENVOI_URL)
    eval_test_paths_json = json.dumps(
        normalize_test_paths(test_paths),
        ensure_ascii=False,
    )
    eval_timeout_seconds_json = json.dumps(
        resolve_evaluation_timeout(timeout_seconds)
    )
    marker_json = json.dumps(EVALUATION_JSON_MARKER)
    quoted_repo_dir = shlex.quote(repo_dir)
    python_script = build_evaluation_python_script(
        repo_dir_json=repo_dir_json,
        envoi_url_json=envoi_url_json,
        eval_test_paths_json=eval_test_paths_json,
        eval_timeout_seconds_json=eval_timeout_seconds_json,
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
    test_paths: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    eval_repo_dir = (
        f"/tmp/envoi-eval-{commit[:12]}-{uuid.uuid4().hex[:8]}"
    )
    resolved_timeout = resolve_evaluation_timeout(timeout_seconds)
    command = build_commit_evaluation_command(
        commit=commit,
        eval_repo_dir=eval_repo_dir,
        test_paths=test_paths,
        timeout_seconds=resolved_timeout,
    )
    exit_code, stdout, stderr = (
        await sandbox.run(
            command,
            timeout=resolved_timeout,
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
    test_paths: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    resolved_timeout = resolve_evaluation_timeout(timeout_seconds)
    command = build_workspace_evaluation_command(
        repo_dir=repo_dir,
        test_paths=test_paths,
        timeout_seconds=resolved_timeout,
    )
    exit_code, stdout, stderr = (
        await sandbox.run(
            command,
            timeout=resolved_timeout,
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
