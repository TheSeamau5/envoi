import asyncio
from pathlib import Path

import envoi


async def main() -> None:
    submissions_dir = Path(__file__).parent / "submissions"

    client = await envoi.connect("http://localhost:8000")
    print(f"Available tests: {client.tests}")

    print("\n=== Good submission ===")
    good_source = (submissions_dir / "good.py").read_text(encoding="utf-8")
    result = await client.test(
        "easy",
        submission=envoi.Documents.from_text("submission.py", good_source),
    )
    for case in result["cases"]:
        status = "pass" if case["passed"] else "FAIL"
        print(
            f"  [{status}] {case['input']} -> {case['actual']} "
            f"(expected {case['expected']})"
        )

    print("\n=== Buggy submission ===")
    buggy_source = (submissions_dir / "buggy.py").read_text(encoding="utf-8")
    result = await client.test(
        "hard",
        submission=envoi.Documents.from_text("submission.py", buggy_source),
    )
    for case in result["cases"]:
        status = "pass" if case["passed"] else "FAIL"
        print(
            f"  [{status}] {case['input']} -> {case['actual']} "
            f"(expected {case['expected']})"
        )


asyncio.run(main())
