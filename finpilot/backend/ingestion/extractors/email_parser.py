"""Emails: order confirmations, bank alerts, bill notices.

Code opens the ``.eml``/``.mbox``, reads the headers, throws away the
irrelevant mail, flattens the HTML body to text, and then reads the payment
fields out of it with :class:`RuleEmailExtractor`.

The relevance filter earns its place before anything else: an inbox is mostly
newsletters and OTPs, and classifying by sender and subject first means the
parser only ever sees mail that is actually about money.

Whatever is extracted goes through the same
:class:`~ingestion.schema.Transaction` gate as a CSV row -- one schema, one
validation path (principle 3) -- and the arithmetic is checked separately in
:func:`check_order_arithmetic` rather than trusted.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import mailbox
import re
from dataclasses import dataclass, field
from decimal import Decimal
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ingestion.normalize import (
    clean_text,
    mask_text,
    parse_amount,
    parse_date,
    parse_time,
    quantize_money,
)
from ingestion.schema import Direction, PaymentMode

# --------------------------------------------------------------------------
# what code gets on its own
# --------------------------------------------------------------------------


@dataclass
class ParsedEmail:
    """Headers plus a flattened body. No interpretation yet."""

    source_ref: str
    message_id: str
    sender: str
    sender_domain: str
    subject: str
    sent_at: dt.datetime | None
    text: str
    kind: str = "unknown"  # order | alert | receipt | bill | unknown

    @property
    def sent_date(self) -> dt.date | None:
        return self.sent_at.date() if self.sent_at else None

    @property
    def sent_time(self) -> dt.time | None:
        return self.sent_at.time().replace(microsecond=0) if self.sent_at else None


# --------------------------------------------------------------------------
# relevance
# --------------------------------------------------------------------------

#: Subjects that are never about money, checked first so that a promotional
#: mail from a bank's own domain is still rejected.
_REJECT_SUBJECT = re.compile(
    r"\b(otp|one[- ]time password|verification code|newsletter|unsubscribe|"
    r"profile views?|weekly deals|deals? roundup|briefing|webinar|survey|"
    r"password reset|welcome to|terms (and|&) conditions|privacy policy)\b",
    re.IGNORECASE,
)

_ORDER_SUBJECT = re.compile(
    r"\b(order|confirmed|confirmation|shipped|delivered|your .* order)\b", re.IGNORECASE
)
_ALERT_SUBJECT = re.compile(
    r"\b(debited|credited|transaction alert|update on your|txn alert|"
    r"account (alert|update)|upi (alert|transaction))\b",
    re.IGNORECASE,
)
_RECEIPT_SUBJECT = re.compile(
    r"\b(receipt|invoice|payment (receipt|confirmation|successful)|"
    r"subscription|renewed|charged)\b",
    re.IGNORECASE,
)
_BILL_SUBJECT = re.compile(
    r"\b(bill|statement|due|dues|outstanding|reminder)\b", re.IGNORECASE
)
_REFUND_SUBJECT = re.compile(r"\b(refund|returned|cancell?ed)\b", re.IGNORECASE)


def classify_email(sender: str, subject: str, text: str = "") -> str:
    """``order`` / ``alert`` / ``receipt`` / ``bill`` / ``unknown``.

    ``unknown`` means "not about a transaction" and the mail is dropped -- the
    model is never shown it, which is most of the cost saving.
    """
    subject = clean_text(subject)
    if _REJECT_SUBJECT.search(subject):
        return "unknown"
    if _REFUND_SUBJECT.search(subject):
        return "order"
    if _ORDER_SUBJECT.search(subject):
        return "order"
    if _ALERT_SUBJECT.search(subject):
        return "alert"
    if _RECEIPT_SUBJECT.search(subject):
        return "receipt"
    if _BILL_SUBJECT.search(subject):
        return "bill"
    # a body that states a rupee amount and a reference is worth a look even
    # when the subject says nothing useful
    if re.search(r"(rs\.?|inr|₹)\s*[\d,]+\.\d{2}", text, re.IGNORECASE) and re.search(
        r"\b(upi ref|utr|order id|txn|transaction id)\b", text, re.IGNORECASE
    ):
        return "alert"
    return "unknown"


def is_relevant(parsed: ParsedEmail) -> bool:
    return parsed.kind != "unknown"


# --------------------------------------------------------------------------
# reading the mail
# --------------------------------------------------------------------------


def _body_text(message: EmailMessage) -> str:
    """Prefer the HTML part, flattened; fall back to text/plain."""
    import html2text

    html_part = None
    text_part = None
    try:
        html_part = message.get_body(preferencelist=("html",))
        text_part = message.get_body(preferencelist=("plain",))
    except Exception:  # noqa: BLE001 - malformed MIME is common in the wild
        pass

    if html_part is not None:
        try:
            converter = html2text.HTML2Text()
            converter.body_width = 0
            converter.ignore_links = True
            converter.ignore_images = True
            return clean_text(converter.handle(html_part.get_content()))
        except Exception:  # noqa: BLE001
            pass
    if text_part is not None:
        try:
            return clean_text(text_part.get_content())
        except Exception:  # noqa: BLE001
            pass
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        return clean_text(payload.decode("utf-8", errors="replace"))
    return clean_text(str(message.get_payload()))


def parse_message(message: EmailMessage, source_ref: str) -> ParsedEmail:
    sender = clean_text(message.get("From", ""))
    subject = clean_text(message.get("Subject", ""))
    address = email.utils.parseaddr(sender)[1]
    domain = address.split("@")[-1].lower() if "@" in address else ""

    sent_at: dt.datetime | None = None
    raw_date = message.get("Date")
    if raw_date:
        try:
            sent_at = email.utils.parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            sent_at = None

    text = _body_text(message)
    return ParsedEmail(
        source_ref=source_ref,
        message_id=clean_text(message.get("Message-ID", "")),
        sender=address or sender,
        sender_domain=domain,
        subject=subject,
        sent_at=sent_at,
        text=text,
        kind=classify_email(address or sender, subject, text),
    )


def parse_eml(path: Path, *, filename: str | None = None) -> ParsedEmail:
    message = message_from_bytes(path.read_bytes(), policy=policy.default)
    name = filename or path.name
    return parse_message(message, f"{name}#1")


def parse_mbox(path: Path, *, filename: str | None = None) -> list[ParsedEmail]:
    name = filename or path.name
    out: list[ParsedEmail] = []
    box = mailbox.mbox(str(path), factory=None)
    try:
        for index, raw in enumerate(box, start=1):
            message = message_from_bytes(raw.as_bytes(), policy=policy.default)
            out.append(parse_message(message, f"{name}#{index}"))
    finally:
        box.close()
    return out


def parse_email_file(path: Path, file_type: str, *, filename: str | None = None
                     ) -> list[ParsedEmail]:
    from ingestion.detect import MBOX

    if file_type == MBOX or path.suffix.lower() == ".mbox":
        return parse_mbox(path, filename=filename)
    return [parse_eml(path, filename=filename)]


# --------------------------------------------------------------------------
# the extracted fields (the schema both extractors must satisfy)
# --------------------------------------------------------------------------


class EmailItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class EmailFields(BaseModel):
    """Fields read off one email, validated the same way every other source is."""

    model_config = ConfigDict(extra="ignore")

    merchant: str | None = None
    amount: Decimal | None = None
    currency: str = "INR"
    direction: str | None = None
    occurred_on: dt.date | None = None
    occurred_at: dt.time | None = None
    reference_no: str | None = None
    order_id: str | None = None
    account_last4: str | None = None
    payment_mode: str | None = None
    items: list[EmailItem] = Field(default_factory=list)
    item_total: Decimal | None = None
    gst_amount: Decimal | None = None
    discount: Decimal | None = None
    delivery_fee: Decimal | None = None
    is_refund: bool = False
    confidence: float = 0.9

    @field_validator("amount", "item_total", "gst_amount", "discount", "delivery_fee",
                     mode="before")
    @classmethod
    def _money(cls, v):
        """Tolerant on the way in.

        An extractor -- a model especially -- may hand back ``"Rs. 1,234.57"``
        or ``"1,234.57"`` rather than a bare number, so the same parser the CSV
        path uses is applied here. Anything genuinely unreadable becomes
        ``None`` and the row is dropped, rather than raising.
        """
        if v is None or v == "":
            return None
        if isinstance(v, (int, float, Decimal)):
            return quantize_money(v)
        parsed = parse_amount(v)
        return None if parsed is None else abs(parsed)

    @field_validator("account_last4")
    @classmethod
    def _last4(cls, v):
        if not v:
            return None
        digits = re.sub(r"[^0-9]", "", str(v))
        return digits[-4:] if len(digits) >= 4 else None

    @field_validator("direction")
    @classmethod
    def _direction(cls, v):
        if not v:
            return None
        v = str(v).lower()
        return v if v in {d.value for d in Direction} else None


class EmailExtractor(Protocol):
    """Anything that can turn parsed emails into fields."""

    name: str

    def extract(self, emails: list[ParsedEmail]) -> list[EmailFields | None]:
        ...


# --------------------------------------------------------------------------
# the rule-based extractor
# --------------------------------------------------------------------------

_AMOUNT = r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{2})?)"


def _labelled(label: str) -> re.Pattern[str]:
    """``Discount | Rs. -50.00`` -> ``50.00``.

    ``[^\\d\\n]`` swallows whatever sits between the label and the number --
    a pipe from the flattened table, a currency symbol, a minus sign -- while
    the newline exclusion stops a label matching a number on the next line.
    The sign is dropped on purpose: every money field in the schema is
    positive, and what a discount means is already in the column name.
    """
    return re.compile(rf"\b{label}\b[^\d\n]{{0,24}}([\d,]+\.\d{{2}})", re.IGNORECASE)


#: Specific labels first. A bare "total" would otherwise match the "total" inside
#: "Item total", which sits above the grand total in every order receipt.
_GRAND_TOTAL = _labelled(
    r"(?:grand total|total paid|amount paid|order total|net payable|total amount)"
)
_LOOSE_TOTAL = re.compile(
    r"(?<!item )(?<!sub)\btotal\b[^\d\n]{0,24}([\d,]+\.\d{2})", re.IGNORECASE
)
_ITEM_TOTAL = _labelled(r"item total")
_GST = _labelled(r"gst(?:\s*\(included\))?")
_DISCOUNT = _labelled(r"discount")
_DELIVERY = _labelled(r"(?:delivery|shipping)(?:\s*(?:fee|charges?))?")
_REFUND_AMOUNT = _labelled(r"refund amount")
_CHARGED = _labelled(r"(?:amount charged|charged)")
_ALERT_AMOUNT = re.compile(
    rf"{_AMOUNT}\s+(?:has been\s+)?(?:is\s+)?(?:been\s+)?(debited|credited)", re.IGNORECASE
)

_ORDER_ID = re.compile(r"\border\s*(?:id|no\.?|number)\s*[:#]?\s*\*{0,2}([A-Z0-9][A-Z0-9\-]{4,30})\*{0,2}", re.IGNORECASE)
_UPI_REF = re.compile(r"\b(?:upi\s*ref(?:erence)?(?:\s*no\.?)?|utr|rrn)\s*[:#]?\s*([A-Z0-9]{6,24})\b", re.IGNORECASE)
_GENERIC_REF = re.compile(r"\breference\s*[:#]?\s*([A-Z0-9]{6,24})\b", re.IGNORECASE)
_ACCOUNT_LAST4 = re.compile(r"\b(?:a/c|account|card|ending)\s*(?:no\.?|number)?\s*[:#]?\s*[X\*x]*(\d{4})\b", re.IGNORECASE)
_VPA_IN_TEXT = re.compile(r"\bvpa\s+([A-Za-z0-9][A-Za-z0-9._\-]{1,63}@[A-Za-z][A-Za-z0-9]{1,24})", re.IGNORECASE)
_PLACED_AT = re.compile(r"\bplaced on\s+(.+?)(?:\n|$)", re.IGNORECASE)
_ON_DATE = re.compile(r"\bon\s+(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})", re.IGNORECASE)
_AT_TIME = re.compile(r"\bat\s+(\d{1,2}[:.]\d{2}(?:\s*[AaPp]\.?[Mm]\.?)?)")
_ITEM_ROW = re.compile(rf"^\s*([^|\n]{{2,80}}?)\s*\|\s*(\d+)\s*\|\s*{_AMOUNT}\s*$", re.IGNORECASE | re.MULTILINE)

_SKIP_ITEM_LABELS = frozenset(
    {"item", "item total", "discount", "delivery fee", "gst", "gst (included)",
     "grand total", "total", "qty", "amount", "subtotal"}
)

#: sender domain -> merchant, for the senders whose body text does not repeat
#: the brand name.
_SENDER_MERCHANTS = {
    "swiggy": "Swiggy",
    "zomato": "Zomato",
    "amazon": "Amazon",
    "myntra": "Myntra",
    "flipkart": "Flipkart",
    "netflix": "Netflix",
    "spotify": "Spotify",
    "bigbasket": "BigBasket",
    "blinkit": "Blinkit",
    "uber": "Uber",
}


@dataclass
class RuleEmailExtractor:
    """Reads the payment fields out of a flattened email body.

    Every field is found by its label -- ``Order ID``, ``UPI Ref``, ``Grand
    total`` -- which is what Indian order confirmations and bank alerts
    actually print. It reports its own confidence, so an email it clearly did
    not understand shows up as low confidence rather than as a confident
    wrong answer.
    """

    name: str = "rules"
    resolver: object | None = None

    def extract(self, emails: list[ParsedEmail]) -> list[EmailFields | None]:
        return [self.extract_one(e) for e in emails]

    def extract_one(self, parsed: ParsedEmail) -> EmailFields | None:
        text = parsed.text
        if not text:
            return None

        fields = EmailFields()
        fields.is_refund = bool(_REFUND_SUBJECT.search(parsed.subject)) or "refund" in text.lower()

        amount, direction = self._amount(text, fields.is_refund)
        if amount is None:
            return None
        fields.amount = amount
        fields.direction = direction

        fields.order_id = _first(_ORDER_ID, text)
        fields.reference_no = _first(_UPI_REF, text) or _first(_GENERIC_REF, text)
        fields.account_last4 = _first(_ACCOUNT_LAST4, text)
        fields.merchant = self._merchant(parsed, text)
        fields.payment_mode = self._payment_mode(text)

        fields.occurred_on, fields.occurred_at = self._when(parsed, text)
        fields.item_total = _money(_ITEM_TOTAL, text)
        fields.gst_amount = _money(_GST, text)
        fields.discount = _money(_DISCOUNT, text)
        fields.delivery_fee = _money(_DELIVERY, text)
        fields.items = self._items(text)
        fields.confidence = self._confidence(fields, parsed)
        return fields

    # -- pieces ----------------------------------------------------------
    def _amount(self, text: str, is_refund: bool) -> tuple[Decimal | None, str | None]:
        if is_refund:
            refund = _money(_REFUND_AMOUNT, text)
            if refund is not None:
                return refund, Direction.CREDIT.value

        alert = _ALERT_AMOUNT.search(text)
        if alert:
            amount = parse_amount(alert.group(1))
            verb = alert.group(2).lower()
            direction = (
                Direction.DEBIT.value if verb.startswith("debit") else Direction.CREDIT.value
            )
            return amount, direction

        for pattern in (_GRAND_TOTAL, _CHARGED, _LOOSE_TOTAL):
            amount = _money(pattern, text)
            if amount is not None:
                return amount, (
                    Direction.CREDIT.value if is_refund else Direction.DEBIT.value
                )
        return None, None

    def _merchant(self, parsed: ParsedEmail, text: str) -> str | None:
        for key, merchant in _SENDER_MERCHANTS.items():
            if key in parsed.sender_domain or key in parsed.sender.lower():
                return merchant
        match = re.search(r"your\s+([A-Za-z][\w.& ]{1,30}?)\s+order", text, re.IGNORECASE)
        if match:
            return clean_text(match.group(1))
        if self.resolver is not None:
            resolved = self.resolver.resolve(parsed.subject)  # type: ignore[attr-defined]
            if resolved.canonical and resolved.method in ("exact", "fuzzy"):
                return resolved.canonical
        return None

    def _payment_mode(self, text: str) -> str | None:
        lowered = text.lower()
        if "upi" in lowered:
            return PaymentMode.UPI.value
        if "credit card" in lowered or "debit card" in lowered:
            return PaymentMode.CARD.value
        if "netbanking" in lowered or "neft" in lowered:
            return PaymentMode.NEFT.value
        return None

    def _when(self, parsed: ParsedEmail, text: str) -> tuple[dt.date | None, dt.time | None]:
        """The date in the body wins over the header -- a receipt emailed the
        next morning is still yesterday's transaction."""
        date_value: dt.date | None = None
        time_value: dt.time | None = None

        placed = _PLACED_AT.search(text)
        if placed:
            phrase = placed.group(1)
            date_value = parse_date(phrase)
            time_value = parse_time(phrase)
        if date_value is None:
            on = _ON_DATE.search(text)
            if on:
                date_value = parse_date(on.group(1))
        if time_value is None:
            at = _AT_TIME.search(text)
            if at:
                time_value = parse_time(at.group(1))

        return (date_value or parsed.sent_date, time_value or parsed.sent_time)

    def _items(self, text: str) -> list[EmailItem]:
        out: list[EmailItem] = []
        for name, qty, amount in _ITEM_ROW.findall(text):
            label = clean_text(name).strip("* ")
            if not label or label.lower() in _SKIP_ITEM_LABELS:
                continue
            out.append(
                EmailItem(
                    name=label,
                    quantity=Decimal(qty),
                    amount=parse_amount(amount),
                )
            )
        return out

    def _confidence(self, fields: EmailFields, parsed: ParsedEmail) -> float:
        score = 0.55
        if fields.amount is not None:
            score += 0.15
        if fields.reference_no:
            score += 0.15
        if fields.merchant:
            score += 0.10
        if fields.order_id:
            score += 0.05
        return min(round(score, 2), 0.95)


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return clean_text(match.group(1)) if match else None


