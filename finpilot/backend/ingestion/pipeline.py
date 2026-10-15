"""The orchestrator.

Phase A runs per file: detect, extract, map, normalise, enrich, validate.
Phase B runs across files: merge, link, write. This module owns the ordering
and the bookkeeping; the thinking lives in the modules it calls.

The one rule worth stating here is the order of masking. Narration parsing
needs the *unmasked* text -- the VPA local part is what distinguishes a shop
from a person -- so the sequence is always: read the raw cell, parse it, then
mask, then store. Nothing unmasked reaches staging, the ledger, an export, or a
model prompt (principle 6).
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import get_settings
from ingestion.detect import Detection, detect
from ingestion.extractors.tabular import RawRow, TabularExtraction, extract_table
from ingestion.mapping import (
    ProposedMapping,
    Template,
    TemplateLibrary,
    propose_mapping,
    template_from_mapping,
)
from ingestion.merchants import MerchantResolver, default_resolver
from ingestion.narration import ParsedNarration, channel_for, parse_narration
from ingestion.normalize import (
    clean_text,
    is_blank,
    mask_text,
    parse_amount,
    parse_amount_with_direction,
    parse_date,
    parse_time,
    signed_to_direction,
    split_debit_credit,
)
from ingestion.schema import (
    METHOD_CONFIDENCE,
    AccountType,
    CounterpartyType,
    Direction,
    DocumentType,
    ExtractionMethod,
    PaymentMode,
    SourceType,
    Transaction,
    make_txn_id,
)
from ingestion.validate import (
    ERROR,
    WARNING,
    Issue,
    Reconciliation,
    validate_document,
    validate_frame,
    validate_row,
)

# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------

_DOC_TO_SOURCE = {
    DocumentType.BANK_STATEMENT.value: SourceType.BANK_STATEMENT.value,
    DocumentType.CC_STATEMENT.value: SourceType.CC_STATEMENT.value,
    DocumentType.SPREADSHEET.value: SourceType.SPREADSHEET.value,
    DocumentType.EMAIL.value: SourceType.EMAIL.value,
    DocumentType.RECEIPT.value: SourceType.RECEIPT.value,
}

_DOC_TO_ACCOUNT_TYPE = {
    DocumentType.BANK_STATEMENT.value: AccountType.BANK.value,
    DocumentType.CC_STATEMENT.value: AccountType.CREDIT_CARD.value,
    DocumentType.SPREADSHEET.value: AccountType.CASH.value,
}

_SHEET_MODES = {
    "cash": PaymentMode.CASH.value,
    "upi": PaymentMode.UPI.value,
    "card": PaymentMode.CARD.value,
    "credit card": PaymentMode.CARD.value,
    "debit card": PaymentMode.CARD.value,
    "neft": PaymentMode.NEFT.value,
    "imps": PaymentMode.IMPS.value,
    "netbanking": PaymentMode.NEFT.value,
    "net banking": PaymentMode.NEFT.value,
    "bank transfer": PaymentMode.NEFT.value,
    "direct deposit": PaymentMode.ACH.value,
    "direct debit": PaymentMode.ACH.value,
    "auto debit": PaymentMode.ACH.value,
    "gpay": PaymentMode.UPI.value,
    "phonepe": PaymentMode.UPI.value,
    "paytm": PaymentMode.UPI.value,
    "wallet": PaymentMode.OTHER.value,
    "cheque": PaymentMode.CHEQUE.value,
}


@dataclass
class DocumentContext:
    """Everything a row needs that comes from the document rather than the row."""

    doc_id: str
    filename: str
    document_type: str
    source_type: str
    account: str
    account_type: str
    extraction_method: str
    issuer: str | None = None
    account_holder: str | None = None
    narration_dialect: str = "generic"
    card_last4: str | None = None
    currency: str = "INR"
    statement_start: dt.date | None = None
    statement_end: dt.date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    dayfirst: bool = True

    @property
    def is_statement(self) -> bool:
        return self.source_type in (
            SourceType.BANK_STATEMENT.value,
            SourceType.CC_STATEMENT.value,
        )

    @property
    def is_row_source(self) -> bool:
        """Documents expected to yield ledger rows.

        Emails and bills contribute links and obligations instead, so having no
        transactions of their own is normal for them, not notable.
        """
        return self.source_type in (
            SourceType.BANK_STATEMENT.value,
            SourceType.CC_STATEMENT.value,
            SourceType.SPREADSHEET.value,
        )


@dataclass
class DocumentResult:
    context: DocumentContext
    transactions: list[Transaction] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    reconciliation: Reconciliation | None = None
    staging: list[dict[str, Any]] = field(default_factory=list)
    template_name: str | None = None
    template_match: str = "none"
    pending_mapping: ProposedMapping | None = None
    detection: Detection | None = None

    @property
    def status(self) -> str:
        from ingestion.schema import DocumentStatus

        if self.pending_mapping is not None:
            return DocumentStatus.NEEDS_MAPPING.value
        if self.detection is not None and self.detection.needs_password:
            return DocumentStatus.NEEDS_PASSWORD.value
        if any(i.severity == ERROR for i in self.issues) and not self.transactions:
            return DocumentStatus.FAILED.value
        if any(i.severity == ERROR for i in self.issues) or any(
            t.needs_review for t in self.transactions
        ):
            return DocumentStatus.NEEDS_REVIEW.value
        if not self.transactions and self.context.is_row_source:
            # nothing was extracted and nothing went wrong: the file simply is
            # not a list of transactions
            return DocumentStatus.NO_TRANSACTIONS.value
        return DocumentStatus.PROCESSED.value

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == WARNING)


def context_from_detection(
    detection: Detection,
    *,
    doc_id: str,
    filename: str,
    template: Template | None = None,
    extraction_method: str = ExtractionMethod.TEMPLATE.value,
) -> DocumentContext:
    document_type = detection.document_type.value if detection.document_type else "unknown"
    source_type = _DOC_TO_SOURCE.get(document_type, SourceType.SPREADSHEET.value)
    account_type = _DOC_TO_ACCOUNT_TYPE.get(document_type, AccountType.BANK.value)
    account = detection.account or _fallback_account(detection, account_type)
    return DocumentContext(
        doc_id=doc_id,
        filename=filename,
        document_type=document_type,
        source_type=source_type,
        account=account,
        account_type=account_type,
        extraction_method=extraction_method,
        issuer=detection.issuer,
        account_holder=detection.account_holder,
        narration_dialect=(template.narration_dialect if template else "generic"),
        card_last4=(detection.account_number or "")[-4:] if detection.is_card else None,
        statement_start=detection.statement_start,
        statement_end=detection.statement_end,
        opening_balance=detection.opening_balance,
        closing_balance=detection.closing_balance,
        dayfirst=template.dayfirst if template else True,
    )


def _fallback_account(detection: Detection, account_type: str) -> str:
    if account_type == AccountType.CASH.value:
        return "CASH"
    from ingestion.normalize import issuer_code

    return f"{issuer_code(detection.issuer)}-XXXX"


# --------------------------------------------------------------------------
# row normalisation
# --------------------------------------------------------------------------


@dataclass
class _RowDraft:
    payload: dict[str, Any]
    identity: tuple
    source_ref: str


def normalize_rows(
    extraction: TabularExtraction,
    template: Template,
    ctx: DocumentContext,
    *,
    resolver: MerchantResolver | None = None,
) -> tuple[list[Transaction], list[Issue], list[dict[str, Any]]]:
    """Raw rows -> validated :class:`Transaction` objects.

    Returns ``(transactions, issues, staging)``. Staging holds the raw cells
    with narration-bearing values already masked, which is what the mapping
    dialog previews.
    """
    resolver = resolver or default_resolver()
    issues: list[Issue] = []
    drafts: list[_RowDraft] = []
    staging: list[dict[str, Any]] = []

    for raw in extraction.rows:
        staging.append(
            {
                "source_ref": raw.source_ref,
                "row_index": raw.index,
                "page": raw.page,
                "payload": {k: mask_text(v) for k, v in raw.values.items()},
            }
        )
        draft, row_issues = _draft_row(raw, template, ctx, resolver)
        issues.extend(row_issues)
        if draft is not None:
            drafts.append(draft)

    _assign_occurrences(drafts)

    transactions: list[Transaction] = []
    for draft in drafts:
        txn, issue = validate_row(draft.payload)
        if issue is not None:
            issues.append(issue)
            continue
        transactions.append(txn)
    return transactions, issues, staging


def _assign_occurrences(drafts: list[_RowDraft]) -> None:
    """Two rows identical in every hashed field get 0, 1, 2...

    Assigned by position, which is stable: the same statement re-uploaded, or
    the overlapping half of a second statement, lists those rows in the same
    order, so the ids agree.
    """
    seen: dict[tuple, int] = {}
    for draft in drafts:
        n = seen.get(draft.identity, 0)
        seen[draft.identity] = n + 1
        if n:
            draft.payload["occurrence"] = n
        draft.payload["txn_id"] = make_txn_id(
            account=draft.payload["account"],
            date=draft.payload["date"],
            amount=draft.payload["amount"],
            direction=draft.payload["direction"],
            reference_no=draft.payload.get("reference_no"),
            raw_description=draft.payload.get("raw_description"),
            occurrence=n,
        )
        draft.payload.pop("occurrence", None)


def _draft_row(
    raw: RawRow,
    template: Template,
    ctx: DocumentContext,
    resolver: MerchantResolver,
) -> tuple[_RowDraft | None, list[Issue]]:
    issues: list[Issue] = []
    cols = template.columns

    date = parse_date(raw.get(cols.get("date")), dayfirst=template.dayfirst)
    if date is None:
        return None, [
            Issue(
                code="unparseable_date",
                severity=ERROR,
                message=f"no usable date in {raw.get(cols.get('date'))!r}",
                source_ref=raw.source_ref,
            )
        ]

    money = _read_amount(raw, template)
    if money is None:
        return None, [
            Issue(
                code="unparseable_amount",
                severity=ERROR,
                message="row has no amount in any mapped amount column",
                source_ref=raw.source_ref,
            )
        ]
    amount, direction = money

    raw_description = clean_text(raw.get(cols.get("description")))
    note = clean_text(raw.get(cols.get("note")))
    row_account = clean_text(raw.get(cols.get("account"))) or ctx.account
    if note and note not in raw_description:
        raw_description = f"{raw_description} ({note})" if raw_description else note

    payload: dict[str, Any] = {
        "txn_id": "",  # filled by _assign_occurrences
        "date": date,
        "value_date": parse_date(raw.get(cols.get("value_date")), dayfirst=template.dayfirst),
        "amount": amount,
        "direction": direction,
        "currency": clean_text(raw.get(cols.get("currency"))) or ctx.currency,
        "account": row_account,
        "account_type": ctx.account_type,
        "source_type": ctx.source_type,
        "source_ref": raw.source_ref,
        "balance_after": parse_amount(raw.get(cols.get("balance_after"))),
        "extraction_method": ctx.extraction_method,
        "extraction_confidence": METHOD_CONFIDENCE.get(ctx.extraction_method, 0.8),
    }
    # an MCC is a 4-digit code; a "Category" column of words is not one
    if (mcc := clean_text(raw.get(cols.get("mcc")))) and mcc.isdigit():
        payload["mcc"] = mcc
    if (time := parse_time(raw.get(cols.get("time")))) :
        payload["time"] = time

    explicit_ref = clean_text(raw.get(cols.get("reference_no"))) or None

    if ctx.source_type == SourceType.SPREADSHEET.value:
        _apply_sheet_fields(payload, raw, template, ctx)
    else:
        _apply_narration_fields(payload, raw_description, ctx, resolver)

    payload["reference_no"] = explicit_ref or payload.get("reference_no")
    # the narration is stored masked; parsing above ran on the raw text
    payload["raw_description"] = mask_text(raw_description)
    if ctx.card_last4 and not payload.get("card_last4"):
        payload["card_last4"] = ctx.card_last4
    payload["linked_sources"] = raw.source_ref

    identity = (
        payload["account"],
        payload["date"],
        payload["amount"],
        payload["direction"],
        (payload.get("reference_no") or payload["raw_description"] or "").upper(),
    )
    return _RowDraft(payload=payload, identity=identity, source_ref=raw.source_ref), issues


def _read_amount(raw: RawRow, template: Template) -> tuple[Decimal, str] | None:
    """Apply whichever of the three amount conventions this layout uses."""
    cols = template.columns
    style = template.amount_style

    if style == "debit_credit":
        return split_debit_credit(raw.get(cols.get("debit")), raw.get(cols.get("credit")))

    value = raw.get(cols.get("amount"))
    if is_blank(value):
        # some layouts declare one style but still split a few rows
        return split_debit_credit(raw.get(cols.get("debit")), raw.get(cols.get("credit")))

    if style == "dr_cr_suffix":
        amount, direction = parse_amount_with_direction(value)
        if amount is None:
            return None
        return abs(amount), direction or Direction.DEBIT.value

    if style == "dr_cr_flag":
        amount = parse_amount(value)
        if amount is None:
            return None
        flag = clean_text(raw.get(cols.get("dr_cr_flag"))).lower()
        if flag.startswith(("c", "cr")):
            return abs(amount), Direction.CREDIT.value
        if flag.startswith(("d", "dr", "w")):
            return abs(amount), Direction.DEBIT.value
        return signed_to_direction(amount)

    amount = parse_amount(value)
    if amount is None:
        return None
    return signed_to_direction(amount)


def _apply_narration_fields(
    payload: dict[str, Any],
    raw_description: str,
    ctx: DocumentContext,
    resolver: MerchantResolver,
) -> None:
    """Enrich from the bank's narration (step 5)."""
    parsed: ParsedNarration = parse_narration(
        raw_description,
        dialect=ctx.narration_dialect,
        account_holder=ctx.account_holder,
        known_merchant=resolver.is_known,
    )
    payload["payment_mode"] = parsed.payment_mode
    payload["channel"] = parsed.channel
    payload["reference_no"] = parsed.reference_no
    payload["counterparty_type"] = parsed.counterparty_type
    payload["counterparty_upi_handle"] = parsed.counterparty_upi_handle
    payload["upi_remark"] = parsed.upi_remark
    payload["is_fee"] = parsed.is_fee
    payload["is_cash_withdrawal"] = parsed.is_cash_withdrawal
    payload["emi_flag"] = parsed.emi_flag
    if parsed.card_last4:
        payload["card_last4"] = parsed.card_last4
    if parsed.merchant_city:
        payload["merchant_city"] = parsed.merchant_city

    if parsed.original_currency:
        payload["original_currency"] = parsed.original_currency
        payload["fx_amount"] = parsed.fx_amount

    payload["merchant"] = _resolve_merchant(parsed, ctx, resolver)


