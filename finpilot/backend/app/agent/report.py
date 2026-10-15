"""The financial report: an agent investigation, written up and saved as PDF.

The agent decides what to investigate and writes the narrative. The appendix
-- the monthly table and the segment table -- is computed here, by code, from
the same tools, so the figures the user is most likely to check are the ones
the model could not have got wrong.

Saved per job under ``exports/<job>/``:

* ``financial_report.md``   the report as written
* ``financial_report.pdf``  the same, typeset, with charts and the appendix
* ``financial_report.json`` when, which model, which tools, which sources
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.agent.prompts import REPORT_TASK, report_system
from app.agent.runner import run_agent
from app.agent.tools import AgentContext, get_financial_overview, list_segments
from app.config import get_settings

log = logging.getLogger("finpilot.report")

DISCLAIMER = (
    "This report was generated automatically by FinPilot's AI from the documents you "
    "uploaded. It is for information only and is not financial, tax or investment advice; "
    "FinPilot is not a SEBI-registered investment adviser. Figures can contain errors -- "
    "check them against your statements, and consult a qualified professional before "
    "making financial decisions."
)

MD_NAME = "financial_report.md"
PDF_NAME = "financial_report.pdf"
META_NAME = "financial_report.json"


def _out_dir(job_id: str) -> Path:
    from app import jobs as job_service

    path = job_service.export_dir(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_report(job_id: str, *, client: Any = None) -> Iterator[dict[str, Any]]:
    """Run the report agent; on success save the report and yield ``report``."""
    ctx = AgentContext(job_id)
    if not ctx.rows:
        yield {"type": "error", "message": "This job has no transactions to report on."}
        return
    answer: dict[str, Any] | None = None
    for event in run_agent(
        job_id,
        system=report_system(ctx),
        message=REPORT_TASK,
        max_steps=get_settings().report_max_steps,
        client=client,
        temperature=0.4,
        context=ctx,
    ):
        if event["type"] == "answer":
            answer = event
            continue
        yield event
    if answer is None:
        return

    from app.agent.charts import clean_for

    markdown = clean_for(_clean_markdown(answer["text"]), ctx)
    appendix = appendix_markdown(ctx)
    meta = {
        "generated_at": answer["generated_at"],
        "model": answer["model"],
        "steps": answer["steps"],
        "tools_used": answer["tools_used"],
        "seconds": answer["seconds"],
        "sources": answer["sources"],
        "tokens": answer.get("tokens"),
    }
    out = _out_dir(job_id)
    disclaimer = f"---\n\n_{DISCLAIMER}_"
    (out / MD_NAME).write_text(markdown + "\n\n" + appendix + "\n\n" + disclaimer, encoding="utf-8")
    (out / META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    try:
        write_pdf(out / PDF_NAME, markdown, ctx)
        meta["pdf"] = True
    except Exception:  # noqa: BLE001 - the markdown is still worth having
        log.exception("report %s: PDF rendering failed", job_id)
        meta["pdf"] = False
    yield {"type": "report", "markdown": markdown + "\n\n" + appendix + "\n\n" + disclaimer, "meta": meta}


def load_report(job_id: str) -> dict[str, Any] | None:
    out = _out_dir(job_id)
    md = out / MD_NAME
    if not md.exists():
        return None
    meta: dict[str, Any] = {}
    if (out / META_NAME).exists():
        try:
            meta = json.loads((out / META_NAME).read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
    meta["pdf"] = (out / PDF_NAME).exists()
    return {"markdown": md.read_text(encoding="utf-8"), "meta": meta}


def _clean_markdown(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


# --------------------------------------------------------------------------
# the code-computed appendix
# --------------------------------------------------------------------------


def _inr(amount: float) -> str:
    return ("-₹" if amount < 0 else "₹") + _indian(abs(amount))


def _indian(amount: float) -> str:
    negative = amount < 0
    whole = f"{abs(amount):.0f}"
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return ("-" if negative else "") + whole


def appendix_tables(ctx: AgentContext) -> dict[str, list[list[str]]]:
    overview = get_financial_overview(ctx)
    monthly = [["Month", "Received", "Spent", "Net"]] + [
        [m["month"], _inr(m["received"]), _inr(m["spent"]), _inr(m["received"] - m["spent"])]
        for m in overview.get("by_month", [])
    ]
    segs = list_segments(ctx)
    segments: list[list[str]] = []
    if segs.get("segmented"):
        segments = [["Segment", "Txns", "Spent", "Share", "Per month"]] + [
            [
                s["name"],
                str(s["transactions"]),
                _inr(s["spent"]),
                f"{s['share_of_all_spending']:.0%}",
                _inr(s["avg_spent_per_active_month"]),
            ]
            for s in segs["segments"]
        ]
        noise = segs["unsegmented_one_offs"]
        if noise["transactions"]:
            segments.append(["One-offs (unsegmented)", str(noise["transactions"]), _inr(noise["spent"]), "", ""])
    return {"monthly": monthly, "segments": segments}


def _md_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head, *body = rows
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def appendix_markdown(ctx: AgentContext) -> str:
    tables = appendix_tables(ctx)
    parts = [
        "---",
        "## Appendix: figures computed directly from your ledger",
        "_These tables are calculated by code from the cleaned ledger, not written by the model._",
        "### Month by month",
        _md_table(tables["monthly"]),
    ]
    if tables["segments"]:
        parts += ["### Spending segments", _md_table(tables["segments"])]
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeuii.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
    ("C:/Windows/Fonts/Nirmala.ttf", "C:/Windows/Fonts/NirmalaB.ttf", "C:/Windows/Fonts/Nirmala.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
]


def _register_fonts() -> tuple[str, str, str, bool]:
    """A body font with a ₹ glyph if the machine has one; else Helvetica + 'Rs.'"""
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "FP-Regular" in pdfmetrics.getRegisteredFontNames():
        return "FP-Regular", "FP-Bold", "FP-Italic", True
    for regular, bold, italic in _FONT_CANDIDATES:
        if not (Path(regular).exists() and Path(bold).exists()):
            continue
        try:
            font = TTFont("FP-Regular", regular)
            if 0x20B9 not in font.face.charToGlyph:
                continue
            pdfmetrics.registerFont(font)
            pdfmetrics.registerFont(TTFont("FP-Bold", bold))
            pdfmetrics.registerFont(TTFont("FP-Italic", italic if Path(italic).exists() else regular))
            addMapping("FP-Regular", 0, 0, "FP-Regular")
            addMapping("FP-Regular", 1, 0, "FP-Bold")
            addMapping("FP-Regular", 0, 1, "FP-Italic")
            addMapping("FP-Regular", 1, 1, "FP-Bold")
            return "FP-Regular", "FP-Bold", "FP-Italic", True
        except Exception:  # noqa: BLE001
            continue
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", False


_ASTRAL = re.compile(r"[\U00010000-\U0010FFFF\u2600-\u27BF\uFE0F]")


def _inline(text: str, rupee: bool) -> str:
    """Markdown inline -> reportlab paragraph markup."""
    text = _ASTRAL.sub("", text)
    text = re.sub(r"\[\[chart:[^\]]*\]\]", "", text)
    if not rupee:
        text = text.replace("₹", "Rs. ")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", text)
    # code spans are almost always a segment or file name: keep the body font
    # (Courier has no ₹ and spaces names out), just set them apart
    text = re.sub(r"`(.+?)`", r"<font color='#1f3a5f'><b>\1</b></font>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<link href='\2'>\1</link>", text)
    return text


def _table_rows(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def markdown_flowables(
    markdown: str,
    styles: dict[str, Any],
    rupee: bool,
    width: float,
    chart: Any = None,
) -> list[Any]:
    """``chart(kind, arg)`` returns the flowables for a ``[[chart:...]]`` line."""
    from reportlab.lib import colors
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        Paragraph,
        Spacer,
    )

    from app.agent.charts import TAG

    out: list[Any] = []
    lines = markdown.splitlines()
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append(Paragraph(_inline(" ".join(para), rupee), styles["body"]))
            para.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue
        tag = TAG.fullmatch(stripped)
        if tag:
            flush()
            if chart is not None:
                drawn = chart(tag.group(1), tag.group(2))
                if drawn:
                    out.append(Spacer(1, 6))
                    out.extend(drawn)
                    out.append(Spacer(1, 10))
            i += 1
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(Paragraph(_inline(heading.group(2), rupee), styles[f"h{min(level, 3)}"]))
            i += 1
            continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            flush()
            out.append(HRFlowable(width="100%", color=colors.HexColor("#d9dee5"), spaceBefore=6, spaceAfter=6))
            i += 1
            continue
        if stripped.startswith("|"):
            flush()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            rows = _table_rows(block)
            if rows:
                out.append(_pdf_table(rows, styles, rupee, width))
                out.append(Spacer(1, 6))
            continue
        bullet = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)", line)
        if bullet:
            flush()
            ordered = bullet.group(2)[0].isdigit()
            items = []
            while i < len(lines):
                m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)", lines[i].rstrip())
                if not m:
                    # continuation line of the previous item
                    if lines[i].strip() and lines[i].startswith("  ") and items:
                        items[-1] += " " + lines[i].strip()
                        i += 1
                        continue
                    break
                items.append(m.group(3))
                i += 1
            out.append(
                ListFlowable(
                    [ListItem(Paragraph(_inline(t, rupee), styles["body"]), leftIndent=14) for t in items],
                    bulletType="1" if ordered else "bullet",
                    start="1" if ordered else None,
                    leftIndent=14,
                    bulletFontSize=8 if not ordered else 9,
                )
            )
            continue
        para.append(stripped)
        i += 1
    flush()
    return out


def _pdf_table(rows: list[list[str]], styles: dict[str, Any], rupee: bool, width: float) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    data = [
        [Paragraph(_inline(c, rupee), styles["cell_head" if ri == 0 else "cell"]) for c in r]
        for ri, r in enumerate(rows)
    ]
    first = width * (0.34 if ncols > 2 else 0.5)
    rest = (width - first) / max(ncols - 1, 1)
    table = Table(data, colWidths=[first] + [rest] * (ncols - 1), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6fa")]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9dee5")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _bar_chart(title: str, labels: list[str], values: list[float], font: str, width: float) -> Any:
    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors

    n = len(values)
    height = 40 + n * 22
    drawing = Drawing(width, height)
    drawing.add(String(0, height - 12, title, fontName=font, fontSize=10, fillColor=colors.HexColor("#1f3a5f")))
    chart = HorizontalBarChart()
    chart.x = 150
    chart.y = 10
    chart.height = height - 35
    chart.width = width - 190
    chart.data = [list(reversed(values))]
    chart.categoryAxis.categoryNames = [label[:28] for label in reversed(labels)]
    chart.categoryAxis.labels.fontName = font
    chart.categoryAxis.labels.fontSize = 8
    chart.categoryAxis.labels.boxAnchor = "e"
    chart.valueAxis.labels.fontName = font
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labelTextFormat = lambda v: _indian(v)
    chart.bars[0].fillColor = colors.HexColor("#2f6db5")
    chart.bars[0].strokeColor = None
    chart.barSpacing = 3
    drawing.add(chart)
    return drawing


def write_pdf(path: Path, markdown: str, ctx: AgentContext) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer

    regular, bold, italic, rupee = _register_fonts()
    ink = colors.HexColor("#1b2430")
    navy = colors.HexColor("#1f3a5f")
    styles = {
        "body": ParagraphStyle("body", fontName=regular, fontSize=9.5, leading=14, textColor=ink,
                               spaceAfter=5, alignment=TA_LEFT),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=20, leading=24, textColor=navy, spaceAfter=8),
        # keepWithNext: a heading never sits alone at the foot of a page
        # while its chart or paragraph starts the next one
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=13.5, leading=18, textColor=navy,
                             spaceBefore=12, spaceAfter=6, keepWithNext=1),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=11, leading=15, textColor=ink,
                             spaceBefore=8, spaceAfter=4, keepWithNext=1),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=8.5, leading=11, textColor=ink),
        "cell_head": ParagraphStyle("cell_head", fontName=bold, fontSize=8.5, leading=11,
                                    textColor=colors.white),
        "small": ParagraphStyle("small", fontName=italic, fontSize=8, leading=11,
                                textColor=colors.HexColor("#5b6675")),
    }
    margin = 18 * mm
    width = A4[0] - 2 * margin
    generated = dt.datetime.now().strftime("%d %b %Y, %H:%M")

    def decorate(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFillColor(navy)
        canvas.rect(0, A4[1] - 8 * mm, A4[0], 8 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont(bold, 9)
        canvas.drawString(margin, A4[1] - 5.5 * mm, "FinPilot")
        canvas.setFont(regular, 8)
        canvas.drawRightString(A4[0] - margin, A4[1] - 5.5 * mm, "Personal financial report")
        canvas.setFillColor(colors.HexColor("#5b6675"))
        canvas.drawString(margin, 10 * mm, f"Generated {generated} · figures from your uploaded documents")
        canvas.drawRightString(A4[0] - margin, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    from reportlab.platypus import KeepTogether as _Keep

    from app.agent.charts import pdf_flowables
    from app.insights import build_insights

    insights = build_insights(ctx.job_id)

    def chart(kind: str, arg: str | None) -> list[Any]:
        drawn = pdf_flowables(kind, arg, insights, regular, width)
        return [_Keep(drawn)] if drawn else []

    story = markdown_flowables(markdown, styles, rupee, width, chart)

    # appendix: code-computed tables and charts
    story += [PageBreak(), Paragraph("Appendix: figures computed directly from your ledger", styles["h2"]),
              Paragraph("These tables and charts are calculated by code from the cleaned ledger, "
                        "not written by the model.", styles["small"]), Spacer(1, 6)]
    tables = appendix_tables(ctx)
    overview = get_financial_overview(ctx)
    months = overview.get("by_month", [])
    if months:
        story.append(KeepTogether([
            _bar_chart("Spending by month (transfers excluded)", [m["month"] for m in months],
                       [m["spent"] for m in months], regular, width),
            Spacer(1, 6),
        ]))
        story += [Paragraph("Month by month", styles["h3"]), _pdf_table(tables["monthly"], styles, rupee, width)]
    segs = list_segments(ctx)
    if segs.get("segmented") and segs["segments"]:
        top = segs["segments"][:10]
        story += [Spacer(1, 10), KeepTogether([
            _bar_chart("Spending by segment", [s["name"] for s in top], [s["spent"] for s in top], regular, width),
        ])]
        story += [Paragraph("Spending segments", styles["h3"]), _pdf_table(tables["segments"], styles, rupee, width)]

    story += [Spacer(1, 14), Paragraph(DISCLAIMER, styles["small"])]

    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=margin, rightMargin=margin,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Personal Financial Report", author="FinPilot",
    )
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
