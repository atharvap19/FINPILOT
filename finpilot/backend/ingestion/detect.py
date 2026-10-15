"""Phase A step 1: what is this file, and whose account does it describe?

Three jobs:

* **identity** -- SHA-256 of the bytes. Re-uploading a file is a no-op because
  the hash is the document key (principle 5).
* **type** -- the real type, from magic bytes rather than the extension. For
  PDFs that also means: is it encrypted, and does it have a text layer? A PDF
  without one is a scan, and this pipeline reads documents with code only, so
  a scan is reported as unreadable rather than guessed at.
* **classification** -- document kind, issuer, account, statement period and
  opening/closing balances, read out of the metadata block banks put above the
  table. When the rules come up empty the document is reported as unrecognised
  and the user is asked, rather than guessed at.

``python-magic`` is used when it imports, but libmagic is usually absent on
Windows, so the signature sniffer below is always the fallback and is what the
tests exercise.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from ingestion.normalize import (
    clean_text,
    mask_account_id,
    parse_amount,
    parse_date,
)
from ingestion.schema import DocumentType

CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------------------------
# file type
# --------------------------------------------------------------------------

TEXT_CSV = "text/csv"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLS = "application/vnd.ms-excel"
PDF = "application/pdf"
EML = "message/rfc822"
MBOX = "application/mbox"
PNG = "image/png"
JPEG = "image/jpeg"

IMAGE_TYPES = frozenset({PNG, JPEG, "image/webp", "image/tiff", "image/bmp"})
TABULAR_TYPES = frozenset({TEXT_CSV, XLSX, XLS, "text/tab-separated-values"})
EMAIL_TYPES = frozenset({EML, MBOX})

_HEADER_RE = re.compile(rb"^(From|To|Subject|Date|Message-ID|Received|Return-Path):",
                        re.IGNORECASE | re.MULTILINE)


def sniff_type(path: Path) -> str:
    """The file's real type. Extension is a tie-breaker, never the authority."""
    with path.open("rb") as fh:
        head = fh.read(8192)
    detected = _sniff_signature(head, path)
    if detected:
        return detected
    magic_type = _try_python_magic(path)
    if magic_type:
        return magic_type
    return _from_extension(path) or TEXT_CSV


def _sniff_signature(head: bytes, path: Path) -> str | None:
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return XLS
    if head.startswith(b"PK\x03\x04"):
        # an OOXML container: look inside for the part that names the format
        try:
            import zipfile

            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
            if any(n.startswith("xl/") for n in names):
                return XLSX
        except Exception:  # noqa: BLE001 - a corrupt zip is just "not xlsx"
            return None
        return None
    if head.startswith(b"From "):
        return MBOX
    if _HEADER_RE.search(head[:4096]):
        return MBOX if path.suffix.lower() == ".mbox" else EML
    return None


def _try_python_magic(path: Path) -> str | None:
    try:
        import magic  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001 - libmagic missing is the normal case on Windows
        return None
    try:
        return magic.from_file(str(path), mime=True)
    except Exception:  # noqa: BLE001
        return None


_EXT_TYPES = {
    ".csv": TEXT_CSV,
    ".txt": TEXT_CSV,
    ".tsv": "text/tab-separated-values",
    ".xlsx": XLSX,
    ".xlsm": XLSX,
    ".xls": XLS,
    ".pdf": PDF,
    ".eml": EML,
    ".mbox": MBOX,
    ".png": PNG,
    ".jpg": JPEG,
    ".jpeg": JPEG,
}


def _from_extension(path: Path) -> str | None:
    return _EXT_TYPES.get(path.suffix.lower())


# --------------------------------------------------------------------------
# PDFs
# --------------------------------------------------------------------------


@dataclass
class PdfInfo:
    encrypted: bool = False
    page_count: int = 0
    has_text_layer: bool = False
    first_page_text: str = ""
    error: str | None = None


