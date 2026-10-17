"""Request and response bodies for the REST API.

Deliberately separate from :mod:`ingestion.schema`: that module is the ledger
contract and must not change shape because a screen wants a different field.
These are the view models, and they are what the frontend's TypeScript types
mirror.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


class FileStatus(ApiModel):
    doc_id: str
    filename: str
    status: str
    document_type: str = "unknown"
    issuer: str | None = None
    account: str | None = None
    statement_start: dt.date | None = None
    statement_end: dt.date | None = None
    extraction_method: str | None = None
    rows: int = 0
    needs_password: bool = False
    needs_mapping: bool = False
    issues: list[dict[str, Any]] = Field(default_factory=list)
    reconciliation: dict[str, Any] | None = None


class JobStatusResponse(ApiModel):
    job_id: str
    status: str
    stage: str
    #: 0.0 - 1.0 across the whole pipeline, for the stepper
    progress: float = 0.0
    stages: list[str] = Field(default_factory=list)
    files: list[FileStatus] = Field(default_factory=list)
    error: str | None = None
    created_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    totals: dict[str, Any] | None = None
    #: expires_at, days_left, choice, extensions (app.retention.view)
    retention: dict[str, Any] | None = None

    @property
    def blocked(self) -> bool:
        return any(f.needs_password or f.needs_mapping for f in self.files)


class JobCreatedResponse(ApiModel):
    job_id: str
    files: list[FileStatus] = Field(default_factory=list)
    skipped_duplicates: list[str] = Field(default_factory=list)


class PasswordRequest(ApiModel):
    #: Used to unlock the PDF for this job only. Never persisted (principle 6).
    password: str = Field(min_length=1, max_length=256)


# --------------------------------------------------------------------------
# column mapping
# --------------------------------------------------------------------------


class ColumnSuggestion(ApiModel):
    header: str
    field_name: str
    confidence: float
    reason: str = ""


class PendingMapping(ApiModel):
    doc_id: str
    filename: str
    headers: list[str]
    sample_rows: list[list[str]]
    suggestions: list[ColumnSuggestion]
    amount_style: str
    uncertain: list[str] = Field(default_factory=list)
    available_fields: list[dict[str, str]] = Field(default_factory=list)
    amount_styles: list[str] = Field(default_factory=list)


class ConfirmMappingRequest(ApiModel):
    #: canonical field name -> header text
    columns: dict[str, str]
    amount_style: str = "debit_credit"
    #: save as a reusable template so the next file of this shape is free
    save_as_template: bool = True
    template_name: str | None = None


# --------------------------------------------------------------------------
# the preview table
# --------------------------------------------------------------------------


class TransactionRowView(ApiModel):
    txn_id: str
    date: dt.date
    time: dt.time | None = None
    amount: Decimal
    direction: str
    currency: str = "INR"
    account: str
    account_type: str
    source_type: str
    source_ref: str
    raw_description: str = ""
    merchant: str | None = None
    counterparty_type: str = "unknown"
    payment_mode: str = "OTHER"
    channel: str | None = None
    balance_after: Decimal | None = None
    reference_no: str | None = None
    order_id: str | None = None
    item_details: str | None = None
    card_last4: str | None = None
    is_transfer: bool = False
    is_refund: bool = False
    is_fee: bool = False
    is_cash_withdrawal: bool = False
    emi_flag: bool = False
    needs_review: bool = False
    linked_sources: str = ""
    extraction_method: str
    extraction_confidence: float = 1.0
    #: filled by the segmentation stage; null until it has been run, and null
    #: on a row no segment claimed
    segment_id: int | None = None
    segment_name: str | None = None
    segment_probability: float | None = None

    @property
    def source_badges(self) -> list[str]:
        return sorted({ref.split("#")[0].split("/")[0] for ref in self.linked_sources.split("|") if ref})


class PreviewResponse(ApiModel):
    rows: list[TransactionRowView]
    total: int
    limit: int
    offset: int
    #: distinct values, so the UI can build its filter dropdowns
    accounts: list[str] = Field(default_factory=list)
    payment_modes: list[str] = Field(default_factory=list)


class LinkedSource(ApiModel):
    source_ref: str
    link_type: str
    match_score: float
    doc_id: str
    filename: str | None = None
    document_type: str | None = None


class ItemView(ApiModel):
    name: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class TransactionDetail(ApiModel):
    transaction: TransactionRowView
    links: list[LinkedSource] = Field(default_factory=list)
    items: list[ItemView] = Field(default_factory=list)
    #: the raw extracted cells this row came from, masked
    raw_values: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# obligations and the report
# --------------------------------------------------------------------------


class ObligationView(ApiModel):
    bill_id: str
    biller: str
    category_hint: str | None = None
    amount_due: Decimal
    bill_date: dt.date | None = None
    due_date: dt.date | None = None
    billing_period: str | None = None
    consumer_no: str | None = None
    status: str
    paid_txn_id: str | None = None
    source_ref: str


# --------------------------------------------------------------------------
# segmentation
# --------------------------------------------------------------------------


class SegmentView(ApiModel):
    segment_id: int
    name: str | None = None
    rationale: str = ""
    #: "llm" when a model named it, "rules" when the profile did
    named_by: str | None = None
    size: int = 0
    share_of_rows: float = 0.0
    median_amount: float = 0.0
    total_amount: float = 0.0
    mean_membership: float = 0.0
    #: the aggregate profile the name was derived from, for the detail panel
    profile: dict[str, Any] | None = None


class SegmentSlice(ApiModel):
    """One segment's share, for the pie."""

    segment_id: int
    name: str
    size: int
    total_amount: float


class SegmentMonth(ApiModel):
    """One month's composition, for the stacked bar."""

    month: str  # YYYY-MM
    counts: dict[str, int] = Field(default_factory=dict)  # segment_id -> rows


class SegmentPoint(ApiModel):
    """One transaction, for the scatter. Deliberately minimal."""

    date: dt.date
    amount: float
    segment_id: int | None = None


class SegmentChartData(ApiModel):
    slices: list[SegmentSlice] = Field(default_factory=list)
    months: list[SegmentMonth] = Field(default_factory=list)
    points: list[SegmentPoint] = Field(default_factory=list)
    unsegmented_rows: int = 0
    #: true when `points` was thinned to stay a sane payload
    points_sampled: bool = False


class SegmentationResponse(ApiModel):
    rows: int = 0
    segments: int = 0
    #: rows no segment claimed -- one-offs, not a habit
    unsegmented_rows: int = 0
    named_by_model: int = 0
    named_by_rules: int = 0
    detail: list[SegmentView] = Field(default_factory=list)


class JobListItem(ApiModel):
    job_id: str
    status: str
    created_at: dt.datetime | None = None
    files: int = 0
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    expires_at: dt.datetime | None = None


class ChatTurn(ApiModel):
    role: str
    content: str


class SummaryResponse(ApiModel):
    """What the summary card shows."""

    files_processed: int = 0
    rows: int = 0
    duplicates_merged: int = 0
    transfers_found: int = 0
    card_payments_found: int = 0
    refunds_found: int = 0
    emails_linked: int = 0
    emails_added_as_rows: int = 0
    spreadsheet_rows_linked: int = 0
    bills_found: int = 0
    bills_paid: int = 0
    links_made: int = 0
    rows_needing_review: int = 0
    reconciliation_pass_rate: float | None = None
    reconciliations: list[dict[str, Any]] = Field(default_factory=list)
