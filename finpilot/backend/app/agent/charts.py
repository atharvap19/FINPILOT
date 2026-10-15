"""Charts the agent can place in an answer or a report, by tag.

The agent writes a line such as ``[[chart:segment_split]]``; the app draws
that chart from the ledger and the segmentation -- the web UI as a live chart,
the PDF as a vector drawing. The model never supplies the numbers, so a chart
can't contradict the data, and placing one costs a few tokens rather than the
hundreds that describing the figures would.

``clean_tags`` runs on everything the agent writes before it is shown or
saved: unknown tags, segment tags before segmentation has run, and segment
ids that don't exist are dropped, and each tag is put on its own line.
"""

from __future__ import annotations

import re
from typing import Any

TAG = re.compile(r"\[\[chart:([a-z_]+)(?::(-?\d+))?\]\]")

#: tag -> (what it shows, needs segmentation)
CATALOG: dict[str, tuple[str, bool]] = {
    "segment_split": ("how spending splits across the spending habits (segments) - donut", True),
    "segment_trends": ("each habit's spending month by month - small multiples", True),
    "segment": ("one habit in detail: trend, cost, merchants, timing - write segment:ID", True),
    "segments_detail": ("the full segmentation charts: share, monthly counts, every transaction", True),
    "money_flow": ("income flowing into each habit and savings", False),
    "monthly": ("money in vs spent, month by month", False),
    "merchants": ("top merchants by spend", False),
    "weekday": ("when spending happens: by weekday (and hour where known)", False),
    "calendar": ("daily spending calendar with the biggest days", False),
    "kpis": ("headline numbers: money in, spent, saved, bills due", False),
}

CAPTIONS = {
    "segment_split": "Chart: how your spending splits across habits",
    "segment_trends": "Chart: each habit month by month",
    "segment": "Chart: habit in detail",
    "segments_detail": "Chart: segmentation detail",
    "money_flow": "Chart: where your money went",
    "monthly": "Chart: money in vs spent by month",
    "merchants": "Chart: top merchants",
    "weekday": "Chart: when you spend",
    "calendar": "Chart: spending calendar",
    "kpis": "Chart: headline numbers",
}


def catalog_text(segmented: bool) -> str:
    lines = [
        "Live charts: to show a chart drawn from the user's own data, write a line that",
        "contains only its tag, e.g. [[chart:segment_split]]. The app draws it from the",
        "ledger and the segmentation -- never write the chart's numbers out as a table",
        "just to draw it. Available tags:",
    ]
    for key, (desc, needs_segments) in CATALOG.items():
        if needs_segments and not segmented:
            continue
        tag = "[[chart:segment:ID]]" if key == "segment" else f"[[chart:{key}]]"
        lines.append(f"- {tag}  {desc}")
    if segmented:
        lines.append("ID in segment:ID is a segment_id from list_segments.")
    else:
        lines.append("(Segment charts appear once segmentation has been run.)")
    return "\n".join(lines)


def clean_tags(text: str, *, segmented: bool, segment_ids: set[int]) -> str:
    """Drop tags that can't be drawn; give each surviving tag its own line."""

    def keep(match: re.Match[str]) -> str:
        kind, arg = match.group(1), match.group(2)
        entry = CATALOG.get(kind)
        if entry is None or (entry[1] and not segmented):
            return ""
        if kind == "segment":
            if arg is None or int(arg) not in segment_ids:
                return ""
            return f"\n\n[[chart:segment:{int(arg)}]]\n\n"
        return f"\n\n[[chart:{kind}]]\n\n"

    out = TAG.sub(keep, text)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def clean_for(text: str, ctx: Any) -> str:
    """clean_tags with the segmentation state of one AgentContext."""
    ids = {r["segment_id"] for r in ctx.rows if r["segment_id"] is not None and r["segment_id"] >= 0}
    return clean_tags(text, segmented=bool(ids), segment_ids=ids)


def captions_for_markdown(text: str, names: dict[int, str]) -> str:
    """For the .md download: a tag becomes an italic caption pointing at the PDF."""

    def caption(match: re.Match[str]) -> str:
        kind, arg = match.group(1), match.group(2)
        label = CAPTIONS.get(kind, "Chart")
        if kind == "segment" and arg is not None:
            label = f"Chart: {names.get(int(arg), 'habit')} in detail"
        return f"_{label} (see the PDF)_"

    return TAG.sub(caption, text)