def _resolve_merchant(
    parsed: ParsedNarration, ctx: DocumentContext, resolver: MerchantResolver
) -> str | None:
    """Who the money went to, as one canonical name.

    Self-dealings have no merchant. Charges and interest have no counterparty
    in the narration either -- the bank never names itself -- so the document's
    issuer supplies it.
    """
    if parsed.counterparty_type == CounterpartyType.SELF.value:
        return None
    if parsed.counterparty_is_issuer:
        return resolver.resolve(ctx.issuer).canonical if ctx.issuer else None
    match = resolver.resolve(
        parsed.counterparty_name,
        vpa=parsed.counterparty_vpa,
        is_person=parsed.counterparty_type == CounterpartyType.PERSON.value,
    )
    return match.canonical


def _apply_sheet_fields(
    payload: dict[str, Any], raw: RawRow, template: Template, ctx: DocumentContext
) -> None:
    """A user's own expense sheet.

    The description column is prose the user typed, not a machine narration, so
    it is not run through the narration parsers -- inventing a merchant from
    "Movie tickets" would be guessing, which is what the counterparty fields
    exist to avoid. Only an explicit merchant column is trusted.
    """
    mode_text = clean_text(raw.get(template.columns.get("payment_mode"))).lower()
    payload["payment_mode"] = _SHEET_MODES.get(mode_text, PaymentMode.CASH.value)
    payload["channel"] = channel_for(payload["payment_mode"])
    payload["counterparty_type"] = CounterpartyType.UNKNOWN.value
    merchant = clean_text(raw.get(template.columns.get("merchant")))
    payload["merchant"] = merchant or None
    # A sheet that names the account each row belongs to is an export from a
    # bank or a money app, not a wallet ledger: those rows sit in that account.
    # A sheet with no account column is the user's own tracker, and its rows
    # keep the document-level default.
    if clean_text(raw.get(template.columns.get("account"))):
        payload["account_type"] = AccountType.BANK.value


