"""Sprint U4 — user-administration PDF reports.

Three regulatory-grade exports the auditor surface needs:

  1. Delegation log per Trial (ICH E6 §4.1.5 + 21 CFR §312.62) — the
     official record of who was delegated what tasks by the PI.
  2. Training matrix per Site (ICH E6 §4.2.4) — every member assigned
     to the site + their training records + expiry status.
  3. User-admin audit log (Part 11 §11.10(e)) — filtered range of
     UserAdminAuditEntry rows for an inspection.

Pipeline mirrors the other report modules: assemble_*_data() builds
the dataclass, then build_pdf() returns bytes. DOCX deferred — PDF
is the regulator-expected format for these surfaces.

PHI posture: zero PHI. These reports cover staff actions, not patient
data. UserProfile = staff metadata, RoleAssignment = grants,
TrainingRecord = credentials, DelegationLogEntry = task assignments.
None of them reference subject data.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
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

from ._shared_styles import (
    AMBER,
    BORDER,
    LIGHT,
    NAVY,
    make_page_decorations,
    make_pdf_styles,
)

logger = logging.getLogger(__name__)


# ── Common dataclass shapes ────────────────────────────────────────────


@dataclass
class TrainingRow:
    user_email: str
    user_name: str
    topic: str
    provider: str | None
    completed_date: datetime
    expires_date: datetime | None
    expired: bool
    expiring_soon: bool


@dataclass
class DelegationRow:
    user_email: str
    user_name: str
    study_role: str
    delegated_tasks: list[str]
    start_date: datetime
    end_date: datetime | None
    signed_at: datetime | None
    signed_by_pi_name: str | None


# ── 1. Per-trial delegation log ───────────────────────────────────────


@dataclass
class DelegationLogData:
    trial_id: str
    trial_title: str
    sponsor: str
    indication: str
    pi_signature_count: int
    unsigned_count: int
    rows: list[DelegationRow] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build_delegation_log_pdf(data: DelegationLogData) -> bytes:
    """Render the ICH E6 §4.1.5 delegation log for a trial."""
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.7 * inch,
        title=f"Delegation log — {data.trial_title}",
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc.addPageTemplates(
        [
            PageTemplate(
                id="dl",
                frames=[frame],
                onPage=make_page_decorations("Delegation log · ICH E6 §4.1.5"),
            )
        ]
    )
    styles = make_pdf_styles()
    story: list[Any] = []
    story.append(Paragraph("DELEGATION LOG", styles["Eyebrow"]))
    story.append(Paragraph(data.trial_title, styles["Title"]))
    meta_bits = []
    if data.sponsor:
        meta_bits.append(f"Sponsor: {data.sponsor}")
    if data.indication:
        meta_bits.append(f"Indication: {data.indication}")
    meta_bits.append(f"Generated: {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    story.append(Paragraph(" · ".join(meta_bits), styles["Body"]))
    story.append(
        Paragraph(
            f"{data.pi_signature_count} signed; {data.unsigned_count} pending PI countersignature.",
            styles["Stat"],
        )
    )
    story.append(Spacer(1, 6))

    header = [
        Paragraph("Member", styles["CellHead"]),
        Paragraph("Role", styles["CellHead"]),
        Paragraph("Delegated tasks", styles["CellHead"]),
        Paragraph("Period", styles["CellHead"]),
        Paragraph("PI signature", styles["CellHead"]),
    ]
    table_rows: list[list[Paragraph]] = [header]
    for r in data.rows:
        tasks = "; ".join(r.delegated_tasks) if r.delegated_tasks else "—"
        period = r.start_date.strftime("%Y-%m-%d")
        period += " → " + (r.end_date.strftime("%Y-%m-%d") if r.end_date else "ongoing")
        sig = (
            f"{r.signed_by_pi_name or '—'}\n{r.signed_at.strftime('%Y-%m-%d')}"
            if r.signed_at
            else "[ UNSIGNED ]"
        )
        member = f"{r.user_name or '—'}\n{r.user_email}"
        table_rows.append(
            [
                Paragraph(member, styles["Cell"]),
                Paragraph(r.study_role, styles["Cell"]),
                Paragraph(tasks, styles["Cell"]),
                Paragraph(period, styles["Cell"]),
                Paragraph(sig, styles["Cell"]),
            ]
        )
    table = Table(
        table_rows,
        colWidths=[1.4 * inch, 1.2 * inch, 2.3 * inch, 1.3 * inch, 1.2 * inch],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), LIGHT),
                ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    if data.unsigned_count > 0:
        story.append(Spacer(1, 6))
        story.append(
            Paragraph(
                f"[ {data.unsigned_count} entries pending PI countersignature — "
                "ICH E6 §4.1.5 requires the PI to sign delegation. ]",
                styles["Amber"],
            )
        )
    doc.build(story)
    return buf.getvalue()


# ── 2. Per-site training matrix ───────────────────────────────────────


@dataclass
class TrainingMatrixData:
    site_id: str
    site_name: str
    site_code: str | None
    members: int
    expired_count: int
    expiring_soon_count: int
    rows: list[TrainingRow] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build_training_matrix_pdf(data: TrainingMatrixData) -> bytes:
    """Render the ICH E6 §4.2.4 training matrix for a site."""
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.7 * inch,
        title=f"Training matrix — {data.site_name}",
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc.addPageTemplates(
        [
            PageTemplate(
                id="tm",
                frames=[frame],
                onPage=make_page_decorations("Training matrix · ICH E6 §4.2.4"),
            )
        ]
    )
    styles = make_pdf_styles()
    story: list[Any] = []
    story.append(Paragraph("SITE TRAINING MATRIX", styles["Eyebrow"]))
    title_bits = [data.site_name]
    if data.site_code:
        title_bits.append(f"({data.site_code})")
    story.append(Paragraph(" ".join(title_bits), styles["Title"]))
    story.append(
        Paragraph(
            f"Generated: {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
            styles["Body"],
        )
    )
    story.append(
        Paragraph(
            f"{data.members} member(s) · "
            f"{data.expired_count} expired · {data.expiring_soon_count} expiring soon.",
            styles["Stat"],
        )
    )
    story.append(Spacer(1, 6))

    if not data.rows:
        story.append(
            Paragraph(
                "No training records on file for this site's members.",
                styles["Amber"],
            )
        )
    else:
        header = [
            Paragraph("Member", styles["CellHead"]),
            Paragraph("Training", styles["CellHead"]),
            Paragraph("Provider", styles["CellHead"]),
            Paragraph("Completed", styles["CellHead"]),
            Paragraph("Expires", styles["CellHead"]),
            Paragraph("Status", styles["CellHead"]),
        ]
        table_rows: list[list[Paragraph]] = [header]
        for r in data.rows:
            status = "OK"
            if r.expired:
                status = "EXPIRED"
            elif r.expiring_soon:
                status = "DUE SOON"
            member = f"{r.user_name or '—'}\n{r.user_email}"
            table_rows.append(
                [
                    Paragraph(member, styles["Cell"]),
                    Paragraph(r.topic, styles["Cell"]),
                    Paragraph(r.provider or "—", styles["Cell"]),
                    Paragraph(r.completed_date.strftime("%Y-%m-%d"), styles["Cell"]),
                    Paragraph(
                        r.expires_date.strftime("%Y-%m-%d") if r.expires_date else "—",
                        styles["Cell"],
                    ),
                    Paragraph(status, styles["Cell"]),
                ]
            )
        table = Table(
            table_rows,
            colWidths=[1.5 * inch, 1.4 * inch, 1.0 * inch, 0.9 * inch, 0.9 * inch, 0.8 * inch],
            repeatRows=1,
        )
        # Highlight EXPIRED rows in amber.
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 1), (-1, -1), LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for idx, r in enumerate(data.rows, start=1):
            if r.expired or r.expiring_soon:
                style.append(("TEXTCOLOR", (5, idx), (5, idx), AMBER))
        table.setStyle(TableStyle(style))
        story.append(table)
    doc.build(story)
    return buf.getvalue()


# ── 3. User-admin audit log ───────────────────────────────────────────


@dataclass
class AuditLogRow:
    created_at: datetime
    actor_email: str | None
    action: str
    target_email: str | None
    scope_type: str | None
    scope_id: str | None
    payload_json: str
    ip_address: str | None


@dataclass
class AuditLogData:
    title: str
    from_ts: datetime | None
    to_ts: datetime | None
    filter_summary: str
    rows: list[AuditLogRow] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _truncate_payload(payload_json: str, *, max_len: int = 200) -> str:
    """Render payload JSON in a compact human-readable form, truncating
    at max_len so a single audit entry doesn't blow up the layout."""
    try:
        decoded = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return payload_json[:max_len]
    if not decoded:
        return "—"
    parts = []
    for k, v in decoded.items():
        if isinstance(v, list | dict):
            v = json.dumps(v, default=str)
        s = f"{k}={v}"
        parts.append(s)
    full = "; ".join(parts)
    if len(full) > max_len:
        return full[: max_len - 1] + "…"
    return full


