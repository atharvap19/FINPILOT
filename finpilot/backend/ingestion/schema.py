"""The ingestion contract.

Everything downstream of ingestion (behaviour features, segmentation, insights,
the agent) reads the shapes defined here, so this module is deliberately the
only place that decides what a FinPilot row looks like.

Three things live here:

* the enums every stage shares,
* the pydantic row models -- ``Transaction``, ``Obligation``, ``Item``,
  ``TxnLink``, ``DocumentRecord`` -- which every extractor must satisfy
  (principle 3: one schema, one gate), and
* the canonical column orders used by the CSV exports, plus the pandera
  DataFrame schemas used for frame-level validation.

Money is always ``Decimal`` and always positive; the sign lives in
``direction``. Columns owned by later stages are declared here as nullable and
left empty by ingestion.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Any

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

# --------------------------------------------------------------------------
# enums
# --------------------------------------------------------------------------


# Every enum below is a ``StrEnum`` so its members compare equal to, and
# serialise as, plain strings -- CSV exports, JSON reports and the SQLite
# columns all store the bare value, and pydantic is configured with
# ``use_enum_values=True`` to match.


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class AccountType(StrEnum):
    BANK = "bank"
    CREDIT_CARD = "credit_card"
    CASH = "cash"
    WALLET = "wallet"


class SourceType(StrEnum):
    BANK_STATEMENT = "bank_statement"
    CC_STATEMENT = "cc_statement"
    EMAIL = "email"
    RECEIPT = "receipt"
    SPREADSHEET = "spreadsheet"


#: When two sources describe the same transaction, the one with the higher
#: precedence wins the conflicting field (spec: statement > email > sheet).
SOURCE_PRECEDENCE: dict[str, int] = {
    SourceType.BANK_STATEMENT.value: 40,
    SourceType.CC_STATEMENT.value: 40,
    SourceType.EMAIL.value: 30,
    SourceType.RECEIPT.value: 20,
    SourceType.SPREADSHEET.value: 10,
}


class CounterpartyType(StrEnum):
    MERCHANT = "merchant"
    PERSON = "person"
    SELF = "self"
    UNKNOWN = "unknown"


class PaymentMode(StrEnum):
    UPI = "UPI"
    CARD = "CARD"
    NEFT = "NEFT"
    IMPS = "IMPS"
    RTGS = "RTGS"
    ACH = "ACH"
    ATM = "ATM"
    CASH = "CASH"
    CHEQUE = "CHEQUE"
    OTHER = "OTHER"


class Channel(StrEnum):
    APP = "app"
    POS = "pos"
    ATM = "atm"
    ONLINE = "online"
    OTHER = "other"


class ExtractionMethod(StrEnum):
    """How a row was read.

    Every value here is a *code* path: the ledger is produced without a model,
    so there is no ``llm_text``/``llm_vision``. A document no parser can read
    -- a scan, a photograph -- produces no rows at all and is reported, rather
    than being guessed at and marked low-confidence.
    """

    TEMPLATE = "template"
    PDFPLUMBER = "pdfplumber"
    SPREADSHEET = "spreadsheet"
    EMAIL = "email"


#: Baseline confidence per extraction method. A parser that read a labelled
#: column in a file the bank generated gets full marks; one that recovered the
#: same fact from prose in an email body gets less. Row-level problems (a
#: reconciliation miss) subtract from this.
METHOD_CONFIDENCE: dict[str, float] = {
    ExtractionMethod.TEMPLATE.value: 1.0,
    ExtractionMethod.SPREADSHEET.value: 1.0,
    ExtractionMethod.PDFPLUMBER.value: 0.98,
    ExtractionMethod.EMAIL.value: 0.90,
}


class DocumentType(StrEnum):
    BANK_STATEMENT = "bank_statement"
    CC_STATEMENT = "cc_statement"
    EMAIL = "email"
    RECEIPT = "receipt"
    SPREADSHEET = "spreadsheet"
    BILL = "bill"
    UNKNOWN = "unknown"


class DocumentStatus(StrEnum):
    """Lifecycle of one uploaded file.

    ``processed`` / ``needs_review`` / ``failed`` are the terminal states the
    ``documents`` table contract names; the rest are transient states the UI
    renders while a job is running.
    """

    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_PASSWORD = "needs_password"
    NEEDS_MAPPING = "needs_mapping"
    PROCESSED = "processed"
    NEEDS_REVIEW = "needs_review"
    #: read fine, but there were no transactions in it -- a budget sheet, a
    #: summary, a covering letter. Not a failure: nothing went wrong and there
    #: is nothing for the user to fix.
    NO_TRANSACTIONS = "no_transactions"
    FAILED = "failed"
    DUPLICATE = "duplicate"


TERMINAL_DOCUMENT_STATUSES = frozenset(
    {
        DocumentStatus.PROCESSED,
        DocumentStatus.NEEDS_REVIEW,
        DocumentStatus.NO_TRANSACTIONS,
        DocumentStatus.FAILED,
        DocumentStatus.DUPLICATE,
    }
)


class ObligationStatus(StrEnum):
    DUE = "due"
    PAID = "paid"
    OVERDUE = "overdue"


class LinkType(StrEnum):
    PRIMARY = "primary"
    EMAIL = "email"
    RECEIPT = "receipt"
    SPREADSHEET = "spreadsheet"
    BILL = "bill"


class JobStage(StrEnum):
    """The pipeline stepper the UI renders."""

    DETECT = "detect"
    EXTRACT = "extract"
    MAP = "map"
    NORMALIZE = "normalize"
    VALIDATE = "validate"
    MERGE = "merge"
    LINK = "link"
    DONE = "done"


JOB_STAGES: tuple[str, ...] = tuple(s.value for s in JobStage)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    DONE = "done"
    FAILED = "failed"


# --------------------------------------------------------------------------
# money helpers
# --------------------------------------------------------------------------

TWO_PLACES = Decimal("0.01")


def quantize_money(value: Decimal | float | int | str) -> Decimal:
    """Round to paise. Every amount stored by ingestion goes through this."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


