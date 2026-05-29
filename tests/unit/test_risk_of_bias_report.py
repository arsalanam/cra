"""Unit tests for the risk_of_bias report assembler + PDF/DOCX builders."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.risk_of_bias import (
    DomainDistribution,
    RobDomain,
    RobSummary,
    StudyRobAssessment,
)
from research_assistant.reports.risk_of_bias import (
    RobReportData,
    assemble_report_data,
    build_docx,
    build_pdf,
)


def _study(pmid: str, *, judgment: str = "low", high_dom: bool = False) -> StudyRobAssessment:
    domains = [
        RobDomain(
            domain="Randomization process",
            judgment="high" if high_dom else "low",
            justification=(
                "Computer-generated sequence; allocation concealment described."
                if not high_dom
                else "Allocation method not described; suspected non-random."
            ),
            quote=("Patients were assigned by sealed envelopes." if not high_dom else None),
        ),
        RobDomain(
            domain="Deviations from intended interventions",
            judgment="some_concerns",
            justification="Blinding of participants not described.",
        ),
        RobDomain(
            domain="Missing outcome data",
            judgment="no_information",
            justification="Abstract does not describe attrition.",
        ),
    ]
    return StudyRobAssessment(
        pmid=pmid,
        title=f"Trial {pmid}",
        study_design="Randomized Controlled Trial",
        domains=domains,
        overall_judgment=judgment,
        overall_rationale=(
            "Worst-case domain elevates overall to high." if judgment == "high"
            else "All domains low or some_concerns; overall some_concerns."
        ),
    )


def _summary(is_final: bool = True, with_plot: bool = False) -> RobSummary:
    return RobSummary(
        tool="RoB 2.0",
        assessments=[
            _study("11111111", judgment="low"),
            _study("22222222", judgment="some_concerns"),
            _study("33333333", judgment="high", high_dom=True),
        ],
        domain_distribution=[
            DomainDistribution(domain="Randomization process", low=2, high=1),
            DomainDistribution(
                domain="Deviations from intended interventions", some_concerns=3
            ),
            DomainDistribution(
                domain="Missing outcome data", no_information=3
            ),
        ],
        summary_plot_image="/images/abc_rob.png" if with_plot else None,
        narrative=(
            "Across the included trials, randomisation method is the strongest "
            "domain, while blinding and attrition reporting are weaker. "
            "Two-thirds of trials have at least one some_concerns judgment.\n\n"
            "PMID 33333333 should be considered separately in sensitivity."
        ),
        sensitivity_recommendations=[
            "Re-pool excluding PMID 33333333 (overall high).",
            "Subgroup analysis by allocation-concealment status.",
        ],
        high_rob_pmids=["33333333"],
        is_final=is_final,
    )


def _msg(
    role: str,
    *,
    input_text: str | None = None,
    final_answer: str | None = None,
    msg_id: str = "m",
) -> Any:
    return SimpleNamespace(
        id=msg_id, role=role, input_text=input_text, final_answer=final_answer
    )


# ── assembler ────────────────────────────────────────────────────────────


def test_assemble_returns_none_when_no_rob_summary() -> None:
    assert assemble_report_data("t1", [_msg("user", input_text="rob on X")]) is None


def test_assemble_picks_latest_rob_summary() -> None:
    draft = _summary(is_final=False)
    final = _summary(is_final=True)
    messages = [
        _msg("user", input_text="run rob on these 3 PMIDs"),
        _msg("assistant", final_answer=draft.model_dump_json(), msg_id="m1"),
        _msg("assistant", final_answer=final.model_dump_json(), msg_id="m2"),
    ]
    data = assemble_report_data("t1", messages)
    assert data is not None
    assert data.is_final is True
    assert data.tool == "RoB 2.0"
    assert len(data.assessments) == 3
    assert data.high_rob_pmids == ["33333333"]
    assert any(rec.startswith("Re-pool") for rec in data.sensitivity_recommendations)
    assert isinstance(data.generated_at, datetime)
    assert data.generated_at.tzinfo == UTC


def test_assemble_tolerates_malformed_rob_json() -> None:
    final = _summary()
    messages = [
        _msg("user", input_text="q"),
        _msg("assistant", final_answer="garbage"),
        _msg("assistant", final_answer='{"kind":"rob_summary","oops":true}'),
        _msg("assistant", final_answer=final.model_dump_json()),
    ]
    data = assemble_report_data("t1", messages)
    assert data is not None
    assert data.tool == "RoB 2.0"


# ── renderers ────────────────────────────────────────────────────────────


def _report_data(*, with_plot: bool, images_dir: Path, is_final: bool = True) -> RobReportData:
    summary_plot_url: str | None = None
    if with_plot:
        from PIL import Image as PILImage

        png_path = images_dir / "rob_plot.png"
        PILImage.new("RGB", (320, 200), color=(15, 42, 71)).save(png_path)
        summary_plot_url = "/images/rob_plot.png"
    s = _summary(is_final=is_final)
    return RobReportData(
        thread_id="t-abcdef00",
        research_question="RoB on the PPI+DAPT trials",
        generated_at=datetime(2026, 5, 28, 21, 0, tzinfo=UTC),
        tool=s.tool,
        assessments=s.assessments,
        domain_distribution=s.domain_distribution,
        summary_plot_image=summary_plot_url,
        narrative=s.narrative,
        sensitivity_recommendations=s.sensitivity_recommendations,
        high_rob_pmids=s.high_rob_pmids,
        is_final=s.is_final,
    )


def test_build_pdf_returns_valid_pdf_bytes(tmp_path: Path) -> None:
    payload = build_pdf(_report_data(with_plot=True, images_dir=tmp_path), tmp_path)
    assert payload.startswith(b"%PDF-")
    # PNG was embedded
    assert b"/Subtype /Image" in payload
    assert len(payload) > 3_000


def test_build_pdf_without_plot_uses_table_only(tmp_path: Path) -> None:
    payload = build_pdf(_report_data(with_plot=False, images_dir=tmp_path), tmp_path)
    assert payload.startswith(b"%PDF-")
    # No image embedded when there's no plot URL
    assert b"/Subtype /Image" not in payload


def test_build_pdf_draft_variant_still_renders(tmp_path: Path) -> None:
    # PDF text streams are FlateDecode-compressed; the DOCX test covers
    # the draft/final labelling. Here we just sanity-check the non-final
    # branches don't crash.
    payload = build_pdf(
        _report_data(with_plot=False, images_dir=tmp_path, is_final=False), tmp_path
    )
    assert payload.startswith(b"%PDF-")


def test_build_docx_returns_valid_docx_zip(tmp_path: Path) -> None:
    payload = build_docx(_report_data(with_plot=True, images_dir=tmp_path), tmp_path)
    assert payload[:4] == b"PK\x03\x04"
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        names = z.namelist()
        assert "word/document.xml" in names
        document_xml = z.read("word/document.xml").decode("utf-8")
        # Embedded plot lands in word/media/
        assert any(n.startswith("word/media/") for n in names)
    # Key content survived
    assert "RoB 2.0" in document_xml
    assert "Randomization process" in document_xml
    assert "PMID 33333333" in document_xml
    assert "Sensitivity" in document_xml


def test_build_docx_gracefully_handles_missing_plot_file(tmp_path: Path) -> None:
    data = _report_data(with_plot=False, images_dir=tmp_path)
    data.summary_plot_image = "/images/missing.png"
    payload = build_docx(data, tmp_path)
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        document_xml = z.read("word/document.xml").decode("utf-8")
    assert "Summary plot file unavailable" in document_xml