#: Below this many characters on the first pages there is no text layer worth
#: parsing, and the document cannot be read by code.
TEXT_LAYER_MIN_CHARS = 120


def inspect_pdf(path: Path, password: str | None = None) -> PdfInfo:
    """Encrypted? How many pages? Is there real text, or is it a scan?

    ``password`` is used for this call only and never written anywhere
    (principle 6).
    """
    info = PdfInfo()
    try:
        import pikepdf

        try:
            with pikepdf.open(path, password=password or "") as pdf:
                info.page_count = len(pdf.pages)
        except pikepdf.PasswordError:
            info.encrypted = True
            return info
    except Exception as exc:  # noqa: BLE001
        info.error = f"could not open PDF: {exc}"
        return info

    try:
        import pdfplumber

        with pdfplumber.open(path, password=password or "") as pdf:
            info.page_count = len(pdf.pages)
            chars = 0
            for page in pdf.pages[:3]:
                text = page.extract_text() or ""
                if not info.first_page_text:
                    info.first_page_text = text
                chars += len(text.strip())
            info.has_text_layer = chars >= TEXT_LAYER_MIN_CHARS
    except Exception as exc:  # noqa: BLE001
        info.error = f"could not read PDF text: {exc}"
    return info


def decrypt_pdf(path: Path, password: str, out_path: Path) -> bool:
    """Write an unlocked copy. The password is not retained afterwards."""
    try:
        import pikepdf

        with pikepdf.open(path, password=password) as pdf:
            pdf.save(out_path)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------


@dataclass
class Detection:
    file_hash: str
    file_type: str
    document_type: DocumentType = DocumentType.UNKNOWN
    issuer: str | None = None
    account_number: str | None = None  # unmasked, used only to build the mask
    account: str | None = None  # masked, what gets stored
    account_holder: str | None = None
    is_card: bool = False
    statement_start: dt.date | None = None
    statement_end: dt.date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    needs_password: bool = False
    page_count: int = 0
    has_text_layer: bool = True
    confidence: float = 0.0
    issues: list[str] = field(default_factory=list)
    #: the header lines the classification was made from, kept for diagnostics
    sample_text: str = ""

    @property
    def rules_were_enough(self) -> bool:
        return self.document_type is not DocumentType.UNKNOWN and self.confidence >= 0.5


ISSUERS: dict[str, tuple[str, ...]] = {
    "HDFC Bank": ("hdfc bank", "hdfc0", "housing development finance"),
    "State Bank of India": ("state bank of india", "sbin0", "sbi "),
    "ICICI Bank": ("icici bank", "icic0"),
    "Axis Bank": ("axis bank", "utib0"),
    "Kotak Mahindra Bank": ("kotak mahindra", "kkbk0"),
    "Punjab National Bank": ("punjab national bank", "punb0"),
    "Bank of Baroda": ("bank of baroda", "barb0"),
    "Yes Bank": ("yes bank", "yesb0"),
    "IDFC First Bank": ("idfc first", "idfb0"),
    "IndusInd Bank": ("indusind", "indb0"),
    "Canara Bank": ("canara bank", "cnrb0"),
    "Union Bank of India": ("union bank of india", "ubin0"),
    "American Express": ("american express", "amex"),
}

_CARD_MARKERS = (
    "credit card statement",
    "card statement",
    "total dues",
    "minimum amount due",
    "payment due date",
    "credit limit",
    "available credit limit",
)
_BANK_MARKERS = (
    "account no",
    "account number",
    "statement of account",
    "account statement",
    "closing balance",
    "opening balance",
    "ifsc",
    "branch",
    "withdrawal amt",
    "deposit amt",
)
_BILL_MARKERS = (
    "consumer number",
    "consumer no",
    "bill date",
    "due date",
    "amount payable",
    "total amount payable",
    "billing period",
    "bill number",
    "units consumed",
)
_RECEIPT_MARKERS = ("invoice", "receipt", "gstin", "tax invoice", "bill to", "subtotal")

