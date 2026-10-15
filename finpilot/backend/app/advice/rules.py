"""Deterministic advice from the brief. Used when no model is configured."""

from __future__ import annotations

from app.advice.brief import AdviceBrief, _inr


def _is_fixed(segment: dict) -> bool:
    return segment.get("transfer_share", 0) > 0.5 or segment.get("emi_share", 0) > 0.5


def recommend(brief: AdviceBrief, question: str) -> dict:
    """Return the same shape the model is asked for."""
    months = max(1, brief.months_covered)
    recs: list[dict] = []

    discretionary = [
        s for s in brief.segments if s["money_out"] > 0 and not _is_fixed(s)
    ]
    for segment in discretionary[:3]:
        monthly = segment["money_out"] / months
        saving = round(monthly * 0.25, 0)
        merchants = ", ".join(m["name"] for m in segment.get("merchants", [])[:3])
        where = f" Typical names: {merchants}." if merchants else ""
        recs.append(
            {
                "title": f"Trim “{segment['name']}” by about a quarter",
                "detail": (
                    f"This habit is {_inr(segment['money_out'])} out over "
                    f"{months} month(s), about {_inr(monthly)} a month "
                    f"({segment['share_of_out']:.0%} of money leaving the account)."
                    f"{where} Cutting it by 25% next month is a concrete target, "
                    f"not a guess."
                ),
                "monthly_saving_rupees": saving,
                "segment_id": segment["segment_id"],
                "segment_name": segment["name"],
            }
        )

    for segment in brief.segments:
        if segment.get("late_night_share", 0) >= 0.35 and not _is_fixed(segment):
            monthly = segment["money_out"] / months
            recs.append(
                {
                    "title": f"Cap late-night {segment['name']}",
                    "detail": (
                        f"{segment['late_night_share']:.0%} of timed rows in this "
                        f"segment land between 22:00 and 01:59. A nightly cap, or "
                        f"moving those orders earlier, usually cuts impulse spend "
                        f"without touching rent or EMI."
                    ),
                    "monthly_saving_rupees": round(monthly * 0.15, 0),
                    "segment_id": segment["segment_id"],
                    "segment_name": segment["name"],
                }
            )
            break

    if brief.unpaid_bills:
        total = sum(b["amount"] for b in brief.unpaid_bills)
        names = ", ".join(b["biller"] for b in brief.unpaid_bills[:4])
        recs.append(
            {
                "title": "Clear unpaid bills before they become fees",
                "detail": (
                    f"{_inr(total)} is still due ({names}). Paying on time does "
                    f"not reduce lifestyle spend, but it stops late fees from "
                    f"showing up as a new habit next month."
                ),
                "monthly_saving_rupees": None,
                "segment_id": None,
                "segment_name": None,
            }
        )

    if brief.flags.get("fees"):
        recs.append(
            {
                "title": "Stop the leak in bank fees",
                "detail": (
                    f"{_inr(brief.flags['fees'])} was tagged as fees across this "
                    f"ledger. Minimum-balance misses and ATM charges are usually "
                    f"cheaper to prevent than to cut elsewhere."
                ),
                "monthly_saving_rupees": round(brief.flags["fees"] / months, 0),
                "segment_id": None,
                "segment_name": None,
            }
        )

    # de-dupe by title, keep order
    seen: set[str] = set()
    unique: list[dict] = []
    for rec in recs:
        if rec["title"] in seen:
            continue
        seen.add(rec["title"])
        unique.append(rec)
    recs = unique[:5]

    if not recs and brief.top_merchants:
        top = brief.top_merchants[0]
        recs.append(
            {
                "title": f"Watch {_inr(top['amount'])} at {top['name']}",
                "detail": (
                    f"{top['name']} is the largest merchant in this upload "
                    f"({top['count']} payments). If next month's goal is to spend "
                    f"less, start there: skip one in four visits, or set a weekly cap."
                ),
                "monthly_saving_rupees": round((top["amount"] / months) * 0.25, 0),
                "segment_id": None,
                "segment_name": None,
            }
        )

    last = (
        f"Last month ({brief.last_month}) you sent {_inr(brief.last_month_out)} out."
        if brief.last_month
        else f"Across this ledger, {_inr(brief.money_out)} left the account."
    )
    if brief.prior_month_out and brief.last_month_out:
        delta = brief.last_month_out - brief.prior_month_out
        if abs(delta) >= 1:
            direction = "more" if delta > 0 else "less"
            last += f" That is {_inr(abs(delta))} {direction} than the month before."

    if brief.segmented:
        names = ", ".join(s["name"] for s in brief.segments[:4]) or "none"
        cluster = (
            f" The habits that show up as segments are: {names}."
        )
    else:
        cluster = (
            " Segments have not been found yet — run “Find segments” for habit-level "
            "cuts rather than a single monthly total."
        )

    asked = question.strip() or "How do I spend less next month?"
    answer = (
        f"On the documents you uploaded: {last}{cluster} "
        f"For “{asked}”, the useful moves are the ones below — they come from "
        f"this ledger, not generic budgeting tips. Transfers, EMI and fees are "
        f"called out separately so you do not try to cut rent to save on food."
    )
    return {"answer": answer, "recommendations": recs}
