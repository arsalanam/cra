"""Generate the Clinical Research Assistant feature-guide PDF.

Run:
    uv run --with reportlab --with matplotlib python scripts/generate_feature_pdf.py

Outputs:
    feature-guide.pdf  (project root)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
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

    story.append(PageBreak())
    story.append(Paragraph("", styles["Body"]))  # ensure new template applied

    # Switch to content template
    from reportlab.platypus import NextPageTemplate

    story.insert(0, NextPageTemplate("cover"))
    story.append(NextPageTemplate("content"))

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
    story.append(Paragraph("What it does today", styles["H1"]))
    story.append(
        Paragraph(
            "Five guided workflows plus a continuous-monitoring service. Each one launches from "
            "natural language or a slash command and produces a structured, citation-anchored output.",
            styles["Body"],
        )
    )
    story.append(Spacer(1, 0.05 * inch))

    workflows = [
        (
            "1",
            "Meta-analysis workflow",
            "End-to-end from a research question to a forest plot. The assistant walks the user "
            "through <b>PICO</b> confirmation, runs the literature search across configured "
            "databases, "
            "presents candidates for inclusion/exclusion, captures the extraction table, then "
            "computes the pooled effect estimate with heterogeneity statistics and renders a "
            "publication-grade forest plot inside an isolated compute sandbox.",
            "what used to be a multi-week analyst engagement becomes a single guided session. "
            "Every PMID traces back to a real database hit.",
        ),
        (
            "2",
            "Search-strategy builder",
            "Constructs Boolean queries with MeSH terms, field tags, and database-specific syntax "
            "(PubMed today; Embase / Cochrane on the roadmap). Iterates on <b>broaden / tighten</b> "
            "suggestions with the user until the strategy is registration-ready.",
            "defensible, reproducible search strings in minutes instead of half a day, with full "
            "iteration history preserved.",
        ),
        (
            "3",
            "Systematic-review protocol drafter",
            "Generates a <b>PRISMA-P–aligned</b> protocol skeleton — background, objectives, "
            "eligibility, search methods, screening plan, data items, risk-of-bias plan, synthesis "
            "approach — ready to deposit in <b>PROSPERO</b>.",
            "a protocol-ready first draft your methodologist edits, not writes from scratch.",
        ),
        (
            "4",
            "Risk-of-Bias assessor",
            "Supports the major instruments: <b>RoB 2</b> (RCTs), <b>ROBINS-I</b> (non-randomised), "
            "<b>Newcastle-Ottawa</b> (observational), <b>QUADAS-2</b> (diagnostic accuracy). Walks "
            "domain-by-domain, asks the judgement questions, and produces the structured assessment "
            "table.",
            "consistency across reviewers and a clean audit trail of why each judgement landed where "
            "it did.",
        ),
        (
            "5",
            "General clinical Q&amp;A",
            "Background reading, definitions, guideline navigation. Uses web and Wikipedia tools, "
            "but is <b>hard-prevented from quoting effect sizes, PMIDs, or guideline citations from "
            "training data</b> — a regex-level validator blocks unsupported clinical claims before "
            "they reach the user.",
            "safe-by-default exploratory Q&amp;A that won't seed a manuscript with hallucinated "
            "evidence.",
        ),
        (
            "6",
            "Living-review watches (scheduled monitoring)",
            "Pin a PICO and a search strategy as a <b>watch</b>, set a cadence (daily / weekly / "
            "monthly), and the assistant re-runs the search on schedule, diffs against the baseline "
            "corpus, triages newly published papers against the original PICO, and "
            "<b>notifies the team when something materially shifts the evidence base</b>.",
            "living reviews stop drifting out of date. Guideline committees and HTA bodies get "
            "alerted to practice-changing evidence as it lands.",
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
            "Anti-hallucination enforced in code",
            "A regex validator and tool-gated PMID rule block fabricated citations even if the "
            "underlying model drifts. Your investigators can trust the bibliography.",
        ),
        (
            "Source-agnostic search across paid + open databases",
            "PubMed, Europe PMC, Embase, Cochrane Library, Scopus, Web of Science — all live "
            "behind a pluggable <b>PaperSource</b> interface and fanned out in parallel with "
            "cross-source de-duplication. One admin panel for credentials and rate limits.",
        ),
        (
            "Local research cache + RAG over pulled content",
            "Every abstract, full-text article, and extraction table you pull lands in the local "
            "research store. A retrieval-augmented pipeline searches that store first, so the "
            "same paper isn't re-fetched — or re-billed — across reviews. Pull paid content "
            "once; reuse it across the entire research programme.",
        ),
        (
            "Sandboxed statistical compute",
            "All Python analysis runs inside a Docker container with networking disabled, "
            "read-only inputs, and capped CPU/memory. No host access; no leakage.",
        ),
        (
            "Workflow-gated tools",
            "The assistant can't skip ahead — no meta-analysis before a confirmed PICO; no full "
            "text before studies are selected. The methodology <i>is</i> the guardrail.",
        ),
        (
            "Flexible, role-aware deployment",
            "Deploy <b>on-premises or in your cloud</b> — the FastAPI core runs anywhere from a "
            "single workstation to a managed container platform. <b>Role-based access control</b> "
            "gates each workflow per user, and onboarding a new researcher is a one-step "
            "provisioning action that scopes their search-API keys, model access, and visibility "
            "into shared review threads from day one.",
        ),
        (
            "Full conversation persistence",
            "Every turn — PICO, included studies, extraction table, generated code, plot — is "
            "stored. Reproducing an analysis a year later is just re-opening the thread.",
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
        "Envelope for a productive day of meta-analysis work: PICO refinement, multi-database "
        "search, screening + extraction across dozens of papers, statistical analysis with code "
        "generation, and forest-plot rendering. Covers <b>Bedrock model tokens + Tavily "
        "web search</b>.",
        3.1 * inch,
    )
    cost_right = cost_card(
        "≈ $0",
        "INCREMENTAL COST OF A RE-OPEN",
        "Re-opening an existing thread to inspect, audit, or re-export the analysis costs only "
        "the few tokens of the current turn. Past tool results (papers, extractions, generated "
        "code, plots) are already persisted in the local store.",
        3.1 * inch,
    )

    cost_row = Table([[cost_left, cost_right]], colWidths=[3.2 * inch, 3.2 * inch])
    cost_row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(Spacer(1, 0.05 * inch))
    story.append(cost_row)
    story.append(Spacer(1, 0.12 * inch))

    story.append(Paragraph("How the envelope is built", styles["H2"]))
    story.append(
        Paragraph(
            "An active meta-analysis session is token-heavy because the model reads abstracts, "
            "reasons over extraction tables, writes statistical code, and reviews its own outputs. "
            "On Claude Sonnet–class pricing, a serious day of work lands in the <b>$200–$300</b> "
            "range — comfortably below the cost of an equivalent human-analyst day. Light browsing, "
            "background reading, or quick definitional Q&amp;A sessions run at a small fraction of "
            "that figure.",
            styles["Body"],
        )
    )
    story.append(
        Paragraph(
            "Tavily web search is consumed only by the General Q&amp;A and protocol-drafting flows; "
            "the clinical search itself runs against PubMed and Europe PMC and does <b>not</b> "
            "consume Tavily credits.",
            styles["Body"],
        )
    )

    story.append(Paragraph("Why the local store keeps costs flat", styles["H2"]))
    story.append(
        Paragraph(
            "Pulled study material — abstracts, MeSH lookups, fetched PMC full text, generated "
            "extraction tables — is persisted in CRA's local store. The next time the same study or "
            "the same PICO is touched (e.g. by a living-review watch, by a sibling project, or by a "
            "re-run on updated data), the assistant reads from the local cache instead of re-issuing "
            "API calls. <b>The marginal cost of revisiting prior work approaches zero.</b>",
            styles["Body"],
        )
    )
    story.append(
        Paragraph(
            "This matters most for teams running <b>multiple parallel reviews</b> in overlapping "
            "areas — cardiovascular, oncology, infectious disease — where the same landmark trials "
            "show up across many questions. The first review pays for the lookup; every subsequent "
            "review reuses it for free.",
            styles["Body"],
        )
    )

    # Cost-control levers — a small reference table
    levers = [
        (
            "Local study cache",
            "Re-use of pulled abstracts / full text across reviews — no repeat API spend.",
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
            "Configurable model tier",
            "Drop to a cheaper Sonnet / Haiku variant per workflow when full Opus reasoning isn't "
            "required.",
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

    story.append(PageBreak())

    # ── ROADMAP ──────────────────────────────────────────────────────────
    story.append(Paragraph("On the roadmap", styles["H1"]))
    story.append(
        Paragraph(
            "Beyond the v1 capability set, the architecture was built to absorb these without "
            "rebuilding the core. Each is a tractable next increment, not a moonshot.",
            styles["Body"],
        )
    )

    story.append(Paragraph("Automated clinical data collection", styles["H2"]))
    story.append(
        Paragraph(
            "<b>eCRF designer</b> — generate a draft electronic case-report form directly from a "
            "study protocol, with field types, validation rules, and skip logic mapped to the "
            "protocol's data items. Reduces the weeks-long round-trip between investigator and "
            "data manager at study start-up.",
            styles["Body"],
        )
    )
    story.append(
        Paragraph(
            "<b>Source-document extraction</b> — point the assistant at structured exports from your "
            "EHR / registry / trial-management system and have it populate the extraction table for "
            "retrospective studies or patient-level meta-analyses, with an audit trail of which "
            "source row produced which output cell.",
            styles["Body"],
        )
    )

    story.append(Paragraph("Patient-facing research handouts", styles["H2"]))
    story.append(
        Paragraph(
            "Generate <b>plain-language summaries</b> of a study's objectives, what participation "
            "involves, and what the early evidence suggests — at a configurable reading level, in "
            "the patient's preferred language. Designed for <b>research participant recruitment</b>, "
            "<b>shared decision-making</b> conversations, and <b>return-of-results</b> obligations "
            "under modern IRB / ethics guidance. The lay summary and the manuscript share a single "
            "evidence base, so they cannot drift apart.",
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

    doc.build(story)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    build_pdf()
