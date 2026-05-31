"""Trial-stats specialist PDF + DOCX report.

Pipeline mirrors the other report modules:

    Message rows ─▶ assemble_report_data() ─▶ TrialStatsReportData
                                                  │
                                                  ├─▶ build_pdf()  ─▶ bytes
                                                  └─▶ build_docx() ─▶ bytes

Sections:
  1. Identity + endpoint roster
  2. Analysis populations
  3. Time-to-event results (K-M curves + Cox HR + log-rank)
  4. Continuous (MMRM) results
  5. Binary endpoint results
  6. Subgroup forests
  7. Primary-summary paragraph
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Inches
from pydantic import ValidationError
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from research_assistant.domain.trial_stats import (
    AnalysisPopulation,
    BinaryResult,
    ContinuousResult,
    SubgroupAnalysis,
    SwimmerResult,
    TimeToEventResult,
    TrialStatsDocument,
    TrialStatsIntake,
    WaterfallResult,
)
from research_assistant.persistence.models import Message
from research_assistant.reports._shared_styles import (
    BORDER,
    DOCX_MUTED,
    LIGHT,
    make_page_decorations,
    make_pdf_styles,
)

logger = logging.getLogger(__name__)


@dataclass
class TrialStatsReportData:
    thread_id: str
    title: str
    generated_at: datetime
    intake: TrialStatsIntake | None
    populations: list[AnalysisPopulation]
    time_to_event: list[TimeToEventResult]
    continuous: list[ContinuousResult]
    binary: list[BinaryResult]
    subgroup: list[SubgroupAnalysis]
    waterfall: list[WaterfallResult]
    swimmer: list[SwimmerResult]
    document: TrialStatsDocument | None


def _peek_kind(s: str) -> str | None:
    try:
        kind = json.loads(s).get("kind")
    except json.JSONDecodeError:
        return None
    return str(kind) if kind is not None else None


def assemble_report_data(
    thread_id: str,
    messages: Iterable[Message],
) -> TrialStatsReportData | None:
    intake: TrialStatsIntake | None = None
    populations: list[AnalysisPopulation] = []
    time_to_event: list[TimeToEventResult] = []
    continuous: list[ContinuousResult] = []
    binary: list[BinaryResult] = []
    subgroup: list[SubgroupAnalysis] = []
    waterfall: list[WaterfallResult] = []
    swimmer: list[SwimmerResult] = []
    document: TrialStatsDocument | None = None

    for msg in messages:
        if msg.role != "assistant" or not msg.final_answer:
            continue
        kind = _peek_kind(msg.final_answer)
        try:
            if kind == "trial_stats_intake":
                intake = TrialStatsIntake.model_validate_json(msg.final_answer)
            elif kind == "analysis_populations":
                # Lazy import to avoid circular references.
                from research_assistant.domain.trial_stats import (
                    AnalysisPopulationsTurn,
                )

                pops = AnalysisPopulationsTurn.model_validate_json(msg.final_answer)
                populations = list(pops.populations)
            elif kind == "time_to_event_results":
                from research_assistant.domain.trial_stats import (
                    TimeToEventResultsTurn,
                )

                tte = TimeToEventResultsTurn.model_validate_json(msg.final_answer)
                time_to_event = list(tte.results)
            elif kind == "continuous_results":
                from research_assistant.domain.trial_stats import (
                    ContinuousResultsTurn,
                )

                cr = ContinuousResultsTurn.model_validate_json(msg.final_answer)
                continuous = list(cr.results)
            elif kind == "binary_results":
                from research_assistant.domain.trial_stats import (
                    BinaryResultsTurn,
                )

                br = BinaryResultsTurn.model_validate_json(msg.final_answer)
                binary = list(br.results)
            elif kind == "subgroup_results":
                from research_assistant.domain.trial_stats import (
                    SubgroupResultsTurn,
                )

                sg = SubgroupResultsTurn.model_validate_json(msg.final_answer)
                subgroup = list(sg.analyses)
            elif kind == "subject_visualisations":
                from research_assistant.domain.trial_stats import (
                    SubjectVisualizationsTurn,
                )

                viz = SubjectVisualizationsTurn.model_validate_json(msg.final_answer)
                waterfall = list(viz.waterfall)
                swimmer = list(viz.swimmer)
            elif kind == "trial_stats_document":
                document = TrialStatsDocument.model_validate_json(msg.final_answer)
        except ValidationError:
            logger.exception("Skipping malformed %s turn in message %s", kind, msg.id)

    if document is not None:
        intake = document.intake
        populations = list(document.populations)
        time_to_event = list(document.time_to_event)
        continuous = list(document.continuous)
        binary = list(document.binary)
        subgroup = list(document.subgroup)
        waterfall = list(document.waterfall)
        swimmer = list(document.swimmer)

    if not any(
        (
            intake,
            populations,
            time_to_event,
            continuous,
            binary,
            subgroup,
            waterfall,
            swimmer,
            document,
        )
    ):
        return None

    title = "Trial-statistics analysis"
    if intake:
        title = f"Trial stats — {intake.study_name[:60]}"

    return TrialStatsReportData(
        thread_id=thread_id,
        title=title,
        generated_at=datetime.now(UTC),
        intake=intake,
        populations=populations,
        time_to_event=time_to_event,
        continuous=continuous,
        binary=binary,
        subgroup=subgroup,
        waterfall=waterfall,
        swimmer=swimmer,
        document=document,
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _kv_rows(rows: list[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> Table:
    body = [
        [
            Paragraph(f"<b>{k}</b>", styles["Cell"]),
            Paragraph(v or "—", styles["Cell"]),
        ]
        for k, v in rows
    ]
    t = Table(body, colWidths=[2.0 * inch, 5.5 * inch])
    t.setStyle(
        TableStyle(
            [
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, LIGHT]),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def _fmt_ci(value: float | None, lo: float | None, hi: float | None) -> str:
    if value is None or lo is None or hi is None:
        return "—"
    return f"{value:.2f} ({lo:.2f}–{hi:.2f})"


def _fmt_p(p: float | None) -> str:
    if p is None:
        return "—"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def _populations_pdf(
    pops: list[AnalysisPopulation],
    styles: dict[str, ParagraphStyle],
) -> Table:
    header = [
        Paragraph(f"<b>{c}</b>", styles["Cell"])
        for c in ("Population", "N total", "Per-arm N", "ADSL source", "Rationale")
    ]
    body = [header]
    for p in pops:
        per_arm = ", ".join(f"{k}: {v}" for k, v in p.n_per_arm.items()) or "—"
        body.append(
            [
                Paragraph(p.kind_name, styles["Cell"]),
                Paragraph(str(p.n_total), styles["Cell"]),
                Paragraph(per_arm, styles["Cell"]),
                Paragraph(p.derived_from, styles["Cell"]),
                Paragraph(p.rationale, styles["Cell"]),
            ]
        )
    t = Table(body, colWidths=[1.0, 0.7, 2.4, 1.6, 2.3], repeatRows=1)
    t._argW = [w * inch for w in t._argW]
    t.setStyle(_table_style())
    return t


def _table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )


def _tte_pdf(rows: list[TimeToEventResult], styles: dict[str, ParagraphStyle]) -> Table:
    header = [
        Paragraph(f"<b>{c}</b>", styles["Cell"])
        for c in (
            "PARAMCD",
            "Endpoint",
            "Pop'n",
            "N (events)",
            "Median",
            "HR (95% CI)",
            "Log-rank p",
            "Source",
        )
    ]
    body = [header]
    for r in rows:
        body.append(
            [
                Paragraph(str(r.paramcd), styles["Cell"]),
                Paragraph(r.param_label, styles["Cell"]),
                Paragraph(r.population, styles["Cell"]),
                Paragraph(f"{r.n_subjects} ({r.n_events})", styles["Cell"]),
                Paragraph(r.median_event_time, styles["Cell"]),
                Paragraph(
                    _fmt_ci(r.hazard_ratio, r.hr_ci_lower, r.hr_ci_upper)
                    if r.hazard_ratio is not None
                    else (r.skip_reason or "—"),
                    styles["Cell"],
                ),
                Paragraph(_fmt_p(r.logrank_p_value), styles["Cell"]),
                Paragraph(r.derived_from, styles["Cell"]),
            ]
        )
    t = Table(body, colWidths=[0.7, 1.6, 0.7, 0.9, 0.9, 1.5, 0.8, 1.4], repeatRows=1)
    t._argW = [w * inch for w in t._argW]
    t.setStyle(_table_style())
    return t


def _mmrm_pdf(rows: list[ContinuousResult], styles: dict[str, ParagraphStyle]) -> Table:
    header = [
        Paragraph(f"<b>{c}</b>", styles["Cell"])
        for c in (
            "PARAMCD",
            "Endpoint",
            "Visit",
            "Model",
            "n_obs",
            "LSMean Δ (95% CI)",
            "p",
            "Source",
        )
    ]
    body = [header]
    for r in rows:
        body.append(
            [
                Paragraph(r.paramcd, styles["Cell"]),
                Paragraph(r.param_label, styles["Cell"]),
                Paragraph(r.visit, styles["Cell"]),
                Paragraph(r.model, styles["Cell"]),
                Paragraph(str(r.n_observed), styles["Cell"]),
                Paragraph(
                    _fmt_ci(r.lsmean_difference, r.diff_ci_lower, r.diff_ci_upper)
                    if r.lsmean_difference is not None
                    else (r.skip_reason or "—"),
                    styles["Cell"],
                ),
                Paragraph(_fmt_p(r.p_value), styles["Cell"]),
                Paragraph(r.derived_from, styles["Cell"]),
            ]
        )
    t = Table(body, colWidths=[0.9, 1.6, 0.8, 0.8, 0.6, 1.7, 0.6, 1.5], repeatRows=1)
    t._argW = [w * inch for w in t._argW]
    t.setStyle(_table_style())
    return t


def _binary_pdf(rows: list[BinaryResult], styles: dict[str, ParagraphStyle]) -> Table:
    header = [
        Paragraph(f"<b>{c}</b>", styles["Cell"])
        for c in (
            "PARAMCD",
            "Endpoint",
            "Method",
            "Treatment",
            "Comparator",
            "RD (95% CI)",
            "RR (95% CI)",
            "p",
            "Source",
        )
    ]
    body = [header]
    for r in rows:
        trt_cell = f"{r.events_treatment}/{r.n_treatment}"
        cmp_cell = f"{r.events_comparator}/{r.n_comparator}"
        body.append(
            [
                Paragraph(r.paramcd, styles["Cell"]),
                Paragraph(r.param_label, styles["Cell"]),
                Paragraph(r.method, styles["Cell"]),
                Paragraph(trt_cell, styles["Cell"]),
                Paragraph(cmp_cell, styles["Cell"]),
                Paragraph(
                    _fmt_ci(r.risk_difference, r.rd_ci_lower, r.rd_ci_upper),
                    styles["Cell"],
                ),
                Paragraph(
                    _fmt_ci(r.risk_ratio, r.rr_ci_lower, r.rr_ci_upper),
                    styles["Cell"],
                ),
                Paragraph(_fmt_p(r.p_value), styles["Cell"]),
                Paragraph(r.derived_from, styles["Cell"]),
            ]
        )
    t = Table(body, colWidths=[0.7, 1.3, 0.9, 0.9, 0.9, 1.3, 1.3, 0.5, 1.2], repeatRows=1)
    t._argW = [w * inch for w in t._argW]
    t.setStyle(_table_style())
    return t


def _subgroup_pdf(
    sg: SubgroupAnalysis,
    styles: dict[str, ParagraphStyle],
) -> Table:
    header = [
        Paragraph(f"<b>{c}</b>", styles["Cell"])
        for c in (
            "Subgroup",
            "N (events)",
            "Effect (95% CI)",
            "p",
        )
    ]
    body = [header]
    for r in sg.rows:
        body.append(
            [
                Paragraph(r.subgroup_label, styles["Cell"]),
                Paragraph(f"{r.n} ({r.n_events})", styles["Cell"]),
                Paragraph(
                    _fmt_ci(r.effect, r.ci_lower, r.ci_upper),
                    styles["Cell"],
                ),
                Paragraph(_fmt_p(r.p_value), styles["Cell"]),
            ]
        )
    t = Table(body, colWidths=[2.0, 1.1, 1.7, 0.7], repeatRows=1)
    t._argW = [w * inch for w in t._argW]
    t.setStyle(_table_style())
    return t


def _resolve_image(images_dir: Path | None, url_or_filename: str) -> Path | None:
    """Map a `/images/<name>` URL or bare filename to a real path on disk."""
    if images_dir is None:
        return None
    name = url_or_filename
    if name.startswith("/images/"):
        name = name[len("/images/") :]
    candidate = images_dir / name
    return candidate if candidate.exists() else None


# ── PDF builder ─────────────────────────────────────────────────────────


def build_pdf(
    data: TrialStatsReportData, images_dir: Path | None = None
) -> bytes:
    buf = io.BytesIO()
    styles = make_pdf_styles()
    decor = make_page_decorations(footer_label="Trial-stats analysis")
    doc = BaseDocTemplate(
        buf,
        pagesize=landscape(LETTER),
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.6 * inch,
        title=data.title,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=decor)])

    flow: list[Any] = []
    flow.append(Paragraph("TRIAL-STATS ANALYSIS", styles["Eyebrow"]))
    flow.append(Paragraph(data.title, styles["Title"]))
    ts = data.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    flow.append(Paragraph(f"Generated {ts}", styles["Body"]))
    flow.append(Spacer(1, 8))
    flow.append(
        Paragraph(
            "<i>Every result row was generated by the sandbox-side analysis script "
            "named in its <b>derived_from</b> field. The schema enforces that "
            "hazard ratios, LSMean differences, risk differences, CIs and p-values "
            "come from a sandbox run — they cannot be authored inline.</i>",
            styles["Body"],
        )
    )

    if data.intake:
        flow.append(Spacer(1, 12))
        flow.append(Paragraph("Study + endpoint roster", styles["H2"]))
        flow.append(
            _kv_rows(
                [
                    ("Study ID", data.intake.study_id),
                    ("Study name", data.intake.study_name),
                    ("Primary endpoint", data.intake.primary_endpoint),
                    (
                        "Secondary endpoints",
                        "; ".join(data.intake.secondary_endpoints) or "—",
                    ),
                    ("Arms (reference first)", ", ".join(data.intake.arms) or "—"),
                    ("Input shape", data.intake.input_shape),
                ],
                styles,
            )
        )

    if data.populations:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Analysis populations", styles["H2"]))
        flow.append(_populations_pdf(data.populations, styles))

    if data.time_to_event:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Time-to-event results", styles["H2"]))
        flow.append(_tte_pdf(data.time_to_event, styles))
        for r in data.time_to_event:
            if r.km_image_url is None:
                continue
            resolved = _resolve_image(images_dir, r.km_image_url)
            if resolved is None:
                continue
            flow.append(Spacer(1, 6))
            flow.append(
                Paragraph(
                    f"<b>K-M curve — {r.paramcd}: {r.param_label}</b>",
                    styles["H3"],
                )
            )
            flow.append(Image(str(resolved), width=6.5 * inch, height=4.0 * inch))

    if data.continuous:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Continuous (MMRM) results", styles["H2"]))
        flow.append(_mmrm_pdf(data.continuous, styles))

    if data.binary:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Binary endpoint results", styles["H2"]))
        flow.append(_binary_pdf(data.binary, styles))

    if data.subgroup:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Subgroup analyses", styles["H2"]))
        for sg in data.subgroup:
            flow.append(Spacer(1, 6))
            heading = (
                f"<b>{sg.parent_paramcd} — {sg.parent_param_label} "
                f"by {sg.subgroup_variable}</b>"
            )
            flow.append(Paragraph(heading, styles["H3"]))
            flow.append(
                Paragraph(
                    "Interaction p-value: " + _fmt_p(sg.interaction_p_value),
                    styles["Body"],
                )
            )
            flow.append(_subgroup_pdf(sg, styles))

    if data.waterfall:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Waterfall — per-subject best response", styles["H2"]))
        for w in data.waterfall:
            flow.append(Spacer(1, 6))
            flow.append(
                Paragraph(f"<b>{w.outcome_label}</b> (n={w.n_subjects})", styles["H3"])
            )
            if w.waterfall_image_url:
                resolved = _resolve_image(images_dir, w.waterfall_image_url)
                if resolved is not None:
                    flow.append(Image(str(resolved), width=8.5 * inch, height=4.5 * inch))
            counts_text = ", ".join(
                f"{k}: {v}" for k, v in (w.response_counts or {}).items()
            )
            if counts_text:
                flow.append(
                    Paragraph(
                        f"<i>RECIST 1.1 counts — {counts_text}</i>",
                        styles["Body"],
                    )
                )

    if data.swimmer:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Swimmer — treatment timeline", styles["H2"]))
        for s in data.swimmer:
            flow.append(Spacer(1, 6))
            flow.append(
                Paragraph(f"<b>{s.outcome_label}</b> (n={s.n_subjects})", styles["H3"])
            )
            if s.swimmer_image_url:
                resolved = _resolve_image(images_dir, s.swimmer_image_url)
                if resolved is not None:
                    flow.append(Image(str(resolved), width=8.5 * inch, height=5.0 * inch))

    if data.document and data.document.primary_summary_paragraph:
        flow.append(Spacer(1, 14))
        flow.append(Paragraph("Primary-endpoint summary", styles["H2"]))
        flow.append(
            Paragraph(
                f"<b>Headline:</b> {data.document.primary_summary.replace('_', ' ')}",
                styles["Body"],
            )
        )
        flow.append(Spacer(1, 4))
        flow.append(Paragraph(data.document.primary_summary_paragraph, styles["Body"]))

    if data.document:
        flow.append(Spacer(1, 10))
        ids = data.document.csr_artefact_ids
        if ids:
            flow.append(
                Paragraph(
                    "<i>CSR-citable artefact ids: " + "; ".join(ids) + "</i>",
                    styles["Body"],
                )
            )

    doc.build(flow)
    return buf.getvalue()


# ── DOCX builder ────────────────────────────────────────────────────────


def build_docx(
    data: TrialStatsReportData, images_dir: Path | None = None
) -> bytes:
    docx = Document()
    docx.core_properties.title = data.title
    docx.add_heading(data.title, level=0)
    runs = docx.add_paragraph(
        f"Generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}"
    ).runs
    if runs:
        runs[0].font.color.rgb = DOCX_MUTED

    if data.intake:
        docx.add_heading("Study + endpoint roster", level=1)
        for label, value in (
            ("Study ID", data.intake.study_id),
            ("Study name", data.intake.study_name),
            ("Primary endpoint", data.intake.primary_endpoint),
            ("Secondary endpoints", "; ".join(data.intake.secondary_endpoints) or "—"),
            ("Arms (reference first)", ", ".join(data.intake.arms) or "—"),
            ("Input shape", data.intake.input_shape),
        ):
            p = docx.add_paragraph()
            p.add_run(label + ": ").bold = True
            p.add_run(value)

    if data.populations:
        docx.add_heading("Analysis populations", level=1)
        cols = ["Population", "N total", "Per-arm N", "ADSL source", "Rationale"]
        t = docx.add_table(rows=1 + len(data.populations), cols=len(cols))
        t.style = "Light Grid Accent 1"
        for j, c in enumerate(cols):
            cell = t.cell(0, j)
            cell.text = c
            cell.paragraphs[0].runs[0].bold = True
        for i, pop in enumerate(data.populations, start=1):
            t.cell(i, 0).text = pop.kind_name
            t.cell(i, 1).text = str(pop.n_total)
            t.cell(i, 2).text = ", ".join(f"{k}: {v}" for k, v in pop.n_per_arm.items()) or "—"
            t.cell(i, 3).text = pop.derived_from
            t.cell(i, 4).text = pop.rationale

    if data.time_to_event:
        docx.add_heading("Time-to-event results", level=1)
        cols = [
            "PARAMCD",
            "Endpoint",
            "N (events)",
            "Median",
            "HR (95% CI)",
            "Log-rank p",
            "Source",
        ]
        t = docx.add_table(rows=1 + len(data.time_to_event), cols=len(cols))
        t.style = "Light Grid Accent 1"
        for j, c in enumerate(cols):
            cell = t.cell(0, j)
            cell.text = c
            cell.paragraphs[0].runs[0].bold = True
        for i, tte in enumerate(data.time_to_event, start=1):
            t.cell(i, 0).text = str(tte.paramcd)
            t.cell(i, 1).text = tte.param_label
            t.cell(i, 2).text = f"{tte.n_subjects} ({tte.n_events})"
            t.cell(i, 3).text = tte.median_event_time
            t.cell(i, 4).text = (
                _fmt_ci(tte.hazard_ratio, tte.hr_ci_lower, tte.hr_ci_upper)
                if tte.hazard_ratio is not None
                else (tte.skip_reason or "—")
            )
            t.cell(i, 5).text = _fmt_p(tte.logrank_p_value)
            t.cell(i, 6).text = tte.derived_from
        for tte in data.time_to_event:
            if tte.km_image_url is None:
                continue
            resolved = _resolve_image(images_dir, tte.km_image_url)
            if resolved is None:
                continue
            cap = docx.add_paragraph()
            cap.add_run(f"K-M curve — {tte.paramcd}: {tte.param_label}").bold = True
            docx.add_picture(str(resolved), width=Inches(6.0))

    if data.continuous:
        docx.add_heading("Continuous (MMRM) results", level=1)
        cols = ["PARAMCD", "Endpoint", "Visit", "n_obs", "LSMean Δ (95% CI)", "p", "Source"]
        t = docx.add_table(rows=1 + len(data.continuous), cols=len(cols))
        t.style = "Light Grid Accent 1"
        for j, c in enumerate(cols):
            cell = t.cell(0, j)
            cell.text = c
            cell.paragraphs[0].runs[0].bold = True
        for i, cr in enumerate(data.continuous, start=1):
            t.cell(i, 0).text = cr.paramcd
            t.cell(i, 1).text = cr.param_label
            t.cell(i, 2).text = cr.visit
            t.cell(i, 3).text = str(cr.n_observed)
            t.cell(i, 4).text = (
                _fmt_ci(cr.lsmean_difference, cr.diff_ci_lower, cr.diff_ci_upper)
                if cr.lsmean_difference is not None
                else (cr.skip_reason or "—")
            )
            t.cell(i, 5).text = _fmt_p(cr.p_value)
            t.cell(i, 6).text = cr.derived_from

    if data.binary:
        docx.add_heading("Binary endpoint results", level=1)
        cols = [
            "PARAMCD",
            "Endpoint",
            "Method",
            "Trt",
            "Cmp",
            "RD (95% CI)",
            "RR (95% CI)",
            "p",
            "Source",
        ]
        t = docx.add_table(rows=1 + len(data.binary), cols=len(cols))
        t.style = "Light Grid Accent 1"
        for j, c in enumerate(cols):
            cell = t.cell(0, j)
            cell.text = c
            cell.paragraphs[0].runs[0].bold = True
        for i, br in enumerate(data.binary, start=1):
            t.cell(i, 0).text = br.paramcd
            t.cell(i, 1).text = br.param_label
            t.cell(i, 2).text = br.method
            t.cell(i, 3).text = f"{br.events_treatment}/{br.n_treatment}"
            t.cell(i, 4).text = f"{br.events_comparator}/{br.n_comparator}"
            t.cell(i, 5).text = _fmt_ci(br.risk_difference, br.rd_ci_lower, br.rd_ci_upper)
            t.cell(i, 6).text = _fmt_ci(br.risk_ratio, br.rr_ci_lower, br.rr_ci_upper)
            t.cell(i, 7).text = _fmt_p(br.p_value)
            t.cell(i, 8).text = br.derived_from

    if data.subgroup:
        docx.add_heading("Subgroup analyses", level=1)
        for sg in data.subgroup:
            docx.add_heading(
                f"{sg.parent_paramcd} — {sg.parent_param_label} by {sg.subgroup_variable}",
                level=2,
            )
            ip = docx.add_paragraph()
            ip.add_run("Interaction p-value: ").bold = True
            ip.add_run(_fmt_p(sg.interaction_p_value))
            cols = ["Subgroup", "N (events)", "Effect (95% CI)", "p"]
            t = docx.add_table(rows=1 + len(sg.rows), cols=len(cols))
            t.style = "Light Grid Accent 1"
            for j, c in enumerate(cols):
                cell = t.cell(0, j)
                cell.text = c
                cell.paragraphs[0].runs[0].bold = True
            for i, sr in enumerate(sg.rows, start=1):
                t.cell(i, 0).text = sr.subgroup_label
                t.cell(i, 1).text = f"{sr.n} ({sr.n_events})"
                t.cell(i, 2).text = _fmt_ci(sr.effect, sr.ci_lower, sr.ci_upper)
                t.cell(i, 3).text = _fmt_p(sr.p_value)

    if data.waterfall:
        docx.add_heading("Waterfall — per-subject best response", level=1)
        for w in data.waterfall:
            docx.add_heading(f"{w.outcome_label} (n={w.n_subjects})", level=2)
            if w.waterfall_image_url:
                resolved = _resolve_image(images_dir, w.waterfall_image_url)
                if resolved is not None:
                    docx.add_picture(str(resolved), width=Inches(6.5))
            counts_text = ", ".join(
                f"{k}: {v}" for k, v in (w.response_counts or {}).items()
            )
            if counts_text:
                wp = docx.add_paragraph(f"RECIST 1.1 counts — {counts_text}")
                for run in wp.runs:
                    run.italic = True

    if data.swimmer:
        docx.add_heading("Swimmer — treatment timeline", level=1)
        for s in data.swimmer:
            docx.add_heading(f"{s.outcome_label} (n={s.n_subjects})", level=2)
            if s.swimmer_image_url:
                resolved = _resolve_image(images_dir, s.swimmer_image_url)
                if resolved is not None:
                    docx.add_picture(str(resolved), width=Inches(6.5))

    if data.document and data.document.primary_summary_paragraph:
        docx.add_heading("Primary-endpoint summary", level=1)
        p = docx.add_paragraph()
        p.add_run("Headline: ").bold = True
        p.add_run(data.document.primary_summary.replace("_", " "))
        docx.add_paragraph(data.document.primary_summary_paragraph)
        ids = data.document.csr_artefact_ids
        if ids:
            p2 = docx.add_paragraph()
            run = p2.add_run("CSR-citable artefact ids: " + "; ".join(ids))
            run.italic = True

    out = io.BytesIO()
    docx.save(out)
    return out.getvalue()


__all__ = [
    "TrialStatsReportData",
    "assemble_report_data",
    "build_docx",
    "build_pdf",
]
