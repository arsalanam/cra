"""Unit tests for the sr_protocol report assembler + PDF/DOCX builders."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.meta_analysis import PicoTable
from research_assistant.domain.sr_protocol import (
    Citation,
    EligibilityCriteria,
    ProsperoFieldMap,
    ProtocolDocument,
    ProtocolMethods,
    RobToolChoice,
    SynthesisPlan,
)
from research_assistant.reports.sr_protocol import (
    SrProtocolReportData,
    assemble_report_data,
    build_docx,
    build_pdf,
)


def _methods() -> ProtocolMethods:
    return ProtocolMethods(
        title=(
            "Sodium-glucose co-transporter-2 inhibitors for prevention of "
            "heart-failure hospitalisation in adults with type 2 diabetes: a "
            "systematic review and meta-analysis of randomised trials"
        ),
        review_type="intervention",
        pico=PicoTable(
            population="Adults ≥18 with T2DM",
            intervention="SGLT2 inhibitor",
            comparison="Placebo or active comparator",
            outcomes=["Heart-failure hospitalisation", "MACE"],
            study_types=["Randomized Controlled Trial"],
        ),
        eligibility=EligibilityCriteria(
            inclusion=["RCTs of SGLT2i", "≥12 weeks follow-up"],
            exclusion=["Type 1 diabetes"],
            study_designs=["Randomized Controlled Trial"],
            language=["English"],
            date_range="2010–present",
        ),
        rob_tool=RobToolChoice(tool="RoB 2.0", rationale="RCTs-only cohort."),
        effect_measures_plan={
            "Heart-failure hospitalisation": "RR",
            "MACE": "RR",
        },
        synthesis_plan=SynthesisPlan(
            primary_method="random_effects_meta",
            planned_subgroups=["Baseline HbA1c"],
        ),
        use_grade=True,
    )


def _document(is_final: bool = True) -> ProtocolDocument:
    return ProtocolDocument(
        methods=_methods(),
        background=(
            "Type 2 diabetes mellitus affects an estimated 537 million adults "
            "worldwide [1]. Landmark trials of SGLT2 inhibitors have shown "
            "consistent reductions in heart-failure hospitalisation [2].\n\n"
            "Current ESC, ADA, and AHA guidelines all recommend SGLT2i as a "
            "preferred glucose-lowering agent for adults with T2DM and "
            "established heart failure [3]."
        ),
        references=[
            Citation(
                text="IDF Diabetes Atlas, 10th edition. 2021.",
                origin="web_search",
                url="https://diabetesatlas.org/",
            ),
            Citation(
                text="EMPA-REG OUTCOME investigators. NEJM. 2015;373:2117.",
                pmid="26378978",
                origin="search_papers",
            ),
            Citation(
                text="2023 ESC guidelines for diabetes. EHJ. 2023;44:4043.",
                pmid="37622663",
                origin="search_papers",
            ),
        ],
        full_markdown="# (markdown body omitted for tests)",
        prospero_field_map=[
            ProsperoFieldMap(field="Review title", content=_methods().title),
            ProsperoFieldMap(
                field="Named contact",
                content="[USER INPUT NEEDED: principal investigator's name + email]",
            ),
        ],
        notes=None,
        is_final=is_final,
    )


def _msg(
    role: str,
    *,
    input_text: str | None = None,
    final_answer: str | None = None,
    msg_id: str = "m",
) -> Any:
    return SimpleNamespace(id=msg_id, role=role, input_text=input_text, final_answer=final_answer)


# ── assembler ────────────────────────────────────────────────────────────


def test_assemble_returns_none_when_no_protocol_document() -> None:
    messages = [_msg("user", input_text="draft a protocol on X")]
    assert assemble_report_data("t1", messages) is None


def test_assemble_picks_latest_protocol_document() -> None:
    old_doc = _document(is_final=False)
    # Simulate an iterative refinement — two ProtocolDocument turns; the
    # latter (final) should win.
    final_doc = _document(is_final=True)
    messages = [
        _msg("user", input_text="draft a protocol on SGLT2i for HF"),
        _msg("assistant", final_answer=old_doc.model_dump_json(), msg_id="m1"),
        _msg("assistant", final_answer=final_doc.model_dump_json(), msg_id="m2"),
    ]
    data = assemble_report_data("t1", messages)
    assert data is not None
    assert data.thread_id == "t1"
    assert data.research_question.startswith("draft a protocol")
    assert data.is_final is True
    assert "SGLT2 inhibitor" in data.methods.pico.intervention
    assert len(data.references) == 3
    assert any(c.origin == "search_papers" for c in data.references)
    assert any(r.content.startswith("[USER INPUT NEEDED") for r in data.prospero_field_map)
    assert isinstance(data.generated_at, datetime)
    assert data.generated_at.tzinfo == UTC


def test_assemble_tolerates_malformed_protocol_json() -> None:
    final_doc = _document()
    messages = [
        _msg("user", input_text="q"),
        _msg("assistant", final_answer="not json"),
        _msg("assistant", final_answer='{"kind":"protocol_document","oops":true}'),
        _msg("assistant", final_answer=final_doc.model_dump_json()),
    ]
    data = assemble_report_data("t1", messages)
    assert data is not None
    assert data.is_final is True


# ── renderers ────────────────────────────────────────────────────────────


def _report_data(is_final: bool = True) -> SrProtocolReportData:
    doc = _document(is_final=is_final)
    return SrProtocolReportData(
        thread_id="t-abcdef00",
        research_question="Do SGLT2 inhibitors reduce HF hospitalisation in T2DM?",
        generated_at=datetime(2026, 5, 28, 21, 0, tzinfo=UTC),
        methods=doc.methods,
        background=doc.background,
        references=doc.references,
        prospero_field_map=doc.prospero_field_map,
        notes=doc.notes,
        is_final=doc.is_final,
    )


def test_build_pdf_returns_valid_pdf_bytes(tmp_path: Path) -> None:
    payload = build_pdf(_report_data(), tmp_path)
    assert payload.startswith(b"%PDF-")
    assert len(payload) > 3_000


def test_build_pdf_draft_variant_still_renders(tmp_path: Path) -> None:
    # PDF text streams are FlateDecode-compressed, so we can't byte-grep for
    # "DRAFT" / "FINAL" — the DOCX test covers the draft/final labelling.
    # Here we just sanity-check that the non-final variant still produces a
    # valid PDF without crashing on the conditional branches.
    payload = build_pdf(_report_data(is_final=False), tmp_path)
    assert payload.startswith(b"%PDF-")


def test_build_docx_returns_valid_docx_zip(tmp_path: Path) -> None:
    payload = build_docx(_report_data(), tmp_path)
    assert payload[:4] == b"PK\x03\x04"
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        assert "word/document.xml" in z.namelist()
        document_xml = z.read("word/document.xml").decode("utf-8")
    # Key content survives the render
    assert "SGLT2 inhibitor" in document_xml
    assert "PROSPERO" in document_xml
    assert "USER INPUT NEEDED" in document_xml
    assert "References" in document_xml


def test_build_docx_marks_draft_when_not_final(tmp_path: Path) -> None:
    payload = build_docx(_report_data(is_final=False), tmp_path)
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        document_xml = z.read("word/document.xml").decode("utf-8")
    assert "DRAFT" in document_xml
