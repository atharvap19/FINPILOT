"""Everything the dashboard draws, computed from the ledger in one pass.

No model is involved: every figure here is a sum over ledger rows, so the
dashboard costs nothing to load and always agrees with the CSV export. It
reuses :class:`app.agent.tools.AgentContext` so "spending" means the same
thing on the dashboard as in the agent's answers -- debits that are not
transfers between the user's own accounts.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from typing import Any

from app.agent.tools import AgentContext

#: Flow diagram: how many named nodes per side before the rest fold into "Other".
FLOW_SOURCES = 3
FLOW_TARGETS = 5
TOP_MERCHANTS = 8


def _month(day: dt.date) -> str:
    return day.strftime("%Y-%m")


def _label(row: dict[str, Any]) -> str:
    if row.get("merchant"):
        return row["merchant"]
    words = [w for w in (row.get("description") or "").replace("-", " ").replace("/", " ").split()
             if w.isalpha() and len(w) > 2]
    return " ".join(words[:3]).title() or "Other"


def _fold(totals: Counter[str], keep: int) -> list[tuple[str, float]]:
    ranked = totals.most_common()
    head = ranked[:keep]
    tail = sum(v for _, v in ranked[keep:])
    if tail > 0.5:
        head.append(("Other", tail))
    return [(k, round(v, 2)) for k, v in head if v > 0.5]


def build_insights(job_id: str) -> dict[str, Any]:
    ctx = AgentContext(job_id)
    rows = [r for r in ctx.rows if r["date"]]
    if not rows:
        return {"empty": True}

    spend = [r for r in rows if r["direction"] == "debit" and not r["is_transfer"]]
    income = [r for r in rows if r["direction"] == "credit" and not r["is_transfer"]]
    first, last = min(r["date"] for r in rows), max(r["date"] for r in rows)

    # --- months ------------------------------------------------------------
    months: dict[str, dict[str, float]] = defaultdict(lambda: {"in": 0.0, "out": 0.0})
    for r in spend:
        months[_month(r["date"])]["out"] += r["amount"]
    for r in income:
        months[_month(r["date"])]["in"] += r["amount"]
    monthly = [
        {"month": m, "in": round(v["in"], 2), "out": round(v["out"], 2), "net": round(v["in"] - v["out"], 2),
         "partial": not _is_full_month(m, first, last)}
        for m, v in sorted(months.items())
    ]
    # Trends use full months only: a statement that ends on the 2nd makes its
    # last month look like a collapse in spending that never happened.
    full = [m for m in monthly if not m["partial"]]
    trend_months = full if len(full) >= 2 else monthly

    total_in = sum(r["amount"] for r in income)
    total_out = sum(r["amount"] for r in spend)
    unpaid = [b for b in ctx.bills if b["status"] in ("due", "overdue")]

    def series(key: str) -> list[float]:
        return [m[key] for m in trend_months]

    def delta(key: str) -> float | None:
        if len(full) < 2 or not full[-2][key]:
            return None
        return round((full[-1][key] - full[-2][key]) / abs(full[-2][key]), 4)

    kpis = {
        "money_in": {"value": round(total_in, 2), "series": series("in"), "delta": delta("in")},
        "money_out": {"value": round(total_out, 2), "series": series("out"), "delta": delta("out")},
        "saved": {
            "value": round(total_in - total_out, 2),
            "rate": round((total_in - total_out) / total_in, 4) if total_in else None,
            "series": series("net"),
            "delta": delta("net"),
        },
        "bills_due": {
            "count": len(unpaid),
            "value": round(sum(b["amount_due"] for b in unpaid), 2),
            "total_bills": len(ctx.bills),
        },
        "transactions": len(rows),
    }

    # --- calendar --------------------------------------------------------------
    daily: Counter[dt.date] = Counter()
    for r in spend:
        daily[r["date"]] += r["amount"]
    calendar = [
        {"date": (first + dt.timedelta(days=i)).isoformat(),
         "out": round(daily.get(first + dt.timedelta(days=i), 0.0), 2)}
        for i in range((last - first).days + 1)
    ]

    # --- weekday x hour --------------------------------------------------------
    grid = [[0.0] * 24 for _ in range(7)]
    weekday_totals = [0.0] * 7
    timed = 0
    for r in spend:
        weekday_totals[r["date"].weekday()] += r["amount"]
        if r["time"]:
            grid[r["date"].weekday()][int(r["time"][:2])] += r["amount"]
            timed += 1
    week_hour = {
        "grid": [[round(v, 2) for v in row] for row in grid],
        "weekday_totals": [round(v, 2) for v in weekday_totals],
        "timed_share": round(timed / len(spend), 3) if spend else 0.0,
    }

    # --- merchants -------------------------------------------------------------
    merchant_total: Counter[str] = Counter()
    merchant_count: Counter[str] = Counter()
    for r in spend:
        if r["merchant"]:
            merchant_total[r["merchant"]] += r["amount"]
            merchant_count[r["merchant"]] += 1
    merchants = [
        {"name": name, "total": round(total, 2), "count": merchant_count[name],
         "share": round(total / total_out, 4) if total_out else 0.0}
        for name, total in merchant_total.most_common(TOP_MERCHANTS)
    ]

    # --- money flow: where it came from -> where it went -------------------------
    sources: Counter[str] = Counter()
    for r in income:
        sources[_label(r)] += r["amount"]
    targets: Counter[str] = Counter()
    by_segment = ctx.segmented
    for r in spend:
        if by_segment:
            key = (r["segment_name"] or "One-off spending") if (r["segment_id"] is not None and r["segment_id"] >= 0) else "One-off spending"
        else:
            key = r["merchant"] or "Other spending"
        targets[key] += r["amount"]
    flow_sources = _fold(sources, FLOW_SOURCES)
    # one-offs are not a habit: keep them out of the ranked, coloured slots
    one_offs = targets.pop("One-off spending", 0.0)
    flow_targets = _fold(targets, FLOW_TARGETS)
    if one_offs > 0.5:
        flow_targets.append(("One-off spending", round(one_offs, 2)))
    gap = total_in - total_out
    if gap > 0.5:
        flow_targets.append(("Saved", round(gap, 2)))
    elif gap < -0.5:
        flow_sources.append(("From your balance", round(-gap, 2)))
    flow = {
        "sources": [{"name": n, "value": v} for n, v in flow_sources],
        "targets": [{"name": n, "value": v, "kind": "saved" if n == "Saved" else "spend"}
                    for n, v in flow_targets],
        "grouped_by": "segment" if by_segment else "merchant",
    }

    # --- habits: one story per segment ----------------------------------------
    month_keys = [m["month"] for m in trend_months]
    segment_trends = _habits(ctx, spend, total_out, month_keys) if by_segment else []

    return {
        "empty": False,
        "period": {"from": first.isoformat(), "to": last.isoformat(),
                   "months": [m["month"] for m in monthly], "trend_months": month_keys},
        "kpis": kpis,
        "monthly": monthly,
        "calendar": calendar,
        "week_hour": week_hour,
        "merchants": merchants,
        "flow": flow,
        "segment_trends": segment_trends,
        "segmented": by_segment,
    }


def _is_full_month(month: str, first: dt.date, last: dt.date) -> bool:
    year, mon = map(int, month.split("-"))
    start = dt.date(year, mon, 1)
    end = (dt.date(year + mon // 12, mon % 12 + 1, 1) - dt.timedelta(days=1))
    # allow a few days of slack: statements rarely start on the 1st exactly
    return first <= start + dt.timedelta(days=4) and last >= end - dt.timedelta(days=4)


WEEKDAY_NAMES = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")


def _habits(ctx: AgentContext, spend: list[dict[str, Any]], total_out: float,
            month_keys: list[str]) -> list[dict[str, Any]]:
    """Per segment: what it costs, where, when, and which way it is heading."""
    rationale: dict[int, str] = {}
    for entry in (ctx.segment_report or {}).get("segments_detail", []) or []:
        if entry.get("rationale"):
            rationale[int(entry["segment_id"])] = entry["rationale"]

    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in spend:
        if r["segment_id"] is not None and r["segment_id"] >= 0:
            groups[r["segment_id"]].append(r)

    out = []
    for seg_id, rows in groups.items():
        total = sum(r["amount"] for r in rows)
        per_month: Counter[str] = Counter()
        merchants: Counter[str] = Counter()
        weekdays: Counter[int] = Counter()
        hours: Counter[int] = Counter()
        for r in rows:
            per_month[_month(r["date"])] += r["amount"]
            if r["merchant"]:
                merchants[r["merchant"]] += r["amount"]
            weekdays[r["date"].weekday()] += 1
            if r["time"]:
                hours[int(r["time"][:2])] += 1
        amounts = sorted(r["amount"] for r in rows)
        series = [round(per_month.get(m, 0.0), 2) for m in month_keys]
        active = [v for v in series if v > 0]
        out.append({
            "segment_id": seg_id,
            "name": rows[0]["segment_name"] or f"segment {seg_id}",
            "why": rationale.get(seg_id),
            "total": round(total, 2),
            "count": len(rows),
            "share": round(total / total_out, 4) if total_out else 0.0,
            "median": round(amounts[len(amounts) // 2], 2),
            "per_month": round(total / max(len(active), 1), 2),
            "series": series,
            "change": round((active[-1] - active[0]) / active[0], 4) if len(active) >= 2 and active[0] else None,
            "merchants": [{"name": k, "total": round(v, 2)} for k, v in merchants.most_common(3)],
            "busiest_day": WEEKDAY_NAMES[weekdays.most_common(1)[0][0]] if weekdays else None,
            "busiest_hour": hours.most_common(1)[0][0] if sum(hours.values()) >= 3 else None,
        })
    out.sort(key=lambda h: -h["total"])
    return out
