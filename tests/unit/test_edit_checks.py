"""Unit tests for the eCRF edit-check expression engine (E2)."""

from __future__ import annotations

import pytest

from research_assistant.domain.ecrf import EditCheck, FormDefinition, Item, Section
from research_assistant.ecrf.edit_checks import (
    ExpressionError,
    evaluate_form,
    required_blank_items,
    validate_expression,
)


def _form() -> FormDefinition:
    return FormDefinition(
        name="vitals",
        title="Vitals",
        sections=[
            Section(
                id="s",
                title="S",
                items=[
                    Item(
                        id="age",
                        label="Age",
                        data_type="integer",
                        required=True,
                        edit_checks=[
                            EditCheck(
                                id="age_range",
                                severity="hard",
                                expression="is_blank(age) or (age >= 0 and age < 120)",
                                message="Age must be 0-119",
                            )
                        ],
                    ),
                    Item(
                        id="sbp",
                        label="Systolic BP",
                        data_type="integer",
                        edit_checks=[
                            EditCheck(
                                id="sbp_dbp",
                                severity="hard",
                                expression="is_blank(sbp) or is_blank(dbp) or dbp <= sbp",
                                message="Diastolic must be <= systolic",
                            )
                        ],
                    ),
                    Item(id="dbp", label="Diastolic BP", data_type="integer"),
                ],
            )
        ],
    )


def _passed(results: list, check_id: str) -> bool:
    return next(r for r in results if r.check_id == check_id).passed


def test_field_range_check() -> None:
    assert _passed(evaluate_form(_form(), {"age": "45"}), "age_range") is True
    assert _passed(evaluate_form(_form(), {"age": "200"}), "age_range") is False
    # blank passes the range check (completeness is enforced separately).
    assert _passed(evaluate_form(_form(), {"age": None}), "age_range") is True


def test_cross_field_check_with_coercion() -> None:
    # "120/80" -> dbp 80 <= sbp 120 : pass
    assert _passed(evaluate_form(_form(), {"sbp": "120", "dbp": "80"}), "sbp_dbp") is True
    # 80/120 inverted: dbp 120 <= sbp 80 : fail
    assert _passed(evaluate_form(_form(), {"sbp": "80", "dbp": "120"}), "sbp_dbp") is False


def test_required_blank_items() -> None:
    assert required_blank_items(_form(), {"age": None, "sbp": "120"}) == ["age"]
    assert required_blank_items(_form(), {"age": "30"}) == []


def test_validate_rejects_unsafe_expressions() -> None:
    allowed = {"age", "is_blank"}
    validate_expression("age >= 0 and age < 120", allowed)  # ok
    with pytest.raises(ExpressionError):
        validate_expression("__import__('os')", allowed)  # unknown name
    with pytest.raises(ExpressionError):
        validate_expression("age.__class__", allowed)  # attribute access
    with pytest.raises(ExpressionError):
        validate_expression("unknown_var > 1", allowed)  # unknown name


def test_broken_rule_is_skipped_not_failed() -> None:
    # A type error at eval time (comparing str to int) is logged + skipped (pass),
    # so a malformed rule never blocks a clinician — author-time validation is the guard.
    form = FormDefinition(
        name="f",
        title="F",
        sections=[
            Section(
                id="s",
                title="S",
                items=[
                    Item(
                        id="x",
                        label="X",
                        data_type="text",
                        edit_checks=[
                            EditCheck(id="bad", severity="hard", expression="x > 5", message="bad")
                        ],
                    )
                ],
            )
        ],
    )
    assert _passed(evaluate_form(form, {"x": "hello"}), "bad") is True