# --------------------------------------------------------------------------
# per-document driver
# --------------------------------------------------------------------------


def process_tabular_document(
    path: Path,
    *,
    doc_id: str,
    filename: str | None = None,
    library: TemplateLibrary | None = None,
    resolver: MerchantResolver | None = None,
    detection: Detection | None = None,
    confirmed_template: Template | None = None,
) -> DocumentResult:
    """Phase A for one CSV or spreadsheet."""
    library = library or TemplateLibrary()
    name = filename or path.name
    det = detection or detect(path, original_name=name)

    extraction = extract_table(path, det.file_type, filename=name)
    if not extraction.rows:
        ctx = context_from_detection(det, doc_id=doc_id, filename=name)
        return DocumentResult(
            context=ctx,
            detection=det,
            issues=[
                # a warning, not an error: a budget sheet or a covering letter
                # is a perfectly good file that happens to hold no transactions
                Issue(code="no_transactions", severity=WARNING,
                      message=m or "no transactions were found in this file")
                for m in (extraction.issues or ["no transactions were found in this file"])
            ],
        )

    template = confirmed_template
    match = "confirmed" if confirmed_template else "none"
    if template is None:
        template, match = library.find(extraction.headers)

    pending: ProposedMapping | None = None
    if template is None:
        proposal = propose_mapping(extraction.headers, extraction.sample_rows)
        proposal = _refine_mapping(proposal, extraction)
        if not proposal.is_usable:
            ctx = context_from_detection(det, doc_id=doc_id, filename=name)
            return DocumentResult(
                context=ctx, detection=det, pending_mapping=proposal,
                issues=[
                    Issue(
                        code="mapping_required",
                        severity=WARNING,
                        message="unrecognised column layout; confirm the mapping to continue",
                    )
                ],
            )
        template = template_from_mapping(
            name=f"{det.issuer or 'Unknown'} {Path(name).stem}",
            headers=extraction.headers,
            columns=proposal.columns,
            amount_style=proposal.amount_style,
            issuer=det.issuer,
            document_type=det.document_type.value,
            account_type=_DOC_TO_ACCOUNT_TYPE.get(det.document_type.value, "bank"),
        )
        match = "auto"

    method = (
        ExtractionMethod.SPREADSHEET.value
        if det.document_type is DocumentType.SPREADSHEET
        else ExtractionMethod.TEMPLATE.value
    )
    ctx = context_from_detection(
        det, doc_id=doc_id, filename=name, template=template, extraction_method=method
    )

    transactions, issues, staging = normalize_rows(
        extraction, template, ctx, resolver=resolver
    )
    foreign = sorted({t.currency.upper() for t in transactions
                      if t.currency and t.currency.upper() not in ("INR", "RS", "₹")})
    if foreign:
        issues.append(Issue(
            code="foreign_currency", severity=WARNING,
            message=f"amounts are in {', '.join(foreign)}; FinPilot shows every amount as rupees "
                    "and does not convert",
        ))

    closing = ctx.closing_balance
    if closing is None and transactions and ctx.is_statement:
        # no stated closing balance: the last printed running balance is the
        # next best thing, which still validates the running-balance walk
        closing = transactions[-1].balance_after

    reconciliation, doc_issues = validate_document(
        transactions,
        account_type=ctx.account_type,
        opening_balance=ctx.opening_balance,
        closing_balance=closing,
        statement_start=ctx.statement_start,
        statement_end=ctx.statement_end,
    )
    issues.extend(doc_issues)

    return DocumentResult(
        context=ctx,
        transactions=transactions,
        issues=issues,
        reconciliation=reconciliation,
        staging=staging,
        template_name=template.name,
        template_match=match,
        pending_mapping=pending,
        detection=det,
    )


