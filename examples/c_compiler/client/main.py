from __future__ import annotations

import argparse
import asyncio
import os
import re
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
Target Linux ARM64 (AArch64). Do NOT generate x86/x86_64 assembly.
Produce Cargo.toml, build.sh, and src/.
Use ENVOI_URL={envoi_url}.
Do not be lazy. Generate code and run tests in a loop until every suite passes.
When tests fail, fix code and rerun affected tests, then rerun previously passing tests to check regressions.
Heavy routes MUST be split:
- wacct: run @wacct/chapter_N, and chunk each chapter with n_tests+offset.
- c_testsuite: run @c_testsuite/part_N.
- torture: run @torture/part_N.
Attempt number: {attempt}
Failure feedback from previous attempt:
{feedback}
"""


def codex_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)

    path_entries = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join(
        entry
        for entry in path_entries
        if "/examples/c_compiler/client/.venv/bin" not in entry
        and "/examples/c_compiler/author/.venv/bin" not in entry
    )
    return env


def resolve_test_path(test_names: list[str], leaf_name: str) -> str:
    if leaf_name in test_names:
        return leaf_name

    matches = [name for name in test_names if name.endswith(f"/{leaf_name}")]
    if not matches:
        raise ValueError(
            f"Missing required test '{leaf_name}'. Available tests: {test_names}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous test leaf '{leaf_name}'. Matches: {matches}"
        )
    return matches[0]


def resolve_suite_root(test_names: list[str], suite_name: str) -> str:
    if any(name == suite_name or name.startswith(f"{suite_name}/") for name in test_names):
        return suite_name
    return resolve_test_path(test_names, suite_name)


def resolve_suite_test_paths(test_names: list[str], suite_name: str) -> list[str]:
    prefix = f"{suite_name}/"
    return sorted(name for name in test_names if name.startswith(prefix))


def resolve_wacct_chapter_paths(test_names: list[str]) -> dict[int, str]:
    chapter_paths: dict[int, str] = {}
    for name in test_names:
        match = re.search(r"(?:^|/)wacct/chapter_(\d+)$", name)
        if not match:
            continue
        chapter = int(match.group(1))
        if chapter in WACCT_CHAPTERS:
            chapter_paths[chapter] = name

    if not chapter_paths:
        return {}

    missing = [chapter for chapter in WACCT_CHAPTERS if chapter not in chapter_paths]
    if missing:
        raise ValueError(f"Incomplete WACCT chapter tests. Missing chapters: {missing}")

    return chapter_paths


def has_test_path(test_names: list[str], path: str) -> bool:
    return path in test_names


def resolve_part_paths(test_names: list[str], suite_name: str) -> dict[int, str]:
    part_paths: dict[int, str] = {}
    pattern = rf"(?:^|/){re.escape(suite_name)}/part_(\d+)$"
    for name in test_names:
        match = re.search(pattern, name)
        if not match:
            continue
        part = int(match.group(1))
        if part >= 1:
            part_paths[part] = name

    if not part_paths:
        return {}

    missing = [part for part in range(1, max(part_paths) + 1) if part not in part_paths]
    if missing:
        raise ValueError(f"Incomplete {suite_name} part tests. Missing parts: {missing}")

    return part_paths


def resolve_optional_test_path(test_names: list[str], leaf_name: str) -> str | None:
    try:
        return resolve_test_path(test_names, leaf_name)
    except ValueError:
        return None


async def run_chunked_suite(
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


async def run_template_part_suite(
    session: envoi.Session,
    suite_name: str,
    *,
    batch_size: int,
) -> tuple[bool, list[str]]:
    part = 1
    ok = True
    failures: list[str] = []
    seen_any = False

    while True:
        part_path = f"{suite_name}/part_{part}"
        try:
            part_ok, part_failures = await run_chunked_suite(
                session,
                part_path,
                batch_size=batch_size,
                label_prefix=part_path,
            )
        except RuntimeError as error:
            message = str(error)
            if "part must be between 1 and" in message:
                if not seen_any:
                    ok = False
                    failures.append(f"{part_path}: {message}")
                break
            raise

        seen_any = True
        ok = ok and part_ok
        failures.extend(part_failures)
        part += 1

        if part > 1000:
            ok = False
            failures.append(f"{suite_name}: exceeded part probe limit (1000)")
            break

    return ok, failures


async def verify(envoi_url: str, workdir: Path) -> tuple[bool, list[str]]:
    async with await envoi.connect(envoi_url) as client:
        basics_path = resolve_suite_root(client.tests, "basics")
        basics_test_paths = resolve_suite_test_paths(client.tests, "basics")
        wacct_chapter_paths = resolve_wacct_chapter_paths(client.tests)
        has_wacct_template = has_test_path(client.tests, "wacct/chapter_{chapter}")
        has_c_testsuite_part_template = has_test_path(client.tests, "c_testsuite/part_{part}")
        has_torture_part_template = has_test_path(client.tests, "torture/part_{part}")
        c_testsuite_part_paths = resolve_part_paths(client.tests, "c_testsuite")
        torture_part_paths = resolve_part_paths(client.tests, "torture")
        wacct_path = resolve_optional_test_path(client.tests, "wacct")
        c_testsuite_path = resolve_optional_test_path(client.tests, "c_testsuite")
        torture_path = (
            resolve_optional_test_path(client.tests, "torture")
            or resolve_optional_test_path(client.tests, "torture_execute")
        )

    docs = envoi.Documents(workdir)
    try:
        async with await envoi.connect_session(envoi_url, submission=docs) as session:
            all_passed = True
            feedback: list[str] = []
            if basics_test_paths:
                for basics_test_path in basics_test_paths:
                    result = await session.test(basics_test_path)
                    passed = int(result.get("passed", 0)) if isinstance(result, dict) else 0
                    failed = int(result.get("failed", 1)) if isinstance(result, dict) else 1
                    total = int(result.get("total", 0)) if isinstance(result, dict) else 0
                    print(f"{basics_test_path}: {passed}/{total} passed")
                    all_passed = all_passed and failed == 0 and total > 0
                    if failed > 0 or total == 0:
                        feedback.append(f"{basics_test_path}: failed={failed} total={total}")
            else:
                result = await session.test(basics_path)
                passed = int(result.get("passed", 0)) if isinstance(result, dict) else 0
                failed = int(result.get("failed", 1)) if isinstance(result, dict) else 1
                total = int(result.get("total", 0)) if isinstance(result, dict) else 0
                print(f"{basics_path}: {passed}/{total} passed")
                all_passed = all_passed and failed == 0 and total > 0
                if failed > 0 or total == 0:
                    feedback.append(f"{basics_path}: failed={failed} total={total}")

            if wacct_chapter_paths:
                for chapter in WACCT_CHAPTERS:
                    chapter_path = wacct_chapter_paths[chapter]
                    ok, failures = await run_chunked_suite(
                        session,
                        chapter_path,
                        batch_size=WACCT_BATCH_SIZE,
                        label_prefix=f"wacct/chapter_{chapter}",
                    )
                    all_passed = all_passed and ok
                    feedback.extend(failures)
            elif has_wacct_template:
                for chapter in WACCT_CHAPTERS:
                    chapter_path = f"wacct/chapter_{chapter}"
                    ok, failures = await run_chunked_suite(
                        session,
                        chapter_path,
                        batch_size=WACCT_BATCH_SIZE,
                        label_prefix=chapter_path,
                    )
                    all_passed = all_passed and ok
                    feedback.extend(failures)
            else:
                if not wacct_path:
                    raise ValueError(
                        "Missing WACCT tests. Expected @wacct, @wacct/chapter_N, "
                        "or @wacct/chapter_{chapter} route."
                    )
                for chapter in WACCT_CHAPTERS:
                    ok, failures = await run_chunked_suite(
                        session,
                        wacct_path,
                        batch_size=WACCT_BATCH_SIZE,
                        base_params={"chapter": chapter},
                        label_prefix=f"wacct chapter={chapter}",
                    )
                    all_passed = all_passed and ok
                    feedback.extend(failures)

            if c_testsuite_part_paths:
                for part in sorted(c_testsuite_part_paths):
                    part_path = c_testsuite_part_paths[part]
                    ok, failures = await run_chunked_suite(
                        session,
                        part_path,
                        batch_size=C_TESTSUITE_BATCH_SIZE,
                        label_prefix=f"c_testsuite/part_{part}",
                    )
                    all_passed = all_passed and ok
                    feedback.extend(failures)
            elif has_c_testsuite_part_template:
                ok, failures = await run_template_part_suite(
                    session,
                    "c_testsuite",
                    batch_size=C_TESTSUITE_BATCH_SIZE,
                )
                all_passed = all_passed and ok
                feedback.extend(failures)
            else:
                if not c_testsuite_path:
                    raise ValueError(
                        "Missing c-testsuite routes. Expected @c_testsuite, "
                        "@c_testsuite/part_N, or @c_testsuite/part_{part}."
                    )
                ok, failures = await run_chunked_suite(
                    session,
                    c_testsuite_path,
                    batch_size=C_TESTSUITE_BATCH_SIZE,
                    label_prefix="c_testsuite",
                )
                all_passed = all_passed and ok
                feedback.extend(failures)

            if torture_part_paths:
                for part in sorted(torture_part_paths):
                    part_path = torture_part_paths[part]
                    ok, failures = await run_chunked_suite(
                        session,
                        part_path,
                        batch_size=TORTURE_BATCH_SIZE,
                        label_prefix=f"torture/part_{part}",
                    )
                    all_passed = all_passed and ok
                    feedback.extend(failures)
            elif has_torture_part_template:
                ok, failures = await run_template_part_suite(
                    session,
                    "torture",
                    batch_size=TORTURE_BATCH_SIZE,
                )
                all_passed = all_passed and ok
                feedback.extend(failures)
            else:
                if not torture_path:
                    raise ValueError(
                        "Missing torture routes. Expected @torture, "
                        "@torture/part_N, or @torture/part_{part}."
                    )
                ok, failures = await run_chunked_suite(
                    session,
                    torture_path,
                    batch_size=TORTURE_BATCH_SIZE,
                    label_prefix=torture_path,
                )
                all_passed = all_passed and ok
                feedback.extend(failures)
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
            env=codex_env(),
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
