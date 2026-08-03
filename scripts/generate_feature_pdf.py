"""Generate the Clinical Research Assistant feature-guide PDF.

Reads `feature-guide.md` from the repo root and renders it through a
branded ReportLab template (navy + teal + accent palette, hero forest
plot on the cover, page header + footer on every content page).
Any edit to `feature-guide.md` is picked up automatically — no
hardcoded copy lives in this script.

Run:
    uv run --with reportlab --with matplotlib python scripts/generate_feature_pdf.py

Outputs:
    feature-guide.pdf  (project root)
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

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
SOURCE_MD = ROOT / "docs" / "guides" / "feature-guide.md"
OUT_PATH = ROOT / "docs" / "guides" / "feature-guide.pdf"

# ── Brand palette ──────────────────────────────────────────────────────────
NAVY = colors.HexColor("#0F2A47")
TEAL = colors.HexColor("#1E6E8C")
ACCENT = colors.HexColor("#2A9D8F")
SAND = colors.HexColor("#F4F1EA")
SLATE = colors.HexColor("#2D3748")
MUTED = colors.HexColor("#6B7280")
LIGHT = colors.HexColor("#F7FAFC")
BORDER = colors.HexColor("#E2E8F0")


# ── Hero forest plot ───────────────────────────────────────────────────────


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
    "PhaseBadge": ParagraphStyle(
        "PhaseBadge",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.white,
        alignment=TA_LEFT,
        spaceAfter=0,
    ),
    "PhaseTitle": ParagraphStyle(
        "PhaseTitle",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=colors.white,
        alignment=TA_LEFT,
        spaceAfter=2,
    ),
    "PhaseSubtitle": ParagraphStyle(
        "PhaseSubtitle",
        parent=base["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#CFE2EC"),
        alignment=TA_LEFT,
    ),
    "H1": ParagraphStyle(
        "H1",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=NAVY,
        spaceBefore=4,
        spaceAfter=6,
    ),
    "H2": ParagraphStyle(
        "H2",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=TEAL,
        spaceBefore=10,
        spaceAfter=4,
    ),
    "H3": ParagraphStyle(
        "H3",
        parent=base["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=NAVY,
        spaceBefore=6,
        spaceAfter=2,
    ),
    "Body": ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=10.2,
        leading=14.5,
        textColor=SLATE,
        alignment=TA_JUSTIFY,
        spaceAfter=5,
    ),
    "Quote": ParagraphStyle(
        "Quote",
        parent=base["BodyText"],
        fontName="Helvetica-Oblique",
        fontSize=10,
        leading=14,
        textColor=TEAL,
        alignment=TA_LEFT,
        leftIndent=10,
        spaceAfter=6,
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
    "Cell": ParagraphStyle(
        "Cell",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=SLATE,
        alignment=TA_LEFT,
        spaceAfter=0,
    ),
    "CellHead": ParagraphStyle(
        "CellHead",
        parent=base["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
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
        spaceAfter=4,
        leftIndent=14,
        bulletIndent=2,
    ),
}


# ── Page templates ─────────────────────────────────────────────────────────


def cover_page(canvas, doc):
    canvas.saveState()
    w, h = LETTER
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 4.6 * inch, w, 4.6 * inch, fill=1, stroke=0)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, h - 4.7 * inch, w, 0.08 * inch, fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawCentredString(w / 2, 0.45 * inch, "Clinical Research Assistant  ·  Feature Guide")
    canvas.restoreState()


def content_page(canvas, doc):
    canvas.saveState()
    w, h = LETTER
    canvas.setFillColor(ACCENT)
    canvas.rect(0.6 * inch, h - 0.6 * inch, 0.7 * inch, 0.05 * inch, fill=1, stroke=0)
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(1.4 * inch, h - 0.6 * inch + 0.01 * inch, "CLINICAL RESEARCH ASSISTANT")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(0.6 * inch, 0.45 * inch, "Feature Guide")
    canvas.drawRightString(w - 0.6 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


# ── Markdown → ReportLab flowables ────────────────────────────────────────


_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<![\*])\*([^*]+)\*(?![\*])")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(s: str) -> str:
    """Convert Markdown inline syntax to ReportLab Paragraph mini-HTML.

    Order matters: bold first (greedy double-star), then italic (single
    star not inside a word), then code, then links. Everything else is
    HTML-escaped after the conversions land.
    """
    # Protect inline transforms by stashing them as sentinels, then
    # html-escape the rest, then restore.
    placeholders: dict[str, str] = {}

    def _stash(html_str: str) -> str:
        key = f"\x00PH{len(placeholders)}\x00"
        placeholders[key] = html_str
        return key

    s = _BOLD_RE.sub(lambda m: _stash(f"<b>{m.group(1)}</b>"), s)
    s = _ITALIC_RE.sub(lambda m: _stash(f"<i>{m.group(1)}</i>"), s)
    s = _CODE_RE.sub(
        lambda m: _stash(f'<font name="Courier" color="#1E6E8C">{html.escape(m.group(1))}</font>'),
        s,
    )
    s = _LINK_RE.sub(
        lambda m: _stash(
            f'<link href="{html.escape(m.group(2))}" color="#1E6E8C">'
            f"{html.escape(m.group(1))}</link>"
        ),
        s,
    )
    s = html.escape(s, quote=False)
    for key, val in placeholders.items():
        s = s.replace(html.escape(key, quote=False), val)
    return s


@dataclass
class Block:
    kind: str  # h1 / h2 / h3 / p / quote / bullet / table / hr
    text: str = ""
    rows: list[list[str]] | None = None
    items: list[str] | None = None


def parse_markdown(md: str) -> list[Block]:
    """Walk the markdown source linearly producing a Block stream.

    Supports the subset we actually use in feature-guide.md: H1 / H2 /
    H3 / paragraphs (joined across soft-wrap), blockquotes (one-level),
    `-` bullet lists, GitHub-style pipe tables, and `---` horizontal
    rules. Code fences are not used in this guide so they're flattened
    to paragraphs.
    """
    lines = md.split("\n")
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("# "):
            blocks.append(Block("h1", text=stripped[2:].strip()))
            i += 1
            continue
        if stripped.startswith("## "):
            blocks.append(Block("h2", text=stripped[3:].strip()))
            i += 1
            continue
        if stripped.startswith("### "):
            blocks.append(Block("h3", text=stripped[4:].strip()))
            i += 1
            continue
        if stripped.startswith("---") and set(stripped) == {"-"}:
            blocks.append(Block("hr"))
            i += 1
            continue
        if stripped.startswith("> "):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip("> ").rstrip())
                i += 1
            blocks.append(Block("quote", text=" ".join(quote_lines)))
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            items: list[str] = []
            while i < len(lines):
                ln = lines[i].rstrip()
                if not ln.strip():
                    break
                if ln.startswith("- ") or ln.startswith("* "):
                    items.append(ln[2:].strip())
                elif ln.startswith("  "):
                    # Continuation of the previous bullet — soft-wrap into
                    # the last item.
                    items[-1] = items[-1] + " " + ln.strip()
                else:
                    break
                i += 1
            blocks.append(Block("bullet", items=items))
            continue
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\s*\|?\s*:?-+", lines[i + 1])
        ):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2  # skip header + separator
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(row_cells)
                i += 1
            blocks.append(Block("table", rows=[header, *rows]))
            continue
        # Paragraph — collect contiguous non-empty non-special lines
        para_lines = [stripped]
        i += 1
        while i < len(lines):
            ln = lines[i].rstrip()
            if not ln.strip():
                break
            if ln.lstrip().startswith(("#", "- ", "* ", "|", "> ", "---")) or (
                ln.strip().startswith("---") and set(ln.strip()) == {"-"}
            ):
                break
            para_lines.append(ln.strip())
            i += 1
        blocks.append(Block("p", text=" ".join(para_lines)))
    return blocks


def md_table(rows: list[list[str]]) -> Table:
    """Render a parsed markdown table as a branded ReportLab Table."""
    n_cols = max(len(r) for r in rows)
    avail = LETTER[0] - 1.2 * inch
    # First column gets a bit more weight when there are 2 columns
    # (matches the "Differentiator | Why it matters" pattern).
    col_widths = [avail * 0.35, avail * 0.65] if n_cols == 2 else [avail / n_cols] * n_cols
    data: list[list[Paragraph]] = []
    for ri, row in enumerate(rows):
        cells = []
        for cell in row:
            style = styles["CellHead"] if ri == 0 else styles["Cell"]
            cells.append(Paragraph(_inline(cell), style))
        # Pad short rows
        while len(cells) < n_cols:
            cells.append(Paragraph("", styles["Cell"]))
        data.append(cells)
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 1), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ]
        )
    )
    return t


def phase_banner(num: str, title: str, sub: str) -> KeepTogether:
    """The brand banner that opens each '## NN · Phase' section."""
    badge = Paragraph(f"PHASE {num}", styles["PhaseBadge"])
    title_p = Paragraph(_inline(title), styles["PhaseTitle"])
    sub_p = Paragraph(_inline(sub), styles["PhaseSubtitle"])
    inner = Table(
        [[badge], [title_p], [sub_p]],
        colWidths=[LETTER[0] - 1.2 * inch - 0.8 * inch],
    )
    inner.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    wrapper = Table([[inner]], colWidths=[LETTER[0] - 1.2 * inch])
    wrapper.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("LINEBEFORE", (0, 0), (0, 0), 6, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 18),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ]
        )
    )
    return KeepTogether([wrapper, Spacer(1, 12)])


# Pattern matching "## 01 · Evidence synthesis"
_PHASE_HEADER_RE = re.compile(r"^(\d{2})\s*[·•∙]\s*(.+)$")


def render_blocks(blocks: list[Block]) -> list:
    """Turn the parsed markdown into a flowables list.

    Treats H2 headers shaped like "01 · Evidence synthesis" as phase
    banners (filled navy band on the next line). Skips the first H1
    (the document title — surfaced on the cover page instead).
    """
    flow: list = []
    seen_h1 = False
    i = 0
    # First, build a lookup so we can read the H3 quote (phase subtitle)
    # that follows a phase H2.
    while i < len(blocks):
        b = blocks[i]
        if b.kind == "h1":
            if not seen_h1:
                # The first H1 is the title; skip — it's on the cover.
                seen_h1 = True
                i += 1
                continue
            flow.append(Paragraph(_inline(b.text), styles["H1"]))
            i += 1
            continue
        if b.kind == "h2":
            m = _PHASE_HEADER_RE.match(b.text)
            if m:
                num, title = m.group(1), m.group(2)
                # Look ahead for an immediately-following blockquote —
                # that's the phase subtitle / context line we want to
                # surface in the banner.
                sub = ""
                if i + 1 < len(blocks) and blocks[i + 1].kind == "quote":
                    sub = blocks[i + 1].text
                    flow.append(phase_banner(num, title, sub))
                    i += 2
                    continue
                flow.append(phase_banner(num, title, ""))
                i += 1
                continue
            flow.append(Paragraph(_inline(b.text), styles["H1"]))
            i += 1
            continue
        if b.kind == "h3":
            flow.append(Paragraph(_inline(b.text), styles["H2"]))
            i += 1
            continue
        if b.kind == "p":
            flow.append(Paragraph(_inline(b.text), styles["Body"]))
            i += 1
            continue
        if b.kind == "quote":
            flow.append(Paragraph(_inline(b.text), styles["Quote"]))
            i += 1
            continue
        if b.kind == "bullet":
            for item in b.items or []:
                flow.append(
                    Paragraph(
                        f"<bullet>•</bullet> {_inline(item)}",
                        styles["Bullet"],
                    )
                )
            flow.append(Spacer(1, 4))
            i += 1
            continue
        if b.kind == "hr":
            flow.append(Spacer(1, 8))
            i += 1
            continue
        if b.kind == "table":
            flow.append(md_table(b.rows or []))
            flow.append(Spacer(1, 8))
            i += 1
            continue
        i += 1
    return flow


# ── Document build ─────────────────────────────────────────────────────────


def build_pdf() -> None:
    build_hero(HERO_PATH)
    md = SOURCE_MD.read_text(encoding="utf-8")
    blocks = parse_markdown(md)

    # Pull the title + tagline from the first H1 + the paragraph that
    # immediately follows it (rendered on the cover page only).
    title = "Clinical Research Assistant — Feature Guide"
    tagline = ""
    for b in blocks:
        if b.kind == "h1":
            title = b.text
            continue
        if title and b.kind == "p":
            tagline = b.text
            break

    doc = BaseDocTemplate(
        str(OUT_PATH),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=title,
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

    flow: list = []

    # Cover
    flow.append(Spacer(1, 0.25 * inch))
    flow.append(Paragraph("CLINICAL RESEARCH ASSISTANT", styles["EyebrowLight"]))
    flow.append(Paragraph(title, styles["TitleBig"]))
    flow.append(Spacer(1, 0.15 * inch))
    if tagline:
        flow.append(Paragraph(_inline(tagline), styles["Tagline"]))
    flow.append(Spacer(1, 0.6 * inch))
    if HERO_PATH.exists():
        hero_w = w - 1.2 * inch
        hero_h = hero_w * 5.2 / 10
        flow.append(Image(str(HERO_PATH), width=hero_w, height=hero_h))

    flow.append(PageBreak())
    flow.append(Spacer(1, 0.05 * inch))

    # Switch to the content template for the rest.
    flow.append(NextPageTemplateSignal("content"))

    # Body — skipping the first H1 + tagline paragraph (already on cover).
    seen_title = False
    seen_tagline = False
    body_blocks: list[Block] = []
    for b in blocks:
        if not seen_title and b.kind == "h1":
            seen_title = True
            continue
        if seen_title and not seen_tagline and b.kind == "p":
            seen_tagline = True
            continue
        body_blocks.append(b)
    flow.extend(render_blocks(body_blocks))

    doc.build(flow)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}")


# ReportLab needs a flowable to switch page templates between cover + content.
class NextPageTemplateSignal:
    """Wrapper around ReportLab's NextPageTemplate that doesn't require
    an import at module load (the platypus one needs to be referenced
    after the doc exists)."""

    def __init__(self, template_id: str) -> None:
        self.template_id = template_id

    def wrap(self, *_: object) -> tuple[int, int]:
        return (0, 0)

    def drawOn(self, *_: object, **__: object) -> None:
        return None

    def draw(self) -> None:
        return None

    def split(self, *_: object) -> list:
        return []

    def isIndexing(self) -> int:
        return 0


def _patch_next_template() -> None:
    """Use ReportLab's NextPageTemplate flowable for the cover→content
    transition. Done at module level so the signal class above can be
    replaced with the real thing."""
    from reportlab.platypus import NextPageTemplate

    global NextPageTemplateSignal
    NextPageTemplateSignal = NextPageTemplate  # type: ignore[assignment]


_patch_next_template()


if __name__ == "__main__":
    build_pdf()
