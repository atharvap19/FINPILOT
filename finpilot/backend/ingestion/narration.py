"""Reading Indian bank narrations.

A narration is a structured record that has been flattened into one string, and
every bank flattens it differently. HDFC joins fields with hyphens::

    UPI-SWIGGY-SWIGGY.PAYU@HDFCBANK-HDFC0000060-500000005001-SWIGGY ORDER
    NEFT CR-ICIC0000001-ACME TECHNOLOGIES PVT LTD-AARAV SHARMA-N0000010000
    NWD-447712XXXXXX4417-HDFC BANK ATM-ANDHERI W MUMBAI

SBI uses slashes inside a ``TO TRANSFER``/``BY TRANSFER`` wrapper::

    TO TRANSFER-UPI/DR/500000005009/Amazon In/ICIC/amazon@apl/Amazon Order--

Card statements print the merchant, the city and a country code::

    SWIGGY BANGALORE IN

Rather than one brittle regex per dialect, the field-joined formats are split
on their delimiter and each part is *identified* by what it looks like -- a VPA
has an ``@``, an IFSC is four letters and seven digits, a UPI reference is
twelve digits. That survives banks adding or dropping a field, which they do.

This module runs on the **unmasked** narration: it needs the VPA local part to
tell a merchant from a person. Masking happens afterwards, on the way to
storage (see :func:`ingestion.normalize.mask_text`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ingestion.normalize import clean_text, mask_upi_handle
from ingestion.schema import Channel, CounterpartyType, PaymentMode, quantize_money

# --------------------------------------------------------------------------
# result
# --------------------------------------------------------------------------


@dataclass
class ParsedNarration:
    payment_mode: str = PaymentMode.OTHER.value
    reference_no: str | None = None
    counterparty_name: str | None = None
    counterparty_vpa: str | None = None  # unmasked; only the handle is stored
    counterparty_type: str = CounterpartyType.UNKNOWN.value
    upi_remark: str | None = None
    channel: str | None = None
    card_last4: str | None = None
    merchant_city: str | None = None
    country: str | None = None
    original_currency: str | None = None
    fx_amount: Decimal | None = None
    is_fee: bool = False
    is_cash_withdrawal: bool = False
    emi_flag: bool = False
    #: the narration itself says this is the user moving their own money
    is_self: bool = False
    #: the bank raised this entry against itself (a charge, interest, a
    #: reversal). The counterparty name comes from the document's issuer.
    counterparty_is_issuer: bool = False
    matched: str = "none"
    confidence: float = 0.0
    tokens: list[str] = field(default_factory=list)

    @property
    def counterparty_upi_handle(self) -> str | None:
        return mask_upi_handle(self.counterparty_vpa)


# --------------------------------------------------------------------------
# shape recognisers
# --------------------------------------------------------------------------

IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$", re.IGNORECASE)
UPI_REF = re.compile(r"^\d{9,18}$")
UTR = re.compile(r"^[A-Z]{1,6}\d{9,20}$", re.IGNORECASE)
VPA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{1,63}@[A-Za-z][A-Za-z0-9]{1,24}$")
MASKED_ACCOUNT = re.compile(r"^[0-9Xx]{6,24}$")
#: A card in a narration is always *partly* masked by the bank, so the ``X`` is
#: required. Without it this pattern would swallow 12-digit UPI reference
#: numbers, which the ledger needs to keep.
CARD_TOKEN = re.compile(r"^(?=[0-9Xx]*[Xx])[0-9Xx]{12,22}$")
BANK_CODE = re.compile(r"^[A-Z]{3,5}$")
PHONE_LOCAL = re.compile(r"^(?:\+?91)?[6-9]\d{9}$")

#: Payment-gateway markers inside a VPA local part. These only ever appear on
#: a collection account, so they are a hard merchant signal.
_GATEWAY_MARKERS = (
    ".rzp", ".payu", ".pz", ".cashfree", ".ccav", ".ipay", ".bd", ".pine", ".yapl",
    ".mrchnt", ".easebuzz", ".billdesk", ".fkrt", ".juspay", ".sbiepay", ".paytmqr",
    ".garage", ".razorpay", ".payswiff", "payu", "rzp",
)
#: Handles that only merchants get. Deliberately short: most handles
#: (``@ybl``, ``@paytm``, ``@okaxis``) are used by individuals and businesses
#: alike, so guessing from the handle alone produces false merchants.
_MERCHANT_HANDLES = ("@paytm-merchant", "@rzp", "@payu", "@cashfree", "@abfspay")

#: Entries the bank itself raised: the counterparty is the bank, and no
#: narration ever spells that out, so the document's issuer supplies the name.
_BANK_ORIGIN_WORDS = (
    "chrg", "charge", "charges", "fee", "fees", "interest", "penal", "penalty",
    "tds", "gst", "reversal", "annual", "amb ", "mab ", "min bal", "sms chrg",
    "cash deposit", "atm decline", "imps return", "nach return",
)

#: Words that make a counterparty an organisation rather than a person.
_ORG_WORDS = frozenset(
    """ltd limited pvt private llp inc corp corporation co company enterprises enterprise
    technologies technology services service solutions systems industries retail retails
    store stores mart supermart supermarket bazaar bazar foods food restaurant restaurants
    cafe hotel hotels petrol pump fuel filling energy power electric electricity telecom
    communications bank banking finance financial insurance capital securities pharma
    hospital clinic labs laboratories motors automobiles agency agencies traders trading
    distributors marketing international india online digital payments gateway network
    entertainment media broadband fibernet mobile postpaid prepaid""".split()
)

_FEE_WORDS = (
    "chrg", "charge", "charges", "fee", "fees", "penal", "penalty", "amb ", "mab ",
    "non maintenance", "min bal", "minimum balance", "late payment", "annual fee",
    "renewal fee", "processing fee", "service tax", "convenience fee", "return charges",
    "cheque return", "ecs return", "over limit",
)
_CASH_WORDS = ("nwd", "atw", "atm wdl", "cash wdl", "cwdr", "atm cash", "cash withdrawal",
               "atm-cash", "self withdrawal")
_SELF_WORDS = ("self transfer", "self trf", "own account", "self-transfer", "selftransfer",
               "to self", "autopay si", "payment received - thank you", "credit card autopay")
_EMI = re.compile(r"\bemi\b|\bequated monthly\b|\binstalment\b|\binstallment\b", re.IGNORECASE)

_CITIES = frozenset(
    """mumbai delhi bangalore bengaluru chennai kolkata hyderabad pune ahmedabad surat
    jaipur lucknow kanpur nagpur indore thane bhopal visakhapatnam patna vadodara
    ghaziabad ludhiana agra nashik faridabad meerut rajkot varanasi srinagar aurangabad
    dhanbad amritsar noida gurgaon gurugram navimumbai howrah ranchi coimbatore jabalpur
    gwalior vijayawada jodhpur madurai raipur kota chandigarh guwahati mysore
    thiruvananthapuram kochi cochin seattle london singapore newyork dublin""".split()
)


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def parse_narration(
    text: str,
    *,
    dialect: str = "generic",
    account_holder: str | None = None,
    known_merchant=None,
) -> ParsedNarration:
    """Parse one narration. Never raises; an unrecognised string comes back as
    ``OTHER`` with confidence 0, which leaves the row usable but unenriched."""
    raw = clean_text(text)
    if not raw:
        return ParsedNarration()

    parsers = {
        "hdfc": (_parse_hdfc, _parse_generic),
        "sbi": (_parse_sbi, _parse_generic),
        "card": (_parse_card,),
        "generic": (_parse_hdfc, _parse_sbi, _parse_generic),
    }.get(dialect, (_parse_generic,))

    result = ParsedNarration()
    for parser in parsers:
        candidate = parser(raw)
        if candidate.confidence > result.confidence:
            result = candidate
        if result.confidence >= 0.9:
            break

    _apply_flags(result, raw)
    _resolve_counterparty_type(result, raw, account_holder, known_merchant)
    result.channel = channel_for(result.payment_mode, result)
    return result


def channel_for(payment_mode: str, parsed: ParsedNarration | None = None) -> str | None:
    """Ingestion derives channel from the instrument, nothing else.

    A statement does not record whether a card was swiped or typed into a web
    form, so inferring anything finer would be invention rather than extraction.
    """
    return {
        PaymentMode.UPI.value: Channel.APP.value,
        PaymentMode.CARD.value: Channel.POS.value,
        PaymentMode.ATM.value: Channel.ATM.value,
        PaymentMode.NEFT.value: Channel.ONLINE.value,
        PaymentMode.IMPS.value: Channel.ONLINE.value,
        PaymentMode.RTGS.value: Channel.ONLINE.value,
        PaymentMode.ACH.value: Channel.ONLINE.value,
        PaymentMode.CASH.value: Channel.OTHER.value,
        PaymentMode.CHEQUE.value: Channel.OTHER.value,
    }.get(payment_mode)


# --------------------------------------------------------------------------
# HDFC: hyphen-joined fields
# --------------------------------------------------------------------------

_HDFC_PREFIXES = {
    "UPI": PaymentMode.UPI.value,
    "IMPS": PaymentMode.IMPS.value,
    "NEFT": PaymentMode.NEFT.value,
    "RTGS": PaymentMode.RTGS.value,
    "ACH": PaymentMode.ACH.value,
    "NACH": PaymentMode.ACH.value,
    "MMT": PaymentMode.IMPS.value,
    "NWD": PaymentMode.ATM.value,
    "ATW": PaymentMode.ATM.value,
    "EAW": PaymentMode.ATM.value,
    "POS": PaymentMode.CARD.value,
    "CHQ": PaymentMode.CHEQUE.value,
    "CC": PaymentMode.OTHER.value,
}


def _parse_hdfc(raw: str) -> ParsedNarration:
    parts = [p.strip() for p in raw.split("-")]
    if not parts:
        return ParsedNarration()
    head = parts[0].upper()
    prefix = head.split()[0] if head else ""
    mode = _HDFC_PREFIXES.get(prefix)
    if mode is None:
        return ParsedNarration()

    out = ParsedNarration(payment_mode=mode, matched=f"hdfc:{prefix}", tokens=parts)

    if prefix == "POS":
        return _parse_pos(raw, out)
    if prefix in ("NWD", "ATW", "EAW"):
        out.card_last4 = _last4(parts[1] if len(parts) > 1 else "")
        out.counterparty_name = _first_named(parts[2:])
        out.is_cash_withdrawal = True
        out.is_self = True
        out.confidence = 0.95
        return out
    if prefix == "CC":
        out.card_last4 = _last4(raw)
        out.is_self = True
        out.confidence = 0.9
        return out

    # Each field is identified by its shape rather than its position, because
    # the field order differs per instrument: UPI is NAME-VPA-IFSC-REF-REMARK
    # while IMPS is REF-NAME-BANK-ACCOUNT-REMARK.
    rest = [p for p in parts[1:] if p]
    texts: list[str] = []
    for i, part in enumerate(rest):
        if VPA.match(part) and out.counterparty_vpa is None:
            out.counterparty_vpa = part.lower()
            continue
        if IFSC.match(part):
            continue
        if MASKED_ACCOUNT.match(part) and "X" in part.upper():
            continue  # the counterparty's masked account number
        if out.reference_no is None and (UPI_REF.match(part) or UTR.match(part)):
            out.reference_no = part.upper()
            continue
        if _is_bank_code(part, rest, i):
            continue
        texts.append(part)

    out.counterparty_name = _join_name(texts[:1])
    # rejoin with the delimiter they were split on: a remark like
    # "MYNTRA REFUND MYN-1180042317" contains a hyphen of its own
    remark = clean_text("-".join(texts[1:])) or None
    if mode == PaymentMode.UPI.value:
        out.upi_remark = remark or None
    out.confidence = 0.95 if (out.reference_no or out.counterparty_vpa) else 0.7
    return out


def _is_bank_code(part: str, rest: list[str], index: int) -> bool:
    """``STAT`` in ``IMPS-ref-AARAV SHARMA-STAT-XXXXXXXX8901-...``.

    Only treated as a bank code when a masked account follows it, so a
    counterparty who happens to have a four-letter name is not discarded.
    """
    if not BANK_CODE.match(part):
        return False
    nxt = rest[index + 1] if index + 1 < len(rest) else ""
    return bool(MASKED_ACCOUNT.match(nxt) and "X" in nxt.upper())


_POS_RE = re.compile(r"^POS\s+(?P<card>[0-9Xx]{8,22})\s+(?P<merchant>.+)$", re.IGNORECASE)


def _parse_pos(raw: str, out: ParsedNarration) -> ParsedNarration:
    match = _POS_RE.match(raw)
    if match:
        out.card_last4 = _last4(match.group("card"))
        out.counterparty_name, out.merchant_city, out.country = _split_card_merchant(
            match.group("merchant")
        )
        out.confidence = 0.92
    else:
        out.counterparty_name = raw[3:].strip(" -")
        out.confidence = 0.6
    return out


# --------------------------------------------------------------------------
# SBI: slash-joined inside TO/BY TRANSFER
# --------------------------------------------------------------------------

_SBI_WRAPPER = re.compile(
    r"^(?P<side>TO|BY)\s+TRANSFER\s*-\s*(?P<body>.+?)-{0,2}$", re.IGNORECASE
)
_SBI_MODES = {
    "UPI": PaymentMode.UPI.value,
    "INB IMPS": PaymentMode.IMPS.value,
    "IMPS": PaymentMode.IMPS.value,
    "NEFT": PaymentMode.NEFT.value,
    "INB NEFT": PaymentMode.NEFT.value,
    "RTGS": PaymentMode.RTGS.value,
    "INB": PaymentMode.NEFT.value,
    "NACH": PaymentMode.ACH.value,
    "ACH": PaymentMode.ACH.value,
}


def _parse_sbi(raw: str) -> ParsedNarration:
    match = _SBI_WRAPPER.match(raw)
    if not match:
        return ParsedNarration()
    body = match.group("body")
    segments = [s.strip() for s in body.split("/")]
    head = segments[0].upper() if segments else ""
    mode = _SBI_MODES.get(head, PaymentMode.OTHER.value)

    out = ParsedNarration(payment_mode=mode, matched=f"sbi:{head or 'transfer'}",
                          tokens=segments)
    named: list[str] = []
    for segment in segments[1:]:
        if not segment:
            continue
        upper = segment.upper()
        if upper in ("DR", "CR"):
            continue
        if VPA.match(segment) and out.counterparty_vpa is None:
            out.counterparty_vpa = segment.lower()
            continue
        if out.reference_no is None and (UPI_REF.match(segment) or UTR.match(segment)):
            out.reference_no = segment.upper()
            continue
        if len(segment) == 4 and segment.isalpha():
            continue  # the counterparty's bank code
        named.append(segment)

    if named:
        out.counterparty_name = named[0]
        if mode == PaymentMode.UPI.value and len(named) > 1:
            out.upi_remark = _join_name(named[1:])
    out.confidence = 0.93 if out.reference_no else 0.75
    return out


# --------------------------------------------------------------------------
# credit card statements
# --------------------------------------------------------------------------

_EMI_RE = re.compile(r"^EMI\s+\d{1,2}\s*/\s*\d{1,2}\s+(?P<merchant>.+)$", re.IGNORECASE)

#: The foreign-currency detail a card statement wraps onto the line below,
#: e.g. ``USD 12.00 @ 86.02``. It is folded into the narration by the PDF
#: extractor, so it has to be lifted back out here.
_FX_RE = re.compile(
    r"\b(?P<ccy>[A-Z]{3})\s+(?P<amount>[\d,]+\.\d{2})\s*(?:@\s*(?P<rate>[\d,]+\.?\d*))?",
)
_FX_NOT_CURRENCY = frozenset({"EMI", "POS", "UPI", "ATM", "GST", "TAX", "REF"})


def _parse_card(raw: str) -> ParsedNarration:
    out = ParsedNarration(payment_mode=PaymentMode.CARD.value, matched="card")
    upper = raw.upper()

    if "PAYMENT RECEIVED" in upper or "PAYMENT - THANK YOU" in upper:
        out.payment_mode = PaymentMode.OTHER.value
        out.is_self = True
        out.matched = "card:payment"
        out.confidence = 0.95
        return out

    body, fx = _split_fx_clause(raw)
    if fx:
        out.original_currency, out.fx_amount = fx

    emi = _EMI_RE.match(body)
    if emi:
        body = emi.group("merchant")
        out.emi_flag = True
        out.matched = "card:emi"

    out.counterparty_name, out.merchant_city, out.country = _split_card_merchant(body)
    out.confidence = 0.9
    return out


def _split_fx_clause(raw: str) -> tuple[str, tuple[str, Decimal] | None]:
    """Separate the merchant part from the foreign-currency part.

    Returns ``(merchant text, (currency, foreign amount) or None)``. The rate
    is deliberately not kept: the rupee amount is already on the row, and
    recomputing it from a printed rate would be doing arithmetic on extracted
    values (principle 2).
    """
    match = _FX_RE.search(raw)
    if not match:
        return raw, None
    currency = match.group("ccy").upper()
    if currency in _FX_NOT_CURRENCY:
        return raw, None
    try:
        amount = quantize_money(Decimal(match.group("amount").replace(",", "")))
    except (InvalidOperation, ValueError):
        return raw, None
    body = clean_text(raw[: match.start()] + " " + raw[match.end():])
    return body or raw, (currency, amount)


def _split_card_merchant(text: str) -> tuple[str | None, str | None, str | None]:
    """``SWIGGY BANGALORE IN`` -> ``("SWIGGY", "BANGALORE", "IN")``."""
    words = clean_text(text).split()
    if not words:
        return None, None, None
    country = None
    if len(words) >= 3 and len(words[-1]) == 2 and words[-1].isalpha():
        country = words[-1].upper()
        words = words[:-1]
    city = None
    if len(words) >= 2:
        if len(words) >= 3 and " ".join(words[-2:]).lower() in _CITIES:
            city, words = " ".join(words[-2:]).upper(), words[:-2]
        elif words[-1].lower() in _CITIES or country is not None:
            city, words = words[-1].upper(), words[:-1]
    name = " ".join(words).strip() or None
    return name, city, country


# --------------------------------------------------------------------------
# generic fallback
# --------------------------------------------------------------------------

_GENERIC_MODES: tuple[tuple[str, str], ...] = (
    (r"\bupi\b|\bvpa\b|@ok[a-z]+|@ybl\b|@paytm\b", PaymentMode.UPI.value),
    (r"\bimps\b|\bp2a\b|\bp2p\b", PaymentMode.IMPS.value),
    (r"\bneft\b", PaymentMode.NEFT.value),
    (r"\brtgs\b", PaymentMode.RTGS.value),
    (r"\bach\b|\bnach\b|\becs\b|\bsi\b|standing instruction|\bmandate\b", PaymentMode.ACH.value),
    (r"\batm\b|\bnwd\b|\batw\b|cash wdl|cash withdrawal", PaymentMode.ATM.value),
    (r"\bpos\b|\bcard\b|\becom\b|\bmerchant\b", PaymentMode.CARD.value),
    (r"\bchq\b|\bcheque\b|\bclg\b", PaymentMode.CHEQUE.value),
    (r"\bcash\b", PaymentMode.CASH.value),
)
_INLINE_VPA = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9._\-]{1,63}@[A-Za-z][A-Za-z0-9]{1,24})\b")
_INLINE_REF = re.compile(r"\b(\d{12}|\d{16}|[A-Z]{1,6}\d{9,20})\b")


def _parse_generic(raw: str) -> ParsedNarration:
    lowered = raw.lower()
    out = ParsedNarration(matched="generic")
    for pattern, mode in _GENERIC_MODES:
        if re.search(pattern, lowered):
            out.payment_mode = mode
            break

    vpa = _INLINE_VPA.search(raw)
    if vpa:
        out.counterparty_vpa = vpa.group(1).lower()
    ref = _INLINE_REF.search(raw)
    if ref:
        out.reference_no = ref.group(1).upper()

    # the longest run of letters is the best guess at who this was
    words = [w for w in re.split(r"[^A-Za-z&.' ]+", raw) if len(w.strip()) > 2]
    if words:
        out.counterparty_name = max(words, key=len).strip()
    out.confidence = 0.5 if out.payment_mode != PaymentMode.OTHER.value else 0.2
    return out


# --------------------------------------------------------------------------
# flags and counterparty classification
# --------------------------------------------------------------------------


def _apply_flags(out: ParsedNarration, raw: str) -> None:
    lowered = " " + raw.lower() + " "
    if any(word in lowered for word in _FEE_WORDS):
        out.is_fee = True
    if any(word in lowered for word in _CASH_WORDS):
        out.is_cash_withdrawal = True
        out.payment_mode = PaymentMode.ATM.value
    if _EMI.search(raw):
        out.emi_flag = True
    if any(word in lowered for word in _SELF_WORDS):
        out.is_self = True
    if out.counterparty_vpa is None and not out.counterparty_name:
        if any(word in lowered for word in _BANK_ORIGIN_WORDS):
            out.counterparty_is_issuer = True
    elif out.is_fee and not out.counterparty_vpa:
        out.counterparty_is_issuer = True
    if any(word in lowered for word in ("credit interest", "debit interest", "int credit")):
        out.counterparty_is_issuer = True
    # A card number in the narration only means something on a card-shaped
    # entry; on a transfer the long masked token is the counterparty's bank
    # account, not a card.
    if out.card_last4 is None and out.payment_mode in (
        PaymentMode.CARD.value, PaymentMode.ATM.value, PaymentMode.OTHER.value
    ):
        token = next((t for t in raw.split() if CARD_TOKEN.match(t)), None)
        if token:
            out.card_last4 = _last4(token)


def _resolve_counterparty_type(
    out: ParsedNarration,
    raw: str,
    account_holder: str | None,
    known_merchant=None,
) -> None:
    """merchant vs person vs self.

    Ordered by how much the signal can be trusted: a payment-gateway VPA is
    conclusive, a bare phone number as the VPA local part almost is, then the
    merchant dictionary, and only then the shape of the name.
    """
    if out.is_self or out.is_cash_withdrawal:
        out.counterparty_type = CounterpartyType.SELF.value
        return
    if out.counterparty_is_issuer:
        out.counterparty_type = CounterpartyType.MERCHANT.value
        return

    if account_holder and out.counterparty_name:
        if _same_person(out.counterparty_name, account_holder):
            out.counterparty_type = CounterpartyType.SELF.value
            out.is_self = True
            return

    vpa = out.counterparty_vpa or ""
    local = vpa.split("@")[0] if vpa else ""
    handle = "@" + vpa.split("@")[-1] if "@" in vpa else ""

    if any(marker in vpa for marker in _GATEWAY_MARKERS):
        out.counterparty_type = CounterpartyType.MERCHANT.value
        return
    if handle in _MERCHANT_HANDLES and local and not PHONE_LOCAL.match(local):
        out.counterparty_type = CounterpartyType.MERCHANT.value
        return
    if local and PHONE_LOCAL.match(local):
        out.counterparty_type = CounterpartyType.PERSON.value
        return
    # The dictionary knows "Amazon In" is a shop and "Ramesh Kumar" is not;
    # the name-shape rules below cannot tell those apart.
    if known_merchant and out.counterparty_name and known_merchant(out.counterparty_name):
        out.counterparty_type = CounterpartyType.MERCHANT.value
        return

    name = (out.counterparty_name or "").lower()
    words = [w.strip(".,") for w in re.split(r"[\s.]+", name) if w.strip(".,")]
    if any(w in _ORG_WORDS for w in words):
        out.counterparty_type = CounterpartyType.MERCHANT.value
        return
    if out.payment_mode == PaymentMode.CARD.value:
        out.counterparty_type = CounterpartyType.MERCHANT.value
        return
    if 1 < len(words) <= 4 and all(w.isalpha() for w in words):
        # two to four plain words with no corporate marker reads as a person
        out.counterparty_type = CounterpartyType.PERSON.value
        return
    if words:
        out.counterparty_type = CounterpartyType.MERCHANT.value
        return
    out.counterparty_type = CounterpartyType.UNKNOWN.value


def _same_person(a: str, b: str) -> bool:
    from rapidfuzz import fuzz as _fuzz

    left = re.sub(r"^(mr|mrs|ms|dr)\.?\s+", "", a.strip().lower())
    right = re.sub(r"^(mr|mrs|ms|dr)\.?\s+", "", b.strip().lower())
    return _fuzz.token_sort_ratio(left, right) >= 88


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _last4(token: str) -> str | None:
    digits = re.sub(r"[^0-9]", "", token or "")
    return digits[-4:] if len(digits) >= 4 else None


def _first_named(parts: list[str]) -> str | None:
    for part in parts:
        cleaned = clean_text(part)
        if cleaned and not MASKED_ACCOUNT.match(cleaned):
            return cleaned
    return None


def _join_name(parts: list[str]) -> str | None:
    joined = " ".join(p for p in parts if p).strip(" -")
    return clean_text(joined) or None
