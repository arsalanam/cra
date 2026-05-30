"""OQ — Operational Qualification runner (eCRF E7).

Runs pytest against the test node-ids declared in the RTM (loaded by
:mod:`research_assistant.validation.rtm`) and joins the pass/fail
result with each requirement so the OQ report can be emitted with
regulatory traceability.

Implementation: shells out to ``uv run pytest`` with
``--tb=no --quiet --no-header`` and parses the per-test "PASSED" /
"FAILED" / "ERROR" lines from stdout. We deliberately don't depend on
``pytest-json-report`` so the validation pack remains build-time
zero-dependency — pytest itself is already a dev dep.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .rtm import Requirement, RequirementsMatrix, load_requirements_matrix

TestStatus = Literal["passed", "failed", "error", "skipped", "missing"]


class TestResult(BaseModel):
    node_id: str
    status: TestStatus
    duration_seconds: float | None = None


class RequirementResult(BaseModel):
    requirement: Requirement
    status: TestStatus = Field(
        description=(
            "Aggregate: 'passed' iff every test passed; 'failed' if any "
            "failed; 'error' if any errored; 'missing' if pytest couldn't "
            "discover any."
        )
    )
    test_results: list[TestResult]


class OperationalReport(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    matrix_version: str
    total_requirements: int
    requirements_passed: int
    requirements_failed: int
    requirements_missing: int
    duration_seconds: float
    requirement_results: list[RequirementResult]
    pytest_command: list[str]
    pytest_returncode: int


_RESULT_PATTERNS: list[tuple[re.Pattern[str], TestStatus]] = [
    (re.compile(r"^(?P<node>\S+::\S+)\s+PASSED"), "passed"),
    (re.compile(r"^(?P<node>\S+::\S+)\s+FAILED"), "failed"),
    (re.compile(r"^(?P<node>\S+::\S+)\s+ERROR"), "error"),
    (re.compile(r"^(?P<node>\S+::\S+)\s+SKIPPED"), "skipped"),
]


def _parse_pytest_output(output: str) -> dict[str, TestStatus]:
    """Parse `pytest -v` per-test lines into a {node_id: status} map.

    Both Windows and POSIX path separators show up; the matrix stores
    forward-slash paths so we normalise.
    """
    results: dict[str, TestStatus] = {}
    for line in output.splitlines():
        for pattern, status in _RESULT_PATTERNS:
            m = pattern.match(line.strip())
            if m:
                node = m.group("node").replace("\\", "/")
                results[node] = status
                break
    return results


def _aggregate(test_results: list[TestResult]) -> TestStatus:
    if not test_results:
        return "missing"
    if any(t.status == "error" for t in test_results):
        return "error"
    if any(t.status == "failed" for t in test_results):
        return "failed"
    if any(t.status == "missing" for t in test_results):
        return "missing"
    if all(t.status == "passed" for t in test_results):
        return "passed"
    if all(t.status in {"passed", "skipped"} for t in test_results):
        return "passed"
    return "failed"


def run_oq(
    matrix: RequirementsMatrix | None = None,
    *,
    pytest_executable: list[str] | None = None,
    cwd: Path | None = None,
) -> OperationalReport:
    """Execute pytest over the matrix tests + return a structured report.

    `pytest_executable` defaults to ``[sys.executable, '-m', 'pytest']``
    so the OQ run uses the same interpreter as the running app.
    """
    matrix = matrix or load_requirements_matrix()
    node_ids = sorted(matrix.all_test_node_ids())
    cmd = list(pytest_executable or [sys.executable, "-m", "pytest"])
    cmd += ["-v", "--tb=no", "--no-header", "-q", *node_ids]
    started = datetime.now(UTC)
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    finished = datetime.now(UTC)
    duration = (finished - started).total_seconds()
    parsed = _parse_pytest_output(completed.stdout + "\n" + completed.stderr)

    requirement_results: list[RequirementResult] = []
    for req in matrix.requirements:
        tests = []
        for node in req.tests:
            tests.append(
                TestResult(
                    node_id=node,
                    status=parsed.get(node, "missing"),
                )
            )
        requirement_results.append(
            RequirementResult(
                requirement=req,
                status=_aggregate(tests),
                test_results=tests,
            )
        )

    summary_passed = sum(1 for r in requirement_results if r.status == "passed")
    summary_failed = sum(1 for r in requirement_results if r.status in {"failed", "error"})
    summary_missing = sum(1 for r in requirement_results if r.status == "missing")

    return OperationalReport(
        matrix_version=matrix.version,
        total_requirements=len(requirement_results),
        requirements_passed=summary_passed,
        requirements_failed=summary_failed,
        requirements_missing=summary_missing,
        duration_seconds=duration,
        requirement_results=requirement_results,
        pytest_command=cmd,
        pytest_returncode=completed.returncode,
    )


__all__ = [
    "OperationalReport",
    "RequirementResult",
    "TestResult",
    "TestStatus",
    "run_oq",
]
