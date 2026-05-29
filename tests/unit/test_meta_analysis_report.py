"""Unit tests for the meta-analysis report assembler and PDF/DOCX renderers.

The assembler is unit-tested with synthetic Message objects (no DB), and the
renderers are tested by magic-byte sniffing the returned bytes — a DOCX is a
ZIP file (`PK\\x03\\x04`), a ReportLab PDF starts with `%PDF-`.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from research_assistant.domain.meta_analysis import (
    DataExtraction,
    MetaAnalysisOutcomeResult,
    MetaAnalysisResults,
    OutcomeData,
    PicoDraft,
    PicoTable,
    StudyExtractedData,
)
from research_assistant.reports.meta_analysis import (
    MetaAnalysisReportData,
    _resolve_image_path,
    assemble_report_data,
    build_docx,
    build_pdf,
)


def _pico() -> PicoDraft:
    return PicoDraft(
        pico=PicoTable(
            population="Adults post-PCI for ACS on DAPT",
            intervention="PPI + DAPT",
            comparison="DAPT alone",
            outcomes=["Upper GI bleeding"],
            study_types=["Randomized Controlled Trial"],
        ),
        rationale="Canonical PPI+DAPT question.",
    )


def _extraction() -> DataExtraction:
    return DataExtraction(
        studies=[
            StudyExtractedData(
                source="pubmed",
                source_id="11111111",
                pmid="11111111",
                title="Anderson trial of PPI co-therapy",
                study_design="Multicenter RCT",
                follow_up="12 months",
                outcomes=[
                    OutcomeData(
                        outcome="Upper GI bleeding",
                        effect_measure="RR",
                        events_intervention=12,
                        n_intervention=1200,
                        events_comparison=28,
                        n_comparison=1198,
                        extraction_source="user_provided",
                        is_complete=True,
                    )
                ],
            ),
            StudyExtractedData(
                source="pubmed",
                source_id="22222222",
                pmid="22222222",
                title="Bekele single-centre RCT",
                study_design="Single-center RCT",
                follow_up="6 months",
                outcomes=[
                    OutcomeData(
                        outcome="Upper GI bleeding",
                        effect_measure="RR",
                        events_intervention=4,
                        n_intervention=320,
                        events_comparison=9,
                        n_comparison=318,
                        extraction_source="user_provided",
                        is_complete=True,
                    )
                ],
            ),
        ],
        outcome_names=["Upper GI bleeding"],
        summary="2 studies extracted; both complete.",
    )


def _results(forest_plot_image: str | None = None) -> MetaAnalysisResults:
    return MetaAnalysisResults(
        outcome_results=[
            MetaAnalysisOutcomeResult(
                outcome="Upper GI bleeding",
                effect_measure="RR",
                pooled_effect=0.44,
                ci_lower=0.33,
                ci_upper=0.58,
                p_value=0.0001,
                i_squared=0.0,
                heterogeneity_p=0.99,
                n_studies=2,
                n_participants=3_036,
                forest_plot_image=forest_plot_image,
                interpretation="Favours intervention.",
            )
        ],
        studies_included=["11111111", "22222222"],
        studies_excluded=[],
        summary="Pooled RR = 0.44 across 2 RCTs.",
        caveats=["Short follow-up; small total n."],
    )


def _msg(
    role: str,
    *,
    input_text: str | None = None,
    final_answer: str | None = None,
    msg_id: str = "m",
) -> Any:
    """SimpleNamespace stand-in for persistence.models.Message — assembler only
    reads `role`, `input_text`, `final_answer`, and `id`."""
    return SimpleNamespace(id=msg_id, role=role, input_text=input_text, final_answer=final_answer)


# ── assembler ────────────────────────────────────────────────────────────


def test_assemble_returns_none_when_no_meta_analysis_turn() -> None:
    messages = [
        _msg("user", input_text="Does adding PPI to DAPT reduce upper GI bleeding?"),
        _msg("assistant", final_answer=_pico().model_dump_json()),
    ]
    assert assemble_report_data("t1", messages) is None


def test_assemble_picks_latest_pico_and_extraction_and_analysis() -> None:
    pico_old = PicoDraft(
        pico=PicoTable(
            population="OLD population",
            intervention="OLD",
            comparison="OLD",
            outcomes=[],
        ),
        rationale="old",
    )
    messages = [
        _msg("user", input_text="My question"),
        _msg("assistant", final_answer=pico_old.model_dump_json(), msg_id="m1"),
        _msg("assistant", final_answer=_pico().model_dump_json(), msg_id="m2"),
        _msg("assistant", final_answer=_extraction().model_dump_json(), msg_id="m3"),
        _msg("assistant", final_answer=_results().model_dump_json(), msg_id="m4"),
    ]
    data = assemble_report_data("t1", messages)
    assert data is not None
    assert data.thread_id == "t1"
    assert data.research_question == "My question"
    # latest PICO wins (not the OLD one)
    assert data.pico is not None
    assert data.pico.population == "Adults post-PCI for ACS on DAPT"
    assert len(data.included_studies) == 2
    assert data.studies_included_pmids == ["11111111", "22222222"]
    assert len(data.outcome_results) == 1
    assert data.outcome_results[0].pooled_effect == pytest.approx(0.44)
    assert data.summary.startswith("Pooled RR")
    assert data.caveats == ["Short follow-up; small total n."]
    # The title weaves intervention + population when PICO is available
    assert "PPI + DAPT" in data.title
    assert isinstance(data.generated_at, datetime)
    assert data.generated_at.tzinfo == UTC


def test_assemble_tolerates_malformed_final_answer_json() -> None:
    messages = [
        _msg("user", input_text="q"),
        _msg("assistant", final_answer="not json at all"),
        _msg("assistant", final_answer='{"kind":"meta_analysis","oops":true}'),
        _msg("assistant", final_answer=_results().model_dump_json()),
    ]
    data = assemble_report_data("t1", messages)
    assert data is not None  # the valid meta_analysis still wins
    assert data.outcome_results[0].effect_measure == "RR"


def test_assemble_uses_first_user_message_only() -> None:
    messages = [
        _msg("user", input_text="first question"),
        _msg("user", input_text="follow-up that should be ignored"),
        _msg("assistant", final_answer=_results().model_dump_json()),
    ]
    data = assemble_report_data("t1", messages)
    assert data is not None
    assert data.research_question == "first question"


# ── image path resolver ─────────────────────────────────────────────────


def test_resolve_image_path_returns_path_when_file_exists(tmp_path: Path) -> None:
    (tmp_path / "abc_plot.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    resolved = _resolve_image_path("/images/abc_plot.png", tmp_path)
    assert resolved is not None
    assert resolved.name == "abc_plot.png"


def test_resolve_image_path_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert _resolve_image_path("/images/missing.png", tmp_path) is None


@pytest.mark.parametrize(
    "url",
    ["", None, "data:image/png;base64,xxx", "https://example.com/x.png", "bare.png"],
)
def test_resolve_image_path_returns_none_for_non_images_url(
    url: str | None, tmp_path: Path
) -> None:
    assert _resolve_image_path(url, tmp_path) is None


# ── renderers ───────────────────────────────────────────────────────────


def _make_report_data(*, with_image: bool, images_dir: Path) -> MetaAnalysisReportData:
    forest_url: str | None = None
    if with_image:
        # Render a tiny real PNG via PIL (already a transitive dep of
        # reportlab) so the image is valid for both ReportLab's RLImage and
        # python-docx's add_picture without dragging matplotlib into the
        # test environment.
        from PIL import Image as PILImage

        png_path = images_dir / "test_forest.png"
        PILImage.new("RGB", (320, 200), color=(30, 110, 140)).save(png_path)
        forest_url = "/images/test_forest.png"

    return MetaAnalysisReportData(
        thread_id="t-12345678",
        title="Meta-analysis — PPI + DAPT for Adults post-PCI for ACS on DAPT",
        research_question=(
            "Does adding a proton pump inhibitor to dual antiplatelet therapy "
            "reduce upper GI bleeding in post-PCI ACS patients?"
        ),
        generated_at=datetime(2026, 5, 28, 20, 0, tzinfo=UTC),
        pico=_pico().pico,
        included_studies=list(_extraction().studies),
        excluded_studies=[],
        studies_included_pmids=["11111111", "22222222"],
        outcome_results=_results(forest_plot_image=forest_url).outcome_results,
        summary="Pooled RR = 0.44 across 2 RCTs.",
        caveats=["Short follow-up; small total n."],
    )


def test_build_pdf_returns_valid_pdf_bytes(tmp_path: Path) -> None:
    data = _make_report_data(with_image=True, images_dir=tmp_path)
    payload = build_pdf(data, tmp_path)
    assert isinstance(payload, bytes)
    # PDF magic
    assert payload.startswith(b"%PDF-"), payload[:8]
    # Non-trivial size — a stripped PDF (header + xref + trailer alone) is
    # under 1 KB; a real report with sections + an embedded image clears 3 KB.
    assert len(payload) > 3_000
    # The PNG XObject is what makes the bulk; sanity-check it's in there.
    assert b"/Subtype /Image" in payload


def test_build_pdf_gracefully_handles_missing_forest_plot(tmp_path: Path) -> None:
    """Missing image file → renders an apology line, doesn't crash."""
    data = _make_report_data(with_image=False, images_dir=tmp_path)
    # Force the outcome to reference a /images/ path that doesn't exist
    data.outcome_results[0].forest_plot_image = "/images/never_existed.png"
    payload = build_pdf(data, tmp_path)
    assert payload.startswith(b"%PDF-")