def build_audit_log_pdf(data: AuditLogData) -> bytes:
    """Render a filtered user-admin audit log."""
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.7 * inch,
        title=data.title,
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc.addPageTemplates(
        [
            PageTemplate(
                id="aud",
                frames=[frame],
                onPage=make_page_decorations("User-admin audit log · 21 CFR Part 11 §11.10(e)"),
            )
        ]
    )
    styles = make_pdf_styles()
    story: list[Any] = []
    story.append(Paragraph("USER-ADMIN AUDIT LOG", styles["Eyebrow"]))
    story.append(Paragraph(data.title, styles["Title"]))
    range_bits = []
    if data.from_ts:
        range_bits.append(f"from {data.from_ts.strftime('%Y-%m-%d')}")
    if data.to_ts:
        range_bits.append(f"to {data.to_ts.strftime('%Y-%m-%d')}")
    range_bits.append(f"generated {data.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    story.append(Paragraph(" · ".join(range_bits), styles["Body"]))
    if data.filter_summary:
        story.append(Paragraph(data.filter_summary, styles["Body"]))
    story.append(Paragraph(f"{len(data.rows)} event(s) in range.", styles["Stat"]))
    story.append(Spacer(1, 6))

    header = [
        Paragraph("When", styles["CellHead"]),
        Paragraph("Actor", styles["CellHead"]),
        Paragraph("Action", styles["CellHead"]),
        Paragraph("Target", styles["CellHead"]),
        Paragraph("Scope", styles["CellHead"]),
        Paragraph("Payload", styles["CellHead"]),
    ]
    table_rows: list[list[Paragraph]] = [header]
    for r in data.rows:
        scope = "—"
        if r.scope_type:
            scope = f"{r.scope_type}"
            if r.scope_id:
                scope += f":{r.scope_id[:8]}"
        when = r.created_at.strftime("%Y-%m-%d %H:%M")
        table_rows.append(
            [
                Paragraph(when, styles["Cell"]),
                Paragraph(r.actor_email or "—", styles["Cell"]),
                Paragraph(r.action, styles["Cell"]),
                Paragraph(r.target_email or "—", styles["Cell"]),
                Paragraph(scope, styles["Cell"]),
                Paragraph(_truncate_payload(r.payload_json), styles["Cell"]),
            ]
        )
    table = Table(
        table_rows,
        colWidths=[1.0 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch, 0.8 * inch, 2.5 * inch],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), LIGHT),
                ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return buf.getvalue()


