from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
from pathlib import Path

import envoi


def build_prompt(envoi_url: str, attempt: int, feedback: str) -> str:
    return f"""Build a REAL C compiler in Rust from scratch.
This is EXTREMELY IMPORTANT: no cheating, no wrappers, no shortcuts.
Do NOT call or wrap cc/gcc/clang/tcc.
Do NOT use saltwater or ANY existing C compiler implementation.
Write all core compiler components yourself in Rust (lexer, parser, codegen, etc).
Target Linux ARM64 (AArch64). Do NOT generate x86/x86_64 assembly.
Produce Cargo.toml, build.sh, and src/.
Use ENVOI_URL={envoi_url}.
Do not be lazy. Generate code and run tests in a loop until every suite passes.
When tests fail, fix code and rerun affected tests, then rerun previously passing tests to check regressions.
Heavy routes MUST be split:
- wacct: run @wacct/chapter_N.
- c_testsuite: run @c_testsuite/part_N.
- torture: run @torture/part_N.
Attempt number: {attempt}
Failure feedback from previous attempt:
{feedback}
"""


async def verify(envoi_url: str, workdir: Path) -> tuple[bool, list[str]]:
    async with await envoi.connect(envoi_url) as client:
        tests = sorted(client.tests)

    if not tests:
        return False, ["no tests found in schema"]

    docs = envoi.Documents(workdir)
    try:
        async with await envoi.connect_session(envoi_url, submission=docs) as session:
            feedback: list[str] = []
            all_passed = True
            for test_path in tests:
                try:
                    result = await session.test(test_path)
                except Exception as error:
                    print(f"{test_path}: error")
                    feedback.append(f"{test_path}: {error}")
                    all_passed = False
                    continue

                passed = int(result.get("passed", 0)) if isinstance(result, dict) else 0
                failed = int(result.get("failed", 1)) if isinstance(result, dict) else 1
                total = int(result.get("total", 0)) if isinstance(result, dict) else 0
                print(f"{test_path}: {passed}/{total} passed")
                if failed > 0 or total == 0:
                    feedback.append(f"{test_path}: failed={failed} total={total}")
                    all_passed = False
            return all_passed, feedback
    except Exception as error:
        print(f"Session/setup failed before test execution: {error}")
        return False, [f"session/setup error: {error}"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--envoi-url", default="http://localhost:8000")
    parser.add_argument("--max-attempts", type=int, default=6)
    parser.add_argument("--workdir", type=Path, default=Path("/private/tmp/envoi-codex-c-compiler"))
    args = parser.parse_args()

    workdir = args.workdir
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"Codex workspace: {workdir}")
    print(f"Repo name: {workdir.name}")
    print(f"Open with: zed {workdir}")
    print(f"Open with: code {workdir}")

    feedback_text = "none"
    base_url = args.envoi_url.rstrip("/")
    attempts = max(1, args.max_attempts)

    for attempt in range(1, attempts + 1):
        print(f"=== Codex attempt {attempt}/{attempts} ===")
        subprocess.run(
            [
                "codex",
                "exec",
                "--skip-git-repo-check",
                build_prompt(base_url, attempt, feedback_text),
                "--sandbox",
                "danger-full-access",
                "--cd",
                str(workdir),
            ],
            check=True,
        )

        passed, failures = asyncio.run(verify(base_url, workdir))
        if passed:
            print("Final result: PASSED")
            return

        preview = failures[:80]
        feedback_text = "\n".join(preview) if preview else "tests failed with no details"
        print("Attempt failed. Feeding failures back to Codex for another fix/test iteration.")

    print("Final result: FAILED")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
