"""Job endpoints: upload, poll, unblock, preview, export."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import billing
from app import jobs as job_service
from app.api.schemas import (
    ColumnSuggestion,
    ConfirmMappingRequest,
    FileStatus,
    ItemView,
    JobCreatedResponse,
    JobListItem,
    JobStatusResponse,
    LinkedSource,
    ObligationView,
    PasswordRequest,
    PendingMapping,
    PreviewResponse,
    SummaryResponse,
    TransactionDetail,
    TransactionRowView,
)
from app.auth import current_user, job_access
from app.config import get_settings
from app.db.models import Document, ItemRow, Job, StagingRow, TransactionRow, TxnLinkRow, User
from app.db.session import get_db
from app.retention import view as retention_view
from ingestion.mapping import CANONICAL_FIELDS
from ingestion.schema import JOB_STAGES, DocumentStatus

router = APIRouter(prefix="/api", tags=["jobs"], dependencies=[Depends(job_access)])

AMOUNT_STYLES = ["debit_credit", "signed", "dr_cr_suffix", "dr_cr_flag"]


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------


@router.post("/jobs", response_model=JobCreatedResponse, status_code=201)
async def create_job(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> JobCreatedResponse:
    """Accept a multipart upload and start processing it in the background."""
    settings = get_settings()
    if not files:
        raise HTTPException(status_code=400, detail="no files were uploaded")

    limit = settings.max_upload_mb * 1024 * 1024
    uploads: list[job_service.UploadedFile] = []
    for upload in files:
        content = await upload.read()
        if len(content) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename} is larger than {settings.max_upload_mb} MB",
            )
        if not content:
            continue
        uploads.append(
            job_service.UploadedFile(filename=upload.filename or "upload", content=content)
        )
    if not uploads:
        raise HTTPException(status_code=400, detail="every uploaded file was empty")

    billing.enforce(db, user, "file", len(uploads))
    job_id = job_service.create_job(uploads, user_id=user.user_id)
    billing.record(db, user.user_id, "file", quantity=len(uploads), job_id=job_id)
    job_service.start(job_id)

    db.expire_all()
    documents = job_service.job_documents(db, job_id)
    return JobCreatedResponse(
        job_id=job_id,
        files=[_file_status(d) for d in documents],
    )


@router.get("/jobs", response_model=list[JobListItem])
def list_jobs(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> list[JobListItem]:
    """The signed-in user's uploads, newest first."""
    jobs = db.scalars(
        select(Job).where(Job.user_id == user.user_id).order_by(Job.created_at.desc()).limit(100)
    ).all()
    out = []
    for job in jobs:
        documents = job_service.job_documents(db, job.job_id)
        starts = [d.statement_start for d in documents if d.statement_start]
        ends = [d.statement_end for d in documents if d.statement_end]
        out.append(
            JobListItem(
                job_id=job.job_id,
                status=job.status,
                created_at=job.created_at,
                files=len(documents),
                period_start=min(starts) if starts else None,
                period_end=max(ends) if ends else None,
                expires_at=job.expires_at,
            )
        )
    return out


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    """Polled once a second by the UI stepper."""
    job = _job_or_404(db, job_id)
    documents = job_service.job_documents(db, job_id)
    totals = (job.report or {}).get("totals") if job.report else None
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        stages=list(JOB_STAGES),
        files=[_file_status(d) for d in documents],
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
        totals=totals,
        retention=retention_view(job),
    )


# --------------------------------------------------------------------------
# how long an upload is kept
# --------------------------------------------------------------------------