#: label -> the Detection attribute it fills
_METADATA_LABELS: list[tuple[str, tuple[str, ...]]] = [
    ("account_number", ("account no", "account number", "a/c no", "a/c number", "card no",
                        "card number", "account #")),
    ("account_holder", ("account name", "customer name", "name", "account holder")),
    ("statement_start", ("statement from", "start date", "from date", "period from",
                         "statement period from")),
    ("statement_end", ("statement to", "end date", "to date", "period to", "statement date",
                       "statement period to")),
    ("opening_balance", ("opening balance", "previous balance", "balance as on",
                         "opening bal", "b/f balance")),
    ("closing_balance", ("closing balance", "total dues", "closing bal", "total amount due")),
]

_LABEL_SPLIT = re.compile(r"\s*[:=]\s*|\t+")
_PERIOD_RE = re.compile(
    r"(?:statement\s+period|period|from)\s*[:\-]?\s*"
    r"(\d{1,2}[-/ ][A-Za-z0-9]{2,9}[-/ ]\d{2,4})\s*(?:to|-|through|–)\s*"
    r"(\d{1,2}[-/ ][A-Za-z0-9]{2,9}[-/ ]\d{2,4})",
    re.IGNORECASE,
)


def classify_text(sample: str, *, file_type: str = TEXT_CSV) -> Detection:
    """Read the metadata block. ``sample`` is the header text of the document."""
    det = Detection(file_hash="", file_type=file_type, sample_text=sample[:4000])
    lowered = sample.lower()

    det.issuer = _match_issuer(lowered)

    card_hits = sum(1 for m in _CARD_MARKERS if m in lowered)
    bank_hits = sum(1 for m in _BANK_MARKERS if m in lowered)
    bill_hits = sum(1 for m in _BILL_MARKERS if m in lowered)
    receipt_hits = sum(1 for m in _RECEIPT_MARKERS if m in lowered)

    scores = {
        DocumentType.CC_STATEMENT: card_hits * 1.4,
        DocumentType.BANK_STATEMENT: bank_hits,
        DocumentType.BILL: bill_hits * 1.2,
        DocumentType.RECEIPT: receipt_hits * 0.9,
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] >= 2:
        det.document_type = best
        det.confidence = min(1.0, 0.45 + 0.15 * scores[best])
    det.is_card = det.document_type is DocumentType.CC_STATEMENT

    _read_labelled_metadata(sample, det)
    _read_period_phrase(sample, det)

    if det.account_number:
        det.account = mask_account_id(det.account_number, det.issuer, is_card=det.is_card)
    return det


def _match_issuer(lowered: str) -> str | None:
    for issuer, markers in ISSUERS.items():
        if any(m in lowered for m in markers):
            return issuer
    return None


def _read_labelled_metadata(sample: str, det: Detection) -> None:
    """Banks print ``Label : value`` rows above the table, one per line."""
    for line in sample.splitlines():
        line = line.strip().strip(",")
        if not line:
            continue
        # CSV metadata rows arrive as "Label,:,value" or "Label :value,,,,"
        line = line.replace(",:,", ": ").rstrip(",")
        parts = _LABEL_SPLIT.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        label = clean_text(parts[0]).lower().strip(" .#")
        value = clean_text(parts[1]).strip(" ,")
        if not value:
            continue
        for attr, keys in _METADATA_LABELS:
            if getattr(det, attr) is not None:
                continue
            if not any(label.startswith(k) or label == k for k in keys):
                continue
            _assign_metadata(det, attr, label, value)
            break


