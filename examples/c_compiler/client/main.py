from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
from pathlib import Path

import envoi

WORKDIR = Path("/private/tmp/envoi-codex-c-compiler")


def build_prompt(envoi_url: str) -> str:
    return f"""Build a REAL C compiler in Rust from scratch.
This is EXTREMELY IMPORTANT: no cheating, no wrappers, no shortcuts.
Do NOT call or wrap cc/gcc/clang/tcc.
Do NOT use saltwater or ANY existing C compiler implementation.
Write all core compiler components yourself in Rust (lexer, parser, codegen, etc).
Produce Cargo.toml, build.sh, and src/.
Use ENVOI_URL={envoi_url}, run tests incrementally, and do not stop until everything passes.
"""


async def verify(envoi_url: str, workdir: Path) -> bool:
    async with await envoi.connect(envoi_url) as client:
        test_names = client.tests

    docs = envoi.Documents(workdir)
    try:
        async with await envoi.connect_session(envoi_url, submission=docs) as session:
            all_passed = True
            for name in test_names:
                if name == "wacct":
                    for chapter in range(1, 21):
                        result = await session.test("wacct", chapter=chapter)
                        passed = int(result.get("passed", 0)) if isinstance(result, dict) else 0
                        failed = int(result.get("failed", 1)) if isinstance(result, dict) else 1
                        total = int(result.get("total", 0)) if isinstance(result, dict) else 0
                        print(f"wacct chapter={chapter}: {passed}/{total} passed")
                        all_passed = all_passed and failed == 0 and total > 0
                    continue

                result = await session.test(name)
                passed = int(result.get("passed", 0)) if isinstance(result, dict) else 0
                failed = int(result.get("failed", 1)) if isinstance(result, dict) else 1
                total = int(result.get("total", 0)) if isinstance(result, dict) else 0
                print(f"{name}: {passed}/{total} passed")
                all_passed = all_passed and failed == 0 and total > 0
            return all_passed
    except Exception as error:
        print(f"Session/setup failed before test execution: {error}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--envoi-url", default="http://localhost:8000")
    args = parser.parse_args()

    workdir = WORKDIR
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"Codex workspace: {workdir}")
    print(f"Repo name: {workdir.name}")
    print(f"Open with: zed {workdir}")
    print(f"Open with: code {workdir}")

    subprocess.run(
        [
            "codex",
            "exec",
            "--skip-git-repo-check",
            build_prompt(args.envoi_url.rstrip("/")),
            "--sandbox",
            "danger-full-access",
            "--cd",
            str(workdir),
        ],
        check=True,
    )

    if not asyncio.run(verify(args.envoi_url.rstrip("/"), workdir)):
        print("Final result: FAILED")
        raise SystemExit(1)
    print("Final result: PASSED")


if __name__ == "__main__":
    main()