Money = Annotated[Decimal, Field(ge=0)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


# --------------------------------------------------------------------------
# txn_id
# --------------------------------------------------------------------------

_ID_WS = re.compile(r"\s+")


def _id_norm(text: str | None) -> str:
    """Collapse a narration to its identity form for hashing."""
    if not text:
        return ""
    return _ID_WS.sub(" ", str(text).strip().upper())


def make_txn_id(
    *,
    account: str,
    date: dt.date,
    amount: Decimal,
    direction: str,
    reference_no: str | None = None,
    raw_description: str | None = None,
    occurrence: int = 0,
) -> str:
    """Stable identity for one transaction.

    The tuple is deliberately restricted to the facts that survive merging and
    linking: account, date, amount, direction, and the strongest available
    identifier (the bank reference if there is one, else the narration).

    ``source_type`` is *not* part of the hash even though it is part of the row.
    The same payment seen in a bank statement and again in the overlapping
    statement, or in an order email, has to hash to the same id -- that is what
    makes re-uploading a file a no-op (principle 5) and what lets ``refund_of``
    keep pointing at the right row after precedence merging rewrites
    ``source_type``.

    ``occurrence`` disambiguates genuinely distinct rows that share every other
    field (two identical 100 rupee payments to the same merchant on one day);
    callers assign it by position within the account+date group.
    """
    ident = reference_no.strip().upper() if reference_no and reference_no.strip() else ""
    if not ident:
        ident = _id_norm(raw_description)
    payload = "|".join(
        [
            _id_norm(account),
            date.isoformat(),
            str(quantize_money(amount)),
            str(direction),
            ident,
            str(occurrence),
        ]
    )
    return "t_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def make_doc_id(file_hash: str, scope: str = "") -> str:
    """Build a stable document ID, scoped when persisted in a job."""
    payload = f"{scope}|{file_hash}" if scope else file_hash
    return "d_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def make_bill_id(
    *,
    biller: str,
    consumer_no: str | None,
    bill_date: dt.date | None,
    amount_due: Decimal,
) -> str:
    payload = "|".join(
        [
            _id_norm(biller),
            _id_norm(consumer_no),
            bill_date.isoformat() if bill_date else "",
            str(quantize_money(amount_due)),
        ]
    )
    return "b_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def make_merchant_id(canonical_name: str) -> str:
    return "m_" + hashlib.sha256(_id_norm(canonical_name).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# row models
# --------------------------------------------------------------------------


class FinPilotModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class Transaction(FinPilotModel):
    """One row of the canonical ledger.

    This is the single gate every extractor passes through -- template-mapped
    CSV rows, pdfplumber tables, spreadsheet rows and fields recovered from an
    email body are all validated here before they are allowed anywhere near the
    ledger (principle 3).
    """

    # --- identity -------------------------------------------------------
    txn_id: str
    date: dt.date
    value_date: dt.date | None = None
    time: dt.time | None = None

    # --- money ----------------------------------------------------------
    amount: Money
    direction: Direction
    currency: str = "INR"
    original_currency: str | None = None
    fx_amount: Money | None = None

    # --- provenance -----------------------------------------------------
    account: str
    account_type: AccountType
    source_type: SourceType
    source_ref: str
    raw_description: str = ""
    balance_after: Decimal | None = None  # may be negative on a card account
    reference_no: str | None = None

    # --- counterparty ---------------------------------------------------
    merchant: str | None = None
    counterparty_type: CounterpartyType = CounterpartyType.UNKNOWN
    counterparty_upi_handle: str | None = None

    # --- instrument -----------------------------------------------------
    payment_mode: PaymentMode = PaymentMode.OTHER
    channel: Channel | None = None
    upi_remark: str | None = None
    card_last4: str | None = None
    mcc: str | None = None
    merchant_city: str | None = None

    # --- order / receipt detail ----------------------------------------
    order_id: str | None = None
    item_details: str | None = None
    gst_amount: Money | None = None
    discount: Money | None = None
    delivery_fee: Money | None = None

    # --- flags ----------------------------------------------------------
    emi_flag: bool = False
    is_fee: bool = False
    is_cash_withdrawal: bool = False
    is_transfer: bool = False
    is_refund: bool = False
    refund_of: str | None = None

    # --- lineage --------------------------------------------------------
    linked_sources: str = ""  # pipe-separated source_refs
    extraction_method: ExtractionMethod
    extraction_confidence: Confidence = 1.0
    needs_review: bool = False

    # --- reserved for later stages (ingestion leaves these empty) -------
    is_recurring: bool | None = None
    recurring_id: str | None = None
    counterparty_label: str | None = None
    segment_id: int | None = None
    segment_name: str | None = None
    segment_probability: float | None = None
    category_id: str | None = None
    category: str | None = None

    @field_validator(
        "amount",
        "fx_amount",
        "gst_amount",
        "discount",
        "delivery_fee",
        "balance_after",
        mode="before",
    )
    @classmethod
    def _coerce_money(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        return quantize_money(v)

    @field_validator("currency", "original_currency")
    @classmethod
    def _upper_currency(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @field_validator("card_last4")
    @classmethod
    def _check_last4(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not re.fullmatch(r"\d{4}", v):
            raise ValueError(f"card_last4 must be exactly 4 digits, got {v!r}")
        return v

    @field_validator("counterparty_upi_handle")
    @classmethod
    def _check_handle(cls, v: str | None) -> str | None:
        """Only the handle domain may be stored -- never the full VPA."""
        if v is None or v == "":
            return None
        v = v.strip().lower()
        if not v.startswith("@"):
            v = "@" + v.split("@")[-1]
        if len(v) < 2:
            raise ValueError(f"unusable upi handle {v!r}")
        return v

    @field_serializer(
        "amount", "fx_amount", "gst_amount", "discount", "delivery_fee", "balance_after"
    )
    def _ser_money(self, v: Decimal | None, _info) -> str | None:
        return None if v is None else f"{v:.2f}"

    @property
    def signed_amount(self) -> Decimal:
        """Convenience for later stages: debits negative, credits positive."""
        return -self.amount if self.direction == Direction.DEBIT.value else self.amount


class Obligation(FinPilotModel):
    """A bill: something owed, which may or may not have been paid yet."""

    bill_id: str
    biller: str
    category_hint: str | None = None
    amount_due: Money
    bill_date: dt.date | None = None
    due_date: dt.date | None = None
    billing_period: str | None = None
    consumer_no: str | None = None  # masked
    status: ObligationStatus = ObligationStatus.DUE
    paid_txn_id: str | None = None
    source_ref: str

    @field_validator("amount_due", mode="before")
    @classmethod
    def _coerce_money(cls, v: Any) -> Any:
        return quantize_money(v) if v is not None and v != "" else v

    @field_serializer("amount_due")
    def _ser_money(self, v: Decimal, _info) -> str:
        return f"{v:.2f}"


class Item(FinPilotModel):
    """A line item off a receipt or order email.

    ``doc_id`` is bookkeeping, not part of the exported contract (see
    :data:`ITEM_COLUMNS`): it records which document the line came from, so
    re-ingesting that document can replace its own items without touching the
    items another document contributed to the same transaction.
    """

    txn_id: str
    name: str
    quantity: Decimal | None = None
    unit_price: Money | None = None
    amount: Money | None = None
    doc_id: str | None = None

    @field_validator("quantity", "unit_price", "amount", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        return Decimal(str(v))


class TxnLink(FinPilotModel):
    txn_id: str
    doc_id: str
    source_ref: str
    link_type: LinkType
    match_score: float = 1.0


class DocumentRecord(FinPilotModel):
    doc_id: str
    filename: str
    file_hash: str
    file_type: str
    document_type: DocumentType = DocumentType.UNKNOWN
    issuer: str | None = None
    account: str | None = None
    statement_start: dt.date | None = None
    statement_end: dt.date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    extraction_method: ExtractionMethod | None = None
    status: DocumentStatus = DocumentStatus.QUEUED
    issues: list[dict[str, Any]] = Field(default_factory=list)
    uploaded_at: dt.datetime | None = None


class MerchantRecord(FinPilotModel):
    merchant_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# canonical column orders (the CSV contract)
# --------------------------------------------------------------------------

#: Columns ingestion fills. Order is the export order of ``transactions.csv``.
TRANSACTION_COLUMNS: tuple[str, ...] = (
    "txn_id",
    "date",
    "value_date",
    "time",
    "amount",
    "direction",
    "currency",
    "original_currency",
    "fx_amount",
    "account",
    "account_type",
    "source_type",
    "source_ref",
    "raw_description",
    "balance_after",
    "reference_no",
    "merchant",
    "counterparty_type",
    "counterparty_upi_handle",
    "payment_mode",
    "channel",
    "upi_remark",
    "card_last4",
    "mcc",
    "merchant_city",
    "order_id",
    "item_details",
    "gst_amount",
    "discount",
    "delivery_fee",
    "emi_flag",
    "is_fee",
    "is_cash_withdrawal",
    "is_transfer",
    "is_refund",
    "refund_of",
    "linked_sources",
    "extraction_method",
    "extraction_confidence",
    "needs_review",
)

#: Owned by later stages. Created nullable, exported empty by ingestion.
RESERVED_COLUMNS: tuple[str, ...] = (
    "is_recurring",
    "recurring_id",
    "counterparty_label",
    "segment_id",
    "segment_name",
    "segment_probability",
    "category_id",
    "category",
)

ALL_TRANSACTION_COLUMNS: tuple[str, ...] = TRANSACTION_COLUMNS + RESERVED_COLUMNS

OBLIGATION_COLUMNS: tuple[str, ...] = (
    "bill_id",
    "biller",
    "category_hint",
    "amount_due",
    "bill_date",
    "due_date",
    "billing_period",
    "consumer_no",
    "status",
    "paid_txn_id",
    "source_ref",
)

ITEM_COLUMNS: tuple[str, ...] = ("txn_id", "name", "quantity", "unit_price", "amount")

TXN_LINK_COLUMNS: tuple[str, ...] = (
    "txn_id",
    "doc_id",
    "source_ref",
    "link_type",
    "match_score",
)

#: Fields ingestion is allowed to overwrite when a higher-precedence source
#: disagrees. Identity and lineage fields are never overwritten by precedence.
PRECEDENCE_FIELDS: tuple[str, ...] = (
    "date",
    "value_date",
    "amount",
    "direction",
    "balance_after",
    "reference_no",
    "raw_description",
    "account",
    "account_type",
)

#: Fields a lower-precedence source may still *fill in* when the winner is empty.
ENRICHABLE_FIELDS: tuple[str, ...] = (
    "time",
    "merchant",
    "counterparty_type",
    "counterparty_upi_handle",
    "payment_mode",
    "channel",
    "upi_remark",
    "card_last4",
    "mcc",
    "merchant_city",
    "order_id",
    "item_details",
    "gst_amount",
    "discount",
    "delivery_fee",
    "original_currency",
    "fx_amount",
)

BOOL_COLUMNS: tuple[str, ...] = (
    "emi_flag",
    "is_fee",
    "is_cash_withdrawal",
    "is_transfer",
    "is_refund",
    "needs_review",
)
MONEY_COLUMNS: tuple[str, ...] = (
    "amount",
    "fx_amount",
    "balance_after",
    "gst_amount",
    "discount",
    "delivery_fee",
)
DATE_COLUMNS: tuple[str, ...] = ("date", "value_date")


# --------------------------------------------------------------------------
# pandera frame schemas
# --------------------------------------------------------------------------


class TransactionFrame(pa.DataFrameModel):
    """Frame-level invariants, checked once per DataFrame after normalisation.

    Deliberately looser than :class:`Transaction` on optional columns -- pydantic
    already gated every row; this catches the cross-row problems pydantic
    cannot see (duplicate ids, unparseable dates surviving as objects).
    """

    txn_id: Series[str] = pa.Field(unique=True, nullable=False)
    date: Series[pa.DateTime] = pa.Field(nullable=False, coerce=True)
    amount: Series[float] = pa.Field(ge=0, nullable=False, coerce=True)
    direction: Series[str] = pa.Field(isin=[d.value for d in Direction])
    currency: Series[str] = pa.Field(nullable=False)
    account: Series[str] = pa.Field(nullable=False)
    account_type: Series[str] = pa.Field(isin=[a.value for a in AccountType])
    source_type: Series[str] = pa.Field(isin=[s.value for s in SourceType])
    source_ref: Series[str] = pa.Field(nullable=False)
    counterparty_type: Series[str] = pa.Field(isin=[c.value for c in CounterpartyType])
    payment_mode: Series[str] = pa.Field(isin=[p.value for p in PaymentMode])
    extraction_method: Series[str] = pa.Field(isin=[e.value for e in ExtractionMethod])
    extraction_confidence: Series[float] = pa.Field(ge=0.0, le=1.0, coerce=True)

    class Config:  # type: ignore[override]
        strict = False
        coerce = True

    @pa.check("source_ref", name="source_ref_is_traceable")
    def _source_ref_shape(cls, s: Series[str]) -> Series[bool]:
        """Principle 4: every row points back at a file and a row/page."""
        return s.str.contains("#", regex=False, na=False)


class ObligationFrame(pa.DataFrameModel):
    bill_id: Series[str] = pa.Field(unique=True, nullable=False)
    biller: Series[str] = pa.Field(nullable=False)
    amount_due: Series[float] = pa.Field(ge=0, coerce=True)
    status: Series[str] = pa.Field(isin=[o.value for o in ObligationStatus])
    source_ref: Series[str] = pa.Field(nullable=False)

    class Config:  # type: ignore[override]
        strict = False
        coerce = True


# --------------------------------------------------------------------------
# DataFrame <-> model helpers
# --------------------------------------------------------------------------


def transactions_to_frame(rows: list[Transaction]) -> pd.DataFrame:
    """Canonical DataFrame: all columns present, in contract order."""
    records = [r.model_dump(mode="json") for r in rows]
    df = pd.DataFrame.from_records(records, columns=list(ALL_TRANSACTION_COLUMNS))
    return coerce_frame_dtypes(df)


def empty_transaction_frame() -> pd.DataFrame:
    return coerce_frame_dtypes(pd.DataFrame(columns=list(ALL_TRANSACTION_COLUMNS)))


def coerce_frame_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Force the canonical dtypes. Safe to call on any partially built frame."""
    df = df.copy()
    for col in ALL_TRANSACTION_COLUMNS:
        if col not in df.columns:
            df[col] = None
    for col in DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in MONEY_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in BOOL_COLUMNS:
        df[col] = df[col].fillna(False).astype(bool)
    df["extraction_confidence"] = pd.to_numeric(
        df["extraction_confidence"], errors="coerce"
    ).fillna(1.0)
    df["linked_sources"] = df["linked_sources"].fillna("")
    df["raw_description"] = df["raw_description"].fillna("")
    return df[list(ALL_TRANSACTION_COLUMNS)]


def frame_to_csv(df: pd.DataFrame, path, columns: tuple[str, ...]) -> None:
    """Write an export CSV with ISO dates and money at two decimals."""
    out = df.copy()
    for col in ("date", "value_date", "bill_date", "due_date"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in MONEY_COLUMNS + ("amount_due",):
        if col in out.columns:
            out[col] = out[col].map(lambda v: "" if pd.isna(v) else f"{Decimal(str(v)):.2f}")
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    out = out[list(columns)]
    out.to_csv(path, index=False, na_rep="", encoding="utf-8")


def validate_transaction_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Run the pandera schema. Returns (frame, list of human-readable errors).

    Never raises and never drops rows -- a failure becomes an issue on the
    document and the rows are flagged ``needs_review`` by the caller.
    """
    if df.empty:
        return df, []
    try:
        validated = TransactionFrame.validate(df, lazy=True)
        return validated, []
    except pa.errors.SchemaErrors as exc:
        errors: list[str] = []
        for _, row in exc.failure_cases.iterrows():
            errors.append(
                f"{row.get('column')}: {row.get('check')} failed for {row.get('failure_case')!r}"
            )
        return df, errors[:50]