def process_pdf_document(
    path: Path,
    *,
    doc_id: str,
    filename: str | None = None,
    password: str | None = None,
    library: TemplateLibrary | None = None,
    resolver: MerchantResolver | None = None,
    detection: Detection | None = None,
    confirmed_template: Template | None = None,
) -> DocumentResult:
    """Phase A for a PDF that has a text layer.

    A PDF with no text layer is a scan, which no parser can read; ``detect``
    is what tells the two apart, and a scan never reaches here.
    """
    import pdfplumber

    from ingestion.extractors.pdf_text import (
        extract_pdf_tables,
        read_balances,
        read_summary_table,
    )

    library = library or TemplateLibrary()
    name = filename or path.name
    det = detection or detect(path, password=password, original_name=name)

    if det.needs_password:
        ctx = context_from_detection(det, doc_id=doc_id, filename=name)
        return DocumentResult(
            context=ctx, detection=det,
            issues=[Issue(code="needs_password", severity=WARNING,
                          message="this PDF is password protected")],
        )

    extraction = extract_pdf_tables(path, filename=name, password=password)
    if not extraction.rows:
        ctx = context_from_detection(det, doc_id=doc_id, filename=name)
        return DocumentResult(
            context=ctx, detection=det,
            issues=[Issue(code="no_rows", severity=ERROR,
                          message=m or "no transaction table found")
                    for m in (extraction.issues or ["no transaction table found"])],
        )

    template = confirmed_template
    match = "confirmed" if confirmed_template else "none"
    if template is None:
        template, match = library.find(extraction.headers)
    if template is None:
        proposal = propose_mapping(extraction.headers, extraction.sample_rows)
        proposal = _refine_mapping(proposal, extraction)
        if not proposal.is_usable:
            ctx = context_from_detection(det, doc_id=doc_id, filename=name)
            return DocumentResult(
                context=ctx, detection=det, pending_mapping=proposal,
                issues=[Issue(code="mapping_required", severity=WARNING,
                              message="unrecognised PDF table layout")],
            )
        template = template_from_mapping(
            name=f"{det.issuer or 'Unknown'} {Path(name).stem}",
            headers=extraction.headers,
            columns=proposal.columns,
            amount_style=proposal.amount_style,
            issuer=det.issuer,
            document_type=det.document_type.value,
            account_type=_DOC_TO_ACCOUNT_TYPE.get(det.document_type.value, "bank"),
            narration_dialect="card" if det.is_card else "generic",
        )
        match = "auto"

    ctx = context_from_detection(
        det, doc_id=doc_id, filename=name, template=template,
        extraction_method=ExtractionMethod.PDFPLUMBER.value,
    )

    # Balances live in the page text or a summary box, not the grid.
    if ctx.opening_balance is None or ctx.closing_balance is None:
        found = read_balances(extraction.full_text)
        if not found:
            with pdfplumber.open(path, password=password or "") as pdf:
                found = read_summary_table(pdf.pages[0].extract_tables())
        if ctx.opening_balance is None:
            ctx.opening_balance = found.get("opening_balance")
        if ctx.closing_balance is None:
            ctx.closing_balance = found.get("closing_balance")

    transactions, issues, staging = normalize_rows(
        extraction, template, ctx, resolver=resolver
    )
    reconciliation, doc_issues = validate_document(
        transactions,
        account_type=ctx.account_type,
        opening_balance=ctx.opening_balance,
        closing_balance=ctx.closing_balance,
        statement_start=ctx.statement_start,
        statement_end=ctx.statement_end,
    )
    issues.extend(doc_issues)

    return DocumentResult(
        context=ctx,
        transactions=transactions,
        issues=issues,
        reconciliation=reconciliation,
        staging=staging,
        template_name=template.name,
        template_match=match,
        detection=det,
    )


