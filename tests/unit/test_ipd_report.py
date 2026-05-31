"""IPD report — assembler + PDF + DOCX byte-signature smoke tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.ipd import (
    IpdDocument,
    IpdIntake,
    IpdMainResults,
    IpdPerTrialEffect,
    IpdPooledEffect,
    IpdSubgroupLevel,
    IpdSubgroupResults,
)
from research_assistant.reports.ipd import (
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
    return SimpleNamespace(id=msg_id, role=role, input_text=None, final_answer=final_answer)


def _pooled(method: str = "MixedLM REML", effect: float = 0.72) -> IpdPooledEffect:
    return IpdPooledEffect(
        effect=effect,
        ci_lower=effect - 0.18,
        ci_upper=effect + 0.22,
        p_value=0.018,
        n_trials=6,
        n_subjects=4250,
        i_squared=28.0,
        tau_squared=0.05,
        method=method,
    )


def _main_results() -> IpdMainResults:
    return IpdMainResults(
        effect_measure="OR",
        one_stage=_pooled("MixedLM REML"),
        two_stage=_pooled("DerSimonian-Laird", effect=0.74),
        per_trial=[
            IpdPerTrialEffect(
                trial_id="A",
                n_subjects=500,
                effect=0.7,
                ci_lower=0.5,
                ci_upper=0.95,
                se=0.15,
            ),
            IpdPerTrialEffect(
                trial_id="B",
                n_subjects=700,
                effect=0.75,
                ci_lower=0.55,
                ci_upper=1.0,
                se=0.13,
            ),
            IpdPerTrialEffect(
                trial_id="C",
                n_subjects=1100,
                effect=0.78,
                ci_lower=0.62,
                ci_upper=0.97,
                se=0.10,
            ),
        ],
        discrepancy_note="One-stage and two-stage estimates agree closely (Δlog≈0.03).",
    )


def _document() -> IpdDocument:
    return IpdDocument(
        intake=IpdIntake(
            research_question="IPD MA of statins on LDL change across 6 RCTs",
            primary_endpoint="LDL change at 12 weeks",
            effect_measure="OR",
        ),
        main_results=_main_results(),
        subgroup_results=[
            IpdSubgroupResults(
                subgroup_variable="sex",
                levels=[
                    IpdSubgroupLevel(
                        level_label="F",
                        n_trials=5,
                        n_subjects=2100,
                        effect=0.68,
                        ci_lower=0.48,
                        ci_upper=0.95,
                    ),
                    IpdSubgroupLevel(
                        level_label="M",
                        n_trials=5,
                        n_subjects=2150,
                        effect=0.76,
                        ci_lower=0.55,
                        ci_upper=1.05,
                    ),
                ],
                interaction_p_value=0.42,
            ),
        ],
        studies_included=["A", "B", "C"],
        interpretation="Treatment reduces odds by ~28% (OR 0.72) with no sex interaction.",
        direction="favours_intervention",
        caveats=["Binary endpoint uses fixed-effects logistic approximation"],
    )


def test_assembler_returns_none_when_no_ipd_turn() -> None:
    messages = [_msg(role="assistant", final_answer='{"kind": "answer"}')]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_picks_up_main_results_alone() -> None:
    msg = _msg(role="assistant", final_answer=_main_results().model_dump_json())
    out = assemble_report_data("thread-1", [msg])
    assert out is not None
    assert out.main_results is not None
    assert out.document is None


def test_assembler_prefers_final_document_when_present() -> None:
    doc = _document()
    doc.is_final = True
    msg = _msg(role="assistant", final_answer=doc.model_dump_json())
    out = assemble_report_data("thread-1", [msg])
    assert out is not None
    assert out.document is not None
    assert out.document.is_final is True
    assert len(out.subgroup_results) == 1


def test_build_pdf_emits_pdf_signature() -> None:
    msg = _msg(role="assistant", final_answer=_document().model_dump_json())
    out = assemble_report_data("thread-1", [msg])
    assert out is not None
    pdf = build_pdf(out, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")


def test_build_docx_emits_zip_signature() -> None:
    msg = _msg(role="assistant", final_answer=_document().model_dump_json())
    out = assemble_report_data("thread-1", [msg])
    assert out is not None
    docx = build_docx(out, images_dir=Path("/tmp"))
    assert docx[:4] == b"PK\x03\x04"


def test_build_pdf_with_main_only_renders() -> None:
    """Report should render even before the final document — useful for
    previewing main results before subgroup runs."""
    msg = _msg(role="assistant", final_answer=_main_results().model_dump_json())
    out = assemble_report_data("thread-1", [msg])
    assert out is not None
    pdf = build_pdf(out, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")
