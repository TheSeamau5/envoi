"""
Basics test suite — hand-written .c files covering core compiler features.

Tests live in tests/basics/<category>/*.c  (smoke, variables, control_flow, etc.)
Each file declares expected output via comment headers:

    // expect_stdout: Hello
    // expect_stdout: World
    // expect_exit: 0          (optional, default 0)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import envoi

from .utils import TestResult, run_cases_parallel, select_cases, to_result

basics = envoi.suite("basics")


def load_single_file_case(source_file: Path) -> dict:
    source = source_file.read_text()
    stdout_lines = re.findall(r"^//\s*expect_stdout:\s*(.+)$", source, re.MULTILINE)
    exit_match = re.search(r"^//\s*expect_exit:\s*(\d+)", source, re.MULTILINE)
    return {
        "name": source_file.stem,
        "source": source,
        "source_path": str(source_file),
        "expected_stdout": "\n".join(stdout_lines),
        "expected_exit_code": int(exit_match.group(1)) if exit_match else 0,
    }


def load_multi_file_case(case_dir: Path) -> dict:
    meta_path = case_dir / "meta.json"
    if not meta_path.is_file():
        raise RuntimeError(f"Missing basics multi-file metadata: {meta_path}")

    meta = json.loads(meta_path.read_text())
    sources: dict[str, str] = {}
    for path in sorted(file_path for file_path in case_dir.rglob("*") if file_path.is_file()):
        if path == meta_path:
            continue
        sources[str(path.relative_to(case_dir))] = path.read_text()

    compile_inputs = meta.get("compile_inputs")
    link_args = meta.get("link_args")
    case = {
        "name": case_dir.name,
        "sources": sources,
        "source": "\n\n".join(
            f"// file: {filename}\n{content}" for filename, content in sources.items()
        ),
        "expected_stdout": str(meta.get("expected_stdout", "")),
        "expected_exit_code": int(meta.get("expected_exit_code", 0)),
        "expect_compile_success": bool(meta.get("expect_compile_success", True)),
    }
    if isinstance(compile_inputs, list):
        case["compile_inputs"] = [str(item) for item in compile_inputs]
    if isinstance(link_args, list):
        case["link_args"] = [str(item) for item in link_args]
    return case


async def run_basics(
    n_tests: int = 0,
    test_name: str | None = None,
    *,
    categories: tuple[str, ...] | None = None,
    run_name: str = "basics",
) -> TestResult:
    basics_dir = Path(__file__).resolve().parent / "basics"
    category_names = (
        categories
        if categories is not None
        else (
            "smoke",
            "variables",
            "control_flow",
            "functions",
            "multi_file",
            "expressions",
            "edge_cases",
            "stress",
        )
    )

    all_cases: list[dict] = []
    for category in category_names:
        category_dir = basics_dir / category
        if not category_dir.is_dir():
            continue

        if category == "multi_file":
            for case_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
                all_cases.append(load_multi_file_case(case_dir))
            continue

        for source_file in sorted(category_dir.glob("*.c")):
            all_cases.append(load_single_file_case(source_file))

    cases = select_cases(all_cases, n_tests=n_tests, test_name=test_name)
    return to_result(
        await run_cases_parallel(
            cases,
            suite_name="basics",
            run_name=run_name,
        )
    )


@basics.test("smoke")
async def smoke(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(
        n_tests=n_tests,
        test_name=test_name,
        categories=("smoke",),
        run_name="basics/smoke",
    )


@basics.test("variables")
async def variables(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(
        n_tests=n_tests,
        test_name=test_name,
        categories=("variables",),
        run_name="basics/variables",
    )


@basics.test("control_flow")
async def control_flow(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(
        n_tests=n_tests,
        test_name=test_name,
        categories=("control_flow",),
        run_name="basics/control_flow",
    )


@basics.test("functions")
async def functions(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(
        n_tests=n_tests,
        test_name=test_name,
        categories=("functions",),
        run_name="basics/functions",
    )


@basics.test("expressions")
async def expressions(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(
        n_tests=n_tests,
        test_name=test_name,
        categories=("expressions",),
        run_name="basics/expressions",
    )


@basics.test("multi_file")
async def multi_file(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(
        n_tests=n_tests,
        test_name=test_name,
        categories=("multi_file",),
        run_name="basics/multi_file",
    )


@basics.test("edge_cases")
async def edge_cases(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(
        n_tests=n_tests,
        test_name=test_name,
        categories=("edge_cases",),
        run_name="basics/edge_cases",
    )


@basics.test("stress")
async def stress(n_tests: int = 0, test_name: str | None = None) -> TestResult:
    return await run_basics(
        n_tests=n_tests,
        test_name=test_name,
        categories=("stress",),
        run_name="basics/stress",
    )