def frame_issues(transactions: list[Transaction]) -> list[Issue]:
    """Frame-level validation over an assembled ledger."""
    from ingestion.schema import transactions_to_frame

    if not transactions:
        return []
    return validate_frame(transactions_to_frame(transactions))


# --------------------------------------------------------------------------
# routing: which extractor does this file need?
# --------------------------------------------------------------------------


#: A text PDF is either a statement grid or a labelled bill; they are read by
#: different parsers, so detection has to say which.
_BILL_TYPES = (DocumentType.BILL, DocumentType.RECEIPT)


def route(detection: Detection) -> str:
    """``tabular`` / ``pdf_text`` / ``bill_pdf`` / ``email`` / ``unsupported``.

    Everything readable here is read by code. A PDF with no text layer is a
    scan and an image is a photograph; neither can be parsed deterministically,
    so both come back ``unsupported`` and are reported rather than guessed at.
    """
    from ingestion.detect import EMAIL_TYPES, IMAGE_TYPES, PDF, TABULAR_TYPES

    if detection.file_type in EMAIL_TYPES:
        return "email"
    if detection.file_type in IMAGE_TYPES:
        return "unsupported"
    if detection.file_type == PDF:
        if not detection.has_text_layer:
            return "unsupported"
        return "bill_pdf" if detection.document_type in _BILL_TYPES else "pdf_text"
    if detection.file_type in TABULAR_TYPES:
        return "tabular"
    return "unsupported"


