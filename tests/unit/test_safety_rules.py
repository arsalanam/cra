"""safety_rules — auto_classify_serious + compute_reporting_deadline.

Locks ICH E2A §III.A criteria. When the rules change, update both the
helper and these tests (and announce in the PR — every clinical
deployment depends on this exact decision matrix).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from research_assistant.persistence.clinical.safety_rules import (
    auto_classify_serious,
    compute_reporting_deadline,
)


def test_grade_1_with_no_flags_is_not_serious() -> None:
    is_serious, reasons = auto_classify_serious(
        severity_grade=1, outcome="recovered"
    )
    assert is_serious is False
    assert reasons == []


def test_grade_3_alone_auto_serious_with_catchall_reason() -> None:
    """A grade-3 AE with no specific serious flags still triggers the
    platform's escalation — internal posture, NOT a regulatory
    requirement; PI can override."""
    is_serious, reasons = auto_classify_serious(
        severity_grade=3, outcome="recovering"
    )
    assert is_serious is True
    assert reasons == ["other_medically_significant"]


def test_grade_5_promotes_to_death_reason() -> None:
    is_serious, reasons = auto_classify_serious(
        severity_grade=5, outcome="not_recovered"
    )
    assert is_serious is True
    assert "death" in reasons


def test_death_outcome_flags_death_regardless_of_grade() -> None:
    """A grade-2 entry with outcome=death still books as a death SAE —
    catches the rare case where the form is captured before the AE's
    grade has been updated to 5."""
    is_serious, reasons = auto_classify_serious(
        severity_grade=2, outcome="death"
    )
    assert is_serious is True
    assert reasons == ["death"]


def test_hospitalisation_flag_supersedes_catchall_reason() -> None:
    """If a specific reason fires (hospitalisation), the catch-all
    `other_medically_significant` is NOT also added."""
    is_serious, reasons = auto_classify_serious(
        severity_grade=3, outcome="recovering", hospitalisation_flag=True
    )
    assert is_serious is True
    assert reasons == ["hospitalisation"]


def test_multiple_reasons_accumulate_in_deterministic_order() -> None:
    """Audit-trail diffs need the reasons list to be stable across
    re-runs even when several criteria fire at once."""
    is_serious, reasons = auto_classify_serious(
        severity_grade=4,
        outcome="not_recovered",
        hospitalisation_flag=True,
        life_threatening_flag=True,
    )
    assert is_serious is True
    # Order matches the function's emission order: death (from grade),
    # life_threatening, hospitalisation, then disability/congenital/other
    # if they were set. Here grade 4 does NOT add death (only grade 5
    # or outcome=death does), so we expect just LT + hosp.
    assert reasons == ["life_threatening", "hospitalisation"]


def test_congenital_anomaly_alone_is_serious() -> None:
    is_serious, reasons = auto_classify_serious(
        severity_grade=1, outcome="recovering", congenital_anomaly_flag=True
    )
    assert is_serious is True
    assert reasons == ["congenital_anomaly"]


def test_persistent_disability_alone_is_serious() -> None:
    is_serious, reasons = auto_classify_serious(
        severity_grade=2,
        outcome="not_recovered",
        persistent_disability_flag=True,
    )
    assert is_serious is True
    assert reasons == ["persistent_disability"]


def test_other_significant_flag_alone_is_serious() -> None:
    is_serious, reasons = auto_classify_serious(
        severity_grade=2,
        outcome="recovering",
        other_medically_significant_flag=True,
    )
    assert is_serious is True
    assert reasons == ["other_medically_significant"]


def test_grade_2_with_no_flags_is_not_serious() -> None:
    is_serious, reasons = auto_classify_serious(
        severity_grade=2, outcome="recovered"
    )
    assert is_serious is False
    assert reasons == []


@pytest.mark.parametrize("grade", [3, 4, 5])
def test_high_grade_always_serious(grade: int) -> None:
    is_serious, _ = auto_classify_serious(
        severity_grade=grade, outcome="recovering"
    )
    assert is_serious is True


# ── compute_reporting_deadline ───────────────────────────────────────────


def test_non_serious_returns_no_deadline() -> None:
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    assert compute_reporting_deadline(is_serious=False, reported_at=now) is None


def test_serious_returns_24h_deadline() -> None:
    now = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
    deadline = compute_reporting_deadline(is_serious=True, reported_at=now)
    assert deadline == now + timedelta(hours=24)
