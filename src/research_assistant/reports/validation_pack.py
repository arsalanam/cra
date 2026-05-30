"""Validation-pack PDF renderers (eCRF E7).

Three documents rendered here:

  • IQ — Installation Qualification snapshot (`render_iq_pdf`)
  • OQ — Operational Qualification requirements run (`render_oq_pdf`)
  • PQ — Performance Qualification runbook (`render_pq_pdf`)

Plus :func:`build_validation_bundle_zip` which packages all three with
a manifest into a single downloadable ZIP for the customer's
validation binder.

Follows the same reportlab posture as the other report modules
(`sae_3500a.py`, `sap.py`, `manuscript.py`) — `_shared_styles` provides
the page chrome.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from research_assistant.reports._shared_styles import (
    BORDER,
    LIGHT,
    make_page_decorations,
    make_pdf_styles,
)
from research_assistant.validation.iq import InstallationSnapshot
from research_assistant.validation.oq import OperationalReport
from research_assistant.validation.pq import PerformanceRunbook

_PAGE_KW = dict(
    pagesize=LETTER,
    leftMargin=0.7 * inch,
    rightMargin=0.7 * inch,
    topMargin=0.9 * inch,
    bottomMargin=0.7 * inch,
)


def _kv(
    rows: Iterable[tuple[str, str]], styles: dict[str, ParagraphStyle]
) -> Table:
    table_rows = [
        [
            Paragraph(f"<b>{k}</b>", styles["Cell"]),
            Paragraph(v or "—", styles["Cell"]),
        ]
        for k, v in rows
    ]
    t = Table(table_rows, colWidths=[2.2 * inch, 4.6 * inch])
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


def _doc(buf: io.BytesIO, *, title: str, footer: str) -> BaseDocTemplate:
    decor = make_page_decorations(footer_label=footer)
    doc = BaseDocTemplate(buf, title=title, **_PAGE_KW)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=decor)])
    return doc


# ── IQ ──────────────────────────────────────────────────────────────────


def render_iq_pdf(snapshot: InstallationSnapshot) -> bytes:
    buf = io.BytesIO()
    styles = make_pdf_styles()
    doc = _doc(
        buf,
        title=f"IQ snapshot — {snapshot.deployment_environment}",
        footer="INSTALLATION QUALIFICATION (IQ)",
    )
    flow: list[object] = []
    flow.append(Paragraph("INSTALLATION QUALIFICATION (IQ)", styles["Eyebrow"]))
    flow.append(Paragraph("Clinical Research Assistant — System Snapshot", styles["Title"]))
    flow.append(
        Paragraph(
            f"Environment: <b>{snapshot.deployment_environment}</b>  ·  "
            f"Captured {snapshot.captured_at.strftime('%Y-%m-%d %H:%M UTC')}",
            styles["Body"],
        )
    )
    flow.append(Spacer(1, 12))

    flow.append(Paragraph("1. Software identity", styles["H2"]))
    flow.append(
        _kv(
            [
                ("Package", snapshot.package_name),
                ("Version", snapshot.package_version),
                ("Python", snapshot.python_version),
                ("Platform", snapshot.platform),
                ("Host", snapshot.host_name),
            ],
            styles,
        )
    )

    flow.append(Spacer(1, 10))
    flow.append(Paragraph("2. Identity provider (Cognito)", styles["H2"]))
    if snapshot.cognito.configured:
        flow.append(
            _kv(
                [
                    ("Configured", "Yes"),
                    ("Region", snapshot.cognito.region or "—"),
                    ("User pool id", snapshot.cognito.user_pool_id or "—"),
                    ("App client id", snapshot.cognito.client_id or "—"),
                    ("Domain", snapshot.cognito.domain or "—"),
                ],
                styles,
            )
        )
    else:
        flow.append(
            Paragraph(
                "<b>Cognito not configured.</b> The instance is running in "
                "auth-disabled (dev) mode — signing re-authentication will "
                "be bypassed. Production installs MUST configure Cognito.",
                styles["Body"],
            )
        )

    flow.append(Spacer(1, 10))
    flow.append(Paragraph("3. Database", styles["H2"]))
    flow.append(
        _kv(
            [
                ("Dialect", snapshot.database.dialect),
                ("Table count", str(snapshot.database.table_count)),
                (
                    "Audit-immutability trigger present",
                    "Yes ✓" if snapshot.database.audit_immutability_trigger_present else "NO ✗",
                ),
                ("Schema version", snapshot.database.schema_version or "—"),
            ],
            styles,
        )
    )
    if not snapshot.database.audit_immutability_trigger_present:
        flow.append(
            Paragraph(
                "<b>WARNING:</b> the database-level audit-immutability trigger "
                "was not detected. The application-layer audit invariant still "
                "holds, but the regulator's second line of defence is missing. "
                "Re-run install scripts to seed the trigger.",
                styles["Body"],
            )
        )

    flow.append(Spacer(1, 10))
    flow.append(Paragraph("4. Feature flags", styles["H2"]))
    flow.append(
        _kv(
            [
                ("Auth enforced", "Yes" if snapshot.feature_flags.auth_enabled else "No"),
                (
                    "Python sandbox enabled",
                    "Yes" if snapshot.feature_flags.sandbox_enabled else "No",
                ),
            ],
            styles,
        )
    )

    flow.append(Spacer(1, 10))
    flow.append(
        Paragraph(
            f"5. Pinned dependencies ({len(snapshot.dependencies)} packages)",
            styles["H2"],
        )
    )
    if snapshot.dependencies:
        rows: list[list[Paragraph]] = [
            [
                Paragraph("<b>Package</b>", styles["Cell"]),
                Paragraph("<b>Version</b>", styles["Cell"]),
            ]
        ]
        for d in sorted(snapshot.dependencies, key=lambda x: x.name.lower()):
            rows.append([Paragraph(d.name, styles["Cell"]), Paragraph(d.version, styles["Cell"])])
        t = Table(rows, colWidths=[4.3 * inch, 2.5 * inch], repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                    ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        flow.append(t)
    else:
        flow.append(
            Paragraph(
                "<i>uv.lock not found — dependency pins could not be captured.</i>",
                styles["Body"],
            )
        )

    doc.build(flow)
    return buf.getvalue()


# ── OQ ──────────────────────────────────────────────────────────────────


def render_oq_pdf(report: OperationalReport) -> bytes:
    buf = io.BytesIO()
    styles = make_pdf_styles()
    doc = _doc(
        buf,
        title=f"OQ report — matrix {report.matrix_version}",
        footer="OPERATIONAL QUALIFICATION (OQ)",
    )
    flow: list[object] = []
    flow.append(Paragraph("OPERATIONAL QUALIFICATION (OQ)", styles["Eyebrow"]))
    flow.append(Paragraph("Requirements Traceability Run", styles["Title"]))
    flow.append(
        Paragraph(
            f"Matrix version <b>{report.matrix_version}</b>  ·  "
            f"Generated {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}  ·  "
            f"Duration {report.duration_seconds:.1f}s  ·  "
            f"pytest exit {report.pytest_returncode}",
            styles["Body"],
        )
    )
    flow.append(Spacer(1, 12))

    flow.append(Paragraph("Summary", styles["H2"]))
    flow.append(
        _kv(
            [
                ("Total requirements", str(report.total_requirements)),
                ("Passed", str(report.requirements_passed)),
                ("Failed / errored", str(report.requirements_failed)),
                ("Missing tests", str(report.requirements_missing)),
            ],
            styles,
        )
    )

    flow.append(Spacer(1, 12))
    flow.append(Paragraph("Per-requirement results", styles["H2"]))
    header = [
        Paragraph("<b>Code</b>", styles["Cell"]),
        Paragraph("<b>Title</b>", styles["Cell"]),
        Paragraph("<b>Anchor</b>", styles["Cell"]),
        Paragraph("<b>Result</b>", styles["Cell"]),
    ]
    rows: list[list[Paragraph]] = [header]
    for rr in report.requirement_results:
        rows.append(
            [
                Paragraph(rr.requirement.code, styles["Cell"]),
                Paragraph(rr.requirement.title, styles["Cell"]),
                Paragraph(rr.requirement.regulatory_anchor, styles["Cell"]),
                Paragraph(rr.status.upper(), styles["Cell"]),
            ]
        )
    t = Table(rows, colWidths=[0.9 * inch, 2.4 * inch, 2.5 * inch, 1.0 * inch], repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    flow.append(t)

    flow.append(Spacer(1, 14))
    flow.append(Paragraph("Per-requirement evidence", styles["H2"]))
    for rr in report.requirement_results:
        flow.append(
            Paragraph(
                f"<b>{rr.requirement.code} — {rr.requirement.title}</b>",
                styles["H3"],
            )
        )
        flow.append(
            Paragraph(
                f"<i>Anchor:</i> {rr.requirement.regulatory_anchor}<br/>"
                f"<i>System:</i> {rr.requirement.system_anchor}",
                styles["Body"],
            )
        )
        flow.append(
            _kv(
                [(t.node_id, t.status.upper()) for t in rr.test_results],
                styles,
            )
        )
        flow.append(Spacer(1, 8))

    doc.build(flow)
    return buf.getvalue()


# ── PQ ──────────────────────────────────────────────────────────────────


def render_pq_pdf(runbook: PerformanceRunbook) -> bytes:
    buf = io.BytesIO()
    styles = make_pdf_styles()
    doc = _doc(
        buf,
        title=runbook.title,
        footer="PERFORMANCE QUALIFICATION (PQ) RUNBOOK",
    )
    flow: list[object] = []
    flow.append(Paragraph("PERFORMANCE QUALIFICATION (PQ)", styles["Eyebrow"]))
    flow.append(Paragraph(runbook.title, styles["Title"]))
    flow.append(Paragraph(f"Runbook version {runbook.version}", styles["Body"]))
    flow.append(Spacer(1, 10))
    flow.append(Paragraph("Overview", styles["H2"]))
    flow.append(Paragraph(runbook.overview, styles["Body"]))
    flow.append(Spacer(1, 12))

    flow.append(Paragraph("Procedure", styles["H2"]))
    for step in runbook.steps:
        flow.append(
            Paragraph(f"<b>Step {step.ordinal} — {step.title}</b>", styles["H3"])
        )
        flow.append(
            _kv(
                [
                    ("Procedure", step.procedure),
                    ("Expected result", step.expected_result),
                    ("Regulatory anchor", step.regulatory_anchor or "—"),
                    ("Actual result", "________________________________________"),
                ],
                styles,
            )
        )
        flow.append(Spacer(1, 8))

    flow.append(Spacer(1, 12))
    flow.append(Paragraph("Sign-off", styles["H2"]))
    flow.append(Paragraph(runbook.signoff_instructions, styles["Body"]))
    flow.append(Spacer(1, 10))
    flow.append(
        _kv(
            [
                ("Operator name (printed)", "____________________________"),
                ("Operator signature", "____________________________"),
                ("Date", "____________________________"),
                ("PI / QA witness", "____________________________"),
            ],
            styles,
        )
    )

    doc.build(flow)
    return buf.getvalue()


# ── Bundle ──────────────────────────────────────────────────────────────


def build_validation_bundle_zip(
    *,
    iq_snapshot: InstallationSnapshot,
    oq_report: OperationalReport | None,
    pq_runbook: PerformanceRunbook,
    generated_at: datetime | None = None,
) -> bytes:
    """Package IQ + OQ (if available) + PQ into a single ZIP with a
    manifest. ``oq_report`` is optional — the customer may want IQ + PQ
    before running OQ for the first time."""
    ts = generated_at or datetime.now(UTC)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("iq-installation-qualification.pdf", render_iq_pdf(iq_snapshot))
        if oq_report is not None:
            z.writestr("oq-operational-qualification.pdf", render_oq_pdf(oq_report))
        z.writestr("pq-performance-qualification-runbook.pdf", render_pq_pdf(pq_runbook))

        manifest_lines = [
            "Validation Pack Manifest",
            "========================",
            f"Generated: {ts.strftime('%Y-%m-%d %H:%M UTC')}",
            f"Environment: {iq_snapshot.deployment_environment}",
            f"Package: {iq_snapshot.package_name} v{iq_snapshot.package_version}",
            "",
            "Contents:",
            "  - iq-installation-qualification.pdf  (auto-generated)",
        ]
        if oq_report is not None:
            manifest_lines.append(
                f"  - oq-operational-qualification.pdf   "
                f"(matrix v{oq_report.matrix_version}, "
                f"{oq_report.requirements_passed}/"
                f"{oq_report.total_requirements} requirements passed)"
            )
        else:
            manifest_lines.append("  - (OQ report not yet generated — run from the admin panel)")
        manifest_lines.extend(
            [
                "  - pq-performance-qualification-runbook.pdf  (customer-executed)",
                "",
                "File this bundle in the customer's validation binder. Re-run on",
                "every major version upgrade.",
            ]
        )
        z.writestr("MANIFEST.txt", "\n".join(manifest_lines))
    return buf.getvalue()


__all__ = [
    "build_validation_bundle_zip",
    "render_iq_pdf",
    "render_oq_pdf",
    "render_pq_pdf",
]
