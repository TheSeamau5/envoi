"""
Writing-a-C-Compiler tests (wacct).

Source: github.com/nlsandler/writing-a-c-compiler-tests
20 chapters of progressively harder C features.

Routes:
- @wacct runs all chapters.
- @wacct/chapter_{chapter} runs one chapter.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import envoi

from .utils import TestResult, fixture_path, run_cases_parallel, select_cases, to_result

wacct = envoi.suite("wacct")


def load_invalid_c23_skip_set() -> set[str]:
    skip_path = Path(__file__).with_name("wacct-invalid-c23-skip.txt")
    if not skip_path.is_file():
        return set()

    return {
        line.strip()
        for line in skip_path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def load_wacct_properties(
    fixture_root: Path,
) -> tuple[set[str], dict[str, list[str]], dict[str, list[str]]]:
    properties_path = fixture_root / "test_properties.json"
    if not properties_path.is_file():
        return set(), {}, {}

    payload = json.loads(properties_path.read_text())
    requires_mathlib = set(payload.get("requires_mathlib", []))
    libs = {
        str(key): [str(item) for item in value]
        for key, value in dict(payload.get("libs", {})).items()
    }
    assembly_libs = {
        str(key): [str(item) for item in value]
        for key, value in dict(payload.get("assembly_libs", {})).items()
    }
    return requires_mathlib, libs, assembly_libs


def load_wacct_regalloc_wrapper_info(fixture_root: Path) -> tuple[set[str], str | None]:
    test_framework_dir = fixture_root / "test_framework"
    if not test_framework_dir.is_dir():
        return set(), None

    inserted = False
    fixture_root_str = str(fixture_root)
    if fixture_root_str not in sys.path:
        sys.path.insert(0, fixture_root_str)
        inserted = True

    try:
        from test_framework import regalloc  # type: ignore
    except Exception:
        return set(), None
    finally:
        if inserted:
            sys.path.remove(fixture_root_str)

    regalloc_program_names = set(regalloc.REGALLOC_TESTS.keys())
    wrapper_path = str(Path(regalloc.WRAPPER_SCRIPT).resolve())
    return regalloc_program_names, wrapper_path


def platform_assembly_suffix() -> str:
    return "_osx.s" if os.uname().sysname.lower() == "darwin" else "_linux.s"


def build_wacct_compile_inputs(
    rel_path: Path,
    source_path: Path,
    tests_dir: Path,
    requires_mathlib: set[str],
    libs_by_program: dict[str, list[str]],
    assembly_libs_by_program: dict[str, list[str]],
    regalloc_program_names: set[str],
    regalloc_wrapper_path: str | None,
) -> tuple[list[str], list[str]]:
    rel_key = rel_path.as_posix()
    input_paths = [str(source_path)]

    if "libraries" in rel_path.parts and not rel_path.stem.endswith("_client"):
        client_path = source_path.with_name(f"{source_path.stem}_client.c")
        if client_path.is_file():
            input_paths.append(str(client_path))

    assembly_suffix = platform_assembly_suffix()
    for asm_dep in assembly_libs_by_program.get(rel_key, []):
        asm_path = tests_dir / f"{asm_dep}{assembly_suffix}"
        if asm_path.is_file():
            input_paths.append(str(asm_path))

    for dep_rel in libs_by_program.get(rel_key, []):
        dep_path = tests_dir / dep_rel
        if dep_path.is_file():
            input_paths.append(str(dep_path))

    if (
        rel_path.parts
        and rel_path.parts[0] == "chapter_20"
        and rel_path.name in regalloc_program_names
        and regalloc_wrapper_path is not None
    ):
        wrapper_path = Path(regalloc_wrapper_path)
        if wrapper_path.is_file():
            input_paths.append(str(wrapper_path))

    link_args: list[str] = []
    if rel_key in requires_mathlib:
        link_args.append("-lm")

    return input_paths, link_args


async def run_wacct_tests_impl(
    chapter: int | None = None,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    tests_dir = fixture_path("wacct", "tests")
    expected_path = fixture_path("wacct", "expected_results.json")
    fixture_root = tests_dir.parent
    invalid_c23_skip_set = load_invalid_c23_skip_set()
    requires_mathlib, libs_by_program, assembly_libs_by_program = load_wacct_properties(
        fixture_root
    )
    regalloc_program_names, regalloc_wrapper_path = load_wacct_regalloc_wrapper_info(
        fixture_root
    )
    if not tests_dir.is_dir():
        raise RuntimeError(f"Missing WACCT fixtures directory: {tests_dir}")
    if not expected_path.is_file():
        raise RuntimeError(f"Missing WACCT expected results file: {expected_path}")
    expected_map = json.loads(expected_path.read_text()) if expected_path.exists() else {}

    if chapter is not None and not 1 <= chapter <= 20:
        raise ValueError("chapter must be between 1 and 20")

    chapters = [chapter] if chapter is not None else list(range(1, 21))
    cases: list[dict] = []
    for chapter_number in chapters:
        chapter_prefix = f"chapter_{chapter_number}/"

        for rel_str in sorted(
            key for key in expected_map if key.startswith(chapter_prefix) and key.endswith(".c")
        ):
            rel_path = Path(rel_str)
            source_path = tests_dir / rel_path
            if not source_path.is_file():
                continue

            entry = expected_map.get(rel_str, {})
            expected_exit = entry.get("return_code", 0) if isinstance(entry, dict) else 0
            expected_stdout = entry.get("stdout", "").strip() if isinstance(entry, dict) else ""
            parts = rel_path.with_suffix("").parts
            suffix = "__".join(parts[1:]) if len(parts) > 1 else rel_path.stem
            input_paths, link_args = build_wacct_compile_inputs(
                rel_path,
                source_path,
                tests_dir,
                requires_mathlib,
                libs_by_program,
                assembly_libs_by_program,
                regalloc_program_names,
                regalloc_wrapper_path,
            )
            cases.append(
                {
                    "name": f"chapter_{chapter_number}:{suffix}",
                    "source": source_path.read_text(errors="replace"),
                    "source_path": str(source_path),
                    "input_paths": input_paths,
                    "link_args": link_args,
                    "expected_stdout": expected_stdout,
                    "expected_exit_code": expected_exit,
                }
            )

        chapter_dir = tests_dir / f"chapter_{chapter_number}"
        if chapter_dir.is_dir():
            for invalid_dir in sorted(chapter_dir.glob("invalid_*")):
                for source_path in sorted(invalid_dir.rglob("*.c")):
                    rel_path = source_path.relative_to(tests_dir)
                    rel_key = rel_path.as_posix()
                    if rel_key in invalid_c23_skip_set:
                        continue
                    parts = rel_path.with_suffix("").parts
                    suffix = "__".join(parts[1:]) if len(parts) > 1 else rel_path.stem
                    cases.append(
                        {
                            "name": f"chapter_{chapter_number}:{suffix}",
                            "source": source_path.read_text(errors="replace"),
                            "source_path": str(source_path),
                            "expected_stdout": "",
                            "expected_exit_code": 1,
                            "expect_compile_success": False,
                        }
                    )

    selected = select_cases(cases, n_tests=n_tests, test_name=test_name, offset=offset)
    if not selected and n_tests == 0 and test_name is None and offset == 0:
        chapter_label = f"chapter_{chapter}" if chapter is not None else "all chapters"
        raise RuntimeError(
            f"No WACCT cases discovered for {chapter_label}; check fixtures under {tests_dir}"
        )
    return to_result(
        await run_cases_parallel(
            selected,
            suite_name="wacct",
            run_name="wacct/all" if chapter is None else f"wacct/chapter_{chapter}",
        )
    )


@wacct.test()
async def run_wacct_all(
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    return await run_wacct_tests_impl(
        chapter=None,
        n_tests=n_tests,
        test_name=test_name,
        offset=offset,
    )


@wacct.test("chapter_{chapter}")
async def run_wacct_tests(
    chapter: int | None = None,
    n_tests: int = 0,
    test_name: str | None = None,
    offset: int = 0,
) -> TestResult:
    return await run_wacct_tests_impl(
        chapter=chapter,
        n_tests=n_tests,
        test_name=test_name,
        offset=offset,
    )
