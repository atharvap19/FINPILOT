"""Today's (or this week's) budget: what is really left to spend.

The user gives a budget and what they plan to spend on. FinPilot subtracts
two things they may not have counted, both learned from their own ledger:

* **commitments** -- fixed payments that fall due inside the window: rent,
  subscriptions, EMIs, bills (a payee paid in several months on roughly the
  same day, at roughly the same amount);
* **usual spends** -- what they typically spend on this weekday that they did
  *not* plan for: the Friday Swiggy order, the daily commute.

  safe to spend = budget - planned - commitments - usual spends

Only a day or a week: within a week, habits repeat closely enough to be
useful; over a month or a year, one-off events (a hospital bill, a wedding)
dominate and any single figure would be false precision.

Everything here is arithmetic over the ledger -- no model call -- and every
figure comes with the basis it was computed from, so the maths can be shown.
"""

from __future__ import annotations

import datetime as dt
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from app.agent.tools import AgentContext

PERIODS = {"day": 1, "week": 7}
WEEKDAYS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
#: a usual spend smaller than this (per day, on average) folds into "small spends"
MIN_USUAL = 40.0
#: a spend is "usual" if it happens on at least this share of those weekdays
#: (or weeks); rarer ones are averaged into one occasional-spends buffer
MIN_FREQUENCY = 0.35
MAX_USUAL_ITEMS = 6
#: a payee counts as a fixed commitment if its amounts vary less than this
FIXED_VARIATION = 0.15
#: spends above this percentile of a person's own debits are treated as
#: one-offs, not habits -- they would inflate every "usual" figure
ONE_OFF_PERCENTILE = 0.95


@dataclass
class PlannedItem:
    label: str
    amount: float


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", (text or "").lower()) if len(t) >= 3}


#: words a planned item and a merchant/habit can share without meaning the same thing
_STOP = {"and", "the", "for", "with", "similar", "spending", "payment", "upi", "order", "orders", "already", "spent"}

#: what someone types -> the kinds of merchant it covers
_SYNONYMS = {
    "lunch": {"swiggy", "zomato", "food", "restaurant", "dining", "cafe"},
    "dinner": {"swiggy", "zomato", "food", "restaurant", "dining"},
    "breakfast": {"swiggy", "zomato", "food", "cafe"},
    "food": {"swiggy", "zomato", "restaurant", "dining"},
    "petrol": {"hpcl", "bpcl", "iocl", "fuel", "indian", "oil", "shell"},
    "fuel": {"hpcl", "bpcl", "iocl", "petrol"},
    "groceries": {"blinkit", "bigbasket", "dmart", "zepto", "instamart", "grocery"},
    "grocery": {"blinkit", "bigbasket", "dmart", "zepto", "instamart"},
    "cab": {"uber", "ola", "rapido"},
    "taxi": {"uber", "ola", "rapido"},
    "commute": {"uber", "ola", "rapido", "metro"},
    "shopping": {"amazon", "flipkart", "myntra", "ajio"},
    "movie": {"bookmyshow", "pvr", "inox"},
    "gym": {"cult", "fit", "gym"},
}


def _covers(item: str, label: str) -> bool:
    """Does a planned item ("lunch") already account for a usual spend ("Swiggy")?"""
    words = _tokens(item) - _STOP
    target = _tokens(label) - _STOP
    if words & target:
        return True
    return any(target & _SYNONYMS.get(w, set()) for w in words)