def _assign_metadata(det: Detection, attr: str, label: str, value: str) -> None:
    if attr in ("statement_start", "statement_end"):
        parsed = parse_date(value)
        if parsed:
            setattr(det, attr, parsed)
    elif attr in ("opening_balance", "closing_balance"):
        amount = parse_amount(value)
        if amount is not None:
            setattr(det, attr, amount)
            # "Balance as on 1 Jun 2025" also tells us when the period starts
            if attr == "opening_balance" and det.statement_start is None:
                as_on = parse_date(label.replace("balance as on", "").strip())
                if as_on:
                    det.statement_start = as_on
    elif attr == "account_number":
        digits = re.sub(r"[^0-9Xx]", "", value)
        if len(digits) >= 6:
            det.account_number = digits
            if "card" in label:
                det.is_card = True
    elif attr == "account_holder":
        cleaned = re.sub(r"^(mr|mrs|ms|dr)\.?\s+", "", value, flags=re.IGNORECASE)
        if 2 < len(cleaned) < 80:
            det.account_holder = cleaned.strip()


def _read_period_phrase(sample: str, det: Detection) -> None:
    """``Statement Period: 20/06/2025 to 19/07/2025`` on one line."""
    if det.statement_start and det.statement_end:
        return
    match = _PERIOD_RE.search(sample)
    if not match:
        return
    start, end = parse_date(match.group(1)), parse_date(match.group(2))
    if start and det.statement_start is None:
        det.statement_start = start
    if end and det.statement_end is None:
        det.statement_end = end


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------

_SAMPLE_ROWS = 45


def detect(path: Path, *, password: str | None = None,
           original_name: str | None = None) -> Detection:
    """Hash, type, and classify one uploaded file."""
    file_hash = sha256_file(path)
    file_type = sniff_type(path)

    if file_type == PDF:
        info = inspect_pdf(path, password)
        det = classify_text(info.first_page_text, file_type=file_type)
        # Some payable ledgers use generic headings and omit bill labels from
        # the first page. An explicit filename is a conservative local signal.
        if det.document_type is DocumentType.UNKNOWN and re.search(
            r"(?:bill|invoice|payable)", (original_name or path.name), re.IGNORECASE
        ):
            det.document_type = DocumentType.BILL
            det.confidence = 0.4
        det.file_hash = file_hash
        det.needs_password = info.encrypted
        det.page_count = info.page_count
        det.has_text_layer = info.has_text_layer
        if info.error:
            det.issues.append(info.error)
        if info.encrypted:
            det.issues.append("PDF is password protected")
        return det

    if file_type in EMAIL_TYPES:
        det = Detection(file_hash=file_hash, file_type=file_type,
                        document_type=DocumentType.EMAIL, confidence=1.0)
        return det

    if file_type in IMAGE_TYPES:
        # An image has no text layer, and this pipeline extracts with code
        # only. Saying so is more useful than silently dropping the file.
        det = Detection(file_hash=file_hash, file_type=file_type,
                        document_type=DocumentType.UNKNOWN, confidence=0.0,
                        has_text_layer=False, page_count=1)
        det.issues.append(
            "images cannot be read: this pipeline extracts with code only, so a "
            "bill or receipt has to be a PDF with a text layer"
        )
        return det

    sample = read_text_sample(path, file_type)
    det = classify_text(sample, file_type=file_type)
    det.file_hash = file_hash
    if det.document_type is DocumentType.UNKNOWN and file_type in TABULAR_TYPES:
        # a plain sheet with no bank metadata is the user's own expense tracker
        det.document_type = DocumentType.SPREADSHEET
        det.confidence = 0.4
    return det


def read_text_sample(path: Path, file_type: str, rows: int = _SAMPLE_ROWS) -> str:
    """The first ``rows`` lines, as text, whatever the container."""
    if file_type in (XLSX, XLS):
        return _excel_sample(path, rows)
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            return "".join(next(fh, "") for _ in range(rows))
    except OSError:
        return ""


def _excel_sample(path: Path, rows: int) -> str:
    try:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        lines = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= rows:
                break
            lines.append(",".join("" if v is None else str(v) for v in row))
        wb.close()
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""
