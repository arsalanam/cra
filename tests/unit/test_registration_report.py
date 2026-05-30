"""Registration report — assembler + PDF + DOCX byte-signature smoke tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from research_assistant.reports.registration import (
    assemble_report_data,
    build_docx,
    build_pdf,
)


def _msg(
    *,
    role: str = "assistant",
    msg_id: str = "m1",
    final_answer: str | None = None,
    input_text: str | None = None,
) -> Any:
    return SimpleNamespace(id=msg_id, role=role, input_text=input_text, final_answer=final_answer)


def _sponsor() -> Sponsor:
    return Sponsor(name="Acme Pharma", sponsor_type="industry")


def _intake() -> RegistrationIntake:
    return RegistrationIntake(
        brief_title="Drug X for Y",
        official_title="A Phase 2 Study of Drug X in Y disease",
        study_type="interventional",
        primary_purpose="treatment",
        phase="phase_2",
        lead_sponsor=_sponsor(),
        conditions=[Condition(name="Y disease")],
        brief_summary="Trial of drug X.",
    )


def _eligibility() -> Eligibility:
    return Eligibility(
        minimum_age="18 Years",
        inclusion_criteria=["Adults 18-65"],
        exclusion_criteria=["Pregnancy"],
    )


def _core() -> CoreFields:
    return CoreFields(
        allocation="randomized",
        intervention_model="parallel",
        masking="double",
        arms=[Arm(label="Drug X", role="experimental", description="200mg")],
        interventions=[Intervention(type="drug", name="Drug X", description="200mg")],
        primary_outcomes=[
            Outcome(role="primary", measure="Change in symptom score", description="x", time_frame="12 weeks"),
        ],
        eligibility=_eligibility(),
        target_enrollment=128,
    )


def _ctgov() -> CtGovDraft:
    return CtGovDraft(
        brief_title="Drug X for Y",
        official_title="A Phase 2 Study of Drug X in Y disease",
        study_type="interventional",
        primary_purpose="treatment",
        phase="phase_2",
        allocation="randomized",
        intervention_model="parallel",
        masking="double",
        arms=[Arm(label="Drug X", role="experimental", description="200mg")],
        interventions=[Intervention(type="drug", name="Drug X", description="200mg")],
        primary_outcomes=[
            Outcome(role="primary", measure="x", description="x", time_frame="12 weeks"),
        ],
        secondary_outcomes=[],
        eligibility=_eligibility(),
        target_enrollment=128,
        enrollment_type="anticipated",
        locations=[],
        lead_sponsor=_sponsor(),
        conditions=[Condition(name="Y")],
    )


def _euctr() -> EuCtrDraft:
    return EuCtrDraft(
        full_title="Drug X for Y in EU",
        public_title="Drug X for Y",
        trial_type="interventional",
        therapeutic_area="Y disease",
        phase="phase_2",
        allocation="randomized",
        intervention_model="parallel",
        masking="double",
        arms=[Arm(label="Drug X", role="experimental", description="200mg")],
        interventions=[Intervention(type="drug", name="Drug X", description="200mg")],
        primary_endpoints=[
            Outcome(role="primary", measure="x", description="x", time_frame="12 weeks"),
        ],
        eligibility=_eligibility(),
        target_enrollment_eu=100,
        target_enrollment_global=128,
        sponsor=_sponsor(),
        conditions=[Condition(name="Y")],
    )


# ── Assembler ────────────────────────────────────────────────────────────


def test_assembler_returns_none_when_no_registration_turn() -> None:
    messages = [
        _msg(role="user", input_text="x"),
        _msg(role="assistant", final_answer='{"kind": "answer", "text": "hi"}'),
    ]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_picks_up_each_stage() -> None:
    intake = _intake()
    core = _core()
    ctgov = _ctgov()
    euctr = _euctr()
    messages = [
        _msg(role="assistant", final_answer=intake.model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=core.model_dump_json()),
        _msg(role="assistant", msg_id="m3", final_answer=ctgov.model_dump_json()),
        _msg(role="assistant", msg_id="m4", final_answer=euctr.model_dump_json()),
    ]
    data = assemble_report_data("thread-1", messages)
    assert data is not None
    assert data.intake is not None
    assert data.core is not None
    assert data.ctgov is not None
    assert data.euctr is not None


def test_assembler_prefers_final_document_when_present() -> None:
    intake = _intake()
    doc = RegistrationDocument(
        intake=intake,
        core=_core(),
        ctgov=_ctgov(),
        euctr=_euctr(),
        background_paragraph="x",
        is_final=True,
    )
    messages = [
        _msg(role="assistant", final_answer=intake.model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=doc.model_dump_json()),
    ]
    data = assemble_report_data("thread-1", messages)
    assert data is not None
    assert data.document is not None
    assert data.document.is_final is True


# ── Builders ─────────────────────────────────────────────────────────────


def test_build_pdf_emits_pdf_signature() -> None:
    doc = RegistrationDocument(
        intake=_intake(),
        core=_core(),
        ctgov=_ctgov(),
        euctr=_euctr(),
        background_paragraph="Disease Y is widespread.",
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    data = assemble_report_data("thread-1", messages)
    assert data is not None
    pdf = build_pdf(data, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")


def test_build_docx_emits_zip_signature() -> None:
    doc = RegistrationDocument(
        intake=_intake(),
        core=_core(),
        ctgov=_ctgov(),
        euctr=_euctr(),
        background_paragraph="x",
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    data = assemble_report_data("thread-1", messages)
    assert data is not None
    docx = build_docx(data, images_dir=Path("/tmp"))
    assert docx[:4] == b"PK\x03\x04"