# --------------------------------------------------------------------------
# the whole job
# --------------------------------------------------------------------------


@dataclass
class JobResult:
    job_id: str
    documents: list[DocumentResult] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    obligations: list[Any] = field(default_factory=list)
    items: list[Any] = field(default_factory=list)
    links: list[Any] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    duplicates_skipped: list[str] = field(default_factory=list)

    @property
    def needs_review_count(self) -> int:
        return sum(1 for t in self.transactions if t.needs_review)


def run_job(
    paths: list[Path],
    *,
    job_id: str = "job",
    base_dir: Path | None = None,
    passwords: dict[str, str] | None = None,
    confirmed_templates: dict[str, Template] | None = None,
    library: TemplateLibrary | None = None,
    resolver: MerchantResolver | None = None,
    email_extractor: Any = None,
    link_settings: Any = None,
    on_stage: Any = None,
) -> JobResult:
    """Phase A over every file, then phase B across them.

    ``on_stage(stage, progress)`` is called as the pipeline advances, which is
    what the UI stepper polls.
    """
    import datetime as _dt

    from ingestion.detect import sha256_file
    from ingestion.extractors.bills import BillRecord, extract_bill_pdf, extract_bill_table_pdf
    from ingestion.extractors.email_parser import (
        RuleEmailExtractor,
        build_email_records,
        parse_email_file,
    )
    from ingestion.link import (
        LinkSettings,
        latest_statement_end_by_account,
        link_all,
    )
    from ingestion.merge import LinkRegistry, merge_documents
    from ingestion.schema import JobStage, make_doc_id

    library = library or TemplateLibrary()
    resolver = resolver or default_resolver()
    email_extractor = email_extractor or RuleEmailExtractor(resolver=resolver)
    link_settings = link_settings or LinkSettings()
    passwords = passwords or {}
    confirmed_templates = confirmed_templates or {}

    result = JobResult(job_id=job_id)
    email_records: list = []
    bill_records: list[BillRecord] = []
    seen_hashes: dict[str, str] = {}

    def progress(stage: str, fraction: float) -> None:
        if on_stage is not None:
            on_stage(stage, fraction)

    # ---- phase A ------------------------------------------------------
    progress(JobStage.DETECT.value, 0.0)
    work: list[tuple[int, Path, str, str, str, str | None, str]] = []
    for index, path in enumerate(paths):
        # the name is the path relative to the upload root, so a file that
        # arrived inside a folder keeps that folder in its source_ref and stays
        # distinguishable from a same-named file elsewhere
        name = _relative_name(path, base_dir)
        file_hash = sha256_file(path)
        if file_hash in seen_hashes:
            # principle 5: the same bytes twice is a no-op
            result.duplicates_skipped.append(name)
            continue
        seen_hashes[file_hash] = name
        doc_id = make_doc_id(file_hash, job_id)
        password = passwords.get(name)
        det = detect(path, password=password, original_name=name)
        kind = route(det)
        work.append((index, path, name, file_hash, doc_id, password, kind))

    def process_file(item: tuple[int, Path, str, str, str, str | None, str]):
        index, path, name, file_hash, doc_id, password, kind = item
        det = detect(path, password=password, original_name=name)
        if kind == "tabular":
            document = process_tabular_document(
                path, doc_id=doc_id, filename=name, library=library,
                resolver=resolver, detection=det,
                confirmed_template=confirmed_templates.get(doc_id),
            )
            return index, document, [], []
        if kind == "pdf_text":
            document = process_pdf_document(
                path, doc_id=doc_id, filename=name, password=password,
                library=library, resolver=resolver, detection=det,
                confirmed_template=confirmed_templates.get(doc_id),
            )
            return index, document, [], []
        if kind == "email":
            parsed = parse_email_file(path, det.file_type, filename=name)
            records, dropped = build_email_records(parsed, email_extractor, doc_id=doc_id)
            document = _passive_document(
                det, doc_id=doc_id, filename=name,
                method=ExtractionMethod.EMAIL.value,
                extracted=len(records), dropped=len(dropped),
            )
            return index, document, records, []
        if kind == "bill_pdf":
            table_fields, problems = extract_bill_table_pdf(
                path, filename=name, password=password
            )
            if table_fields:
                fields_list = table_fields
            else:
                single, single_problems = extract_bill_pdf(
                    path, filename=name, password=password
                )
                fields_list = [single] if single.amount_due is not None else []
                problems = problems if fields_list else problems + single_problems
            readable = bool(fields_list)
            document = _passive_document(
                det, doc_id=doc_id, filename=name,
                method=ExtractionMethod.PDFPLUMBER.value,
                extracted=len(fields_list), dropped=0 if readable else 1,
            )
            for problem in problems:
                document.issues.append(
                    Issue(code="bill_extraction",
                          severity=WARNING if readable else ERROR,
                          message=problem)
                )
            bills = [
                BillRecord(fields=fields, doc_id=doc_id, source_ref=f"{name}#1:{index + 1}")
                for index, fields in enumerate(fields_list)
            ]
            return index, document, [], bills
        document = _passive_document(
            det, doc_id=doc_id, filename=name, method=None, extracted=0, dropped=0,
        )
        document.issues.append(
            Issue(code="unsupported_file", severity=ERROR,
                  message=f"unsupported file type {det.file_type}")
        )
        return index, document, [], []

    # File extraction is independent; keep the worker count bounded so the
    # API remains responsive and memory stays bounded.
    with ThreadPoolExecutor(max_workers=min(4, max(len(work), 1))) as executor:
        completed = sorted(executor.map(process_file, work), key=lambda item: item[0])
    for index, document, records, bills in completed:
        result.documents.append(document)
        email_records.extend(records)
        bill_records.extend(bills)
        progress(JobStage.EXTRACT.value, (index + 1) / max(len(paths), 1))

    progress(JobStage.VALIDATE.value, 1.0)

    # Merchants the cheap tiers only tidied up get one batched pass through
    # embeddings and then the model. Done here, once, over the distinct
    # leftovers of the whole job rather than per row.
    _refine_merchants(result.documents, resolver)

    # ---- phase B ------------------------------------------------------
    progress(JobStage.MERGE.value, 0.0)
    merged = merge_documents(result.documents)
    result.issues.extend(merged.issues)
    registry = LinkRegistry(merged)

    progress(JobStage.LINK.value, 0.0)
    horizons = latest_statement_end_by_account(result.documents)
    doc_id_by_source = {d.context.filename: d.context.doc_id for d in result.documents}
    as_of = max(horizons.values()) if horizons else _dt.date.today()

    linked = link_all(
        merged.transactions,
        registry=registry,
        email_records=email_records,
        bill_records=bill_records,
        latest_statement_end=horizons,
        doc_id_by_source=doc_id_by_source,
        settings=link_settings,
        resolver=resolver,
        as_of=as_of,
    )

    result.transactions = linked.transactions
    result.obligations = linked.obligations
    result.items = linked.items
    result.links = registry.all()
    result.issues.extend(linked.issues)
    result.issues.extend(frame_issues(result.transactions))
    result.report = build_report(result, merged=merged, linked=linked,
                                 email_count=len(email_records))
    progress(JobStage.DONE.value, 1.0)
    return result