# ── Assemble helpers (assembled from DB rows by the endpoint layer) ─


def assemble_delegation_log_data(
    *,
    trial_id: str,
    trial_title: str,
    sponsor: str,
    indication: str,
    entries: Sequence[Any],  # DelegationLogEntry rows
    users_by_id: dict[str, Any],  # user_id -> User row
    profiles_by_id: dict[str, Any],  # user_id -> UserProfile row
) -> DelegationLogData:
    """Convert raw DB rows into a renderable dataclass."""
    rows: list[DelegationRow] = []
    signed = 0
    unsigned = 0
    for e in entries:
        user = users_by_id.get(e.user_id)
        profile = profiles_by_id.get(e.user_id)
        name = " ".join(
            filter(None, [profile and profile.first_name, profile and profile.last_name])
        )
        pi_user = users_by_id.get(e.signed_by_pi_user_id) if e.signed_by_pi_user_id else None
        pi_profile = profiles_by_id.get(e.signed_by_pi_user_id) if e.signed_by_pi_user_id else None
        pi_name = " ".join(
            filter(
                None,
                [pi_profile and pi_profile.first_name, pi_profile and pi_profile.last_name],
            )
        )
        if not pi_name and pi_user:
            pi_name = pi_user.email or ""
        try:
            tasks = json.loads(e.delegated_tasks_json or "[]")
            if not isinstance(tasks, list):
                tasks = []
        except json.JSONDecodeError:
            tasks = []
        rows.append(
            DelegationRow(
                user_email=user.email if user else "—",
                user_name=name,
                study_role=e.study_role,
                delegated_tasks=[str(t) for t in tasks],
                start_date=e.start_date,
                end_date=e.end_date,
                signed_at=e.signed_at,
                signed_by_pi_name=pi_name or None,
            )
        )
        if e.signed_at:
            signed += 1
        else:
            unsigned += 1
    return DelegationLogData(
        trial_id=trial_id,
        trial_title=trial_title,
        sponsor=sponsor,
        indication=indication,
        pi_signature_count=signed,
        unsigned_count=unsigned,
        rows=rows,
    )
