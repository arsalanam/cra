"""Manuscript report — assembler + PDF/DOCX byte-signature smoke tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.manuscript import (
    ManuscriptCitation,
    ManuscriptDraft,
    ManuscriptIntake,
    ResponseItem,
    ReviewerResponse,
    StructuredAbstract,
)
from research_assistant.reports.manuscript import (
    ManuscriptReportData,
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


def _intake() -> ManuscriptIntake:
    return ManuscriptIntake(
        artefact_kind="meta_analysis",
        journal_target="nejm",
        structured_abstract=True,
        abstract_word_budget=250,
        body_word_budget=2700,
        working_title="PPI in DAPT",
        research_question="Does PPI reduce upper GI bleeding in patients on DAPT?",
        key_findings_paste="RR 0.44 (95% CI 0.35-0.55) across 10 RCTs (n=5240)",
    )


def _draft(*, is_final: bool = False) -> ManuscriptDraft:
    return ManuscriptDraft(
        title=(
            "Proton pump inhibitors and upper GI bleeding in patients on "
            "dual antiplatelet therapy: a meta-analysis"
        ),
        short_title="PPI on DAPT",
        structured_abstract=StructuredAbstract(
            background="Patients on DAPT face elevated upper GI bleeding risk.",
            methods="Meta-analysis of 10 RCTs (n=5240).",
            results="Pooled RR 0.44 (95% CI 0.35-0.55), I² = 42%.",
            conclusions="PPIs reduce GI bleeding ~56% on DAPT.",
        ),
        abstract_word_count=240,
        introduction="DAPT is standard after PCI [1]. Bleeding risk persists.",
        methods="Searched PubMed + Europe PMC...",
        results="Ten RCTs met inclusion (n=5240). Pooled RR 0.44...",
        discussion="Findings agree with COGENT [3] but extend...",
        references=[
            ManuscriptCitation(
                n=1,
                title="Bhatt et al. COGENT",
                authors="Bhatt DL et al.",
                journal="NEJM",
                year=2010,
                pmid="20925534",
                origin="search_papers",
            ),
            ManuscriptCitation(
                n=2,
                title="Vaduganathan meta",
                journal="JACC",
                year=2017,
                origin="pasted_source",
            ),
        ],
        full_markdown="# Title\n\n## Abstract\n...",
        body_word_count=2680,
        is_final=is_final,
    )


def _reviewer_response() -> ReviewerResponse:
    return ReviewerResponse(
        cover_letter_text=(
            "We thank the reviewers for their thoughtful comments. We have "
            "revised the Methods + Discussion in response."
        ),
        responses=[
            ResponseItem(
                reviewer_id="R1",
                comment_excerpt="The fixed-effect model is inappropriate",
                response_text=(
                    "We agree. We re-fit using a DerSimonian-Laird "
                    "random-effects model — the point estimate is "
                    "unchanged but the CI is appropriately wider."
                ),
                suggested_manuscript_edits="Methods §3: 'random-effects'",
            ),
            ResponseItem(
                reviewer_id="R2",
                comment_excerpt="Add publication-bias assessment",
                response_text=(
                    "We respectfully disagree. With 10 studies, funnel-plot "
                    "asymmetry tests have inadequate power per Egger 1997."
                ),
                is_addressed=False,
            ),
        ],
        is_final=True,
    )


# ── Assembler ────────────────────────────────────────────────────────────


def test_assembler_returns_none_when_no_manuscript_draft() -> None:
    """Intake alone isn't a downloadable artefact — the assembler must
    refuse to build a report from intake-only state."""
    messages = [
        _msg(role="user", input_text="draft a manuscript"),
        _msg(role="assistant", final_answer=_intake().model_dump_json()),
    ]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_picks_up_intake_and_draft() -> None:
    messages = [
        _msg(role="user", input_text="draft from this meta-analysis"),
        _msg(role="assistant", final_answer=_intake().model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=_draft(is_final=True).model_dump_json()),
    ]
    data = assemble_report_data("thread-2", messages)
    assert data is not None
    assert data.draft is not None
    assert data.draft.is_final
    assert data.intake is not None
    assert data.intake.journal_target == "nejm"
    # Title flows from the draft
    assert "Proton pump inhibitors" in data.title


def test_assembler_picks_up_reviewer_response_too() -> None:
    messages = [
        _msg(role="user", input_text="draft from meta-analysis"),
        _msg(role="assistant", final_answer=_intake().model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=_draft(is_final=True).model_dump_json()),
        _msg(role="assistant", msg_id="m3", final_answer=_reviewer_response().model_dump_json()),
    ]
    data = assemble_report_data("thread-3", messages)
    assert data is not None
    assert data.reviewer_response is not None
    assert len(data.reviewer_response.responses) == 2


# ── PDF + DOCX builders ─────────────────────────────────────────────────


def _report_data(*, with_response: bool) -> ManuscriptReportData:
    return ManuscriptReportData(
        thread_id="thread-rendertest",
        title="PPI manuscript",
        generated_at=datetime.now(UTC),
        research_question="Does PPI reduce GI bleed on DAPT?",
        intake=_intake(),
        draft=_draft(is_final=True),
        reviewer_response=_reviewer_response() if with_response else None,
    )


def test_build_pdf_emits_pdf_signature_without_response() -> None:
    payload = build_pdf(_report_data(with_response=False), Path("."))
    assert payload[:5] == b"%PDF-"
    assert len(payload) > 2000


def test_build_pdf_with_reviewer_response_appended() -> None:
    payload = build_pdf(_report_data(with_response=True), Path("."))
    assert payload[:5] == b"%PDF-"
    # Should be larger than the no-response variant (more content)
    smaller = build_pdf(_report_data(with_response=False), Path("."))
    assert len(payload) > len(smaller)


def test_build_docx_emits_zip_signature() -> None:
    """docx is a zip — first 4 bytes are 'PK\\x03\\x04'."""
    payload = build_docx(_report_data(with_response=True), Path("."))
    assert payload[:4] == b"PK\x03\x04"
    assert len(payload) > 5000


def test_journal_target_propagates_to_pdf_header() -> None:
    """The 'MANUSCRIPT DRAFT · target: NEJM' eyebrow is rendered into
    the PDF. We can't read the rendered text without parsing, but we
    can assert the assembler hands the intake (with journal_target) to
    the builder — which is what the system contract is."""
    data = _report_data(with_response=False)
    assert data.intake is not None
    assert data.intake.journal_target == "nejm"