def _money(pattern: re.Pattern[str], text: str) -> Decimal | None:
    match = pattern.search(text)
    return parse_amount(match.group(1)) if match else None


# --------------------------------------------------------------------------
# arithmetic is checked in code, never trusted from the extractor
# --------------------------------------------------------------------------

ARITHMETIC_TOLERANCE = Decimal("0.05")


def check_order_arithmetic(fields: EmailFields) -> str | None:
    """``item total - discount + delivery = grand total``.

    Returns a message when it does not hold. GST is not added: Indian order
    receipts quote it as a component of the amount charged, not on top.
    Principle 2 -- the extractor supplies numbers, code decides whether they
    are consistent.
    """
    if fields.amount is None or fields.item_total is None:
        return None
    expected = fields.item_total
    if fields.discount:
        expected -= fields.discount
    if fields.delivery_fee:
        expected += fields.delivery_fee
    difference = expected - fields.amount
    if abs(difference) <= ARITHMETIC_TOLERANCE:
        return None
    return (
        f"order total does not add up: items {fields.item_total:.2f} "
        f"- discount {(fields.discount or 0):.2f} + delivery "
        f"{(fields.delivery_fee or 0):.2f} = {expected:.2f}, "
        f"but the email says {fields.amount:.2f}"
    )


# --------------------------------------------------------------------------
# emails -> candidate transactions
# --------------------------------------------------------------------------


