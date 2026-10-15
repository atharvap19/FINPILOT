"""SQLAlchemy 2 models -- the persistent shape of the ingestion contract.

These mirror :mod:`ingestion.schema` one-for-one. The pydantic models are the
in-flight representation (what extractors produce and what gets validated);
these are the at-rest representation. Money is stored as ``Numeric(14, 2)`` so
SQLite hands back ``Decimal`` rather than float.

Later stages own the columns in the "reserved" block on
:class:`TransactionRow`; ingestion creates them nullable and leaves them NULL.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Time,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON, list[dict[str, Any]]: JSON}


def _money(**kw: Any) -> Mapped[Decimal | None]:
    return mapped_column(Numeric(14, 2), **kw)


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


class Job(Base):
    """One upload batch. Drives the UI stepper and the ingestion report."""

    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: who uploaded it; NULL only for jobs created before accounts existed
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    #: when the whole upload is erased (see app.retention); NULL = no expiry
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    #: None until the user answers "delete now / keep for 30 days"; then "keep"
    retention_choice: Mapped[str | None] = mapped_column(String(12), nullable=True)
    extensions: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    reminder_7d_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_1d_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(20), default="detect")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC).replace(tzinfo=None)
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    documents: Mapped[list[Document]] = relationship(back_populates="job")


# --------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------


class Document(Base):
    """``documents`` table from the contract, plus the job/file bookkeeping the
    UI needs (stored path, password requirement, pending column mapping)."""

    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.job_id"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    file_type: Mapped[str] = mapped_column(String(64), default="")
    document_type: Mapped[str] = mapped_column(String(32), default="unknown")
    issuer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    statement_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    statement_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    opening_balance: Mapped[Decimal | None] = _money(nullable=True)
    closing_balance: Mapped[Decimal | None] = _money(nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC).replace(tzinfo=None)
    )

    # --- bookkeeping, not part of the exported contract ------------------
    stored_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    #: set when detect() finds an encrypted PDF; cleared once unlocked.
    #: The password itself is never written here or anywhere else (principle 6).
    needs_password: Mapped[bool] = mapped_column(Boolean, default=False)
    #: header fingerprint -> proposed mapping, awaiting user confirmation
    pending_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: set when this upload duplicated an already-ingested file
    duplicate_of: Mapped[str | None] = mapped_column(String(40), nullable=True)

    job: Mapped[Job | None] = relationship(back_populates="documents")


# --------------------------------------------------------------------------
# transactions
# --------------------------------------------------------------------------


class TransactionRow(Base):
    """The canonical ledger."""

    __tablename__ = "transactions"

    txn_id: Mapped[str] = mapped_column(String(40), primary_key=True)

    date: Mapped[dt.date] = mapped_column(Date, index=True)
    value_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    time: Mapped[dt.time | None] = mapped_column(Time, nullable=True)

    amount: Mapped[Decimal] = _money(nullable=False)
    direction: Mapped[str] = mapped_column(String(8), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    original_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    fx_amount: Mapped[Decimal | None] = _money(nullable=True)

    account: Mapped[str] = mapped_column(String(64), index=True)
    account_type: Mapped[str] = mapped_column(String(16))
    source_type: Mapped[str] = mapped_column(String(24))
    source_ref: Mapped[str] = mapped_column(String(512))
    raw_description: Mapped[str] = mapped_column(String, default="")
    balance_after: Mapped[Decimal | None] = _money(nullable=True)
    reference_no: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    merchant: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    counterparty_type: Mapped[str] = mapped_column(String(16), default="unknown")
    counterparty_upi_handle: Mapped[str | None] = mapped_column(String(32), nullable=True)

    payment_mode: Mapped[str] = mapped_column(String(12), default="OTHER")
    channel: Mapped[str | None] = mapped_column(String(12), nullable=True)
    upi_remark: Mapped[str | None] = mapped_column(String(256), nullable=True)
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    mcc: Mapped[str | None] = mapped_column(String(8), nullable=True)
    merchant_city: Mapped[str | None] = mapped_column(String(64), nullable=True)

    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    item_details: Mapped[str | None] = mapped_column(String, nullable=True)
    gst_amount: Mapped[Decimal | None] = _money(nullable=True)
    discount: Mapped[Decimal | None] = _money(nullable=True)
    delivery_fee: Mapped[Decimal | None] = _money(nullable=True)

    emi_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fee: Mapped[bool] = mapped_column(Boolean, default=False)
    is_cash_withdrawal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    is_refund: Mapped[bool] = mapped_column(Boolean, default=False)
    refund_of: Mapped[str | None] = mapped_column(String(40), nullable=True)

    linked_sources: Mapped[str] = mapped_column(String, default="")
    extraction_method: Mapped[str] = mapped_column(String(16))
    extraction_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # --- reserved for later stages --------------------------------------
    is_recurring: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recurring_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    counterparty_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    segment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    segment_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    segment_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    category_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_txn_account_date", "account", "date"),
        Index("ix_txn_date_amount", "date", "amount"),
    )


# --------------------------------------------------------------------------
# obligations / links / items / merchants
# --------------------------------------------------------------------------


class ObligationRow(Base):
    __tablename__ = "obligations"

    bill_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: the job that last described this bill; NULL for bills from before
    #: accounts, which are then matched to a job by filename
    job_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    biller: Mapped[str] = mapped_column(String(128), index=True)
    category_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_due: Mapped[Decimal] = _money(nullable=False)
    bill_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    billing_period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consumer_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="due", index=True)
    paid_txn_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_ref: Mapped[str] = mapped_column(String(512))


class TxnLinkRow(Base):
    """Every document that contributed to a transaction, primary source first.

    The natural key *is* the primary key. "This row was described by that
    source, in that role" is a fact that is either true or not -- recording it
    twice is meaningless, and a surrogate id made a re-upload of a corrected
    file collide with the link the first upload had already written.
    """

    __tablename__ = "txn_links"

    txn_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_ref: Mapped[str] = mapped_column(String(512), primary_key=True)
    link_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(40), index=True)
    match_score: Mapped[float] = mapped_column(Float, default=1.0)

    __table_args__ = (Index("ix_txn_links_txn", "txn_id"),)


class ItemRow(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_id: Mapped[str] = mapped_column(String(40), index=True)
    #: which document contributed this line, so a re-ingest replaces only its own
    doc_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit_price: Mapped[Decimal | None] = _money(nullable=True)
    amount: Mapped[Decimal | None] = _money(nullable=True)


class MerchantRow(Base):
    """Merchant dictionary, grown by the resolver and reused across jobs."""

    __tablename__ = "merchants"

    merchant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)


class StagingRow(Base):
    """Raw extracted rows, values unchanged, kept for traceability and for the
    column-mapping dialog (the UI previews the first 10 rows from here)."""

    __tablename__ = "staging_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(40), index=True)
    source_ref: Mapped[str] = mapped_column(String(512))
    row_index: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ColumnTemplate(Base):
    """A learned tabular layout, keyed by header fingerprint.

    Shipped templates live in ``ingestion/templates/*.json``; templates a user
    confirms in the mapping dialog are written both here and back out to that
    directory so they survive a fresh database.
    """

    __tablename__ = "column_templates"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    issuer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    document_type: Mapped[str] = mapped_column(String(32), default="bank_statement")
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(dt.UTC).replace(tzinfo=None)
    )


# --------------------------------------------------------------------------
# accounts, usage and billing
# --------------------------------------------------------------------------


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class User(Base):
    """One person. Owns jobs; carries the plan they are on."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    #: scrypt, ``salt$hash`` in hex; never the password
    password_hash: Mapped[str] = mapped_column(String(256))
    #: "free" or "pro". Pro also needs ``pro_until`` in the future.
    plan: Mapped[str] = mapped_column(String(16), default="free")
    pro_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    #: what the user agreed to at sign-up (DPDP: consent must be provable)
    consent_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    consented_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    age_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class PasswordReset(Base):
    """A single-use, short-lived reset link. Only the token's hash is stored."""

    __tablename__ = "password_resets"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class AuthSession(Base):
    """A signed-in browser. The cookie holds a random token; only its SHA-256
    is stored, so a leaked database does not hand out sessions."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


class UsageEvent(Base):
    """One metered action: a chat answer, a report, an uploaded file.

    Monthly plan limits are counted from here, and the model token counts are
    kept alongside so cost per user can be measured."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    job_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


class Payment(Base):
    """A checkout: created when the user picks a plan, paid once the
    provider's signature checks out."""

    __tablename__ = "payments"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    plan_code: Mapped[str] = mapped_column(String(32))
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(12), default="created", index=True)
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
