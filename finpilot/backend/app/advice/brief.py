"""A privacy-safe snapshot of one job, for advice.

Same boundary as :mod:`segmentation.profile`: statistics and merchant names,
never raw descriptions, account numbers, UPI handles or person names.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from typing import Any

LATE_NIGHT = frozenset({22, 23, 0, 1})
TOP_N = 5


def _money(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _inr(amount: float) -> str:
    return f"₹{amount:,.0f}"


def _hour(value: time | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value.hour
    return None


@dataclass
class AdviceBrief:
    """Everything the advisor is allowed to know about a job."""

    period_start: str | None = None
    period_end: str | None = None
    months_covered: int = 0
    documents: list[dict[str, Any]] = field(default_factory=list)
    money_out: float = 0.0
    money_in: float = 0.0
    last_month: str | None = None
    last_month_out: float = 0.0
    prior_month_out: float = 0.0
    monthly: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    segmented: bool = False
    unsegmented_rows: int = 0
    top_merchants: list[dict[str, Any]] = field(default_factory=list)
    unpaid_bills: list[dict[str, Any]] = field(default_factory=list)
    flags: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "months_covered": self.months_covered,
            "documents": self.documents,
            "money_out": round(self.money_out, 2),
            "money_in": round(self.money_in, 2),
            "last_month": self.last_month,
            "last_month_out": round(self.last_month_out, 2),
            "prior_month_out": round(self.prior_month_out, 2),
            "monthly": self.monthly,
            "segments": self.segments,
            "segmented": self.segmented,
            "unsegmented_rows": self.unsegmented_rows,
            "top_merchants": self.top_merchants,
            "unpaid_bills": self.unpaid_bills,
            "flags": self.flags,
        }

    def as_prompt(self) -> str:
        """Compact text a model can read without inventing extra structure."""
        lines: list[str] = []
        if self.period_start and self.period_end:
            lines.append(
                f"Ledger period: {self.period_start} to {self.period_end} "
                f"({self.months_covered} months)"
            )
        else:
            lines.append("Ledger period: unknown")
        if self.documents:
            docs = ", ".join(
                f"{d['document_type']}"
                + (f" ({d['issuer']})" if d.get("issuer") else "")
                for d in self.documents
            )
            lines.append(f"Uploaded documents: {docs}")
        lines.append(
            f"Money out: {_inr(self.money_out)}; money in: {_inr(self.money_in)}"
        )
        if self.last_month:
            lines.append(
                f"Last month ({self.last_month}) out: {_inr(self.last_month_out)}"
            )
        if self.prior_month_out:
            lines.append(f"Month before that, out: {_inr(self.prior_month_out)}")
        if self.monthly:
            months = ", ".join(
                f"{m['month']} out {_inr(m['out'])}" for m in self.monthly
            )
            lines.append(f"By month: {months}")
        if self.flags:
            flagged = ", ".join(f"{k} {_inr(v)}" for k, v in self.flags.items() if v)
            if flagged:
                lines.append(f"Tagged spend: {flagged}")
        if self.segmented and self.segments:
            lines.append("Spending segments (habits the clusterer found):")
            for seg in self.segments:
                merchants = ", ".join(
                    f"{m['name']} {_inr(m['amount'])}" for m in seg.get("merchants", [])
                )
                extra = f"; merchants: {merchants}" if merchants else ""
                lines.append(
                    f"- {seg['name']}: {seg['size']} rows, "
                    f"out {_inr(seg['money_out'])} "
                    f"({seg['share_of_out']:.0%} of money out)"
                    f"{extra}"
                )
            if self.unsegmented_rows:
                lines.append(
                    f"Unsegmented one-off rows: {self.unsegmented_rows}"
                )
        else:
            lines.append("Segmentation has not been run on this ledger.")
        if self.top_merchants:
            named = ", ".join(
                f"{m['name']} {_inr(m['amount'])} ({m['count']} times)"
                for m in self.top_merchants
            )
            lines.append(f"Top merchants overall: {named}")
        if self.unpaid_bills:
            bills = ", ".join(
                f"{b['biller']} {_inr(b['amount'])} ({b['status']})"
                for b in self.unpaid_bills
            )
            lines.append(f"Unpaid bills: {bills}")
        return "\n".join(lines)


def build_brief(
    *,
    documents: list[Any],
    rows: list[Any],
    obligations: list[Any],
) -> AdviceBrief:
    """ORM (or duck-typed) rows -> the advisor's brief."""
    brief = AdviceBrief()
    seen_docs: list[dict[str, Any]] = []
    for doc in documents:
        kind = getattr(doc, "document_type", None) or "unknown"
        issuer = getattr(doc, "issuer", None)
        seen_docs.append({"document_type": kind, "issuer": issuer})
    brief.documents = seen_docs

    if not rows:
        return brief

    dates = [r.date for r in rows if getattr(r, "date", None)]
    if dates:
        brief.period_start = min(dates).isoformat()
        brief.period_end = max(dates).isoformat()

    month_out: dict[str, float] = defaultdict(float)
    month_in: dict[str, float] = defaultdict(float)
    merchant_amount: Counter[str] = Counter()
    merchant_count: Counter[str] = Counter()
    flag_totals = {
        "transfers": 0.0,
        "fees": 0.0,
        "emi": 0.0,
        "cash_withdrawals": 0.0,
        "refunds": 0.0,
    }

    segments: dict[int, dict[str, Any]] = {}
    unsegmented = 0

    for row in rows:
        amount = abs(_money(getattr(row, "amount", 0)))
        direction = getattr(row, "direction", "") or ""
        when: date | None = getattr(row, "date", None)
        month = when.strftime("%Y-%m") if when else "unknown"
        is_debit = direction == "debit"

        if is_debit:
            brief.money_out += amount
            month_out[month] += amount
        elif direction == "credit":
            brief.money_in += amount
            month_in[month] += amount

        if getattr(row, "is_transfer", False) and is_debit:
            flag_totals["transfers"] += amount
        if getattr(row, "is_fee", False) and is_debit:
            flag_totals["fees"] += amount
        if getattr(row, "emi_flag", False) and is_debit:
            flag_totals["emi"] += amount
        if getattr(row, "is_cash_withdrawal", False) and is_debit:
            flag_totals["cash_withdrawals"] += amount
        if getattr(row, "is_refund", False):
            flag_totals["refunds"] += amount

        merchant = (getattr(row, "merchant", None) or "").strip()
        counterparty = getattr(row, "counterparty_type", "") or ""
        if merchant and counterparty == "merchant" and is_debit:
            merchant_amount[merchant] += amount
            merchant_count[merchant] += 1

        segment_id = getattr(row, "segment_id", None)
        if segment_id is None:
            unsegmented += 1
            continue

        entry = segments.setdefault(
            int(segment_id),
            {
                "segment_id": int(segment_id),
                "name": getattr(row, "segment_name", None) or "unnamed",
                "size": 0,
                "money_out": 0.0,
                "merchants": Counter(),
                "late_night": 0,
                "with_time": 0,
                "transfers": 0,
                "emi": 0,
            },
        )
        entry["size"] += 1
        if is_debit:
            entry["money_out"] += amount
            if merchant and counterparty == "merchant":
                entry["merchants"][merchant] += amount
        hour = _hour(getattr(row, "time", None))
        if hour is not None:
            entry["with_time"] += 1
            if hour in LATE_NIGHT:
                entry["late_night"] += 1
        if getattr(row, "is_transfer", False):
            entry["transfers"] += 1
        if getattr(row, "emi_flag", False):
            entry["emi"] += 1

    months = sorted(set(month_out) | set(month_in))
    brief.months_covered = len([m for m in months if m != "unknown"])
    brief.monthly = [
        {
            "month": month,
            "out": round(month_out[month], 2),
            "in": round(month_in[month], 2),
        }
        for month in months
        if month != "unknown"
    ]
    known_months = [m for m in months if m != "unknown"]
    if known_months:
        brief.last_month = known_months[-1]
        brief.last_month_out = month_out[known_months[-1]]
        if len(known_months) >= 2:
            brief.prior_month_out = month_out[known_months[-2]]

    brief.flags = {k: round(v, 2) for k, v in flag_totals.items() if v}
    brief.unsegmented_rows = unsegmented
    brief.segmented = bool(segments)
    total_out = brief.money_out or 1.0
    ranked = sorted(segments.values(), key=lambda s: -s["money_out"])
    brief.segments = [
        {
            "segment_id": s["segment_id"],
            "name": s["name"],
            "size": s["size"],
            "money_out": round(s["money_out"], 2),
            "share_of_out": round(s["money_out"] / total_out, 4),
            "late_night_share": round(s["late_night"] / s["with_time"], 4)
            if s["with_time"]
            else 0.0,
            "transfer_share": round(s["transfers"] / s["size"], 4) if s["size"] else 0.0,
            "emi_share": round(s["emi"] / s["size"], 4) if s["size"] else 0.0,
            "merchants": [
                {"name": name, "amount": round(amount, 2)}
                for name, amount in s["merchants"].most_common(TOP_N)
            ],
        }
        for s in ranked
    ]
    brief.top_merchants = [
        {
            "name": name,
            "amount": round(merchant_amount[name], 2),
            "count": merchant_count[name],
        }
        for name, _ in merchant_amount.most_common(TOP_N)
    ]

    unpaid = []
    for bill in obligations:
        status = getattr(bill, "status", "") or ""
        if status in ("due", "overdue"):
            unpaid.append(
                {
                    "biller": getattr(bill, "biller", "") or "unknown biller",
                    "amount": round(_money(getattr(bill, "amount_due", 0)), 2),
                    "status": status,
                    "due_date": getattr(bill, "due_date", None).isoformat()
                    if getattr(bill, "due_date", None)
                    else None,
                }
            )
    brief.unpaid_bills = unpaid
    return brief
