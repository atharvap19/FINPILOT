"""Expenses typed by hand: "lunch 250 cash, petrol 500 upi, yesterday chai 40".

Not everything leaves a statement behind -- cash, a friend paid back, today's
spending that no bank export covers yet. These are typed into a chat-style
box and land in the same ledger as everything else, so the dashboard, the
budget, the exports and the agent all see them.

Parsing is plain code (no model call): each comma- or line-separated piece
needs an amount; the rest is the label, minus words that say how ("cash",
"upi", "card") and when ("today", "yesterday", "kal") it was paid. Words like
"salary", "received" or "refund" mark money coming in.

Typed entries hang off one "Typed expenses" document per upload, linked like
any other source -- so deleting the upload, the account, or letting the upload
expire removes them with everything else.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, Job, TransactionRow, TxnLinkRow
from ingestion.schema import make_doc_id

MANUAL_FILENAME = "Typed expenses"
MANUAL_TYPE = "manual"
MANUAL_ACCOUNT = "TYPED"

_MODES = {
    "cash": "CASH",
    "upi": "UPI", "gpay": "UPI", "googlepay": "UPI", "phonepe": "UPI", "paytm": "UPI", "bhim": "UPI",
    "card": "CARD", "debit": "CARD", "credit": "CARD", "creditcard": "CARD",
    "netbanking": "NEFT", "neft": "NEFT", "imps": "IMPS", "transfer": "IMPS",
}
_INCOME = {"salary", "received", "receive", "got", "refund", "income", "credited", "cashback", "earned"}
#: day offsets. "kal" is both yesterday and tomorrow in Hindi; when logging
#: what was spent, it is yesterday.
_DAYS = {"today": 0, "aaj": 0, "yesterday": -1, "kal": -1, "parso": -2}
_FILLER = {"for", "on", "at", "via", "by", "paid", "spent", "spend", "rs", "inr", "rupees", "of", "to", "a", "the"}


@dataclass
class Entry:
    label: str
    amount: float
    date: dt.date
    mode: str
    direction: str  # "debit" or "credit"


def parse_expenses(text: str, today: dt.date | None = None) -> tuple[list[Entry], list[str]]:
    """Text -> entries, plus the pieces that had no amount (to tell the user)."""
    today = today or dt.date.today()
    entries: list[Entry] = []
    skipped: list[str] = []
    # "day before yesterday" first, so "yesterday" inside it doesn't win
    text = re.sub(r"day before yesterday", "parso", text or "", flags=re.IGNORECASE)
    for chunk in re.split(r"(?:,(?!\d)|[\n;]|\band\b)+", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        # take a typed date out first, so "12/10 movie 450" doesn't read as ₹12
        date = today
        explicit = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", chunk)
        if explicit:
            day, month, year = int(explicit.group(1)), int(explicit.group(2)), explicit.group(3)
            year_n = int(year) + (2000 if year and len(year) == 2 else 0) if year else today.year
            try:
                date = dt.date(year_n, month, day)
            except ValueError:
                pass
            chunk = (chunk[: explicit.start()] + " " + chunk[explicit.end():]).strip()
        amount_match = re.search(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:k\b)?", chunk, re.IGNORECASE)
        if not amount_match:
            skipped.append(chunk)
            continue
        amount = float(amount_match.group(1).replace(",", ""))
        if re.search(r"\d\s*k\b", amount_match.group(0), re.IGNORECASE):
            amount *= 1000
        if amount <= 0:
            skipped.append(chunk)
            continue
        rest = (chunk[: amount_match.start()] + " " + chunk[amount_match.end():]).lower()
        words = re.findall(r"[a-z]+", rest)
        mode = "OTHER"
        direction = "debit"
        label_words = []
        for word in words:
            if word in _DAYS:
                date = today + dt.timedelta(days=_DAYS[word])
            elif word in _MODES:
                mode = _MODES[word]
            elif word in _INCOME:
                direction = "credit"
                if word in ("salary", "refund", "cashback", "income"):
                    label_words.append(word)
            elif word not in _FILLER:
                label_words.append(word)
        label = " ".join(label_words).strip().title() or ("Income" if direction == "credit" else "Expense")
        if date > today:
            date = today  # nothing typed here has happened in the future
        entries.append(Entry(label=label[:60], amount=round(amount, 2), date=date, mode=mode, direction=direction))
    return entries, skipped


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


def manual_document(session: Session, job_id: str) -> Document:
    """The upload's "Typed expenses" document, created on first use."""
    doc_id = make_doc_id("manual-entries", job_id)
    document = session.get(Document, doc_id)
    if document is None:
        document = Document(
            doc_id=doc_id,
            job_id=job_id,
            filename=MANUAL_FILENAME,
            file_hash="manual",
            file_type="text/plain",
            document_type=MANUAL_TYPE,
            account=MANUAL_ACCOUNT,
            extraction_method="manual",
            status="processed",
        )
        session.add(document)
        session.flush()
    return document


