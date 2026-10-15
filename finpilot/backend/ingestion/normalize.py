"""Value-level normalisation and masking.

Indian retail banking data is messy in specific, predictable ways, and this
module is where each of those is dealt with exactly once:

* dates are day-first (``05/06/25`` is the fifth of June), except when they are
  obviously ISO,
* amounts carry rupee signs, Indian digit grouping (``1,23,456.00``),
  parentheses for negatives, and ``Dr``/``Cr`` suffixes,
* account, card and UPI identifiers must be masked *before* anything is stored
  or sent to a model (principle 6).

Masking is deliberately narrow rather than aggressive: a UPI reference number
and a UTR are transaction identifiers the ledger has to keep, so the patterns
below target card/account shapes and VPA local parts and leave references
alone. Every rule here is covered by ``tests/unit/test_normalize.py``.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from dateutil import parser as date_parser

from ingestion.schema import Direction, quantize_money

# --------------------------------------------------------------------------
# whitespace / text
# --------------------------------------------------------------------------

_WS = re.compile(r"[ \t   ]+")
_AROUND_NL = re.compile(r" *\n *")
_MULTI_NL = re.compile(r"\n{3,}")


def clean_text(value: object) -> str:
    """Collapse whitespace and normalise unicode. Never returns None.

    Trailing spaces before a newline are dropped as well as leading ones after
    it -- PDF text extraction produces both, and leaving them in would make two
    copies of the same narration hash differently.
    """
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = _AROUND_NL.sub("\n", text)
    text = _MULTI_NL.sub("\n\n", text)
    return text.strip()


def is_blank(value: object) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null", "-", "--"}


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------

_ISO_LIKE = re.compile(r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}")
_EXCEL_EPOCH = dt.date(1899, 12, 30)


def parse_date(value: object, *, dayfirst: bool = True) -> dt.date | None:
    """Day-first by default; ISO strings are recognised and left alone.

    Returns ``None`` rather than raising -- the caller decides whether an
    unparseable date is a footer row to drop or a real problem to flag.
    """
    if value is None or is_blank(value):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Excel serial date; anything outside a sane window is not a date
        if 20000 < float(value) < 60000:
            return _EXCEL_EPOCH + dt.timedelta(days=int(value))
        return None
    text = clean_text(value)
    if not text:
        return None
    text = text.split(" 00:00:00")[0]
    use_dayfirst = dayfirst and not _ISO_LIKE.match(text)
    try:
        parsed = date_parser.parse(text, dayfirst=use_dayfirst, fuzzy=False)
    except (ValueError, OverflowError, TypeError):
        return None
    # dateutil happily fills in today's date for a bare time like "12:30", so
    # insist the text actually looked like a date: either two numeric parts, or
    # a day next to a month name ("1 Jun 2025", "01-Jun-2025").
    if not re.search(r"\d{1,4}[-/. ]\d{1,2}", text) and not re.search(
        r"\d{1,2}[-\s/.]*[A-Za-z]{3}", text
    ):
        return None
    return parsed.date()


_TIME_RE = re.compile(
    r"\b(?P<h>\d{1,2})[:.](?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?"
)


def parse_time(value: object) -> dt.time | None:
    """Pull a wall-clock time out of a string. ``None`` when there isn't one."""
    if value is None or is_blank(value):
        return None
    if isinstance(value, dt.datetime):
        return value.time().replace(microsecond=0)
    if isinstance(value, dt.time):
        return value.replace(microsecond=0)
    match = _TIME_RE.search(clean_text(value))
    if not match:
        return None
    hour, minute = int(match.group("h")), int(match.group("m"))
    second = int(match.group("s") or 0)
    ampm = (match.group("ampm") or "").lower().replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59 or second > 59:
        return None
    return dt.time(hour, minute, second)


def within(date: dt.date | None, start: dt.date | None, end: dt.date | None,
           slack_days: int = 0) -> bool:
    if date is None:
        return False
    if start and date < start - dt.timedelta(days=slack_days):
        return False
    if end and date > end + dt.timedelta(days=slack_days):
        return False
    return True


# --------------------------------------------------------------------------
# amounts
# --------------------------------------------------------------------------

_CURRENCY_CHARS = "₹$£€"
_AMOUNT_STRIP = re.compile(rf"[{_CURRENCY_CHARS}]|\b(?:rs|inr|rupees)\b\.?", re.IGNORECASE)
_DR_SUFFIX = re.compile(r"\b(dr|debit|db|w/d|wdl)\b\.?\s*$", re.IGNORECASE)
_CR_SUFFIX = re.compile(r"\b(cr|credit)\b\.?\s*$", re.IGNORECASE)
_NUMERIC = re.compile(r"-?\d[\d,]*\.?\d*")