@router.post("/jobs/{job_id}/retention/keep")
def keep_upload(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The user chose "keep for 30 days" after the analysis."""
    job = _job_or_404(db, job_id)
    job.retention_choice = "keep"
    db.commit()
    return retention_view(job)


@router.post("/jobs/{job_id}/retention/extend")
def extend_upload(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, Any]:
    """Keep an upload for another retention period. A Pro feature."""
    from app.retention import extend, retention_days

    job = _job_or_404(db, job_id)
    if get_settings().auth_required and billing.effective_plan(user) != "pro":
        raise HTTPException(
            status_code=402,
            detail={
                "code": "pro_required",
                "feature": "retention",
                "message": (
                    f"Free uploads are deleted {retention_days()} days after analysis to keep your "
                    "bank data safe. Upgrade to Pro to keep this one for longer."
                ),
            },
        )
    extend(job)
    db.commit()
    return retention_view(job)


@router.get("/jobs/{job_id}/report")
def get_report(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = _job_or_404(db, job_id)
    if not job.report:
        raise HTTPException(status_code=409, detail="this job has not finished yet")
    return job.report


@router.get("/jobs/{job_id}/summary", response_model=SummaryResponse)
def get_summary(job_id: str, db: Session = Depends(get_db)) -> SummaryResponse:
    """What the summary card shows, without the per-file detail."""
    job = _job_or_404(db, job_id)
    report = job.report or {}
    totals = report.get("totals", {})
    reconciliations = [
        {
            "filename": entry["filename"],
            "account": entry.get("account"),
            **(entry.get("reconciliation") or {}),
        }
        for entry in report.get("files", [])
        if (entry.get("reconciliation") or {}).get("checked")
    ]
    known = set(SummaryResponse.model_fields)
    return SummaryResponse(
        **{k: v for k, v in totals.items() if k in known},
        reconciliations=reconciliations,
    )


# --------------------------------------------------------------------------
# unblocking: passwords
# --------------------------------------------------------------------------


@router.post("/jobs/{job_id}/files/{doc_id}/password", response_model=JobStatusResponse)
def submit_password(
    job_id: str,
    doc_id: str,
    body: PasswordRequest,
    db: Session = Depends(get_db),
) -> JobStatusResponse:
    """Unlock a protected PDF and re-run.

    The password is held in memory for this process only -- it is not written
    to the database, the report or the logs.
    """
    _job_or_404(db, job_id)
    try:
        job_service.submit_password(job_id, doc_id, body.password)
    except job_service.DocumentNotFound:
        raise HTTPException(status_code=404, detail="no such file in this job") from None
    job_service.start(job_id)
    db.expire_all()
    return get_job_status(job_id, db)


# --------------------------------------------------------------------------
# unblocking: column mapping
# --------------------------------------------------------------------------


@router.get("/jobs/{job_id}/mappings/pending", response_model=list[PendingMapping])
def pending_mappings(job_id: str, db: Session = Depends(get_db)) -> list[PendingMapping]:
    """Layouts nothing recognised, for the mapping dialog."""
    _job_or_404(db, job_id)
    out: list[PendingMapping] = []
    for document in job_service.job_documents(db, job_id):
        if not document.pending_mapping:
            continue
        pending = document.pending_mapping
        out.append(
            PendingMapping(
                doc_id=document.doc_id,
                filename=document.filename,
                headers=pending.get("headers", []),
                sample_rows=pending.get("sample_rows", []),
                suggestions=[
                    ColumnSuggestion(
                        header=p["header"],
                        field_name=p["field"],
                        confidence=p["confidence"],
                        reason=p.get("reason", ""),
                    )
                    for p in pending.get("proposals", [])
                ],
                amount_style=pending.get("amount_style", "debit_credit"),
                uncertain=pending.get("uncertain", []),
                available_fields=[
                    {"name": f.name, "description": f.description} for f in CANONICAL_FIELDS
                ],
                amount_styles=AMOUNT_STYLES,
            )
        )
    return out


@router.post("/jobs/{job_id}/mappings/{doc_id}", response_model=JobStatusResponse)
def confirm_mapping(
    job_id: str,
    doc_id: str,
    body: ConfirmMappingRequest,
    db: Session = Depends(get_db),
) -> JobStatusResponse:
    """Accept a confirmed mapping, save it as a template, and re-run."""
    _job_or_404(db, job_id)
    if body.amount_style not in AMOUNT_STYLES:
        raise HTTPException(
            status_code=422, detail=f"amount_style must be one of {AMOUNT_STYLES}"
        )
    missing = [f for f in ("date", "description") if f not in body.columns]
    if missing:
        raise HTTPException(
            status_code=422, detail=f"a mapping must include: {', '.join(missing)}"
        )
    if not ({"debit", "credit", "amount"} & set(body.columns)):
        raise HTTPException(
            status_code=422,
            detail="a mapping must include an amount column (debit/credit or amount)",
        )
    try:
        job_service.confirm_mapping(
            job_id,
            doc_id,
            columns=body.columns,
            amount_style=body.amount_style,
            save_as_template=body.save_as_template,
            template_name=body.template_name,
        )
    except job_service.DocumentNotFound:
        raise HTTPException(status_code=404, detail="no such file in this job") from None
    job_service.start(job_id)
    db.expire_all()
    return get_job_status(job_id, db)


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------


@router.get("/jobs/{job_id}/preview", response_model=PreviewResponse)
def preview(
    job_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    account: str | None = None,
    direction: str | None = None,
    payment_mode: str | None = None,
    needs_review: bool | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    search: str | None = None,
    sort: str = Query("date", pattern="^(date|amount|merchant|account)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> PreviewResponse:
    """The preview table, filtered and sorted."""
    _job_or_404(db, job_id)
    rows = job_service.job_transactions(db, job_id)

    filtered = [
        r for r in rows
        if (account is None or r.account == account)
        and (direction is None or r.direction == direction)
        and (payment_mode is None or r.payment_mode == payment_mode)
        and (needs_review is None or bool(r.needs_review) is needs_review)
        and (date_from is None or r.date >= date_from)
        and (date_to is None or r.date <= date_to)
        and (search is None or _matches(r, search))
    ]

    key = {
        "date": lambda r: (r.date, r.txn_id),
        "amount": lambda r: (r.amount, r.txn_id),
        "merchant": lambda r: ((r.merchant or "").lower(), r.txn_id),
        # every key ends in txn_id so the order is total: without it a sort and
        # its reverse are not each other's mirror, and pagination wobbles
        "account": lambda r: (r.account, r.date, r.txn_id),
    }[sort]
    filtered.sort(key=key, reverse=(order == "desc"))

    window = filtered[offset : offset + limit]
    return PreviewResponse(
        rows=[TransactionRowView.model_validate(r) for r in window],
        total=len(filtered),
        limit=limit,
        offset=offset,
        accounts=sorted({r.account for r in rows}),
        payment_modes=sorted({r.payment_mode for r in rows}),
    )


def _matches(row: TransactionRow, needle: str) -> bool:
    needle = needle.lower()
    haystack = " ".join(
        str(v or "") for v in (row.raw_description, row.merchant, row.reference_no,
                               row.order_id, row.item_details)
    ).lower()
    return needle in haystack


@router.get("/jobs/{job_id}/transactions/{txn_id}", response_model=TransactionDetail)
def transaction_detail(
    job_id: str, txn_id: str, db: Session = Depends(get_db)
) -> TransactionDetail:
    """The side panel: every source that contributed, and the raw values."""
    _job_or_404(db, job_id)
    row = db.get(TransactionRow, txn_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such transaction")

    links = list(db.query(TxnLinkRow).filter(TxnLinkRow.txn_id == txn_id))
    documents = {d.doc_id: d for d in job_service.job_documents(db, job_id)}
    items = list(db.query(ItemRow).filter(ItemRow.txn_id == txn_id))

    staged = (
        db.query(StagingRow).filter(StagingRow.source_ref == row.source_ref).first()
    )
    return TransactionDetail(
        transaction=TransactionRowView.model_validate(row),
        links=[
            LinkedSource(
                source_ref=link.source_ref,
                link_type=link.link_type,
                match_score=link.match_score,
                doc_id=link.doc_id,
                filename=documents[link.doc_id].filename if link.doc_id in documents else None,
                document_type=(
                    documents[link.doc_id].document_type if link.doc_id in documents else None
                ),
            )
            for link in sorted(links, key=lambda x: (x.link_type, x.source_ref))
        ],
        items=[ItemView.model_validate(i) for i in items],
        raw_values=staged.payload if staged else None,
    )


@router.get("/jobs/{job_id}/obligations", response_model=list[ObligationView])
def obligations(job_id: str, db: Session = Depends(get_db)) -> list[ObligationView]:
    _job_or_404(db, job_id)
    return [
        ObligationView.model_validate(o) for o in job_service.job_obligations(db, job_id)
    ]


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------


@router.get("/jobs/{job_id}/export/{name}")
def download(job_id: str, name: str, db: Session = Depends(get_db)) -> FileResponse:
    """``transactions.csv``, ``obligations.csv``, ``ingestion_report.json``, or
    ``finpilot_export.zip`` (all of them, plus the financial report if one has
    been generated).

    The CSVs are rebuilt from the database on every download, so they always
    carry the latest segments and every file the job has absorbed since.
    """
    _job_or_404(db, job_id)
    allowed = {"transactions.csv", "obligations.csv", "ingestion_report.json", "finpilot_export.zip"}
    if name not in allowed:
        raise HTTPException(status_code=404, detail=f"choose one of {sorted(allowed)}")
    if not (job_service.export_dir(job_id) / "ingestion_report.json").exists():
        raise HTTPException(status_code=409, detail="this job has not finished yet")
    job_service.refresh_exports(db, job_id)
    if name == "finpilot_export.zip":
        path = job_service.export_bundle(job_id)
        return FileResponse(path, media_type="application/zip", filename=name)
    path = job_service.export_dir(job_id) / name
    if name.endswith(".csv"):
        return Response(
            content=job_service.spreadsheet_safe_csv(path),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    return FileResponse(path, media_type="application/json", filename=name)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, db: Session = Depends(get_db)) -> None:
    _job_or_404(db, job_id)
    job_service.delete_job(job_id)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _job_or_404(db: Session, job_id: str):
    try:
        return job_service.get_job(db, job_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="no such job") from None


def _file_status(document: Document) -> FileStatus:
    return FileStatus(
        doc_id=document.doc_id,
        filename=document.filename,
        status=document.status,
        document_type=document.document_type,
        issuer=document.issuer,
        account=document.account,
        statement_start=document.statement_start,
        statement_end=document.statement_end,
        extraction_method=document.extraction_method,
        rows=document.row_count,
        needs_password=bool(document.needs_password),
        needs_mapping=document.status == DocumentStatus.NEEDS_MAPPING.value,
        issues=document.issues or [],
        reconciliation=_reconciliation_for(document),
    )


def _reconciliation_for(document: Document) -> dict[str, Any] | None:
    if document.opening_balance is None and document.closing_balance is None:
        return None
    return {
        "opening_balance": _money(document.opening_balance),
        "closing_balance": _money(document.closing_balance),
    }


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"
