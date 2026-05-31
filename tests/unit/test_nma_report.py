"""NMA report — assembler + PDF + DOCX byte-signature smoke tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.nma import (
    LeagueRow,
    LeagueTable,
    NetworkEdge,
    NetworkGraph,
    NetworkNode,
    NmaResults,
    PicoNetwork,
    SucraRow,
)
from research_assistant.reports.nma import (
    assemble_report_data,
    build_docx,
    build_pdf,
)


def _msg(
    *,
    role: str = "assistant",
    msg_id: str = "m1",
    final_answer: str | None = None,
) -> Any:
    return SimpleNamespace(
        id=msg_id, role=role, input_text=None, final_answer=final_answer
    )


def _results() -> NmaResults:
    return NmaResults(
        pico=PicoNetwork(
            population="Adults with AF",
            interventions=["Placebo", "Drug A", "Drug B", "Drug C"],
            outcome="Stroke prevention",
            effect_measure="OR",
        ),
        backend="frequentist",
        league_table=LeagueTable(
            effect_measure="OR",
            rows=[
                LeagueRow(
                    row_intervention="Drug A",
                    col_intervention="Placebo",
                    effect=0.72,
                    ci_lower=0.55,
                    ci_upper=0.94,
                    n_direct_trials=3,
                    n_indirect_paths=0,
                ),
                LeagueRow(
                    row_intervention="Drug B",
                    col_intervention="Placebo",
                    effect=0.85,
                    ci_lower=0.66,
                    ci_upper=1.10,
                    n_direct_trials=2,
                    n_indirect_paths=0,
                ),
                LeagueRow(
                    row_intervention="Drug C",
                    col_intervention="Placebo",
                    effect=0.91,
                    ci_lower=0.70,
                    ci_upper=1.18,
                    n_direct_trials=1,
                    n_indirect_paths=2,
                ),
            ],
        ),
        sucra=[
            SucraRow(intervention="Drug A", sucra=0.82, mean_rank=1.5, rank=1),
            SucraRow(intervention="Drug B", sucra=0.55, mean_rank=2.4, rank=2),
            SucraRow(intervention="Drug C", sucra=0.38, mean_rank=3.0, rank=3),
            SucraRow(intervention="Placebo", sucra=0.25, mean_rank=3.1, rank=4),
        ],
        network=NetworkGraph(
            nodes=[
                NetworkNode(intervention="Placebo", n_studies=6, n_participants=1800),
                NetworkNode(intervention="Drug A", n_studies=3, n_participants=900),
                NetworkNode(intervention="Drug B", n_studies=2, n_participants=600),
                NetworkNode(intervention="Drug C", n_studies=1, n_participants=300),
            ],
            edges=[
                NetworkEdge(source_intervention="Placebo", target_intervention="Drug A", n_trials=2),
                NetworkEdge(source_intervention="Placebo", target_intervention="Drug B", n_trials=2),
                NetworkEdge(source_intervention="Drug A", target_intervention="Drug C", n_trials=1),
            ],
        ),
        studies_included=["12345", "12346", "12347"],
        interpretation="Drug A has the highest SUCRA (0.82) with a 28% relative odds reduction vs placebo.",
        caveats=["Sparse network for Drug C", "Single trial on Drug A vs Drug C edge"],
    )


def test_assembler_returns_none_when_no_results_turn() -> None:
    messages = [_msg(role="assistant", final_answer='{"kind": "answer"}')]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_picks_up_results() -> None:
    msg = _msg(role="assistant", final_answer=_results().model_dump_json())
    out = assemble_report_data("thread-1", [msg])
    assert out is not None
    assert out.results is not None
    assert out.results.backend == "frequentist"


def test_build_pdf_emits_pdf_signature() -> None:
    msg = _msg(role="assistant", final_answer=_results().model_dump_json())
    out = assemble_report_data("thread-1", [msg])
    assert out is not None
    pdf = build_pdf(out, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")


def test_build_docx_emits_zip_signature() -> None:
    msg = _msg(role="assistant", final_answer=_results().model_dump_json())
    out = assemble_report_data("thread-1", [msg])
    assert out is not None
    docx = build_docx(out, images_dir=Path("/tmp"))
    assert docx[:4] == b"PK\x03\x04"


def test_build_pdf_when_no_results_returns_empty_bytes() -> None:
    """Defensive: empty bytes when no results — assembler returns None
    in practice, but this is a safety net for the dispatch endpoint."""
    from datetime import UTC, datetime

    from research_assistant.reports.nma import NmaReportData

    data = NmaReportData(
        thread_id="t",
        title="x",
        generated_at=datetime.now(UTC),
        results=None,
    )
    assert build_pdf(data) == b""
    assert build_docx(data) == b""
