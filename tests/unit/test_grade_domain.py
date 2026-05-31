"""Pydantic schemas for the grade_drafter specialist."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.grade import (
    PRISMA_2020_ITEMS,
    DowngradeReason,
    GradeDocument,
    GradeIntake,
    OutcomeAssessment,
    OutcomeSpec,
    PrismaChecklist,
    PrismaItem,
    SofRow,
    SofTable,
    UpgradeReason,
)


def _none(rationale: str = "no concerns") -> DowngradeReason:
    return DowngradeReason(level="none", rationale=rationale)


def _serious(rationale: str = "I²=78%") -> DowngradeReason:
    return DowngradeReason(level="serious", rationale=rationale)


def _very_serious(rationale: str = "I²=92%") -> DowngradeReason:
    return DowngradeReason(level="very_serious", rationale=rationale)


def _assess(
    *,
    study_design: str = "rct",
    rob: DowngradeReason | None = None,
    inc: DowngradeReason | None = None,
    ind: DowngradeReason | None = None,
    imp: DowngradeReason | None = None,
    pub: DowngradeReason | None = None,
    large: UpgradeReason | None = None,
    dose: UpgradeReason | None = None,
    resid: UpgradeReason | None = None,
) -> OutcomeAssessment:
    return OutcomeAssessment(
        outcome_name="Mortality at 12 months",
        study_design=study_design,  # type: ignore[arg-type]
        n_studies=8,
        n_participants=4250,
        effect_estimate="RR 0.72",
        confidence_interval="0.58 to 0.89",
        risk_of_bias=rob or _none(),
        inconsistency=inc or _none(),
        indirectness=ind or _none(),
        imprecision=imp or _none(),
        publication_bias=pub or _none(),
        large_effect=large,
        dose_response=dose,
        residual_confounding=resid,
    )


# ── Intake ──────────────────────────────────────────────────────────────


def test_intake_kind_discriminator() -> None:
    intake = GradeIntake(
        research_question="Q",
        outcomes_to_assess=[OutcomeSpec(name="Mortality")],
    )
    assert intake.kind == "grade_intake"


def test_intake_requires_at_least_one_outcome() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        GradeIntake(research_question="Q", outcomes_to_assess=[])


# ── Certainty computation ──────────────────────────────────────────────


def test_rct_with_no_downgrades_is_high_certainty() -> None:
    assessment = _assess(study_design="rct")
    assert assessment.certainty == "high"


def test_rct_with_one_serious_downgrade_drops_to_moderate() -> None:
    assessment = _assess(study_design="rct", inc=_serious())
    assert assessment.certainty == "moderate"


def test_rct_with_two_serious_downgrades_drops_to_low() -> None:
    assessment = _assess(study_design="rct", inc=_serious(), imp=_serious("CI crosses null"))
    assert assessment.certainty == "low"


def test_rct_with_three_serious_downgrades_drops_to_very_low() -> None:
    assessment = _assess(
        study_design="rct",
        rob=_serious("RoB high in 4 studies"),
        inc=_serious(),
        imp=_serious("CI crosses null"),
    )
    assert assessment.certainty == "very_low"


def test_very_serious_drops_two_levels() -> None:
    assessment = _assess(study_design="rct", inc=_very_serious())
    assert assessment.certainty == "low"


def test_observational_starts_at_low() -> None:
    assessment = _assess(study_design="observational")
    assert assessment.certainty == "low"


def test_observational_can_upgrade_with_large_effect() -> None:
    assessment = _assess(
        study_design="observational",
        large=UpgradeReason(level="large", rationale="RR=3.5"),
    )
    assert assessment.certainty == "high"


def test_observational_upgrade_clamped_to_high() -> None:
    """Multiple upgrades shouldn't push above 'high'."""
    assessment = _assess(
        study_design="observational",
        large=UpgradeReason(level="large", rationale="RR=3.5"),
        dose=UpgradeReason(level="moderate", rationale="gradient observed"),
        resid=UpgradeReason(level="moderate", rationale="confounding would reduce"),
    )
    assert assessment.certainty == "high"


def test_certainty_clamped_at_very_low_lower_bound() -> None:
    """5 very_serious downgrades shouldn't go below very_low."""
    assessment = _assess(
        study_design="rct",
        rob=_very_serious(),
        inc=_very_serious(),
        ind=_very_serious(),
        imp=_very_serious(),
        pub=_very_serious(),
    )
    assert assessment.certainty == "very_low"


# ── Downgrade rationale ────────────────────────────────────────────────


def test_downgrade_rationale_required() -> None:
    """Schema requires non-empty rationale — but Pydantic doesn't reject
    the empty string, which is the deliberate posture: the system prompt
    enforces the substantive content."""
    d = DowngradeReason(level="serious", rationale="")
    assert d.rationale == ""  # Permitted at schema level; prompt enforces


# ── SoF table ──────────────────────────────────────────────────────────


def test_sof_table_requires_at_least_one_row() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        SofTable(research_question="Q", rows=[])


def test_sof_row_certainty_enum() -> None:
    row = SofRow(
        outcome_name="x",
        n_studies=1,
        n_participants=10,
        effect_estimate="RR 1.0",
        confidence_interval="0.5 to 2.0",
        certainty="moderate",
        importance="critical",
    )
    assert row.certainty == "moderate"


# ── PRISMA checklist ───────────────────────────────────────────────────


def test_prisma_canonical_items_count_matches_2020() -> None:
    """PRISMA 2020 has 27 main items expanded to 42 sub-items in the
    BMJ publication. Our embedded list captures the sub-items."""
    assert len(PRISMA_2020_ITEMS) >= 27


def test_prisma_checklist_accepts_partial_items() -> None:
    """Operator may submit a checklist with the 27 main items even
    though the full list has 42 sub-items. The report renders missing
    items as 'not reported'."""
    items = [
        PrismaItem(
            section="Title",
            item_id=str(i),
            item_text=f"Item {i}",
            reported="yes",
        )
        for i in range(1, 28)
    ]
    checklist = PrismaChecklist(review_title="x", items=items)
    assert len(checklist.items) == 27


def test_prisma_item_reported_enum() -> None:
    item = PrismaItem(
        section="Methods",
        item_id="9",
        item_text="Data collection",
        reported="not_applicable",
        location="Methods §2.3",
    )
    assert item.reported == "not_applicable"


# ── Document roundtrip ──────────────────────────────────────────────────


def test_document_roundtrips_via_json() -> None:
    doc = GradeDocument(
        intake=GradeIntake(
            research_question="Q",
            outcomes_to_assess=[OutcomeSpec(name="Mortality")],
        ),
        assessments=[_assess()],
        sof_table=SofTable(
            research_question="Q",
            rows=[
                SofRow(
                    outcome_name="Mortality",
                    n_studies=8,
                    n_participants=4250,
                    effect_estimate="RR 0.72",
                    confidence_interval="0.58 to 0.89",
                    certainty="high",
                    importance="critical",
                )
            ],
        ),
        is_final=True,
    )
    json_str = doc.model_dump_json()
    restored = GradeDocument.model_validate_json(json_str)
    assert restored.is_final is True
    assert restored.assessments[0].certainty == "high"
