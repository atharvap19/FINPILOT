"""Advice from a ledger brief. No network, no database."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.advice.brief import build_brief
from app.advice.gemini import _parse
from app.advice.rules import recommend


def _row(**kwargs):
    defaults = dict(
        date=dt.date(2025, 7, 10),
        amount=500,
        direction="debit",
        merchant="Swiggy",
        counterparty_type="merchant",
        time=dt.time(23, 15),
        segment_id=0,
        segment_name="late-night food delivery",
        is_transfer=False,
        is_fee=False,
        emi_flag=False,
        is_cash_withdrawal=False,
        is_refund=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_brief_keeps_merchants_and_drops_people():
    rows = [
        _row(),
        _row(
            amount=20000,
            merchant="Amit",
            counterparty_type="person",
            segment_id=1,
            segment_name="payments to individuals",
            time=None,
        ),
        _row(
            date=dt.date(2025, 8, 2),
            amount=800,
            merchant="Swiggy",
            segment_id=0,
            time=dt.time(23, 40),
        ),
    ]
    brief = build_brief(documents=[], rows=rows, obligations=[])
    assert brief.segmented
    assert brief.money_out == 21300
    names = {m["name"] for m in brief.top_merchants}
    assert "Swiggy" in names
    assert "Amit" not in names
    food = next(s for s in brief.segments if s["segment_id"] == 0)
    assert food["late_night_share"] == 1.0
    assert food["merchants"][0]["name"] == "Swiggy"


def test_rules_answer_spend_less_with_a_cut_on_the_habit():
    brief = build_brief(
        documents=[SimpleNamespace(document_type="bank_statement", issuer="HDFC")],
        rows=[
            _row(amount=4000),
            _row(date=dt.date(2025, 8, 4), amount=4000),
            _row(
                amount=15000,
                merchant=None,
                counterparty_type="self",
                segment_id=2,
                segment_name="loan EMI payments",
                emi_flag=True,
                time=None,
            ),
        ],
        obligations=[],
    )
    result = recommend(brief, "how do I spend less money next month")
    assert result["recommendations"]
    titles = " ".join(r["title"] for r in result["recommendations"]).lower()
    assert "food" in titles or "trim" in titles
    # EMI is fixed; the cut should not be aimed at it as the first discretionary target
    first = result["recommendations"][0]
    assert first["segment_name"] != "loan EMI payments"
    assert first["monthly_saving_rupees"] is not None
    assert "4000" in result["answer"] or "8,000" in result["answer"] or "₹" in result["answer"]


def test_model_json_is_parsed_even_inside_fences():
    raw = """```json
{"answer": "Cut delivery.", "recommendations": [
  {"title": "Skip late Swiggy", "detail": "23:00 orders", "monthly_saving_rupees": 500,
   "segment_id": 0, "segment_name": "late-night food delivery"}
]}
```"""
    parsed = _parse(raw)
    assert parsed is not None
    assert parsed["answer"] == "Cut delivery."
    assert parsed["recommendations"][0]["monthly_saving_rupees"] == 500.0
