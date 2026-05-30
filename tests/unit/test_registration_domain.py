"""Pydantic schemas for the registration_drafter specialist."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_assistant.domain.registration import (
    Arm,
    Condition,
    CoreFields,
    CtGovDraft,
    Eligibility,
    EuCtrDraft,
    Intervention,
    Outcome,
    RegistrationDocument,
    RegistrationIntake,
    Sponsor,
)


def _sponsor() -> Sponsor:
    return Sponsor(name="Acme Pharma", sponsor_type="industry")


def _intake() -> RegistrationIntake:
    return RegistrationIntake(
        brief_title="Drug X for Y disease",
        official_title="A Phase 2, Randomized, Double-Blind Study of Drug X in Y",
        study_type="interventional",
        primary_purpose="treatment",
        phase="phase_2",
        lead_sponsor=_sponsor(),
        conditions=[Condition(name="Y disease")],
        brief_summary="Study to evaluate Drug X.",
    )


def _eligibility() -> Eligibility:
    return Eligibility(
        minimum_age="18 Years",
        inclusion_criteria=["Adults 18-65", "Confirmed Y diagnosis"],
        exclusion_criteria=["Pregnancy", "Prior Drug X exposure"],
    )


def _arm() -> Arm:
    return Arm(
        label="Drug X",
        role="experimental",
        description="200 mg twice daily",
        intervention_names=["Drug X"],
    )


def _outcome(role: str) -> Outcome:
    return Outcome(
        role=role,  # type: ignore[arg-type]
        measure="Change from baseline" if role == "primary" else "Adverse events",
        description="Continuous endpoint",
        time_frame="12 weeks",
    )


# ── Intake ──────────────────────────────────────────────────────────────


def test_intake_carries_kind_discriminator() -> None:
    intake = _intake()
    assert intake.kind == "registration_intake"


def test_intake_requires_at_least_one_condition() -> None:
    with pytest.raises(ValidationError, match=r"at least 1 item"):
        RegistrationIntake(
            brief_title="x",
            official_title="x",
            study_type="interventional",
            primary_purpose="treatment",
            phase="phase_2",
            lead_sponsor=_sponsor(),
            conditions=[],
            brief_summary="x",
        )


# ── Core fields ─────────────────────────────────────────────────────────


def test_core_requires_arms_interventions_and_primary_outcomes() -> None:
    with pytest.raises(ValidationError):
        CoreFields(
            allocation="randomized",
            intervention_model="parallel",
            masking="double",
            arms=[],
            interventions=[Intervention(type="drug", name="X", description="x")],
            primary_outcomes=[_outcome("primary")],
            eligibility=_eligibility(),
            target_enrollment=100,
        )


def test_core_target_enrollment_must_be_positive() -> None:
    with pytest.raises(ValidationError, match=r"greater than 0"):
        CoreFields(
            allocation="randomized",
            intervention_model="parallel",
            masking="double",
            arms=[_arm()],
            interventions=[Intervention(type="drug", name="X", description="x")],
            primary_outcomes=[_outcome("primary")],
            eligibility=_eligibility(),
            target_enrollment=0,
        )


# ── CtGovDraft ──────────────────────────────────────────────────────────


def test_ctgov_draft_sponsor_input_placeholders_default() -> None:
    draft = CtGovDraft(
        brief_title="x",
        official_title="x",
        study_type="interventional",
        primary_purpose="treatment",
        phase="phase_2",
        allocation="randomized",
        intervention_model="parallel",
        masking="double",
        arms=[_arm()],
        interventions=[Intervention(type="drug", name="X", description="x")],
        primary_outcomes=[_outcome("primary")],
        secondary_outcomes=[],
        eligibility=_eligibility(),
        target_enrollment=100,
        enrollment_type="anticipated",
        locations=[],
        lead_sponsor=_sponsor(),
        conditions=[Condition(name="Y")],
    )
    assert draft.overall_official_name == "[SPONSOR INPUT]"
    assert draft.central_contact_email == "[SPONSOR INPUT]"


# ── EuCtrDraft ──────────────────────────────────────────────────────────


def test_euctr_draft_splits_enrollment_eu_and_global() -> None:
    draft = EuCtrDraft(
        full_title="x",
        public_title="x",
        iso_basket_codes=["DE", "FR"],
        trial_type="interventional",
        therapeutic_area="Oncology",
        phase="phase_2",
        allocation="randomized",
        intervention_model="parallel",
        masking="double",
        arms=[_arm()],
        interventions=[Intervention(type="drug", name="X", description="x")],
        primary_endpoints=[_outcome("primary")],
        eligibility=_eligibility(),
        target_enrollment_eu=80,
        target_enrollment_global=100,
        sponsor=_sponsor(),
        conditions=[Condition(name="Y")],
    )
    assert draft.target_enrollment_eu < draft.target_enrollment_global


# ── RegistrationDocument ────────────────────────────────────────────────


def test_document_starts_unfinished_by_default() -> None:
    doc = RegistrationDocument(
        intake=_intake(),
        core=CoreFields(
            allocation="randomized",
            intervention_model="parallel",
            masking="double",
            arms=[_arm()],
            interventions=[Intervention(type="drug", name="X", description="x")],
            primary_outcomes=[_outcome("primary")],
            eligibility=_eligibility(),
            target_enrollment=100,
        ),
        ctgov=CtGovDraft(
            brief_title="x",
            official_title="x",
            study_type="interventional",
            primary_purpose="treatment",
            phase="phase_2",
            allocation="randomized",
            intervention_model="parallel",
            masking="double",
            arms=[_arm()],
            interventions=[Intervention(type="drug", name="X", description="x")],
            primary_outcomes=[_outcome("primary")],
            secondary_outcomes=[],
            eligibility=_eligibility(),
            target_enrollment=100,
            enrollment_type="anticipated",
            locations=[],
            lead_sponsor=_sponsor(),
            conditions=[Condition(name="Y")],
        ),
        euctr=EuCtrDraft(
            full_title="x",
            public_title="x",
            trial_type="interventional",
            therapeutic_area="Oncology",
            phase="phase_2",
            allocation="randomized",
            intervention_model="parallel",
            masking="double",
            arms=[_arm()],
            interventions=[Intervention(type="drug", name="X", description="x")],
            primary_endpoints=[_outcome("primary")],
            eligibility=_eligibility(),
            target_enrollment_eu=80,
            target_enrollment_global=100,
            sponsor=_sponsor(),
            conditions=[Condition(name="Y")],
        ),
        background_paragraph="Disease Y affects N people.",
    )
    assert doc.is_final is False
    assert doc.kind == "registration_document"


def test_document_roundtrips_via_json() -> None:
    doc = RegistrationDocument(
        intake=_intake(),
        core=CoreFields(
            allocation="randomized",
            intervention_model="parallel",
            masking="double",
            arms=[_arm()],
            interventions=[Intervention(type="drug", name="X", description="x")],
            primary_outcomes=[_outcome("primary")],
            eligibility=_eligibility(),
            target_enrollment=100,
        ),
        ctgov=CtGovDraft(
            brief_title="x",
            official_title="x",
            study_type="interventional",
            primary_purpose="treatment",
            phase="phase_2",
            allocation="randomized",
            intervention_model="parallel",
            masking="double",
            arms=[_arm()],
            interventions=[Intervention(type="drug", name="X", description="x")],
            primary_outcomes=[_outcome("primary")],
            secondary_outcomes=[],
            eligibility=_eligibility(),
            target_enrollment=100,
            enrollment_type="anticipated",
            locations=[],
            lead_sponsor=_sponsor(),
            conditions=[Condition(name="Y")],
        ),
        euctr=EuCtrDraft(
            full_title="x",
            public_title="x",
            trial_type="interventional",
            therapeutic_area="Oncology",
            phase="phase_2",
            allocation="randomized",
            intervention_model="parallel",
            masking="double",
            arms=[_arm()],
            interventions=[Intervention(type="drug", name="X", description="x")],
            primary_endpoints=[_outcome("primary")],
            eligibility=_eligibility(),
            target_enrollment_eu=80,
            target_enrollment_global=100,
            sponsor=_sponsor(),
            conditions=[Condition(name="Y")],
        ),
        background_paragraph="x",
        is_final=True,
    )
    json_str = doc.model_dump_json()
    restored = RegistrationDocument.model_validate_json(json_str)
    assert restored.is_final is True
    assert restored.intake.brief_title == doc.intake.brief_title


def test_eligibility_defaults_to_all_sexes_no_healthy_volunteers() -> None:
    e = Eligibility(
        minimum_age="18 Years",
        inclusion_criteria=["x"],
        exclusion_criteria=["y"],
    )
    assert e.sexes == "all"
    assert e.accepts_healthy_volunteers is False
    assert e.maximum_age == "N/A"


def test_arm_role_is_constrained_to_known_set() -> None:
    with pytest.raises(ValidationError):
        Arm(label="x", role="invalid", description="x")  # type: ignore[arg-type]