def parse_items(text: str) -> list[PlannedItem]:
    """ "lunch 200, petrol 500\\nmovie ₹350" -> items. Amount before or after the label."""
    items = []
    # a comma between digits is a thousands separator ("1,200"), not a break
    for chunk in re.split(r"(?:,(?!\d)|[\n;])+", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = re.search(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)", chunk, re.IGNORECASE)
        if not match:
            continue
        amount = float(match.group(1).replace(",", ""))
        label = (chunk[: match.start()] + " " + chunk[match.end() :]).strip(" -:")
        label = re.sub(r"\b(rs\.?|inr)\b", "", label, flags=re.IGNORECASE).strip() or "Planned"
        items.append(PlannedItem(label=label[:60], amount=round(amount, 2)))
    return items


# --------------------------------------------------------------------------
# what the ledger says about this person
# --------------------------------------------------------------------------


def _label(row: dict[str, Any]) -> str:
    if row.get("merchant"):
        return row["merchant"]
    if row.get("segment_name") and (row.get("segment_id") or -1) >= 0:
        return row["segment_name"]
    return "Other spending"


def _commitments(spend: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    """Payees paid in 2+ months at a steady amount on a steady day."""
    by_payee: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in spend:
        if r["merchant"]:
            by_payee[r["merchant"]].append(r)
    found, names = [], set()
    for payee, rows in by_payee.items():
        months = {r["date"].strftime("%Y-%m") for r in rows}
        if len(months) < 2 or len(rows) > len(months) + 1:
            continue  # one-off, or too frequent to be a fixed payment
        amounts = [r["amount"] for r in rows]
        mean = statistics.mean(amounts)
        if mean <= 0 or statistics.pstdev(amounts) / mean > FIXED_VARIATION:
            continue
        day = Counter(r["date"].day for r in rows).most_common(1)[0][0]
        found.append({"payee": payee, "amount": round(mean, 2), "day_of_month": day, "months": len(months)})
        names.add(payee)
    return found, names


def _history(ctx: AgentContext) -> dict[str, Any]:
    rows = [r for r in ctx.rows if r["date"]]
    spend = [r for r in rows if r["direction"] == "debit" and not r["is_transfer"]]
    if not spend:
        return {"empty": True}
    commitments, fixed = _commitments(spend)
    habitual = [r for r in spend if r["merchant"] not in fixed]
    if habitual:
        amounts = sorted(r["amount"] for r in habitual)
        cap = amounts[min(len(amounts) - 1, int(ONE_OFF_PERCENTILE * len(amounts)))]
        habitual = [r for r in habitual if r["amount"] <= cap]

    first = min(r["date"] for r in rows)
    last = max(r["date"] for r in rows)
    # how many of each weekday the history covers -- days with no spending
    # count too, or every average would be inflated
    weekday_count = Counter((first + dt.timedelta(days=i)).weekday() for i in range((last - first).days + 1))
    per_weekday: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    days_with: dict[int, dict[str, set[dt.date]]] = defaultdict(lambda: defaultdict(set))
    weeks_with: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for r in habitual:
        wd = r["date"].weekday()
        per_weekday[wd][_label(r)] += r["amount"]
        days_with[wd][_label(r)].add(r["date"])
        weeks_with[_label(r)].add(tuple(r["date"].isocalendar()[:2]))
    all_weeks = {tuple((first + dt.timedelta(days=i)).isocalendar()[:2]) for i in range((last - first).days + 1)}
    return {
        "empty": False,
        "from": first,
        "to": last,
        "weekday_count": weekday_count,
        "per_weekday": per_weekday,
        "days_with": days_with,
        "weeks_with": weeks_with,
        "weeks": len(all_weeks),
        "commitments": commitments,
    }


def _usual_for(history: dict[str, Any], day: dt.date) -> list[dict[str, Any]]:
    wd = day.weekday()
    n = history["weekday_count"].get(wd, 0)
    if not n:
        return []
    out = []
    for label, total in history["per_weekday"][wd].items():
        avg = total / n
        hits = len(history["days_with"][wd][label])
        out.append({"label": label, "amount": avg, "hits": hits, "of": n})
    out.sort(key=lambda u: -u["amount"])
    return out


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------


def plan(
    ctx: AgentContext,
    *,
    period: str,
    budget: float,
    items: list[PlannedItem],
    start: dt.date | None = None,
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    """Budget - planned - commitments - usual spends, with the workings."""
    if period not in PERIODS:
        raise ValueError("plan a day or a week -- further ahead, one-off expenses make any figure unreliable")
    if budget < 0:
        raise ValueError("the budget can't be negative")
    start = start or dt.date.today()
    days = [start + dt.timedelta(days=i) for i in range(PERIODS[period])]
    exclude = exclude or set()
    history = _history(ctx)
    planned_total = round(sum(i.amount for i in items), 2)

    # what has already gone out inside the window (typed or from statements)
    today = dt.date.today()
    window = {d for d in days if d <= today}
    spent_by: dict[str, float] = defaultdict(float)
    for r in ctx.rows:
        if r["date"] in window and r["direction"] == "debit" and not r["is_transfer"]:
            spent_by[_label(r)] += r["amount"]
    spent = [{"label": k, "amount": round(v, 2)} for k, v in sorted(spent_by.items(), key=lambda kv: -kv[1])]
    spent_total = round(sum(spent_by.values()), 2)
    # a usual spend or a fixed payment already made today is not still to come
    done = [PlannedItem(label=f"already spent: {k}", amount=v) for k, v in spent_by.items()]
    cover_with = list(items) + done

    commitments: list[dict[str, Any]] = []
    usual: list[dict[str, Any]] = []
    if not history["empty"]:
        fixed_days = {}
        for c in history["commitments"]:
            for day in days:
                if day.day == c["day_of_month"]:
                    fixed_days[c["payee"]] = day
        for c in history["commitments"]:
            if c["payee"] not in fixed_days:
                continue
            key = f"commitment:{c['payee']}"
            covered = next((i.label for i in cover_with if _covers(i.label, c["payee"])), None)
            commitments.append({
                "key": key,
                "label": c["payee"],
                "amount": c["amount"],
                "date": fixed_days[c["payee"]].isoformat(),
                "basis": f"paid around the {_ordinal(c['day_of_month'])} in {c['months']} months",
                "covered_by": covered,
                "included": covered is None and key not in exclude,
            })

        totals: dict[str, dict[str, Any]] = {}
        for day in days:
            for u in _usual_for(history, day):
                entry = totals.setdefault(u["label"], {"amount": 0.0, "basis": []})
                entry["amount"] += u["amount"]
                entry["basis"].append((day, u))
        small = 0.0
        small_parts: list[tuple[str, float]] = []
        for label, entry in sorted(totals.items(), key=lambda kv: -kv[1]["amount"]):
            per_day = entry["amount"] / len(days)
            covered = next((i.label for i in cover_with if _covers(i.label, label)), None)
            if period == "day":
                _, first_basis = entry["basis"][0]
                frequency = first_basis["hits"] / max(first_basis["of"], 1)
            else:
                frequency = len(history["weeks_with"].get(label, ())) / max(history["weeks"], 1)
            if frequency < MIN_FREQUENCY or per_day < MIN_USUAL or len(usual) >= MAX_USUAL_ITEMS:
                # a planned item already pays for this one -- don't count it twice
                if covered is None:
                    small += entry["amount"]
                    small_parts.append((label, entry["amount"]))
                continue
            key = f"usual:{label}"
            day, u = entry["basis"][0]
            basis = (
                f"on {u['hits']} of the last {u['of']} {WEEKDAYS[day.weekday()]}, about ₹{u['amount'] * u['of'] / max(u['hits'], 1):,.0f} each time"
                if period == "day"
                else f"about ₹{per_day:,.0f} a day in a typical week"
            )
            usual.append({
                "key": key,
                "label": label,
                "amount": round(entry["amount"], 2),
                "basis": basis,
                "covered_by": covered,
                "included": covered is None and key not in exclude,
            })
        if small >= 1:
            key = "usual:small"
            usual.append({
                "key": key,
                "label": "Buffer for occasional spends",
                "amount": round(small, 2),
                "basis": "the average of your smaller and occasional payments on a typical "
                + ("day like this" if period == "day" else "week")
                + ", mostly "
                + ", ".join(f"{name} ₹{amount:,.0f}" for name, amount in small_parts[:3])
                + " -- untick it if this one will be quiet",
                "covered_by": None,
                "included": key not in exclude,
            })

    commitments_total = round(sum(c["amount"] for c in commitments if c["included"]), 2)
    usual_total = round(sum(u["amount"] for u in usual if u["included"]), 2)
    safe = round(budget - spent_total - planned_total - commitments_total - usual_total, 2)
    status = "over" if safe < 0 else "tight" if budget and safe < 0.1 * budget else "ok"
    return {
        "period": period,
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
        "days": len(days),
        "budget": round(budget, 2),
        "planned": [{"label": i.label, "amount": i.amount} for i in items],
        "planned_total": planned_total,
        "spent": spent,
        "spent_total": spent_total,
        "commitments": commitments,
        "commitments_total": commitments_total,
        "usual": usual,
        "usual_total": usual_total,
        "safe_to_spend": safe,
        "per_day": round(safe / len(days), 2),
        "status": status,
        "history": None if history["empty"] else {
            "from": history["from"].isoformat(),
            "to": history["to"].isoformat(),
            "weeks": round(((history["to"] - history["from"]).days + 1) / 7, 1),
        },
    }


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
