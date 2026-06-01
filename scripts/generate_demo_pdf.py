"""Generate the Clinical Research Assistant demo-guide PDF.

Reads `demoguide.md` from the repo root and renders it to PDF with
plain styling — black body, monospace code blocks, simple headings.
The point is to ship a printable / shareable copy of the demo guide;
the demo paste blocks need to round-trip verbatim, so code-fence
handling is the load-bearing feature.

Run:
    uv run --with reportlab python scripts/generate_demo_pdf.py

Outputs:
    demo-guide.pdf  (project root)
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCE_MD = ROOT / "demoguide.md"
OUT_PATH = ROOT / "demo-guide.pdf"


# ── Styles (plain) ─────────────────────────────────────────────────────────

base = getSampleStyleSheet()

styles = {
    "Title": ParagraphStyle(
        "Title",
        parent=base["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=28,
        textColor=colors.black,
        alignment=TA_LEFT,
        spaceAfter=12,
    ),
    "H1": ParagraphStyle(
        "H1",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=colors.black,
        spaceBefore=12,
        spaceAfter=8,
    ),
    "H2": ParagraphStyle(
        "H2",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.black,
        spaceBefore=10,
        spaceAfter=4,
    ),
    "H3": ParagraphStyle(
        "H3",
        parent=base["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.black,
        spaceBefore=6,
        spaceAfter=2,
    ),
    "Body": ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=13.5,
        textColor=colors.black,
        spaceAfter=5,
    ),
    "Quote": ParagraphStyle(
        "Quote",
        parent=base["BodyText"],
        fontName="Helvetica-Oblique",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#444444"),
        leftIndent=12,
        spaceAfter=6,
    ),
    "Bullet": ParagraphStyle(
        "Bullet",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=13.5,
        textColor=colors.black,
        leftIndent=18,
        bulletIndent=6,
        spaceAfter=3,
    ),
    "Code": ParagraphStyle(
        "Code",
        parent=base["Code"],
        fontName="Courier",
        fontSize=8.5,
        leading=11,
        textColor=colors.black,
        backColor=colors.HexColor("#F5F5F5"),
        borderColor=colors.HexColor("#CCCCCC"),
        borderWidth=0.4,
        borderPadding=6,
        leftIndent=0,
        rightIndent=0,
        spaceBefore=4,
        spaceAfter=6,
    ),
    "Cell": ParagraphStyle(
        "Cell",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.black,
        spaceAfter=0,
    ),
    "CellHead": ParagraphStyle(
        "CellHead",
        parent=base["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.white,
        spaceAfter=0,
    ),
}


def page_decorations(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.setFont("Helvetica", 8)
    canvas.drawString(0.7 * inch, 0.45 * inch, "Clinical Research Assistant · Demo Guide")
    canvas.drawRightString(LETTER[0] - 0.7 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


# ── Markdown → flowables ───────────────────────────────────────────────────

_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<![\*])\*([^*]+)\*(?![\*])")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(s: str) -> str:
    """Convert Markdown inline syntax to ReportLab Paragraph mini-HTML."""
    placeholders: dict[str, str] = {}

    def _stash(html_str: str) -> str:
        key = f"\x00PH{len(placeholders)}\x00"
        placeholders[key] = html_str
        return key

    s = _BOLD_RE.sub(lambda m: _stash(f"<b>{html.escape(m.group(1))}</b>"), s)
    s = _ITALIC_RE.sub(lambda m: _stash(f"<i>{html.escape(m.group(1))}</i>"), s)
    s = _CODE_RE.sub(
        lambda m: _stash(
            f'<font name="Courier" backColor="#F5F5F5">{html.escape(m.group(1))}</font>'
        ),
        s,
    )
    def _link_repl(m: re.Match[str]) -> str:
        label, target = m.group(1), m.group(2)
        # Anchor-only links (#foo) don't resolve in the PDF — render the
        # label as bold text. External links keep their URL.
        if target.startswith("#"):
            return _stash(f"<b>{html.escape(label)}</b>")
        return _stash(
            f'<link href="{html.escape(target)}" color="#1E6E8C">'
            f"{html.escape(label)}</link>"
        )

    s = _LINK_RE.sub(_link_repl, s)
    s = html.escape(s, quote=False)
    for key, val in placeholders.items():
        s = s.replace(html.escape(key, quote=False), val)
    return s


@dataclass
class Block:
    kind: str  # h1 / h2 / h3 / p / quote / bullet / table / hr / code
    text: str = ""
    rows: list[list[str]] | None = None
    items: list[str] | None = None
    lang: str = ""


def parse_markdown(md: str) -> list[Block]:
    """Walk the markdown source linearly producing a Block stream.

    Code fences (```), pipe tables, blockquotes, bullets, and HR all
    handled. Code fences are load-bearing for the demo guide — paste
    blocks must round-trip verbatim.
    """
    lines = md.split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        # Code fence — preserve verbatim until the closing fence.
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            # Skip the closing fence.
            if i < n:
                i += 1
            blocks.append(Block("code", text="\n".join(code_lines), lang=lang))
            continue

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
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip("> ").rstrip())
                i += 1
            blocks.append(Block("quote", text=" ".join(quote_lines)))
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            # Capture this bullet block's indentation off the first line
            # so nested-indent bullets (e.g. inside a numbered-list item)
            # are recognised as siblings instead of looking like
            # continuation lines.
            base_indent = len(raw) - len(raw.lstrip())
            items: list[str] = []
            while i < n:
                ln_raw = lines[i].rstrip()
                ln_stripped = ln_raw.lstrip()
                if not ln_stripped:
                    break
                indent = len(ln_raw) - len(ln_stripped)
                if indent == base_indent and (
                    ln_stripped.startswith("- ") or ln_stripped.startswith("* ")
                ):
                    items.append(ln_stripped[2:].strip())
                    i += 1
                    continue
                if indent > base_indent and items:
                    items[-1] = items[-1] + " " + ln_stripped
                    i += 1
                    continue
                break
            blocks.append(Block("bullet", items=items))
            continue
        ol_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if ol_match:
            base_indent = len(raw) - len(raw.lstrip())
            items_o: list[str] = []
            while i < n:
                ln_raw = lines[i].rstrip()
                ln_stripped = ln_raw.lstrip()
                if not ln_stripped:
                    break
                indent = len(ln_raw) - len(ln_stripped)
                m = re.match(r"^(\d+)\.\s+(.*)$", ln_stripped)
                if indent == base_indent and m:
                    items_o.append(f"{m.group(1)}. {m.group(2)}")
                    i += 1
                    continue
                if indent > base_indent and items_o:
                    items_o[-1] = items_o[-1] + " " + ln_stripped
                    i += 1
                    continue
                break
            blocks.append(Block("ol", items=items_o))
            continue
        if stripped.startswith("|") and i + 1 < n and re.match(
            r"^\s*\|?\s*:?-+", lines[i + 1]
        ):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                row_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(row_cells)
                i += 1
            blocks.append(Block("table", rows=[header] + rows))
            continue
        # Paragraph
        para_lines = [stripped]
        i += 1
        while i < n:
            ln = lines[i].rstrip()
            if not ln.strip():
                break
            if ln.lstrip().startswith(
                ("#", "- ", "* ", "|", "> ", "```")
            ) or re.match(r"^\s*\d+\.\s+", ln) or (
                ln.strip().startswith("---") and set(ln.strip()) == {"-"}
            ):
                break
            para_lines.append(ln.strip())
            i += 1
        blocks.append(Block("p", text=" ".join(para_lines)))
    return blocks


