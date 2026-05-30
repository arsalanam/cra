"""OQ runner output assembly — joins parsed pytest output with the RTM."""

from __future__ import annotations

from research_assistant.validation.oq import _aggregate, _parse_pytest_output


def test_parse_pytest_output_extracts_passed_and_failed() -> None:
    out = (
        "tests/unit/test_a.py::test_one PASSED                       [ 50%]\n"
        "tests/unit/test_a.py::test_two FAILED                       [100%]\n"
    )
    parsed = _parse_pytest_output(out)
    assert parsed == {
        "tests/unit/test_a.py::test_one": "passed",
        "tests/unit/test_a.py::test_two": "failed",
    }


def test_parse_pytest_normalises_windows_separators() -> None:
    out = "tests\\unit\\test_a.py::test_one PASSED"
    parsed = _parse_pytest_output(out)
    assert "tests/unit/test_a.py::test_one" in parsed


def test_aggregate_all_passed() -> None:
    from research_assistant.validation.oq import TestResult

    results = [
        TestResult(node_id="a", status="passed"),
        TestResult(node_id="b", status="passed"),
    ]
    assert _aggregate(results) == "passed"


def test_aggregate_any_failed_dominates() -> None:
    from research_assistant.validation.oq import TestResult

    results = [
        TestResult(node_id="a", status="passed"),
        TestResult(node_id="b", status="failed"),
    ]
    assert _aggregate(results) == "failed"


def test_aggregate_missing_dominates_when_no_failures() -> None:
    from research_assistant.validation.oq import TestResult

    results = [
        TestResult(node_id="a", status="passed"),
        TestResult(node_id="b", status="missing"),
    ]
    assert _aggregate(results) == "missing"


def test_aggregate_empty_results_is_missing() -> None:
    assert _aggregate([]) == "missing"


def test_aggregate_skipped_with_passed_is_passed() -> None:
    from research_assistant.validation.oq import TestResult

    results = [
        TestResult(node_id="a", status="passed"),
        TestResult(node_id="b", status="skipped"),
    ]
    assert _aggregate(results) == "passed"
