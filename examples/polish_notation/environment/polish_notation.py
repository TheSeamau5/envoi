"""
Polish notation evaluator.

Submit a file called submission.py that takes a polish notation
expression as its first argument and prints the integer result to stdout.

Operators: +, -, *
Operands: integers (can be negative)
"""

from pydantic import BaseModel

import envoi


class CaseResult(BaseModel):
    input: str
    expected: str
    actual: str
    passed: bool
    error: str | None = None


class TestResult(BaseModel):
    cases: list[CaseResult]


async def run_cases(cases):
    results = []
    for expr, expected in cases:
        run = await envoi.run(f'python3 submission.py "{expr}"')
        results.append(
            CaseResult(
                input=expr,
                expected=expected,
                actual=run.stdout,
                passed=run.stdout == expected and run.exit_code == 0,
                error=run.stderr or None,
            )
        )
    return TestResult(cases=results)


@envoi.test
async def easy(submission: envoi.Documents) -> TestResult:
    """Simple two-operand expressions."""
    return await run_cases([
        ("+ 3 4", "7"),
        ("* 2 5", "10"),
        ("- 10 3", "7"),
    ])


@envoi.test
async def medium(submission: envoi.Documents) -> TestResult:
    """Nested expressions with multiple operators."""
    return await run_cases([
        ("+ * 2 3 4", "10"),
        ("* + 1 2 + 3 4", "21"),
        ("- * 3 3 + 2 2", "5"),
    ])


@envoi.test
async def hard(submission: envoi.Documents) -> TestResult:
    """Edge cases with zeros and negative operands."""
    return await run_cases([
        ("+ 0 0", "0"),
        ("* 1 1", "1"),
        ("- 0 1", "-1"),
        ("+ -3 7", "4"),
        ("* -2 -3", "6"),
    ])
