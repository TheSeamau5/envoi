from __future__ import annotations

import argparse
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import httpx


def fetch_schema(envoi_url: str) -> dict[str, Any]:
    response = httpx.get(f"{envoi_url}/schema", timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Schema response must be a JSON object.")
    return payload


def build_prompt(schema: dict[str, Any], envoi_url: str) -> str:
    return f"""Write a C compiler in Rust.
Requirements:
- CLI must be: ./cc input.c -o output
- Put source files in src/
- Include Cargo.toml and build.sh

Full envoi schema JSON:
{json.dumps(schema, indent=2)}

Testing instructions:
- Create archive: tar czf submission.tar.gz build.sh Cargo.toml src/
- Submit one test: curl -s -X POST {envoi_url}/test/{{test_name}} -F 'file=@submission.tar.gz'
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


def verify(schema: dict[str, Any], workdir: Path, envoi_url: str) -> dict[str, Any]:
    archive = workdir / "submission.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for rel in ("build.sh", "Cargo.toml", "src"):
            path = workdir / rel
            if not path.exists():
                raise RuntimeError(f"Missing required submission path: {rel}")
            tar.add(path, arcname=rel)

    tests = [t["name"] for t in schema.get("tests", []) if isinstance(t, dict) and isinstance(t.get("name"), str)]
    if not tests:
        raise RuntimeError("Schema did not include any tests.")

    results: dict[str, Any] = {}
    with httpx.Client(timeout=300) as client:
        for test_name in tests:
            with archive.open("rb") as file_obj:
                response = client.post(
                    f"{envoi_url}/test/{test_name}",
                    files={"file": ("submission.tar.gz", file_obj)},
                )
            try:
                payload: Any = response.json()
            except ValueError:
                payload = {"status_code": response.status_code, "body": response.text}
            results[test_name] = payload
            cases = payload.get("cases", []) if isinstance(payload, dict) else []
            all_passed = bool(cases) and all(isinstance(c, dict) and c.get("passed") is True for c in cases)
            print(f"{test_name}: all_passed={all_passed}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--envoi-url",
        default=os.environ.get("ENVOI_URL", "http://localhost:8000"),
        help="Base URL for envoi runtime (default: ENVOI_URL or http://localhost:8000)",
    )
    args = parser.parse_args()
    envoi_url = args.envoi_url.rstrip("/")
    schema = fetch_schema(envoi_url)
    with tempfile.TemporaryDirectory(prefix="envoi-codex-") as tmp:
        workdir = Path(tmp)
        run_codex(build_prompt(schema, envoi_url), workdir)
        print(json.dumps(verify(schema, workdir, envoi_url), indent=2))


if __name__ == "__main__":
    main()
