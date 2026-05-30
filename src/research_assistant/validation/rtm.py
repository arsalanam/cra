"""Requirements Traceability Matrix loader (eCRF E7 — validation pack).

The matrix is a versioned JSON file (`requirements_matrix.json`) that
binds each requirement code to its regulatory anchor (Part 11 §,
ICH E6, ALCOA+ tenet) and the test cases that demonstrate it.

The OQ runner (:mod:`research_assistant.validation.oq`) joins this
matrix with a pytest JSON-report run to produce per-requirement
pass/fail evidence.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, Field


class Requirement(BaseModel):
    code: str = Field(description="Unique short code, e.g. 'AUDIT-001'.")
    title: str
    regulatory_anchor: str = Field(description="Part 11 §, ICH E6 §, etc.")
    system_anchor: str = Field(description="Code location that implements it.")
    tests: list[str] = Field(
        description="pytest node ids: 'tests/.../test_*.py::test_name'.",
    )


class RequirementsMatrix(BaseModel):
    version: str
    generated_at: str
    requirements: list[Requirement]

    def by_code(self) -> dict[str, Requirement]:
        return {r.code: r for r in self.requirements}

    def all_test_node_ids(self) -> set[str]:
        return {t for r in self.requirements for t in r.tests}


def load_requirements_matrix() -> RequirementsMatrix:
    """Read the bundled matrix JSON via importlib.resources."""
    raw: Any = json.loads(
        files("research_assistant.validation")
        .joinpath("requirements_matrix.json")
        .read_text(encoding="utf-8")
    )
    return RequirementsMatrix.model_validate(raw)


__all__ = ["Requirement", "RequirementsMatrix", "load_requirements_matrix"]