# --------------------------------------------------------------------------
# PDF drawings
# --------------------------------------------------------------------------

SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
DEEMPH = "#c3c2b7"
GOOD = "#0ca30c"
INK = "#1b2430"
MUTED = "#6b7280"
GRID = "#e1e0d9"
SEQ = ["#eeede8", "#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _inr(v: float) -> str:
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e7:
        return f"{sign}₹{v / 1e7:.2f}Cr"
    if v >= 1e5:
        return f"{sign}₹{v / 1e5:.2f}L"
    if v >= 1e3:
        return f"{sign}₹{v / 1e3:.1f}K"
    return f"{sign}₹{v:.0f}"


def _month(key: str) -> str:
    import datetime as dt

    y, m = map(int, key.split("-"))
    return dt.date(y, m, 1).strftime("%b %y")


def _title(drawing: Any, text: str, font: str, height: float) -> None:
    from reportlab.graphics.shapes import String
    from reportlab.lib import colors

    drawing.add(String(0, height - 12, text, fontName=font, fontSize=10.5, fillColor=colors.HexColor("#1f3a5f")))


def _legend_rows(drawing: Any, x: float, y_top: float, rows: list[tuple[str, str, str]], font: str,
                 value_x: float = 230, max_label: int = 34) -> None:
    """rows: (color, label, value). Text in ink; the swatch carries identity."""
    from reportlab.graphics.shapes import Rect, String
    from reportlab.lib import colors

    for i, (color, label, value) in enumerate(rows):
        y = y_top - i * 17
        drawing.add(Rect(x, y - 1, 9, 9, fillColor=colors.HexColor(color), strokeColor=None, rx=2, ry=2))
        drawing.add(String(x + 15, y, label[:max_label], fontName=font, fontSize=8.5, fillColor=colors.HexColor(INK)))
        drawing.add(String(x + value_x, y, value, fontName=font, fontSize=8.5, fillColor=colors.HexColor(INK), textAnchor="end"))


def _segment_split(data: dict[str, Any], font: str, width: float) -> Any:
    from reportlab.graphics.charts.doughnut import Doughnut
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors

    total = data["kpis"]["money_out"]["value"] or 1
    habits = data["segment_trends"][: len(SLOTS)]
    rest = total - sum(h["total"] for h in habits)
    rows = [(SLOTS[i], h["name"], h["total"]) for i, h in enumerate(habits)]
    if rest > 0.5:
        rows.append((DEEMPH, "One-offs & other", rest))
    height = max(170, 40 + len(rows) * 17)
    d = Drawing(width, height)
    _title(d, "How your spending splits across habits", font, height)
    pie = Doughnut()
    pie.x, pie.y, pie.width, pie.height = 10, 8, 130, 130
    pie.data = [r[2] for r in rows]
    pie.innerRadiusFraction = 0.58
    pie.labels = None
    pie.slices.strokeColor = colors.white
    pie.slices.strokeWidth = 2
    for i, r in enumerate(rows):
        pie.slices[i].fillColor = colors.HexColor(r[0])
    d.add(pie)
    d.add(String(75, 76, _inr(total), fontName=font, fontSize=11, textAnchor="middle", fillColor=colors.HexColor(INK)))
    d.add(String(75, 63, "spent", fontName=font, fontSize=7.5, textAnchor="middle", fillColor=colors.HexColor(MUTED)))
    _legend_rows(d, 170, height - 40, [(c, n, f"{_inr(v)} · {v / total:.0%}") for c, n, v in rows], font)
    return d


def _line_chart(title: str, months: list[str], series: list[tuple[str, str, list[float]]],
                font: str, width: float, height: float = 170) -> Any:
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors

    d = Drawing(width, height)
    _title(d, title, font, height)
    chart = HorizontalLineChart()
    chart.x, chart.y = 45, 22
    chart.width = width - (230 if len(series) > 1 else 70)
    chart.height = height - 52
    chart.data = [s[2] for s in series]
    chart.categoryAxis.categoryNames = [_month(m) for m in months]
    chart.categoryAxis.labels.fontName = font
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.labels.fontName = font
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labelTextFormat = _inr
    chart.valueAxis.gridStrokeColor = colors.HexColor(GRID)
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.strokeColor = None
    chart.joinedLines = True
    for i, (color, _, _) in enumerate(series):
        chart.lines[i].strokeColor = colors.HexColor(color)
        chart.lines[i].strokeWidth = 2
    d.add(chart)
    if len(series) > 1:
        _legend_rows(d, width - 200, height - 40, [(c, n, _inr(sum(v))) for c, n, v in series], font,
                     value_x=198, max_label=24)
    return d


def _monthly(data: dict[str, Any], font: str, width: float) -> Any:
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors

    months = data["monthly"]
    height = 190
    d = Drawing(width, height)
    _title(d, "Money in vs spent, by month", font, height)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 45, 22, width - 60, height - 62
    chart.data = [[m["in"] for m in months], [m["out"] for m in months]]
    chart.categoryAxis.categoryNames = [_month(m["month"]) + ("*" if m.get("partial") else "") for m in months]
    chart.categoryAxis.labels.fontName = font
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.labels.fontName = font
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labelTextFormat = _inr
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = colors.HexColor(GRID)
    chart.bars[0].fillColor = colors.HexColor(SLOTS[0])
    chart.bars[1].fillColor = colors.HexColor(SLOTS[1])
    chart.bars.strokeColor = None
    chart.barSpacing = 2
    chart.groupSpacing = 14
    d.add(chart)
    for i, (color, label) in enumerate(((SLOTS[0], "Money in"), (SLOTS[1], "Spent"))):
        x = width - 150 + i * 75
        d.add(Rect(x, height - 13, 8, 8, fillColor=colors.HexColor(color), strokeColor=None))
        d.add(String(x + 12, height - 12, label, fontName=font, fontSize=8, fillColor=colors.HexColor(INK)))
    return d


def _money_flow(data: dict[str, Any], font: str, width: float) -> Any:
    """In print the flow becomes one 100% bar: where each rupee went."""
    from reportlab.graphics.shapes import Drawing, Rect
    from reportlab.lib import colors

    targets = data["flow"]["targets"]
    total = sum(t["value"] for t in targets) or 1
    slot = 0
    rows = []
    for t in targets:
        if t["kind"] == "saved":
            color = GOOD
        elif t["name"] in ("Other", "Other spending", "One-off spending"):
            color = DEEMPH
        else:
            color = SLOTS[slot] if slot < len(SLOTS) else DEEMPH
            slot += 1
        rows.append((color, t["name"], t["value"]))
    height = 60 + len(rows) * 17
    d = Drawing(width, height)
    _title(d, "Where your money went", font, height)
    x = 0.0
    bar_y = height - 42
    for color, _, value in rows:
        w = (value / total) * width
        d.add(Rect(x, bar_y, max(w - 2, 0.5), 16, fillColor=colors.HexColor(color), strokeColor=None))
        x += w
    _legend_rows(d, 0, bar_y - 20, [(c, n, f"{_inr(v)} · {v / total:.0%}") for c, n, v in rows], font)
    return d


def _hbars(title: str, rows: list[tuple[str, float]], font: str, width: float) -> Any:
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors

    height = 30 + len(rows) * 20
    d = Drawing(width, height)
    _title(d, title, font, height)
    top = max((v for _, v in rows), default=1) or 1
    for i, (name, value) in enumerate(rows):
        y = height - 36 - i * 20
        d.add(String(0, y + 3, name[:26], fontName=font, fontSize=8.5, fillColor=colors.HexColor(INK)))
        w = (value / top) * (width - 230)
        d.add(Rect(150, y, w, 11, fillColor=colors.HexColor(SLOTS[0]), strokeColor=None, rx=2, ry=2))
        d.add(String(150 + w + 6, y + 2, _inr(value), fontName=font, fontSize=8.5, fillColor=colors.HexColor(INK)))
    return d


def _calendar(data: dict[str, Any], font: str, width: float) -> Any:
    import datetime as dt

    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors

    days = data["calendar"]
    values = sorted(d["out"] for d in days if d["out"] > 0)
    cuts = [values[min(len(values) - 1, int(p * len(values)))] for p in (0.2, 0.45, 0.7, 0.9)] if values else []
    first = dt.date.fromisoformat(days[0]["date"])
    offset = first.weekday()
    cell, gap = 11, 2.5
    height = 30 + 7 * (cell + gap)
    d = Drawing(width, height)
    _title(d, "Spending calendar - darker means more spent", font, height)
    for i, day in enumerate(days):
        slot = i + offset
        x = 28 + (slot // 7) * (cell + gap)
        y = height - 28 - (slot % 7 + 1) * (cell + gap)
        v = day["out"]
        b = 0 if v <= 0 else 1 + sum(1 for c in cuts if v > c)
        d.add(Rect(x, y, cell, cell, fillColor=colors.HexColor(SEQ[min(b, 5)]), strokeColor=None, rx=2, ry=2))
    for r in (0, 2, 4, 6):
        d.add(String(0, height - 28 - (r + 1) * (cell + gap) + 2, WEEKDAYS[r], fontName=font, fontSize=7,
                     fillColor=colors.HexColor(MUTED)))
    return d


def _kpis(data: dict[str, Any], font: str, width: float) -> Any:
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors

    k = data["kpis"]
    tiles = [
        ("Money in", _inr(k["money_in"]["value"])),
        ("Spent", _inr(k["money_out"]["value"])),
        ("Saved" if k["saved"]["value"] >= 0 else "Overspent", _inr(abs(k["saved"]["value"]))),
        ("Bills due", f"{k['bills_due']['count']} · {_inr(k['bills_due']['value'])}"),
    ]
    height = 58
    d = Drawing(width, height)
    w = (width - 18) / 4
    for i, (label, value) in enumerate(tiles):
        x = i * (w + 6)
        d.add(Rect(x, 0, w, height, fillColor=colors.HexColor("#f3f6fa"), strokeColor=None, rx=6, ry=6))
        d.add(String(x + 10, height - 18, label, fontName=font, fontSize=8, fillColor=colors.HexColor(MUTED)))
        d.add(String(x + 10, 14, value, fontName=font, fontSize=15, fillColor=colors.HexColor(INK)))
    return d


def pdf_flowables(tag: str, arg: str | None, data: dict[str, Any], font: str, width: float) -> list[Any]:
    """The drawing(s) for one tag, or [] when it can't be drawn."""
    if data.get("empty"):
        return []
    habits = data.get("segment_trends") or []
    months = data["period"].get("trend_months") or data["period"]["months"]
    try:
        if tag == "segment_split" and habits:
            return [_segment_split(data, font, width)]
        if tag == "segment_trends" and habits:
            top = habits[: len(SLOTS)]
            return [_line_chart("Each habit, month by month", months,
                                [(SLOTS[i], h["name"], h["series"]) for i, h in enumerate(top)], font, width)]
        if tag == "segment" and arg is not None:
            for i, h in enumerate(habits):
                if h["segment_id"] == int(arg):
                    color = SLOTS[i] if i < len(SLOTS) else DEEMPH
                    title = f"{h['name']} - {_inr(h['total'])} ({h['share']:.0%} of spending), ≈{_inr(h['per_month'])}/month"
                    return [_line_chart(title, months, [(color, h["name"], h["series"])], font, width, 140)]
            return []
        if tag == "segments_detail" and habits:
            return pdf_flowables("segment_split", None, data, font, width) + pdf_flowables(
                "segment_trends", None, data, font, width)
        if tag == "money_flow":
            return [_money_flow(data, font, width)]
        if tag == "monthly":
            return [_monthly(data, font, width)]
        if tag == "merchants":
            return [_hbars("Top merchants", [(m["name"], m["total"]) for m in data["merchants"]], font, width)]
        if tag == "weekday":
            totals = data["week_hour"]["weekday_totals"]
            return [_hbars("Spending by weekday", list(zip(WEEKDAYS, totals, strict=False)), font, width)]
        if tag == "calendar":
            return [_calendar(data, font, width)]
        if tag == "kpis":
            return [_kpis(data, font, width)]
    except Exception:  # noqa: BLE001 - a chart that fails to draw is left out, not fatal
        import logging

        logging.getLogger("finpilot.report").exception("chart %s:%s failed", tag, arg)
    return []