def _refine_mapping(proposal, extraction):
    """Escalate an uncertain mapping to the local embedding tier."""
    if proposal.is_usable:
        return proposal
    from ingestion.mapping import refine_proposal

    return refine_proposal(
        proposal,
        extraction.headers,
        extraction.sample_rows,
        embedding_threshold=get_settings().embedding_auto_map_threshold,
    )


def _refine_merchants(documents: list[DocumentResult], resolver: MerchantResolver) -> None:
    """One batched pass over the names the cheap tiers only tidied up."""
    names = [t.merchant for d in documents for t in d.transactions if t.merchant]
    if not names:
        return
    settings = get_settings()
    resolved = resolver.refine(
        names, embedding_threshold=settings.merchant_embedding_threshold
    )
    if not resolved:
        return
    for document in documents:
        for txn in document.transactions:
            if txn.merchant in resolved:
                txn.merchant = resolved[txn.merchant]



def _relative_name(path: Path, base_dir: Path | None) -> str:
    if base_dir is None:
        return path.name
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _passive_document(
    detection: Detection,
    *,
    doc_id: str,
    filename: str,
    method: str | None,
    extracted: int,
    dropped: int,
) -> DocumentResult:
    """A document that contributes links rather than ledger rows."""
    ctx = context_from_detection(
        detection, doc_id=doc_id, filename=filename,
        extraction_method=method or ExtractionMethod.TEMPLATE.value,
    )
    document = DocumentResult(context=ctx, detection=detection)
    document.staging = []
    document.template_name = None
    document.template_match = "n/a"
    document.reconciliation = None
    document.context.source_type = _DOC_TO_SOURCE.get(
        detection.document_type.value, ctx.source_type
    )
    document.issues = []
    document.transactions = []
    document.extracted_records = extracted  # type: ignore[attr-defined]
    document.dropped_records = dropped  # type: ignore[attr-defined]
    return document