def parse_amount(value: object) -> Decimal | None:
    """Parse one money cell. Sign is preserved; direction is a separate call.

    Handles ``1,23,456.00`` (Indian grouping), ``Rs. 1,234.56``, ``(500.00)``
    for negative, unicode minus, and trailing ``Dr``/``Cr``.
    """
    amount, _ = parse_amount_with_direction(value)
    return amount


def parse_amount_with_direction(
    value: object,
) -> tuple[Decimal | None, str | None]:
    """Returns ``(signed amount, direction or None)``.

    The direction comes back only when the *cell itself* says so -- a ``Dr``
    suffix or a leading minus. Statements that use separate debit/credit
    columns carry the direction in the column, not the value, so the mapper
    supplies it there instead.
    """
    if value is None or is_blank(value):
        return None, None
    if isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float, Decimal)):
        amount = quantize_money(Decimal(str(value)))
        return amount, _direction_from_sign(amount)

    text = clean_text(value)
    text = text.replace("−", "-")  # unicode minus
    direction: str | None = None

    if _DR_SUFFIX.search(text):
        direction = Direction.DEBIT.value
        text = _DR_SUFFIX.sub("", text)
    elif _CR_SUFFIX.search(text):
        direction = Direction.CREDIT.value
        text = _CR_SUFFIX.sub("", text)

    negative = False
    if text.strip().startswith("(") and text.strip().endswith(")"):
        negative = True
        text = text.strip()[1:-1]

    text = _AMOUNT_STRIP.sub("", text).strip()
    match = _NUMERIC.search(text)
    if not match:
        return None, direction
    raw = match.group(0).replace(",", "")
    if raw in {"-", "", "."}:
        return None, direction
    try:
        amount = quantize_money(Decimal(raw))
    except (InvalidOperation, ValueError):
        return None, direction
    if negative:
        amount = -amount
    if direction is None:
        direction = _direction_from_sign(amount)
    elif direction == Direction.DEBIT.value and amount < 0:
        # "(500.00) Dr" -- the suffix and the sign agree, do not double-negate
        amount = -amount
    return amount, direction


def _direction_from_sign(amount: Decimal) -> str | None:
    if amount < 0:
        return Direction.DEBIT.value
    if amount > 0:
        return Direction.CREDIT.value
    return None


def split_debit_credit(
    debit: object, credit: object
) -> tuple[Decimal, str] | None:
    """Separate-columns style: whichever cell has a value wins.

    Returns ``(positive amount, direction)`` or ``None`` when both are blank --
    which is how total/footer rows announce themselves.
    """
    d = parse_amount(debit)
    c = parse_amount(credit)
    if d is not None and d != 0:
        return abs(d), Direction.DEBIT.value
    if c is not None and c != 0:
        return abs(c), Direction.CREDIT.value
    return None


def signed_to_direction(amount: Decimal) -> tuple[Decimal, str]:
    """Signed-amount style: negative means money left the account."""
    if amount < 0:
        return abs(amount), Direction.DEBIT.value
    return amount, Direction.CREDIT.value


# --------------------------------------------------------------------------
# masking (principle 6)
# --------------------------------------------------------------------------

#: A pure-digit run long enough to be a card or account number. The lookarounds
#: matter: they stop the rule eating the digits of a UTR like ``N12345678901234``
#: or an IFSC, which the ledger needs to keep as ``reference_no``.
_LONG_DIGITS = re.compile(r"(?<![0-9A-Za-z])\d{13,19}(?![0-9A-Za-z])")

#: Partially-masked identifiers banks print themselves, e.g. ``447712XXXXXX4417``
#: or ``XXXXXXXX8901``. Re-masking these is a no-op, which keeps mask_text
#: idempotent.
_MIXED_MASKED = re.compile(r"(?<![0-9A-Za-z])(?=[0-9]*[Xx])[0-9Xx]{10,24}(?![0-9A-Za-z])")

#: ``4477 1234 5678 4417`` / ``4477-12XX-XXXX-4417``
_SPACED_CARD = re.compile(
    r"(?<![0-9A-Za-z])(?:[0-9Xx]{4}[ -]){3}[0-9Xx]{4}(?![0-9A-Za-z])"
)

#: A VPA: the handle has no dot, which is what separates it from an email
#: address. Only the local part is masked; the handle domain is kept, because
#: ``@ybl`` vs ``@okaxis`` is a real signal about who the counterparty is.
#:
#: The local part deliberately excludes ``-``. Statement dialects use the hyphen
#: as a field separator (``UPI-RAMESH KUMAR-RAMESH.KUMAR@OKAXIS-IFSC-...``), so
#: allowing it here would let the match run backwards over the counterparty name
#: and mask that too. The cost is that a VPA whose local part really does
#: contain a hyphen keeps its first segment; those are rare on UPI, where
#: handles are ``name@bank``, ``phone@bank`` or ``merchant.gateway@bank``.
#:
#: The trailing guard is ``(?!\w)(?!\.[A-Za-z0-9])`` rather than ``(?![\w.])``:
#: what distinguishes a VPA from an email address is a dot *inside* the domain,
#: not a dot after it. A bank alert almost always ends the sentence right after
#: the handle ("...to VPA ramesh.kumar@okaxis."), and the stricter guard let
#: every one of those through unmasked.
_VPA = re.compile(
    r"(?<![\w.@])([A-Za-z0-9][A-Za-z0-9._]{1,63})@([A-Za-z][A-Za-z0-9]{1,24})"
    r"(?!\w)(?!\.[A-Za-z0-9])"
)

