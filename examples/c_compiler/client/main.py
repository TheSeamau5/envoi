from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
from pathlib import Path

import envoi

WORKDIR = Path("/private/tmp/envoi-codex-c-compiler")
WACCT_CHAPTERS = range(1, 21)
WACCT_BATCH_SIZE = 48
C_TESTSUITE_BATCH_SIZE = 48
TORTURE_BATCH_SIZE = 40


def build_prompt(envoi_url: str, attempt: int, feedback: str) -> str:
    return f"""Build a REAL C compiler in Rust from scratch.
This is EXTREMELY IMPORTANT: no cheating, no wrappers, no shortcuts.
Do NOT call or wrap cc/gcc/clang/tcc.
Do NOT use saltwater or ANY existing C compiler implementation.
Write all core compiler components yourself in Rust (lexer, parser, codegen, etc).
Produce Cargo.toml, build.sh, and src/.
Use ENVOI_URL={envoi_url}.
Do not be lazy. Generate code and run tests in a loop until every suite passes.
When tests fail, fix code and rerun affected tests, then rerun previously passing tests to check regressions.
Heavy routes MUST be split:
- wacct: chapter-by-chapter, and chunk each chapter with n_tests+offset.
- c_testsuite: run in n_tests+offset chunks.
- torture_execute: run in n_tests+offset chunks.
Attempt number: {attempt}
Failure feedback from previous attempt:
{feedback}
"""


async def _run_chunked_suite(
    session: envoi.Session,
    name: str,
    *,
    batch_size: int,
    base_params: dict[str, object] | None = None,
    label_prefix: str | None = None,
) -> tuple[bool, list[str]]:
    offset = 0
    seen_any = False
    ok = True
    failures: list[str] = []

    while True:
        params: dict[str, object] = {"n_tests": batch_size, "offset": offset}
        if base_params:
            params.update(base_params)

        result = await session.test(name, **params)
        passed = int(result.get("passed", 0)) if isinstance(result, dict) else 0
        failed = int(result.get("failed", 1)) if isinstance(result, dict) else 1
        total = int(result.get("total", 0)) if isinstance(result, dict) else 0

        label = f"{label_prefix} offset={offset}" if label_prefix else f"{name} offset={offset}"
        if total == 0:
            if not seen_any:
                print(f"{label}: 0/0 passed")
                ok = False
                failures.append(f"{label}: returned 0 tests")
            break

        seen_any = True
        print(f"{label}: {passed}/{total} passed")
        if failed > 0:
            ok = False
            failures.append(f"{label}: failed={failed} total={total}")
        offset += total
        if total < batch_size:
            break

    return ok, failures


async def verify(envoi_url: str, workdir: Path) -> tuple[bool, list[str]]:
    async with await envoi.connect(envoi_url) as client:
        test_names = client.tests

    docs = envoi.Documents(workdir)
    try:
        async with await envoi.connect_session(envoi_url, submission=docs) as session:
            all_passed = True
            feedback: list[str] = []
            for name in test_names:
                if name == "wacct":
                    for chapter in WACCT_CHAPTERS:
                        ok, failures = await _run_chunked_suite(
                            session,
                            "wacct",
                            batch_size=WACCT_BATCH_SIZE,
                            base_params={"chapter": chapter},
                            label_prefix=f"wacct chapter={chapter}",
                        )
                        all_passed = all_passed and ok
                        feedback.extend(failures)
                    continue

                if name == "c_testsuite":
                    ok, failures = await _run_chunked_suite(
                        session,
                        "c_testsuite",
                        batch_size=C_TESTSUITE_BATCH_SIZE,
                        label_prefix="c_testsuite",
                    )
                    all_passed = all_passed and ok
                    feedback.extend(failures)
                    continue

                if name == "torture_execute":
                    ok, failures = await _run_chunked_suite(
                        session,
                        "torture_execute",
                        batch_size=TORTURE_BATCH_SIZE,
                        label_prefix="torture_execute",
                    )
                    all_passed = all_passed and ok
                    feedback.extend(failures)
                    continue

                result = await session.test(name)
                passed = int(result.get("passed", 0)) if isinstance(result, dict) else 0
                failed = int(result.get("failed", 1)) if isinstance(result, dict) else 1
                total = int(result.get("total", 0)) if isinstance(result, dict) else 0
                print(f"{name}: {passed}/{total} passed")
                all_passed = all_passed and failed == 0 and total > 0
                if failed > 0 or total == 0:
                    feedback.append(f"{name}: failed={failed} total={total}")
            return all_passed, feedback
    except Exception as error:
        print(f"Session/setup failed before test execution: {error}")
        return False, [f"session/setup error: {error}"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--envoi-url", default="http://localhost:8000")
    parser.add_argument("--max-attempts", type=int, default=6)
    args = parser.parse_args()

    workdir = WORKDIR
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