# --------------------------------------------------------------------------
# the ingestion report
# --------------------------------------------------------------------------


def build_report(
    result: JobResult, *, merged: Any, linked: Any, email_count: int
) -> dict[str, Any]:
    """``ingestion_report.json``: per-file stats plus job totals."""
    files: list[dict[str, Any]] = []
    for document in result.documents:
        ctx = document.context
        entry: dict[str, Any] = {
            "doc_id": ctx.doc_id,
            "filename": ctx.filename,
            "document_type": ctx.document_type,
            "issuer": ctx.issuer,
            "account": ctx.account,
            "statement_period": [
                ctx.statement_start.isoformat() if ctx.statement_start else None,
                ctx.statement_end.isoformat() if ctx.statement_end else None,
            ],
            "extraction_method": ctx.extraction_method,
            "template": document.template_name,
            "template_match": document.template_match,
            "rows_extracted": len(document.transactions)
            or getattr(document, "extracted_records", 0),
            "status": document.status,
            "reconciliation": (
                document.reconciliation.as_dict() if document.reconciliation else None
            ),
            "issues": [i.as_dict() for i in document.issues],
        }
        files.append(entry)

    reconciled = [
        d for d in result.documents
        if d.reconciliation is not None and d.reconciliation.checked
    ]
    return {
        "job_id": result.job_id,
        "files": files,
        "totals": {
            "files_processed": len(result.documents),
            "files_skipped_as_duplicates": len(result.duplicates_skipped),
            "rows": len(result.transactions),
            "duplicates_merged": merged.duplicates_merged,
            "transfers_found": linked.stats.transfers_found,
            "card_payments_found": linked.stats.card_payments_found,
            "refunds_found": linked.stats.refunds_found,
            "emails_seen": email_count,
            "emails_linked": linked.stats.emails_linked,
            "emails_added_as_rows": linked.stats.emails_added,
            "emails_unmatched": linked.stats.emails_unmatched,
            "spreadsheet_rows_linked": linked.stats.spreadsheet_linked,
            "spreadsheet_rows_kept": linked.stats.spreadsheet_kept,
            "bills_found": linked.stats.bills_found,
            "bills_paid": linked.stats.bills_paid,
            "links_made": len(result.links),
            "items_extracted": len(result.items),
            "rows_needing_review": result.needs_review_count,
            "reconciliation_pass_rate": (
                round(sum(1 for d in reconciled if d.reconciliation.ok) / len(reconciled), 4)
                if reconciled else None
            ),
        },
    }


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------


def export_job(result: JobResult, out_dir: Path) -> dict[str, Path]:
    """Write ``transactions.csv``, ``obligations.csv`` and the report."""
    import json

    from ingestion.schema import (
        ALL_TRANSACTION_COLUMNS,
        OBLIGATION_COLUMNS,
        frame_to_csv,
        transactions_to_frame,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    transactions_path = out_dir / "transactions.csv"
    obligations_path = out_dir / "obligations.csv"
    report_path = out_dir / "ingestion_report.json"

    frame_to_csv(
        transactions_to_frame(result.transactions), transactions_path, ALL_TRANSACTION_COLUMNS
    )

    import pandas as pd

    obligations = pd.DataFrame.from_records(
        [o.model_dump(mode="json") for o in result.obligations],
        columns=list(OBLIGATION_COLUMNS),
    )
    frame_to_csv(obligations, obligations_path, OBLIGATION_COLUMNS)
    report_path.write_text(json.dumps(result.report, indent=2, default=str), encoding="utf-8")

    return {
        "transactions": transactions_path,
        "obligations": obligations_path,
        "report": report_path,
    }
