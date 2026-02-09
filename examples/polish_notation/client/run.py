import asyncio
from pathlib import Path

import envoi


async def main() -> None:
    submissions_dir = Path(__file__).parent / "submissions"

    client = await envoi.connect("http://localhost:8000")
    print(f"Available tests: {client.tests}")

    print("\n=== Good submission ===")
    result = await client.test(
        "easy",
        submission=envoi.Documents(submissions_dir / "good.py"),
    )
    for case in result["cases"]:
        status = "pass" if case["passed"] else "FAIL"
        print(
            f"  [{status}] {case['input']} -> {case['actual']} "
            f"(expected {case['expected']})"
        )

    print("\n=== Buggy submission ===")
    result = await client.test(
        "hard",
        submission=envoi.Documents(submissions_dir / "buggy.py"),
    )
    for case in result["cases"]:
        status = "pass" if case["passed"] else "FAIL"
        print(
            f"  [{status}] {case['input']} -> {case['actual']} "
            f"(expected {case['expected']})"
        )


asyncio.run(main())