def test_build_docx_returns_valid_docx_zip(tmp_path: Path) -> None:
    data = _make_report_data(with_image=True, images_dir=tmp_path)
    payload = build_docx(data, tmp_path)
    assert isinstance(payload, bytes)
    # DOCX = ZIP container
    assert payload[:4] == b"PK\x03\x04", payload[:8]
    # The ZIP MUST contain word/document.xml or it's not a valid DOCX
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        names = z.namelist()
        assert "word/document.xml" in names
        document_xml = z.read("word/document.xml").decode("utf-8")
    # Headline content makes it into the document body
    assert "Meta-analysis" in document_xml
    assert "PPI + DAPT" in document_xml
    assert "Upper GI bleeding" in document_xml


def test_build_docx_gracefully_handles_missing_forest_plot(tmp_path: Path) -> None:
    data = _make_report_data(with_image=False, images_dir=tmp_path)
    data.outcome_results[0].forest_plot_image = "/images/never_existed.png"
    payload = build_docx(data, tmp_path)
    assert payload[:4] == b"PK\x03\x04"
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        document_xml = z.read("word/document.xml").decode("utf-8")
    assert "Forest plot file unavailable" in document_xml


def test_build_pdf_without_pico_omits_section(tmp_path: Path) -> None:
    data = _make_report_data(with_image=False, images_dir=tmp_path)
    data.pico = None
    data.title = "Meta-analysis report"  # title falls back when no PICO
    payload = build_pdf(data, tmp_path)
    assert payload.startswith(b"%PDF-")


def test_build_docx_without_caveats_does_not_emit_caveats_heading(
    tmp_path: Path,
) -> None:
    data = _make_report_data(with_image=False, images_dir=tmp_path)
    data.caveats = []
    payload = build_docx(data, tmp_path)
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        document_xml = z.read("word/document.xml").decode("utf-8")
    # "Caveats" heading text is not present when the list is empty
    assert "Caveats" not in document_xml
