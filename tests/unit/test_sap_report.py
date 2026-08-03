"""SAP report — assembler + PDF + DOCX byte-signature smoke tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.sap import (
    AnalysisPlan,
    EffectAssumptions,
    InterimAnalysis,
    PicotTable,
    SampleSizeResult,
    SapCitation,
    SapDocument,
)
from research_assistant.reports.sap import (
    SapReportData,
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
    """SimpleNamespace stand-in for persistence.models.Message — assembler
    only reads role, input_text, final_answer, id."""
    return SimpleNamespace(id=msg_id, role=role, input_text=input_text, final_answer=final_answer)


def _picot() -> PicotTable:
    return PicotTable(
        population="adults with HTN",
        intervention="drug X",
        comparator="placebo",
        primary_outcome="SBP at 12 weeks",
        secondary_outcomes=["DBP", "MACE at 1 year"],
        timeframe="12 weeks primary; 52 weeks follow-up",
        design="parallel_rct",
        hypothesis_type="superiority",
        outcome_type="continuous",
    )


def _sample_size_result(p: PicotTable) -> SampleSizeResult:
    return SampleSizeResult(
        picot=p,
        assumptions=EffectAssumptions(
            control_mean=130.0,
            intervention_mean=125.0,
            standard_deviation=10.0,
        ),
        n_per_arm_intervention=64,
        n_per_arm_control=64,
        n_total=128,
        formula_name="Two-sample t-test (Cohen, common SD)",
        formula_reference="Cohen 1988 §2.4",
    )


def _analysis_plan() -> AnalysisPlan:
    return AnalysisPlan(
        primary_analysis_description=(
            "Two-sample t-test comparing intervention vs control on SBP at "
            "12 weeks, stratified by enrolment site, adjusted for baseline SBP."
        ),
        primary_test_name="Two-sample t-test",
        multiplicity_strategy="none",
        missing_data_strategy="multiple_imputation_mar",
        interim_analyses=[
            InterimAnalysis(at_fraction=0.5, rule="O'Brien-Fleming superiority"),
        ],
        sensitivity_analyses=["Per-protocol exclusion of major violators"],
        subgroup_analyses=["Age <65 vs ≥65"],
        safety_monitoring="DMC reviews quarterly; SAEs reported within 24h.",
    )


# ── Assembler ────────────────────────────────────────────────────────────


def test_assembler_returns_none_when_no_sap_turn() -> None:
    messages = [
        _msg(role="user", input_text="research question"),
        _msg(role="assistant", final_answer='{"kind": "answer", "text": "hi"}'),
    ]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_picks_up_each_stage() -> None:
    p = _picot()
    s = _sample_size_result(p)
    ap = _analysis_plan()
    messages = [
        _msg(role="user", input_text="Power the new HTN trial"),
        _msg(role="assistant", final_answer=p.model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=s.model_dump_json()),
        _msg(role="assistant", msg_id="m3", final_answer=ap.model_dump_json()),
    ]
    data = assemble_report_data("thread-2", messages)
    assert data is not None
    assert data.picot is not None
    assert data.picot.intervention == "drug X"
    assert data.sample_size is not None
    assert data.sample_size.n_total == 128
    assert data.analysis_plan is not None
    assert data.sap_document is None  # never finalized
    assert "Statistical Analysis Plan" in data.title or "drug X" in data.title


def test_assembler_prefers_final_document_when_present() -> None:
    """When a SapDocument is present, its embedded picot/sample_size/
    analysis_plan override the standalone earlier-stage turns — the
    final document is the source of truth for the rendered report."""
    p = _picot()
    s = _sample_size_result(p)
    ap = _analysis_plan()
    doc = SapDocument(
        picot=p,
        sample_size=s,
        analysis_plan=ap,
        background="The HTN burden is significant in this population.",
        references=[
            SapCitation(n=1, title="WHO HTN report", url="https://who.int", origin="web_search"),
        ],
        full_markdown="# SAP\n\nFull content here.",
        is_final=True,
    )
    messages = [
        _msg(role="user", input_text="Build a SAP"),
        _msg(role="assistant", final_answer=doc.model_dump_json()),
    ]
    data = assemble_report_data("thread-3", messages)
    assert data is not None
    assert data.sap_document is not None
    assert data.sap_document.is_final
    assert len(data.sap_document.references) == 1


# ── PDF + DOCX byte signatures ──────────────────────────────────────────


def _report_data(*, with_doc: bool) -> SapReportData:
    p = _picot()
    s = _sample_size_result(p)
    ap = _analysis_plan()
    doc = None
    if with_doc:
        doc = SapDocument(
            picot=p,
            sample_size=s,
            analysis_plan=ap,
            background="Background paragraph.",
            references=[
                SapCitation(n=1, title="WHO report", origin="web_search", url="https://who.int"),
            ],
            full_markdown="# SAP\nFull text.",
            is_final=True,
        )
    return SapReportData(
        thread_id="thread-rendertest",
        title="SAP — drug X in adults with HTN",
        research_question="Does drug X lower SBP vs placebo?",
        generated_at=datetime.now(UTC),
        picot=p,
        sample_size=s,
        analysis_plan=ap,
        sap_document=doc,
    )


def test_build_pdf_emits_pdf_signature_without_document() -> None:
    payload = build_pdf(_report_data(with_doc=False), Path("."))
    assert payload[:5] == b"%PDF-"
    assert len(payload) > 1000  # not an empty stub


def test_build_pdf_with_full_document() -> None:
    payload = build_pdf(_report_data(with_doc=True), Path("."))
    assert payload[:5] == b"%PDF-"
    assert len(payload) > 1500


def test_build_docx_emits_zip_signature() -> None:
    """docx files are zip archives — first 4 bytes are 'PK\\x03\\x04'."""
    payload = build_docx(_report_data(with_doc=True), Path("."))
    assert payload[:4] == b"PK\x03\x04"
    assert len(payload) > 5000
