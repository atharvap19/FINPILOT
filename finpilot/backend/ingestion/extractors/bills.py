"""Bills and invoices, read by code.

A utility or telecom bill is a labelled document -- ``Bill Date``, ``Due Date``,
``Consumer Number``, ``Total Amount Payable`` -- which makes it a parsing job,
not a reading-comprehension job. Every field below is found by looking for its
label and taking what follows, the same way :mod:`ingestion.detect` reads a
statement's metadata block.

That is the whole reason bills arrive as text PDFs rather than photographs:
a PDF with a text layer can be parsed deterministically, and the ledger stays
reproducible. A scanned bill would need a vision model, and this pipeline does
not use one -- an image is reported as unreadable instead of guessed at.

What is deliberately *not* extracted is whether the bill has been paid. That is
a fact about the ledger, not about the document, and it is worked out in
:mod:`ingestion.link` by looking for a matching debit.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from ingestion.normalize import (
    clean_text,
    mask_tail,
    parse_amount,
    parse_date,
    quantize_money,
)

# --------------------------------------------------------------------------
# the extracted shape
# --------------------------------------------------------------------------


class BillFields(BaseModel):
    """One bill, as printed on the document."""

    model_config = ConfigDict(extra="ignore")

    biller: str | None = None
    category_hint: str | None = None
    amount_due: Decimal | None = None
    bill_date: dt.date | None = None
    due_date: dt.date | None = None
    billing_period: str | None = None
    consumer_no: str | None = None
    account_holder: str | None = None
    confidence: float = 1.0

    @field_validator("amount_due", mode="before")
    @classmethod
    def _money(cls, v):
        if v in (None, ""):
            return None
        if isinstance(v, (int, float, Decimal)):
            return quantize_money(v)
        parsed = parse_amount(v)
        return None if parsed is None else abs(parsed)

    @field_validator("bill_date", "due_date", mode="before")
    @classmethod
    def _date(cls, v):
        if v in (None, ""):
            return None
        return v if isinstance(v, dt.date) else parse_date(v)

    @field_validator("consumer_no")
    @classmethod
    def _mask_consumer(cls, v):
        """A consumer number is an account number, so it is masked on the way
        in and never stored in full (principle 6)."""
        if not v:
            return None
        cleaned = re.sub(r"\s+", "", str(v))
        return mask_tail(cleaned) if len(cleaned) > 4 else cleaned

    @field_validator("biller", "billing_period", "category_hint", "account_holder")
    @classmethod
    def _text(cls, v):
        return (clean_text(v) or None) if v else None


@dataclass
class BillRecord:
    """An extracted bill, ready for :mod:`ingestion.link` to match to a payment."""

    fields: BillFields
    doc_id: str
    source_ref: str
    extraction_method: str = "pdfplumber"
    issues: list[str] = field(default_factory=list)

    @property
    def biller(self) -> str:
        return self.fields.biller or "Unknown biller"

    @property
    def amount_due(self) -> Decimal | None:
        return self.fields.amount_due


# --------------------------------------------------------------------------
# labels
# --------------------------------------------------------------------------

#: What each field is called on an Indian bill. First match wins, so the more
#: specific label is listed first.
_LABELS: dict[str, tuple[str, ...]] = {
    "amount_due": (
        "total amount payable", "total amount due", "amount payable", "net payable",
        "bill amount", "total amount", "total due", "amount due", "grand total",
    ),
    "bill_date": ("bill date", "invoice date", "statement date", "date of issue",
                  "bill issue date"),
    "due_date": ("due date", "payment due date", "pay by", "last date of payment",
                 "due on"),
    "consumer_no": ("consumer number", "consumer no", "consumer id", "customer number",
                    "customer id", "account number", "account no", "ca number",
                    "connection number", "service number", "subscriber id",
                    "relationship number"),
    "billing_period": ("billing period", "bill period", "period", "billing cycle",
                       "for the period"),
    "account_holder": ("customer name", "consumer name", "name", "billed to"),
}

#: Biller -> what kind of bill it is. Used as a hint only; the categoriser is a
#: later stage's job.
_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("electricity", ("power", "electricity", "electric", "bescom", "msedcl", "tneb",
                     "kseb", "discom", "vidyut", "energy")),
    ("mobile", ("airtel", "jio", "vodafone", "vi ", "bsnl postpaid", "postpaid",
                "mobile")),
    ("broadband", ("fibernet", "broadband", "fiber", "internet", "act ", "hathway",
                   "excitel")),
    ("gas", ("gas", "indane", "hp gas", "bharat gas", "mahanagar")),
    ("water", ("water", "jal board", "jal nigam")),
    ("dth", ("dth", "tata play", "dish tv", "d2h")),
    ("insurance", ("insurance", "premium", "policy")),
)

#: Lines that are never the biller's name, however near the top they sit.
_NOT_A_BILLER = re.compile(
    r"^(tax invoice|invoice|bill|statement|gstin|cin|pan|page \d|original|duplicate)\b",
    re.IGNORECASE,
)

_MONEY = r"(?:rs\.?|inr|₹)?\s*([\d,]+\.\d{2}|[\d,]+)"


def _labelled(label: str) -> re.Pattern[str]:
    """``Due Date   :  20-07-2025`` -> ``20-07-2025``.

    The separator is optional because bills are laid out in columns as often as
    in ``label: value`` pairs, and pdfplumber flattens both to one line.
    """
    return re.compile(
        rf"\b{re.escape(label)}\b\s*[:\-]?\s*([^\n]{{1,60}})", re.IGNORECASE
    )


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def parse_bill_text(text: str, *, filename: str = "") -> BillFields:
    """Read a bill from the text of its first page.

    Every value is collected into a dict and the model is constructed once, at
    the end. That matters: assigning to a pydantic field after construction
    does not re-run the validators, so building the object up field by field
    would skip the date parsing, the money parsing and -- the one that counts --
    the masking of the consumer number.
    """
    cleaned = clean_text(text)
    if not cleaned:
        return BillFields(confidence=0.0)

    found: dict[str, str] = {}
    for name, labels in _LABELS.items():
        for label in labels:
            match = _labelled(label).search(cleaned)
            if not match:
                continue
            value = _first_value(name, match.group(1))
            if value:
                found[name] = value
                break

    biller = _find_biller(cleaned, filename)
    return BillFields(
        **found,
        biller=biller,
        category_hint=_category_for(f"{biller or ''} {cleaned[:400]}"),
        # a bill with no amount is not a bill anything can be done with
        confidence=1.0 if found.get("amount_due") else 0.3,
    )


def _first_value(name: str, raw: str) -> str | None:
    """Take just the value off the rest of the line."""
    text = clean_text(raw)
    if not text:
        return None
    if name == "amount_due":
        match = re.search(_MONEY, text, re.IGNORECASE)
        return match.group(1) if match else None
    if name in ("bill_date", "due_date"):
        match = re.search(
            r"\d{1,2}[-/\s][A-Za-z0-9]{2,9}[-/\s]\d{2,4}|\d{4}-\d{2}-\d{2}", text
        )
        return match.group(0) if match else None
    if name == "consumer_no":
        match = re.search(r"[A-Z0-9][A-Z0-9\-/]{4,24}", text, re.IGNORECASE)
        return match.group(0) if match else None
    # billing period and names run to the end of the label's line, but stop
    # before the next label starts
    return re.split(r"\s{3,}|\s(?=[A-Z][a-z]+ (?:Date|Number|Name)\b)", text)[0].strip()


def _find_biller(text: str, filename: str) -> str | None:
    """The biller is the first real line of the document.

    Bills put the company name at the top, above everything else. Falling back
    to the filename is deliberate: ``bill_tatapower_jul2025.pdf`` says who it is
    from even when the header is a logo rather than text.
    """
    for line in text.splitlines():
        candidate = clean_text(line)
        if len(candidate) < 3 or _NOT_A_BILLER.match(candidate):
            continue
        if re.fullmatch(r"[\d\W]+", candidate):
            continue
        if ":" in candidate and len(candidate.split(":")[0]) < 24:
            continue  # this is a label line, not a masthead
        return _title(candidate[:60])

    stem = Path(filename).stem if filename else ""
    slug = re.sub(r"(bill|invoice|statement)[_\-\s]*", "", stem, flags=re.IGNORECASE)
    slug = re.sub(r"[_\-]+\d{4,}.*$", "", slug)
    slug = re.sub(r"[_\-]+", " ", slug).strip()
    return _title(slug) if len(slug) > 2 else None


def _title(text: str) -> str:
    from ingestion.merchants import smart_title

    return smart_title(text)


def _category_for(text: str) -> str | None:
    lowered = text.lower()
    for category, markers in _CATEGORY_HINTS:
        if any(marker in lowered for marker in markers):
            return category
    return None


def extract_bill_pdf(
    path: Path, *, filename: str | None = None, password: str | None = None
) -> tuple[BillFields, list[str]]:
    """Read a bill from a text-layer PDF. Returns ``(fields, issues)``."""
    import pdfplumber

    name = filename or path.name
    issues: list[str] = []
    try:
        with pdfplumber.open(path, password=password or "") as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages[:2])
    except Exception as exc:  # noqa: BLE001
        return BillFields(confidence=0.0), [f"could not read the PDF: {exc}"]

    if len(text.strip()) < 40:
        return (
            BillFields(confidence=0.0),
            ["this PDF has no text layer, so the bill could not be read"],
        )

    fields = parse_bill_text(text, filename=name)
    if fields.amount_due is None:
        issues.append("no amount payable was found on this bill")
    problem = check_bill_dates(fields)
    if problem:
        issues.append(problem)
    return fields, issues


def extract_bill_table_pdf(
    path: Path, *, filename: str | None = None, password: str | None = None
) -> tuple[list[BillFields], list[str]]:
    """Extract one obligation per row from a bills-payable PDF table."""
    import pdfplumber

    fields: list[BillFields] = []
    issues: list[str] = []
    try:
        with pdfplumber.open(path, password=password or "") as pdf:
            for page in pdf.pages:
                for raw_table in page.extract_tables() or []:
                    rows = [[clean_text(cell) for cell in row] for row in raw_table if row]
                    if len(rows) < 2:
                        continue
                    header = [cell.lower() for cell in rows[0]]
                    if not {"biller", "due date", "amount"}.issubset(header):
                        continue
                    positions = {name: header.index(name) for name in ("biller", "due date", "amount")}
                    issue_index = header.index("issue date") if "issue date" in header else None
                    type_index = header.index("bill type") if "bill type" in header else None
                    for row in rows[1:]:
                        if len(row) <= max(positions.values()):
                            continue
                        biller = row[positions["biller"]]
                        amount = row[positions["amount"]]
                        if not biller or parse_amount(amount) is None:
                            continue
                        fields.append(BillFields(
                            biller=biller,
                            category_hint=row[type_index] if type_index is not None else None,
                            amount_due=amount,
                            bill_date=row[issue_index] if issue_index is not None else None,
                            due_date=row[positions["due date"]],
                        ))
            
    except Exception as exc:  # noqa: BLE001
        return [], [f"could not read the bill table: {exc}"]
    if not fields:
        issues.append("no bills payable table found in the PDF")
    return fields, issues


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def check_bill_dates(fields: BillFields) -> str | None:
    if fields.bill_date and fields.due_date and fields.due_date < fields.bill_date:
        return f"due date {fields.due_date} is before the bill date {fields.bill_date}"
    return None


# --------------------------------------------------------------------------
# bills -> obligations
# --------------------------------------------------------------------------


def bill_to_obligation(record: BillRecord, *, as_of: dt.date):
    """Turn an extracted bill into an :class:`~ingestion.schema.Obligation`.

    ``status`` starts at ``due`` or ``overdue`` judged against ``as_of`` -- the
    latest date the uploaded documents cover, not today's date. A bill can only
    be known unpaid up to the evidence available; linking upgrades it to
    ``paid`` if a matching debit turns up.
    """
    from ingestion.schema import Obligation, ObligationStatus, make_bill_id

    fields = record.fields
    amount = fields.amount_due or Decimal("0.00")
    status = ObligationStatus.DUE.value
    if fields.due_date and fields.due_date < as_of:
        status = ObligationStatus.OVERDUE.value
    return Obligation(
        bill_id=make_bill_id(
            biller=record.biller,
            consumer_no=fields.consumer_no,
            bill_date=fields.bill_date,
            amount_due=amount,
        ),
        biller=record.biller,
        category_hint=fields.category_hint,
        amount_due=amount,
        bill_date=fields.bill_date,
        due_date=fields.due_date,
        billing_period=fields.billing_period,
        consumer_no=fields.consumer_no,
        status=status,
        source_ref=record.source_ref,
    )
