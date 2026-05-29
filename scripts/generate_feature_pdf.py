"""Generate the Clinical Research Assistant feature-guide PDF.

Run:
    uv run --with reportlab --with matplotlib python scripts/generate_feature_pdf.py

Outputs:
    feature-guide.pdf  (project root)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
HERO_PATH = ROOT / "scripts" / "_hero.png"
OUT_PATH = ROOT / "feature-guide.pdf"

# ── Brand palette ──────────────────────────────────────────────────────────
NAVY = colors.HexColor("#0F2A47")
TEAL = colors.HexColor("#1E6E8C")
ACCENT = colors.HexColor("#2A9D8F")
SAND = colors.HexColor("#F4F1EA")
SLATE = colors.HexColor("#2D3748")
MUTED = colors.HexColor("#6B7280")
LIGHT = colors.HexColor("#F7FAFC")
BORDER = colors.HexColor("#E2E8F0")


# ── Hero image ─────────────────────────────────────────────────────────────


def build_hero(path: Path) -> None:
    """Stylised forest-plot hero image — on-brand for the product."""
    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=180)
    fig.patch.set_facecolor("#0F2A47")
    ax.set_facecolor("#0F2A47")

    studies = [
        ("Anderson 2014", 0.43, 0.22, 0.84),
        ("Bekele 2016", 0.44, 0.14, 1.42),
        ("Chen 2018", 0.43, 0.25, 0.75),
        ("Diaz 2019", 0.50, 0.20, 1.23),
        ("Eriksen 2020", 0.71, 0.23, 2.25),
        ("Faruq 2021", 0.40, 0.19, 0.83),
        ("Garcia 2022", 0.37, 0.10, 1.40),
        ("Huang 2023", 0.43, 0.23, 0.80),
    ]
    pooled_rr, pooled_lo, pooled_hi = 0.44, 0.33, 0.58

    n = len(studies)
    y_positions = np.arange(n, 0, -1)

    for y, (_name, rr, lo, hi) in zip(y_positions, studies, strict=True):
        ax.plot([lo, hi], [y, y], color="#9CC9DA", linewidth=1.6, alpha=0.9)
        ax.plot([lo, lo], [y - 0.18, y + 0.18], color="#9CC9DA", linewidth=1.4)
        ax.plot([hi, hi], [y - 0.18, y + 0.18], color="#9CC9DA", linewidth=1.4)
        ax.scatter(
            [rr],
            [y],
            s=110,
            marker="s",
            color="#2A9D8F",
            edgecolors="white",
            linewidths=1.2,
            zorder=3,
        )

    # Diamond for the pooled estimate
    diamond_y = 0
    diamond = plt.Polygon(
        [
            (pooled_lo, diamond_y),
            (pooled_rr, diamond_y + 0.35),
            (pooled_hi, diamond_y),
            (pooled_rr, diamond_y - 0.35),
        ],
        closed=True,
        facecolor="#F4A261",
        edgecolor="white",
        linewidth=1.5,
        zorder=4,
    )
    ax.add_patch(diamond)

    ax.axvline(1.0, color="#9CC9DA", linewidth=0.9, linestyle=(0, (4, 4)), alpha=0.6)

    ax.set_xscale("log")
    ax.set_xlim(0.08, 3.5)
    ax.set_ylim(-1.2, n + 0.8)
    ax.set_xticks([0.1, 0.25, 0.5, 1.0, 2.0])
    ax.set_xticklabels(
        ["0.1", "0.25", "0.5", "1.0", "2.0"],
        color="#CFE2EC",
        fontsize=10,
    )
    ax.set_yticks([])

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis="x", colors="#CFE2EC", length=0, pad=6)
    ax.tick_params(axis="y", length=0)

    ax.text(
        0.08,
        n + 0.4,
        "Pooled risk ratio across included studies — favours intervention ←  → favours control",
        color="#CFE2EC",
        fontsize=10,
        alpha=0.85,
    )

    fig.tight_layout(pad=0.6)
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight", dpi=180)
    plt.close(fig)


# ── PDF styles ─────────────────────────────────────────────────────────────

base = getSampleStyleSheet()

styles = {
    "TitleBig": ParagraphStyle(
        "TitleBig",
        parent=base["Title"],
        fontName="Helvetica-Bold",
        fontSize=32,
        leading=38,
        textColor=colors.white,
        alignment=TA_LEFT,
        spaceAfter=6,
    ),
    "Tagline": ParagraphStyle(
        "Tagline",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#CFE2EC"),
        alignment=TA_LEFT,
        spaceAfter=0,
    ),
    "EyebrowLight": ParagraphStyle(
        "EyebrowLight",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        textColor=colors.HexColor("#9CC9DA"),
        alignment=TA_LEFT,
        spaceAfter=6,
    ),
    "H1": ParagraphStyle(
        "H1",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=NAVY,
        spaceBefore=0,
        spaceAfter=8,
    ),
    "H2": ParagraphStyle(
        "H2",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=TEAL,
        spaceBefore=10,
        spaceAfter=4,
    ),
    "H3": ParagraphStyle(
        "H3",
        parent=base["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=NAVY,
        spaceBefore=6,
        spaceAfter=2,
    ),
    "Body": ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        textColor=SLATE,
        alignment=TA_JUSTIFY,
        spaceAfter=6,
    ),
    "BodyTight": ParagraphStyle(
        "BodyTight",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=SLATE,
        alignment=TA_LEFT,
        spaceAfter=4,
    ),
    "Outcome": ParagraphStyle(
        "Outcome",
        parent=base["BodyText"],
        fontName="Helvetica-Oblique",
        fontSize=10,
        leading=13,
        textColor=ACCENT,
        alignment=TA_LEFT,
        spaceAfter=0,
    ),
    "Footer": ParagraphStyle(
        "Footer",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=MUTED,
        alignment=TA_CENTER,
    ),
    "CostFigure": ParagraphStyle(
        "CostFigure",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=26,
        leading=30,
        textColor=NAVY,
        alignment=TA_LEFT,
        spaceAfter=2,
    ),
    "CostLabel": ParagraphStyle(
        "CostLabel",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=TEAL,
        alignment=TA_LEFT,
        spaceAfter=4,
    ),
    "Cell": ParagraphStyle(
        "Cell",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=SLATE,
        alignment=TA_LEFT,
        spaceAfter=0,
    ),
    "CellHead": ParagraphStyle(
        "CellHead",
        parent=base["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=TA_LEFT,
        spaceAfter=0,
    ),
    "Bullet": ParagraphStyle(
        "Bullet",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=SLATE,
        alignment=TA_LEFT,
        spaceAfter=6,
        leftIndent=12,
        bulletIndent=0,
    ),
}


# ── Page templates ─────────────────────────────────────────────────────────


def cover_page(canvas, doc):
    canvas.saveState()
    w, h = LETTER
    # Navy band
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 4.6 * inch, w, 4.6 * inch, fill=1, stroke=0)
    # Accent stripe
    canvas.setFillColor(ACCENT)
    canvas.rect(0, h - 4.7 * inch, w, 0.08 * inch, fill=1, stroke=0)
    # Footer
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawCentredString(w / 2, 0.45 * inch, "Clinical Research Assistant  ·  Feature Guide")
    canvas.restoreState()


def content_page(canvas, doc):
    canvas.saveState()
    w, h = LETTER
    # Top accent rule
    canvas.setFillColor(ACCENT)
    canvas.rect(0.6 * inch, h - 0.6 * inch, 0.7 * inch, 0.05 * inch, fill=1, stroke=0)
    # Header label
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(1.4 * inch, h - 0.6 * inch + 0.01 * inch, "CLINICAL RESEARCH ASSISTANT")
    # Footer
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(0.6 * inch, 0.45 * inch, "Feature Guide")
    canvas.drawRightString(w - 0.6 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


# ── Reusable builders ──────────────────────────────────────────────────────


def workflow_card(num: str, title: str, body: str, outcome: str) -> KeepTogether:
    num_para = Paragraph(
        f'<font color="#2A9D8F" size="20"><b>{num}</b></font>',
        styles["H3"],
    )
    title_para = Paragraph(f"<b>{title}</b>", styles["H2"])
    body_para = Paragraph(body, styles["Body"])
    outcome_para = Paragraph(f"<b>Outcome —</b> {outcome}", styles["Outcome"])

    inner = Table(
        [[num_para, [title_para, body_para, outcome_para]]],
        colWidths=[0.55 * inch, None],
    )
    inner.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    wrapper = Table([[inner]], colWidths=[None])
    wrapper.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("LINEBEFORE", (0, 0), (0, 0), 3, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return KeepTogether([wrapper, Spacer(1, 8)])


def differentiator_table(rows: list[tuple[str, str]]) -> Table:
    data = [["Differentiator", "Why it matters"]]
    for d, w in rows:
        data.append(
            [Paragraph(f"<b>{d}</b>", styles["BodyTight"]), Paragraph(w, styles["BodyTight"])]
        )
    t = Table(data, colWidths=[2.0 * inch, 4.4 * inch])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ]
        )
    )
    return t


def cost_card(figure: str, label: str, sub: str, width: float) -> Table:
    body = [
        [Paragraph(label, styles["CostLabel"])],
        [Paragraph(figure, styles["CostFigure"])],
        [Paragraph(sub, styles["BodyTight"])],
    ]
    t = Table(body, colWidths=[width])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SAND),
                ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return t


def matrix_table(
    headers: list[str],
    rows: list[list[str]],
    col_widths: list[float],
    header_bg: colors.Color = NAVY,
) -> Table:
    """Compact multi-column reference table for the appendices."""
    data = [[Paragraph(h, styles["CellHead"]) for h in headers]]
    for row in rows:
        data.append([Paragraph(c, styles["Cell"]) for c in row])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ]
        )
    )
    return t


# ── Document build ─────────────────────────────────────────────────────────


def build_pdf() -> None:
    build_hero(HERO_PATH)

    doc = BaseDocTemplate(
        str(OUT_PATH),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title="Clinical Research Assistant — Feature Guide",
        author="Clinical Research Assistant",
    )

    w, h = LETTER
    cover_frame = Frame(
        0.6 * inch,
        0.6 * inch,
        w - 1.2 * inch,
        h - 1.2 * inch,
        id="cover",
        showBoundary=0,
    )
    content_frame = Frame(
        0.6 * inch,
        0.7 * inch,
        w - 1.2 * inch,
        h - 1.5 * inch,
        id="content",
        showBoundary=0,
    )

    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[cover_frame], onPage=cover_page),
            PageTemplate(id="content", frames=[content_frame], onPage=content_page),
        ]
    )

    story: list = []

    # ── COVER ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.35 * inch))
    story.append(Paragraph("FEATURE GUIDE  ·  2026", styles["EyebrowLight"]))
    story.append(Paragraph("Clinical Research<br/>Assistant", styles["TitleBig"]))
    story.append(Spacer(1, 0.18 * inch))
    story.append(
        Paragraph(
            "An AI co-investigator for systematic reviews, meta-analyses,<br/>"
            "and evidence synthesis — built for hospital research offices,<br/>"
            "academic medical centres, and clinical research organisations.",
            styles["Tagline"],
        )
    )
    story.append(Spacer(1, 0.55 * inch))

    hero = Image(str(HERO_PATH), width=7.0 * inch, height=3.65 * inch)
    story.append(hero)

    story.append(Spacer(1, 0.18 * inch))
    story.append(
        Paragraph(
            '<font color="#6B7280" size="9"><i>'
            "Sample forest plot output — every effect estimate traces back to a real database hit."
            "</i></font>",
            styles["BodyTight"],
        )
    )

    # Page 1 uses the cover template; switch to the content template *before*
    # the page break so page 2 onward render as white content pages (not under
    # the cover's navy band).
    from reportlab.platypus import NextPageTemplate

    story.insert(0, NextPageTemplate("cover"))
    story.append(NextPageTemplate("content"))
    story.append(PageBreak())

    # ── WHY ──────────────────────────────────────────────────────────────
    story.append(Paragraph("Why this exists", styles["H1"]))
    story.append(
        Paragraph(
            "A high-quality systematic review takes a small team <b>6–12 months</b>: scoping the "
            "question, building search strategies, screening thousands of abstracts, extracting data, "
            "running the statistics, assessing bias, and writing it up. Most of that work is "
            "repetitive, "
            "methodology-bound, and easy to get wrong in ways that don't show up until a reviewer "
            "rejects the manuscript.",
            styles["Body"],
        )
    )
    story.append(
        Paragraph(
            "The <b>Clinical Research Assistant</b> compresses the methodological scaffolding into a "
            "guided, auditable workflow — without ever fabricating evidence. Your investigators stay "
            "in the driver's seat for clinical judgment. The assistant handles the parts that should "
            "never have been manual in the first place.",
            styles["Body"],
        )
    )

    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("What the assistant does", styles["H1"]))
    story.append(
        Paragraph(
            "Five guided evidence workflows, a continuous-monitoring service, a local research store "
            "that grows in value the more you use it, and a regulatory-grade <b>electronic "
            "data-capture (eCRF/EDC)</b> subsystem for running your own studies. Each workflow "
            "launches from natural language or a slash command and produces a structured, "
            "citation-anchored output you can take into a manuscript, a registration, or a grant "
            "application.",
            styles["Body"],
        )
    )
    story.append(Spacer(1, 0.05 * inch))

    workflows = [
        (
            "1",
            "Meta-analysis workflow",
            "End-to-end: from a research question to a forest plot. The assistant walks the user "
            "through <b>PICO</b> confirmation, runs the literature search across every database your "
            "institution has licensed, presents candidate studies for inclusion/exclusion, captures "
            "the extraction table, then computes the pooled effect estimate with heterogeneity "
            "statistics and renders a publication-grade forest plot inside an isolated compute "
            "sandbox.",
            "what used to be a multi-week analyst engagement is a single guided session. "
            "Every PMID in the output traces back to a real database hit — the model is structurally "
            "prevented from inventing citations.",
        ),
        (
            "2",
            "Search-strategy builder",
            "Constructs Boolean queries with MeSH terms, field tags, and database-specific syntax "
            "across <b>PubMed, Europe PMC, Embase, Cochrane Library, Scopus, and Web of Science</b> "
            "— whichever your institution has licensed, in a single fan-out. Iterates on "
            "<b>broaden / tighten</b> suggestions with the user until the strategy is "
            "registration-ready.",
            "information specialists get a defensible, multi-database, reproducible search string in "
            "minutes instead of half a day, with the iteration history preserved.",
        ),
        (
            "3",
            "Systematic-review protocol drafter",
            "Generates a <b>PRISMA-P–aligned</b> protocol skeleton — background, objectives, "
            "eligibility, search methods, screening plan, data items, risk-of-bias plan, synthesis "
            "approach — ready to deposit in <b>PROSPERO</b>.",
            "a protocol-ready first draft your methodologist edits, not writes from scratch. Helps "
            "surface gaps before they cost you a peer-review round.",
        ),
        (
            "4",
            "Risk-of-Bias assessor",
            "Supports the major instruments: <b>RoB 2</b> (RCTs), <b>ROBINS-I</b> (non-randomised), "
            "<b>Newcastle-Ottawa</b> (observational), <b>QUADAS-2</b> (diagnostic accuracy). Walks "
            "domain-by-domain, asks the operator the judgement questions, and produces the structured "
            "assessment table for the review.",
            "consistency across reviewers and a clean audit trail of why each judgement landed where "
            "it did.",
        ),
        (
            "5",
            "General clinical Q&amp;A",
            "For the questions that aren't a full systematic review: background reading, definition "
            "of methods, navigation of guidelines. The assistant uses web and Wikipedia tools, "
            "but is <b>hard-prevented from quoting effect sizes, PMIDs, or guideline citations from "
            "training data</b> — a regex-level validator blocks unsupported clinical claims before "
            "they reach the user.",
            "safe-by-default exploratory Q&amp;A that won't seed a manuscript with hallucinated "
            "evidence.",
        ),
        (
            "6",
            "Living-review watches (scheduled monitoring)",
            "A meta-analysis or systematic review doesn't have to be a snapshot. Pin a PICO and a "
            "search strategy as a <b>watch</b>, set a cadence (daily / weekly / monthly), and the "
            "assistant re-runs the search on schedule, diffs against the baseline corpus, triages "
            "newly published papers against the original PICO, and <b>notifies the team when "
            "something materially shifts the evidence base</b>.",
            "living reviews stop drifting out of date. Guideline committees and HTA bodies get "
            "alerted to practice-changing evidence as it lands.",
        ),
        (
            "7",
            "Electronic data capture (eCRF / EDC)",
            "Beyond synthesising other people's evidence, the assistant runs <b>your own studies' "
            "data collection</b> — an electronic case-report-form (eCRF) and data-capture (EDC) "
            "subsystem built to <b>CDISC</b> and <b>21 CFR Part 11 / ALCOA+</b> conventions. Paste a "
            "protocol and the assistant drafts CDASH-aligned forms plus a visit schedule (a designer "
            "reviews — never auto-publish); versioned, immutable definitions export as <b>CDISC "
            "ODM-XML</b>. Two capture surfaces — a <b>site EDC</b> screen and a consent-gated, "
            "magic-link <b>participant ePRO</b> surface — validate on entry, with hard/soft "
            "edit-checks feeding a full query workflow. Subject PHI lives in a <b>separate "
            "clinical-data store</b>, never sent to the model. E-signatures bind to the exact signed "
            "data, lock against edits, and roll up to a subject-casebook sign-off; source-data "
            "verification and a <b>tamper-proof, append-only audit trail enforced by the database</b> "
            "capture every change with who, when, old→new value, and reason.",
            "the weeks-long investigator↔data-manager round-trip at study start-up collapses to a "
            "guided session, and the resulting capture system is audit-ready by construction.",
        ),
    ]
    for num, title, body, outcome in workflows[:3]:
        story.append(workflow_card(num, title, body, outcome))

    story.append(PageBreak())
    for num, title, body, outcome in workflows[3:]:
        story.append(workflow_card(num, title, body, outcome))

    # ── DIFFERENTIATORS ──────────────────────────────────────────────────
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("What makes it different", styles["H1"]))
    differentiators = [
        (
            "Anti-hallucination enforced in code, not just prompt",
            "A regex validator and tool-gated PMID rule block fabricated citations even if the "
            "underlying model drifts. Your investigators can trust the bibliography.",
        ),
        (
            "Source-agnostic search across paid + open databases",
            "PubMed, Europe PMC, Embase, Cochrane Library, Scopus, Web of Science — all live "
            "behind a pluggable <b>PaperSource</b> interface and fanned out in parallel with "
            "cross-source de-duplication. One admin panel for credentials, rate-limits, "
            "enable/disable.",
        ),
        (
            "Local research cache + RAG over pulled content",
            "Every abstract, full-text article, MeSH lookup, and extraction table you pull lands in "
            "the local research store. A retrieval-augmented pipeline searches that store first, so "
            "the same paper isn't re-fetched — or re-billed — across reviews. Pull paid content "
            "once; reuse it across the entire research programme.",
        ),
        (
            "Sandboxed statistical compute",
            "All Python analysis runs inside a Docker container with networking disabled, "
            "read-only inputs, and capped CPU/memory. The model can run a random-effects "
            "meta-analysis without ever touching the host or the wider internet.",
        ),
        (
            "Workflow-gated tools",
            "The assistant cannot skip ahead. It can't run a meta-analysis before a PICO is "
            "confirmed; it can't fetch full text before studies are selected. The methodology "
            "<i>is</i> the guardrail.",
        ),
        (
            "Regulatory-grade data capture built in",
            "The eCRF/EDC subsystem follows <b>CDISC</b> (ODM-XML export, CDASH naming) and "
            "<b>21 CFR Part 11 / ALCOA+</b>: subject PHI in a separate store, e-signatures bound to "
            "the signed data, source-data verification, and an <b>append-only audit trail enforced "
            "by the database</b> — not just application code. Author a CRF, deploy it, and collect "
            "site + participant data without leaving the platform.",
        ),
        (
            "Flexible, secure deployment",
            "Deploy on-premises or in your cloud. <b>OAuth2 / OpenID Connect</b> integrates with "
            "your existing identity provider; <b>role-based access control</b> scopes each workflow "
            "per user; sensitive material (API keys, PHI) lives in your <b>secrets manager</b>, "
            "never in plaintext config.",
        ),
        (
            "Full conversation persistence",
            "Every turn — PICO, included studies, extraction table, generated code, plot — is "
            "stored. Reproducing an analysis a year later means re-opening the thread.",
        ),
    ]
    story.append(differentiator_table(differentiators))

    story.append(PageBreak())

    # ── COST ─────────────────────────────────────────────────────────────
    story.append(Paragraph("What it costs to run", styles["H1"]))
    story.append(
        Paragraph(
            "Two variable costs to plan around: LLM tokens on AWS Bedrock and web-search calls on "
            "Tavily. Everything else — paper database access, paper storage, the FastAPI app, "
            "the sandbox — sits on infrastructure you already own. Paid bibliographic databases "
            "(Embase, Scopus, Cochrane, etc.) are assumed to be covered by your existing "
            "institutional subscriptions.",
            styles["Body"],
        )
    )

    cost_left = cost_card(
        "$200 – $300",
        "ACTIVE RESEARCH DAY (PER USER)",
        "Covers Bedrock tokens + Tavily for a productive day: PICO refinement, multi-database "
        "search, screening + extraction across dozens of papers, statistical analysis with code "
        "generation, forest-plot rendering.",
        2.0 * inch,
    )
    cost_mid = cost_card(
        "≈ $0",
        "RE-OPEN / INSPECT A THREAD",
        "Past tool results (papers, extractions, generated code, plots) are already in the local "
        "store. Re-opening costs only the few tokens of the current turn.",
        2.0 * inch,
    )
    cost_right = cost_card(
        "≈ $0",
        "RE-USE A PAPER ACROSS REVIEWS",
        "The RAG pipeline hits the local research store first. The same landmark trial reused "
        "across cardio / onco / ID reviews is paid for once.",
        2.0 * inch,
    )

    cost_row = Table(
        [[cost_left, cost_mid, cost_right]],
        colWidths=[2.43 * inch, 2.43 * inch, 2.43 * inch],
    )
    cost_row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
                ("LEFTPADDING", (1, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(Spacer(1, 0.05 * inch))
    story.append(cost_row)
    story.append(Spacer(1, 0.14 * inch))

    story.append(Paragraph("Cost-control levers built in", styles["H2"]))

    # Cost-control levers — a small reference table
    levers = [
        (
            "Local cache + RAG",
            "Re-use of pulled abstracts and full text across reviews — no repeat API spend on "
            "content you've already paid for.",
        ),
        (
            "Workflow-gated tools",
            "Expensive tools (sandbox, full-text fetch) are unreachable until the workflow stage "
            "needs them — no idle calls.",
        ),
        (
            "Per-turn ceilings",
            "<b>max_model_requests</b> and <b>agent_timeout_seconds</b> bound runaway loops to a "
            "known maximum cost.",
        ),
        (
            "Configurable model tier per workflow",
            "Drop to a cheaper Sonnet / Haiku variant when full Opus reasoning isn't required.",
        ),
        (
            "Tavily scoped to two flows",
            "Tavily is consumed only by general Q&amp;A + protocol drafting; the clinical search "
            "runs against bibliographic databases and does not consume Tavily credits.",
        ),
    ]
    lever_data = [["Cost-control lever", "How it keeps spend predictable"]]
    for name, why in levers:
        lever_data.append(
            [Paragraph(f"<b>{name}</b>", styles["BodyTight"]), Paragraph(why, styles["BodyTight"])]
        )
    lever_table = Table(lever_data, colWidths=[2.0 * inch, 4.4 * inch])
    lever_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), TEAL),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ]
        )
    )
    story.append(Spacer(1, 0.06 * inch))
    story.append(lever_table)
    story.append(Spacer(1, 0.1 * inch))
    story.append(
        Paragraph(
            "A token-heavy day still lands comfortably <b>below the cost of an equivalent "
            "human-analyst day</b> — and per-paper marginal cost flattens as your local store grows.",
            styles["Body"],
        )
    )

    story.append(PageBreak())

    # ── DEPLOYMENT & GOVERNANCE ──────────────────────────────────────────
    story.append(Paragraph("Deployment &amp; governance", styles["H1"]))
    governance = [
        (
            "Flexible deployment",
            "Runs on-premises or in your cloud (AWS, GCP, Azure). Single-tenant by default; same "
            "codebase, your choice of trust boundary.",
        ),
        (
            "Authentication",
            "OAuth2 / OpenID Connect — drops into Okta, Azure AD, Auth0, Keycloak, or your "
            "homegrown IdP. No bespoke user database.",
        ),
        (
            "Authorisation",
            "Role-based access control per user. New-researcher onboarding is a one-step "
            "provisioning action that scopes their search-API entitlements, model access, and "
            "visibility into shared review threads.",
        ),
        (
            "Sensitive data",
            "API keys, Bedrock credentials, and any PHI live in a secrets manager (AWS Secrets "
            "Manager, HashiCorp Vault, etc.) — never in plaintext config or SQLite.",
        ),
        (
            "Data egress",
            "Outbound traffic is limited to your configured bibliographic APIs and your Bedrock "
            "endpoint. No telemetry. No shared multi-tenant cloud — LLM calls go to your own AWS "
            "account, so PHI-grade compute stays inside your trust boundary.",
        ),
        (
            "Audit",
            "Every turn is persisted with full message + tool-call history. Re-opening a thread "
            "reproduces the analysis exactly. Exportable as part of a regulatory submission package.",
        ),
    ]
    gov_data = [["Area", "What you get"]]
    for name, why in governance:
        gov_data.append(
            [Paragraph(f"<b>{name}</b>", styles["BodyTight"]), Paragraph(why, styles["BodyTight"])]
        )
    gov_table = Table(gov_data, colWidths=[1.6 * inch, 4.8 * inch])
    gov_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ]
        )
    )
    story.append(Spacer(1, 0.05 * inch))
    story.append(gov_table)

    story.append(PageBreak())

    # ── ROADMAP ──────────────────────────────────────────────────────────
    story.append(Paragraph("On the roadmap", styles["H1"]))
    story.append(
        Paragraph(
            "Beyond the v1 capability set above, the architecture was built to absorb these without "
            "rebuilding the core. Each is a tractable next increment, not a moonshot.",
            styles["Body"],
        )
    )

    story.append(Paragraph("Source-document extraction", styles["H2"]))
    story.append(
        Paragraph(
            "The eCRF / EDC subsystem (§7 above) now ships. The remaining piece of automated data "
            "collection is <b>source-document extraction</b> — point the assistant at structured "
            "exports from your EHR / registry / trial-management system and have it populate the "
            "extraction table (or pre-fill eCRF instances) for retrospective studies or "
            "patient-level meta-analyses, with an audit trail of which source row produced which "
            "output cell.",
            styles["Body"],
        )
    )

    story.append(Paragraph("Deepening eCRF compliance toward a formal validation pack", styles["H2"]))
    story.append(
        Paragraph(
            "The eCRF subsystem is built to Part 11 / ALCOA+ conventions; the next increments "
            "formalise it for an audited deployment: full password re-authentication at signing, "
            "study-level (not just subject-level) lock, and a documented computer-system-validation "
            "(IQ/OQ/PQ) package.",
            styles["Body"],
        )
    )

    story.append(Paragraph("Patient-facing research handouts", styles["H2"]))
    story.append(
        Paragraph(
            "Generate <b>plain-language summaries</b> of a study's objectives, what participation "
            "involves, and what the early evidence suggests — at a configurable reading level, in "
            "the patient's preferred language. Designed for <b>research participant recruitment</b>, "
            "<b>shared-decision-making conversations</b>, and <b>post-study return-of-results</b> "
            "obligations under modern IRB / ethics guidance. Sources back to the same evidence base "
            "the clinical workflow uses, so the lay summary and the manuscript are in lock-step.",
            styles["Body"],
        )
    )

    story.append(Paragraph("Research-gap analysis specialist", styles["H2"]))
    story.append(
        Paragraph(
            "Given a body of literature, surface where the evidence base is thin — by population, "
            "intervention, outcome, geography, or study design — so investigators can prioritise "
            "the next grant proposal where the field actually needs new data.",
            styles["Body"],
        )
    )

    story.append(Paragraph("Group-level living-review subscriptions", styles["H2"]))
    story.append(
        Paragraph(
            "Group watches with quorum-based notification rules — designed for guideline committees "
            "and HTA bodies who need consensus signalling on practice-changing evidence rather than "
            "per-user alerts.",
            styles["Body"],
        )
    )

    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("In short", styles["H2"]))
    story.append(
        Paragraph(
            '<font color="#6B7280" size="9"><i>'
            "If your researchers spend more time finding and formatting evidence than interpreting "
            "it, this is what you point them at. They keep the clinical judgement. The assistant "
            "absorbs the methodological choreography — and writes it down as it goes."
            "</i></font>",
            styles["BodyTight"],
        )
    )

    # ── APPENDIX A — CAPABILITY MATRIX ───────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Appendix A — Capability Matrix", styles["H1"]))
    cap_headers = [
        "Workflow",
        "Trigger / slash",
        "Inputs collected",
        "Tools available",
        "Structured outputs",
        "Hardened guardrails",
    ]
    cap_rows = [
        [
            "<b>Meta-analysis</b>",
            '"meta-analysis on…", "pooled effect of…", /meta',
            "PICO → included studies → extraction table",
            "search_papers, rag_search, mesh_lookup, fetch_pmc_fulltext, sandbox_exec, calculator",
            "PICO card, study-selection card, extraction card, forest plot + summary",
            "PMIDs must come from search; plot rules enforced in sandbox",
        ],
        [
            "<b>Search strategy</b>",
            '"build a search strategy…", /search',
            "Concept terms, MeSH, filters",
            "mesh_lookup, search_papers (preview counts)",
            "Boolean string per database, MeSH map, broaden/tighten suggestions",
            "None needed — output is the query itself",
        ],
        [
            "<b>SR protocol</b>",
            '"draft a protocol…", /protocol, /prisma',
            "PICO + scope choices",
            "web_search, wikipedia, fetch_document",
            "PRISMA-P–shaped protocol sections",
            "No effect-size or PMID synthesis allowed",
        ],
        [
            "<b>Risk of Bias</b>",
            '"risk of bias…", "RoB 2", /rob',
            "Tool choice + per-study source",
            "fetch_pmc_fulltext, rag_search, read_file",
            "Domain-by-domain judgements with rationale",
            "Operator must confirm each judgement",
        ],
        [
            "<b>General Q&amp;A</b>",
            "Everything else, /general",
            "Free-form question",
            "web_search, wikipedia, fetch_document, rag_search, read_file, describe_image",
            "Cited prose answer",
            "Regex validator blocks unsupported clinical claims",
        ],
        [
            "<b>Watch triage</b> <i>(background)</i>",
            "Configured per-PICO schedule",
            "New PMIDs since last run",
            "(none — text-only)",
            "Per-paper triage + run summary + optional notification",
            "Same anti-hallucination posture as user-facing flows",
        ],
        [
            "<b>eCRF authoring</b>",
            'Form Builder UI · "draft CRFs from protocol"',
            "Protocol text → form definitions",
            "ecrf_design specialist",
            "Versioned form definitions, ODM-XML export",
            "Integrity-validated definitions; human review before publish; AI never auto-publishes",
        ],
        [
            "<b>EDC capture</b> <i>(site)</i>",
            "Data Capture UI",
            "Subject data against deployed forms",
            "(collection API)",
            "Captured item data, queries, signatures",
            "Edit-checks (hard block / soft query); signed forms edit-locked; append-only audit "
            "(DB-enforced)",
        ],
        [
            "<b>ePRO capture</b> <i>(participant)</i>",
            "Magic-link /epro",
            "Patient-reported outcomes",
            "(token-scoped API)",
            "Captured item data",
            "Consent gate; per-subject token scope; same edit-checks + audit",
        ],
    ]
    story.append(Spacer(1, 0.05 * inch))
    story.append(
        matrix_table(
            cap_headers,
            cap_rows,
            col_widths=[
                0.78 * inch,
                1.12 * inch,
                1.12 * inch,
                1.42 * inch,
                1.26 * inch,
                1.6 * inch,
            ],
        )
    )

    story.append(Spacer(1, 0.16 * inch))
    story.append(Paragraph("Tool inventory", styles["H2"]))
    tool_rows = [
        [
            "Clinical",
            "search_papers",
            "Fan-out search across enabled databases (PubMed, Europe PMC, Embase, Cochrane, Scopus, "
            "Web of Science); dedupes by PMID → DOI → source-id",
        ],
        [
            "Clinical",
            "rag_search",
            "Retrieval-augmented search over the local research store — checks pulled content before "
            "re-issuing API calls",
        ],
        ["Clinical", "mesh_lookup", "MeSH term resolution + tree-walk"],
        [
            "Clinical",
            "fetch_pmc_fulltext",
            "Full-text retrieval; results cached into the local research store",
        ],
        [
            "Data science",
            "sandbox_exec",
            "Docker-isolated Python with pandas / numpy / scipy / statsmodels / matplotlib / seaborn "
            "/ forestplot — no network, capped CPU &amp; memory",
        ],
        ["Data science", "python_repl", "Lightweight in-process Python for arithmetic-only ops"],
        ["Data science", "calculator", "Safe arithmetic evaluator"],
        ["General", "web_search", "Tavily-backed search for non-clinical context"],
        ["General", "wikipedia", "Definitional / background lookups"],
        ["General", "fetch_document", "Fetch + extract text from arbitrary URLs"],
        ["General", "read_file", "Read user-uploaded documents (PDF, DOCX, TXT)"],
        ["General", "describe_image", "Vision model for chart / figure / scan interpretation"],
    ]
    story.append(Spacer(1, 0.04 * inch))
    story.append(
        matrix_table(
            ["Category", "Tool", "What it does"],
            tool_rows,
            col_widths=[0.9 * inch, 1.3 * inch, 5.1 * inch],
            header_bg=TEAL,
        )
    )

    story.append(Spacer(1, 0.16 * inch))
    story.append(Paragraph("Configuration surface", styles["H2"]))
    config_rows = [
        [
            "Paper-source enable / disable, rate limits, credentials",
            "Admin panel (credentials read from your secrets manager)",
            "Plug in your institution's PubMed / NCBI key, Embase, Cochrane, Scopus, Web of Science "
            "subscriptions — toggle without redeploying",
        ],
        ["Identity provider", "OIDC client config", "Wire to Okta / Azure AD / Auth0 / Keycloak / homegrown"],
        [
            "Role-based access",
            "Admin panel",
            "Per-user scoping of search entitlements, model tier, and shared-thread visibility",
        ],
        [
            "LLM model + region",
            "Per-workflow config",
            "Pin a Sonnet / Opus / Haiku version your governance team has approved; drop to cheaper "
            "tier for lighter workflows",
        ],
        [
            "Sandbox limits",
            "Config (sandbox_timeout_seconds, sandbox_memory_mb, sandbox_cpu)",
            "Cap compute per-analysis",
        ],
        [
            "Context window &amp; summarisation",
            "Config (context_window_messages, summarize_after_messages)",
            "Keep long meta-analysis threads from blowing past model limits",
        ],
        [
            "Runaway-loop bounds",
            "Config (max_model_requests, agent_timeout_seconds)",
            "Hard ceilings on per-turn cost",
        ],
    ]
    story.append(Spacer(1, 0.04 * inch))
    story.append(
        matrix_table(
            ["Setting", "Where", "Why an admin cares"],
            config_rows,
            col_widths=[2.0 * inch, 1.8 * inch, 3.5 * inch],
        )
    )

    # ── APPENDIX B — COMPLIANCE & GUARDRAILS ─────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Appendix B — Compliance &amp; guardrails at a glance", styles["H1"]))
    story.append(Spacer(1, 0.05 * inch))
    compliance = [
        "<b>No fabricated citations.</b> Clinical PMIDs are only emitted when they originated from "
        "a live search_papers call. The general Q&amp;A flow has a separate regex validator that "
        "rejects answers attempting to quote effect sizes, PMIDs, or guideline statements not "
        "present in the conversation's tool results.",
        "<b>No silent skipping.</b> Workflow specialists hide downstream tools until the upstream "
        "stage is confirmed by the user — e.g. sandbox_exec is invisible to the model until an "
        "extraction table exists.",
        "<b>No host access from generated code.</b> The Python sandbox runs in a Docker container "
        "with networking disabled, the script and inputs mounted read-only, and only a designated "
        "output directory writable.",
        "<b>No plaintext secrets.</b> API keys, model credentials, and any PHI live in your secrets "
        "manager and are read at runtime — never persisted in config files or the local database.",
        "<b>No third-party LLM intermediary.</b> Model calls go directly to your AWS Bedrock "
        "account; you choose the region; the assistant has no shared multi-tenant cloud.",
        "<b>Full reproducibility.</b> Threads persist every user message, model message, tool call, "
        "tool result, and structured output. The same thread re-opened tomorrow reproduces today's "
        "forest plot.",
    ]
    for item in compliance:
        story.append(Paragraph(item, styles["Bullet"], bulletText="•"))

    doc.build(story)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    build_pdf()
