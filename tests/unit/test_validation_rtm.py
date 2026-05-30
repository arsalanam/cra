"""Requirements Traceability Matrix (eCRF E7 — validation pack)."""

from __future__ import annotations

import re
from pathlib import Path

from research_assistant.validation.rtm import load_requirements_matrix


def test_matrix_loads_and_has_required_codes() -> None:
    matrix = load_requirements_matrix()
    codes = {r.code for r in matrix.requirements}
    # The validation pack's core compliance gates must be in the matrix.
    must_have = {
        "AUDIT-001",
        "SIGN-001",
        "SIGN-002",
        "LOCK-001",
        "LOCK-002",
        "RBAC-001",
        "PHI-001",
    }
    missing = must_have - codes
    assert not missing, f"RTM is missing required codes: {missing}"


def test_matrix_codes_are_unique() -> None:
    matrix = load_requirements_matrix()
    codes = [r.code for r in matrix.requirements]
    assert len(set(codes)) == len(codes), "Duplicate requirement code(s)"


def test_each_requirement_lists_at_least_one_test() -> None:
    matrix = load_requirements_matrix()
    for r in matrix.requirements:
        assert r.tests, f"{r.code} has no tests listed"


def test_each_test_node_id_format_is_valid() -> None:
    """Test references must be pytest node ids: 'tests/.../test_x.py::test_name'."""
    pattern = re.compile(r"^tests/[\w/]+\.py::test_\w+$")
    matrix = load_requirements_matrix()
    for r in matrix.requirements:
        for node in r.tests:
            assert pattern.match(node), f"{r.code} has malformed node id: {node!r}"


def test_referenced_test_files_exist_in_repo() -> None:
    """Spot-check: at least every distinct test FILE referenced exists.

    We don't validate that the individual test function exists (that's
    what the OQ runner does at run time); but the file path being wrong
    would silently cause every requirement to be "missing" in the OQ
    report.
    """
    repo_root = Path(__file__).resolve().parents[2]
    matrix = load_requirements_matrix()
    referenced_files = {node.split("::", 1)[0] for r in matrix.requirements for node in r.tests}
    missing = [f for f in referenced_files if not (repo_root / f).exists()]
    assert not missing, f"RTM references missing test files: {missing}"


def test_each_requirement_has_regulatory_anchor() -> None:
    matrix = load_requirements_matrix()
    for r in matrix.requirements:
        assert r.regulatory_anchor.strip(), f"{r.code} has empty regulatory_anchor"
        assert r.system_anchor.strip(), f"{r.code} has empty system_anchor"


def test_matrix_version_is_semver_shape() -> None:
    matrix = load_requirements_matrix()
    assert re.match(r"^\d+\.\d+\.\d+$", matrix.version)