def md_table(rows: list[list[str]]) -> Table:
    n_cols = max(len(r) for r in rows)
    avail = LETTER[0] - 1.4 * inch
    if n_cols == 2:
        col_widths = [avail * 0.32, avail * 0.68]
    else:
        col_widths = [avail / n_cols] * n_cols
    data: list[list[Paragraph]] = []
    for ri, row in enumerate(rows):
        cells = []
        for cell in row:
            style = styles["CellHead"] if ri == 0 else styles["Cell"]
            cells.append(Paragraph(_inline(cell), style))
        while len(cells) < n_cols:
            cells.append(Paragraph("", styles["Cell"]))
        data.append(cells)
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ("TOPPADDING", (0, 0), (-1, 0), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 1), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
            ]
        )
    )
    return t


def render_blocks(blocks: list[Block]) -> list:
    flow: list = []
    seen_h1 = False
    for b in blocks:
        if b.kind == "h1":
            if not seen_h1:
                seen_h1 = True
                flow.append(Paragraph(_inline(b.text), styles["Title"]))
            else:
                flow.append(Paragraph(_inline(b.text), styles["H1"]))
        elif b.kind == "h2":
            flow.append(Paragraph(_inline(b.text), styles["H1"]))
        elif b.kind == "h3":
            flow.append(Paragraph(_inline(b.text), styles["H2"]))
        elif b.kind == "p":
            flow.append(Paragraph(_inline(b.text), styles["Body"]))
        elif b.kind == "quote":
            flow.append(Paragraph(_inline(b.text), styles["Quote"]))
        elif b.kind == "bullet":
            for item in b.items or []:
                flow.append(
                    Paragraph(f"<bullet>•</bullet> {_inline(item)}", styles["Bullet"])
                )
            flow.append(Spacer(1, 3))
        elif b.kind == "ol":
            for item in b.items or []:
                # The leading "N. " is preserved verbatim in the text
                # by the parser; we just render it with the same bullet
                # style so the layout matches unordered lists.
                flow.append(Paragraph(_inline(item), styles["Bullet"]))
            flow.append(Spacer(1, 3))
        elif b.kind == "code":
            # Preformatted preserves whitespace + monospace. Wrap long
            # lines without breaking — useful for long paste blocks.
            flow.append(Preformatted(b.text, styles["Code"], maxLineLength=110))
        elif b.kind == "hr":
            flow.append(Spacer(1, 6))
        elif b.kind == "table":
            flow.append(md_table(b.rows or []))
            flow.append(Spacer(1, 6))
    return flow


def build_pdf() -> None:
    md = SOURCE_MD.read_text(encoding="utf-8")
    blocks = parse_markdown(md)

    doc = BaseDocTemplate(
        str(OUT_PATH),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title="Clinical Research Assistant — Demo Guide",
        author="Clinical Research Assistant",
    )
    w, h = LETTER
    frame = Frame(
        0.7 * inch,
        0.7 * inch,
        w - 1.4 * inch,
        h - 1.4 * inch,
        id="content",
        showBoundary=0,
    )
    doc.addPageTemplates(
        [PageTemplate(id="content", frames=[frame], onPage=page_decorations)]
    )
    doc.build(render_blocks(blocks))
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    build_pdf()
