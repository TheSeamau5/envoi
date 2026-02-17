from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

import envoi


async def fetch_schema(envoi_url: str) -> dict[str, object]:
    async with await envoi.connect(envoi_url) as client:
        return client.schema


def test_names(schema: dict[str, object]) -> list[str]:
    tests = schema.get("tests")
    if not isinstance(tests, list):
        return []
    return [
        item["name"]
        for item in tests
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]


def build_prompt(schema: dict[str, object], envoi_url: str) -> str:
    return f"""Write a C compiler in Rust.
Task:
- CLI must be: ./cc input.c -o output
- Put source files in src/
- Include Cargo.toml and build.sh

Full envoi schema JSON:
{json.dumps(schema, indent=2)}

How to test:
- Create archive: tar czf submission.tar.gz build.sh Cargo.toml src/
- This environment has setup, so create a session with the submission tar:
  curl -s -X POST {envoi_url}/session -F 'file=@submission.tar.gz'
- For each test, call:
  curl -s -X POST {envoi_url}/session/{{session_id}}/test/{{test_name}}
- When done, close the session:
  curl -s -X DELETE {envoi_url}/session/{{session_id}}

Rules:
- Do not run all tests at once; they are expensive and slow.
- Test incrementally.
- When you fix something, re-run previously passing tests to check regressions.
- Do not stop until every test returns all passed: true.
"""


def run_codex(prompt: str, workdir: Path) -> None:
    subprocess.run(
        ["codex", "exec", prompt, "--sandbox", "danger-full-access", "--cd", str(workdir)],
        check=True,
    )


async def verify_submission(envoi_url: str, workdir: Path, tests: list[str]) -> bool:
    all_passed = True
    async with await envoi.connect_session(envoi_url, submission=envoi.Documents(workdir)) as session:
        for name in tests:
            result = await session.test(name)
            cases = result.get("cases", []) if isinstance(result, dict) else []
            passed = sum(1 for case in cases if isinstance(case, dict) and case.get("passed") is True)
            total = len(cases)
            if total == 0 or passed != total:
                all_passed = False
            print(f"{name}: {passed}/{total} passed")
    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--envoi-url",
        default=os.environ.get("ENVOI_URL", "http://localhost:8000"),
        help="Base URL for envoi runtime (default: ENVOI_URL or http://localhost:8000)",
    )
    args = parser.parse_args()
    envoi_url = args.envoi_url.rstrip("/")
    schema = asyncio.run(fetch_schema(envoi_url))
    tests = test_names(schema)
    if not tests:
        raise RuntimeError("No tests found in schema.")

    with tempfile.TemporaryDirectory(prefix="envoi-codex-") as tmp:
        workdir = Path(tmp)
        run_codex(build_prompt(schema, envoi_url), workdir)
        ok = asyncio.run(verify_submission(envoi_url, workdir, tests))
        if not ok:
            raise SystemExit(1)
        print("All suites passed.")


if __name__ == "__main__":
    main()