#: ``A/c No 50100234567890`` / ``Account Number: 20012345678901``
_ACCOUNT_LABELLED = re.compile(
    r"((?:a\s*/\s*c|acct|account)\s*(?:no\.?|number|#)?\s*[:\-]?\s*)([0-9Xx]{6,20})",
    re.IGNORECASE,
)

MASK_CHAR = "X"


def mask_tail(token: str, keep: int = 4) -> str:
    """``50100234567890`` -> ``XXXXXXXXXX7890``. Separators are dropped."""
    compact = re.sub(r"[ \-]", "", token)
    if len(compact) <= keep:
        return compact
    return MASK_CHAR * (len(compact) - keep) + compact[-keep:]


def mask_upi_handle(vpa: str | None) -> str | None:
    """``ramesh.kumar@okaxis`` -> ``@okaxis``. The stored form is domain only."""
    if not vpa or "@" not in vpa:
        return None
    domain = vpa.rsplit("@", 1)[-1].strip().lower()
    return f"@{domain}" if domain else None


def mask_vpa(vpa: str) -> str:
    """``ramesh.kumar@okaxis`` -> ``XXXX@okaxis`` (for in-narration masking)."""
    if "@" not in vpa:
        return vpa
    return f"{MASK_CHAR * 4}@{vpa.rsplit('@', 1)[-1]}"


def mask_text(text: object) -> str:
    """Mask every account number, card number and VPA in a free-text string.

    Idempotent: masking an already-masked narration returns it unchanged. This
    runs before the narration is written to staging, to the ledger, or into an
    export.
    """
    if text is None:
        return ""
    out = clean_text(text)
    if not out:
        return ""

    out = _SPACED_CARD.sub(lambda m: mask_tail(m.group(0)), out)
    out = _ACCOUNT_LABELLED.sub(lambda m: m.group(1) + mask_tail(m.group(2)), out)
    out = _MIXED_MASKED.sub(lambda m: mask_tail(m.group(0)), out)
    out = _LONG_DIGITS.sub(lambda m: mask_tail(m.group(0)), out)
    out = _VPA.sub(lambda m: f"{MASK_CHAR * 4}@{m.group(2)}", out)
    return out


_ISSUER_CODES = {
    "hdfc bank": "HDFC",
    "hdfc": "HDFC",
    "state bank of india": "SBI",
    "sbi": "SBI",
    "icici bank": "ICICI",
    "icici": "ICICI",
    "axis bank": "AXIS",
    "axis": "AXIS",
    "kotak mahindra bank": "KOTAK",
    "kotak": "KOTAK",
    "punjab national bank": "PNB",
    "bank of baroda": "BOB",
    "yes bank": "YES",
    "idfc first bank": "IDFC",
    "induslnd bank": "INDUSIND",
    "indusind bank": "INDUSIND",
    "canara bank": "CANARA",
    "union bank of india": "UNION",
}


def issuer_code(issuer: str | None) -> str:
    """``State Bank of India`` -> ``SBI``. Unknown issuers keep a short slug."""
    if not issuer:
        return "ACCT"
    key = clean_text(issuer).lower()
    if key in _ISSUER_CODES:
        return _ISSUER_CODES[key]
    for name, code in _ISSUER_CODES.items():
        if name in key:
            return code
    slug = re.sub(r"[^A-Za-z]", "", clean_text(issuer).upper())
    return (slug[:8] or "ACCT")


def mask_account_id(
    number: str | None, issuer: str | None, *, is_card: bool = False
) -> str:
    """The ``account`` column: ``HDFC-XX7890`` / ``CARD-XX4417``.

    Card accounts are labelled ``CARD`` rather than by issuer so that a bank
    account and a credit card at the same bank never collide.
    """
    digits = re.sub(r"[^0-9]", "", str(number or ""))
    last4 = digits[-4:] if len(digits) >= 4 else digits
    prefix = "CARD" if is_card else issuer_code(issuer)
    if not last4:
        return f"{prefix}-XXXX"
    return f"{prefix}-XX{last4}"


_LAST4 = re.compile(r"(\d{4})(?!.*\d)")


def extract_last4(text: str | None) -> str | None:
    if not text:
        return None
    match = _LAST4.search(str(text))
    return match.group(1) if match else None
