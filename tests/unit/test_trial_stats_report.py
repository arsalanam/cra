"""trial_stats report — assembler + PDF + DOCX byte-signature smoke tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from research_assistant.domain.trial_stats import (
    AnalysisPopulation,
    AnalysisPopulationsTurn,
    BinaryResult,
    BinaryResultsTurn,
    ContinuousResult,
    ContinuousResultsTurn,
    SubgroupAnalysis,
    SubgroupResultsTurn,
    SubgroupRow,
    TimeToEventResult,
    TimeToEventResultsTurn,
    TrialStatsDocument,
    TrialStatsIntake,
)
from research_assistant.reports.trial_stats import (
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


def _intake() -> TrialStatsIntake:
    return TrialStatsIntake(
        study_id="ABC-2026-001",
        study_name="DrugA in T2DM",
        primary_endpoint="Overall survival",
        arms=["Placebo", "Drug A"],
    )


def _populations() -> AnalysisPopulationsTurn:
    return AnalysisPopulationsTurn(
        populations=[
            AnalysisPopulation(
                kind_name="ITT",
                n_total=200,
                n_per_arm={"Placebo": 100, "Drug A": 100},
                derived_from="ADSL.ITTFL=Y",
                rationale="Every randomised subject.",
            ),
            AnalysisPopulation(
                kind_name="Safety",
                n_total=195,
                n_per_arm={"Placebo": 98, "Drug A": 97},
                derived_from="ADSL.SAFFL=Y",
                rationale="Subjects with any study drug.",
            ),
        ]
    )


def _tte() -> TimeToEventResultsTurn:
    return TimeToEventResultsTurn(
        results=[
            TimeToEventResult(
                paramcd="OS",
                param_label="Overall survival",
                n_subjects=200,
                n_events=85,
                median_event_time="14.2 months",
                hazard_ratio=0.72,
                hr_ci_lower=0.55,
                hr_ci_upper=0.94,
                logrank_p_value=0.014,
                comparison_label="Drug A vs Placebo",
                derived_from="sandbox:km:OS",
            )
        ]
    )


def _continuous() -> ContinuousResultsTurn:
    return ContinuousResultsTurn(
        results=[
            ContinuousResult(
                paramcd="CHGFBL",
                param_label="HbA1c change from baseline",
                visit="Week 24",
                n_observed=180,
                lsmean_difference=-0.8,
                diff_ci_lower=-1.1,
                diff_ci_upper=-0.5,
                p_value=0.0001,
                derived_from="sandbox:mmrm:CHGFBL",
            )
        ]
    )


def _binary() -> BinaryResultsTurn:
    return BinaryResultsTurn(
        results=[
            BinaryResult(
                paramcd="ORR",
                param_label="Overall response rate",
                method="fisher_exact",
                n_treatment=100,
                events_treatment=42,
                n_comparator=100,
                events_comparator=21,
                risk_difference=0.21,
                rd_ci_lower=0.08,
                rd_ci_upper=0.34,
                p_value=0.001,
                derived_from="sandbox:binary:ORR",
            )
        ]
    )


def _subgroup() -> SubgroupResultsTurn:
    return SubgroupResultsTurn(
        analyses=[
            SubgroupAnalysis(
                parent_paramcd="OS",
                parent_param_label="Overall survival",
                subgroup_variable="SEX",
                rows=[
                    SubgroupRow(subgroup_label="Female", n=100, n_events=40, effect=0.70, ci_lower=0.50, ci_upper=0.95, p_value=0.025),
                    SubgroupRow(subgroup_label="Male", n=100, n_events=45, effect=0.75, ci_lower=0.55, ci_upper=1.00, p_value=0.052),
                ],
                interaction_p_value=0.42,
                derived_from="sandbox:subgroup:OS:by:SEX",
            )
        ]
    )


# ── Assembler ────────────────────────────────────────────────────────────


def test_assembler_returns_none_when_no_trial_stats_turn() -> None:
    messages = [_msg(role="assistant", final_answer='{"kind": "answer"}')]
    assert assemble_report_data("thread-1", messages) is None


def test_assembler_collects_each_stage() -> None:
    messages = [
        _msg(role="assistant", final_answer=_intake().model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=_populations().model_dump_json()),
        _msg(role="assistant", msg_id="m3", final_answer=_tte().model_dump_json()),
        _msg(role="assistant", msg_id="m4", final_answer=_continuous().model_dump_json()),
        _msg(role="assistant", msg_id="m5", final_answer=_binary().model_dump_json()),
        _msg(role="assistant", msg_id="m6", final_answer=_subgroup().model_dump_json()),
    ]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    assert out.intake is not None
    assert len(out.populations) == 2
    assert len(out.time_to_event) == 1
    assert len(out.continuous) == 1
    assert len(out.binary) == 1
    assert len(out.subgroup) == 1


def test_assembler_prefers_final_document_when_present() -> None:
    doc = TrialStatsDocument(
        intake=_intake(),
        populations=list(_populations().populations),
        time_to_event=list(_tte().results),
        continuous=list(_continuous().results),
        binary=list(_binary().results),
        subgroup=list(_subgroup().analyses),
        primary_summary="favours_intervention",
        primary_summary_paragraph="Drug A reduced the hazard for the primary endpoint OS in the ITT population.",
        is_final=True,
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    assert out.document is not None
    assert out.document.is_final is True
    assert "TrialStats t-km-OS" in out.document.csr_artefact_ids


# ── Builders ─────────────────────────────────────────────────────────────


def test_build_pdf_emits_pdf_signature() -> None:
    doc = TrialStatsDocument(
        intake=_intake(),
        populations=list(_populations().populations),
        time_to_event=list(_tte().results),
        continuous=list(_continuous().results),
        binary=list(_binary().results),
        subgroup=list(_subgroup().analyses),
        primary_summary="favours_intervention",
        primary_summary_paragraph="Drug A reduced the hazard for OS.",
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    pdf = build_pdf(out, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")


def test_build_docx_emits_zip_signature() -> None:
    doc = TrialStatsDocument(
        intake=_intake(),
        populations=list(_populations().populations),
        time_to_event=list(_tte().results),
        continuous=list(_continuous().results),
        binary=list(_binary().results),
        subgroup=list(_subgroup().analyses),
    )
    messages = [_msg(role="assistant", final_answer=doc.model_dump_json())]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    docx = build_docx(out, images_dir=Path("/tmp"))
    assert docx[:4] == b"PK\x03\x04"


def test_pdf_with_only_intake_and_populations_renders() -> None:
    """A report with no analyses yet (early in the workflow) still
    renders cleanly — useful for previewing the populations card before
    Cox PH runs land."""
    messages = [
        _msg(role="assistant", final_answer=_intake().model_dump_json()),
        _msg(role="assistant", msg_id="m2", final_answer=_populations().model_dump_json()),
    ]
    out = assemble_report_data("thread-1", messages)
    assert out is not None
    pdf = build_pdf(out, images_dir=Path("/tmp"))
    assert pdf.startswith(b"%PDF-")