@dataclass
class EmailRecord:
    """One email, parsed and extracted, ready for linking."""

    parsed: ParsedEmail
    fields: EmailFields
    doc_id: str
    issues: list[str] = field(default_factory=list)

    @property
    def source_ref(self) -> str:
        return self.parsed.source_ref

    @property
    def amount(self) -> Decimal | None:
        return self.fields.amount

    @property
    def date(self) -> dt.date | None:
        return self.fields.occurred_on

    @property
    def item_details(self) -> str | None:
        names = [i.name for i in self.fields.items if i.name]
        return ", ".join(names) if names else None


def build_email_records(
    emails: list[ParsedEmail],
    extractor: EmailExtractor,
    *,
    doc_id: str,
) -> tuple[list[EmailRecord], list[str]]:
    """Extract fields for the relevant emails and discard the rest."""
    relevant = [e for e in emails if is_relevant(e)]
    dropped = [e.source_ref for e in emails if not is_relevant(e)]
    if not relevant:
        return [], dropped

    results = extractor.extract(relevant)
    records: list[EmailRecord] = []
    for parsed, fields in zip(relevant, results, strict=False):
        if fields is None or fields.amount is None:
            dropped.append(parsed.source_ref)
            continue
        issues = []
        problem = check_order_arithmetic(fields)
        if problem:
            issues.append(problem)
        records.append(EmailRecord(parsed=parsed, fields=fields, doc_id=doc_id, issues=issues))
    return records, dropped


def email_description(record: EmailRecord) -> str:
    """The ``raw_description`` for a transaction created from an email."""
    merchant = record.fields.merchant or record.parsed.sender_domain or "email"
    if record.fields.order_id:
        return mask_text(f"{merchant} order {record.fields.order_id}")
    return mask_text(f"{merchant} {record.parsed.subject}".strip())
