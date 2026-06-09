"""Markdown → PDF using reportlab. Replaces WeasyPrint to avoid GTK3 dependency on Windows."""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_styles = getSampleStyleSheet()
ACCENT = colors.HexColor("#1e40af")
DARK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#555")
SOFT = colors.HexColor("#f3f4f6")
BORDER = colors.HexColor("#cbd5e1")

H1 = ParagraphStyle(
    "H1",
    parent=_styles["Heading1"],
    fontName="Helvetica-Bold",
    fontSize=18,
    leading=24,
    spaceBefore=14,
    spaceAfter=10,
    textColor=ACCENT,
)
H2 = ParagraphStyle(
    "H2",
    parent=_styles["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=18,
    spaceBefore=12,
    spaceAfter=6,
    textColor=ACCENT,
    keepWithNext=True,
)
H3 = ParagraphStyle(
    "H3",
    parent=_styles["Heading3"],
    fontName="Helvetica-Bold",
    fontSize=11,
    leading=15,
    spaceBefore=8,
    spaceAfter=4,
    textColor=colors.HexColor("#374151"),
    keepWithNext=True,
)
BODY = ParagraphStyle(
    "Body",
    parent=_styles["BodyText"],
    fontName="Helvetica",
    fontSize=10.5,
    leading=15,
    spaceAfter=8,
    alignment=TA_JUSTIFY,
    textColor=DARK,
)
BODY_LEFT = ParagraphStyle("BodyLeft", parent=BODY, alignment=TA_LEFT)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=9, leading=12, textColor=MUTED)
QUOTE = ParagraphStyle(
    "Quote",
    parent=BODY,
    fontName="Helvetica-Oblique",
    leftIndent=18,
    rightIndent=18,
    textColor=MUTED,
    spaceAfter=8,
)
CODE_STYLE = ParagraphStyle(
    "Code",
    parent=BODY,
    fontName="Courier",
    fontSize=9,
    leading=12,
    leftIndent=12,
    backColor=SOFT,
    borderPadding=4,
)


def _inline(text: str) -> str:
    # Decode known entities first so they survive the escape step below
    text = text.replace("&mdash;", "—").replace("&nbsp;", " ")
    # Escape raw <, >, & so they don't break reportlab's strict tag parser.
    # PMC reference contamination occasionally embeds patterns like "<= 3" or
    # "</N>" in paper titles, which reportlab otherwise reads as malformed tags.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Markdown -> reportlab markup (these new tags must NOT be escaped)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="9">\1</font>', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<link href="\2" color="#1e40af">\1</link>', text)
    return text


def _table(rows):
    t = Table(rows, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ]
        )
    )
    return t


def _md_to_flowables(md_text: str):
    out = []
    lines = md_text.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].rstrip()
        if not s.strip():
            i += 1
            continue
        if s.startswith("# "):
            out.append(Paragraph(_inline(s[2:]), H1))
            i += 1
        elif s.startswith("## "):
            out.append(Paragraph(_inline(s[3:]), H2))
            i += 1
        elif s.startswith("### "):
            out.append(Paragraph(_inline(s[4:]), H3))
            i += 1
        elif s.startswith("#### "):
            out.append(Paragraph(f"<b>{_inline(s[5:])}</b>", BODY))
            i += 1
        elif s.startswith("---"):
            out.append(Spacer(1, 0.2 * cm))
            i += 1
        elif s.startswith("|"):
            tbl_lines = []
            while i < n and lines[i].strip().startswith("|"):
                tbl_lines.append(lines[i].strip())
                i += 1
            rows = []
            for tl in tbl_lines:
                if re.fullmatch(r"\|[\s\-:\|]+\|", tl):
                    continue
                cells = [c.strip() for c in tl.strip("|").split("|")]
                rows.append([Paragraph(_inline(c), BODY_LEFT) for c in cells])
            if rows:
                out.append(_table(rows))
                out.append(Spacer(1, 0.2 * cm))
        elif s.startswith("- ") or s.startswith("* "):
            items = []
            while i < n and (lines[i].strip().startswith("- ") or lines[i].strip().startswith("* ")):
                items.append(Paragraph(_inline(lines[i].strip()[2:]), BODY_LEFT))
                i += 1
            out.append(
                ListFlowable(
                    [ListItem(p, leftIndent=20) for p in items],
                    bulletType="bullet",
                    leftIndent=12,
                    bulletFontSize=9,
                )
            )
        elif s.startswith("> "):
            qlines = []
            while i < n and lines[i].strip().startswith(">"):
                qlines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(Paragraph(_inline(" ".join(qlines)), QUOTE))
        elif s.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append(Paragraph("<br/>".join(b.replace(" ", "&nbsp;") for b in buf), CODE_STYLE))
        else:
            para_lines = []
            while (
                i < n
                and lines[i].strip()
                and not lines[i].lstrip().startswith(("#", "|", "- ", "* ", "> ", "```", "---"))
            ):
                para_lines.append(lines[i].rstrip())
                i += 1
            text = " ".join(para_lines)
            out.append(Paragraph(_inline(text), BODY))
    return out


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"{doc.page}")
    title = getattr(doc, "_doc_title", "")
    if title:
        canvas.drawString(2 * cm, 1.2 * cm, title)
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.3)
    canvas.line(2 * cm, 1.5 * cm, A4[0] - 2 * cm, 1.5 * cm)
    canvas.restoreState()


def markdown_to_pdf(md_body: str, out_path: Path, title: str = "Report") -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=title,
    )
    doc._doc_title = title
    story = _md_to_flowables(md_body)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