def add_entries(session: Session, job_id: str, entries: list[Entry]) -> list[dict[str, Any]]:
    document = manual_document(session, job_id)
    out = []
    for entry in entries:
        txn_id = "t_" + uuid.uuid4().hex[:20]
        ref = f"{MANUAL_FILENAME}#{txn_id}"
        session.add(
            TransactionRow(
                txn_id=txn_id,
                date=entry.date,
                amount=Decimal(str(entry.amount)),
                direction=entry.direction,
                account=MANUAL_ACCOUNT,
                account_type="cash" if entry.mode == "CASH" else "other",
                source_type=MANUAL_TYPE,
                source_ref=ref,
                raw_description=entry.label,
                merchant=entry.label,
                counterparty_type="merchant" if entry.direction == "debit" else "unknown",
                payment_mode=entry.mode,
                linked_sources=ref,
                extraction_method="manual",
                extraction_confidence=1.0,
            )
        )
        session.add(TxnLinkRow(txn_id=txn_id, source_ref=ref, link_type="primary", doc_id=document.doc_id))
        out.append(_view(txn_id, entry.date, entry.amount, entry.direction, entry.label, entry.mode))
    document.row_count = (document.row_count or 0) + len(entries)
    return out


def _view(txn_id: str, date: dt.date, amount: float, direction: str, label: str, mode: str) -> dict[str, Any]:
    return {"txn_id": txn_id, "date": date.isoformat(), "amount": float(amount),
            "direction": direction, "label": label, "mode": mode}


def entries_on(session: Session, job_id: str, day: dt.date) -> list[dict[str, Any]]:
    doc_id = make_doc_id("manual-entries", job_id)
    rows = session.scalars(
        select(TransactionRow)
        .join(TxnLinkRow, TxnLinkRow.txn_id == TransactionRow.txn_id)
        .where(TxnLinkRow.doc_id == doc_id, TransactionRow.date == day)
        .order_by(TransactionRow.txn_id)
    )
    return [_view(r.txn_id, r.date, float(r.amount), r.direction, r.merchant or r.raw_description, r.payment_mode)
            for r in rows]


def delete_entry(session: Session, job_id: str, txn_id: str) -> bool:
    """Undo one typed entry. Only typed ones -- never a statement row."""
    doc_id = make_doc_id("manual-entries", job_id)
    link = session.scalar(select(TxnLinkRow).where(TxnLinkRow.txn_id == txn_id, TxnLinkRow.doc_id == doc_id))
    if link is None:
        return False
    session.delete(link)
    row = session.get(TransactionRow, txn_id)
    if row is not None and row.source_type == MANUAL_TYPE:
        session.delete(row)
    document = session.get(Document, doc_id)
    if document is not None and document.row_count:
        document.row_count -= 1
    return True


def create_manual_ledger(session: Session, user_id: str) -> str:
    """An upload with no files: somewhere to type expenses before (or instead
    of) uploading statements."""
    from app.retention import first_expiry

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    session.add(Job(job_id=job_id, user_id=user_id, status="done", stage="done", progress=1.0,
                    finished_at=now, expires_at=first_expiry(now), report={"totals": {}}))
    session.flush()
    manual_document(session, job_id)
    return job_id
