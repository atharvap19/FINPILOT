"""Running an upload as a background job, and persisting what it produced.

The API is deliberately poll-based rather than streaming: a job is a row in
SQLite that a worker thread updates, and ``GET /api/jobs/{id}`` reads it. That
survives a page reload, works with several browser tabs, and needs no
websocket.

Two states let a job pause for the user rather than fail:

* **needs_password** -- an encrypted PDF. The password is used for one call and
  is never written to the database (principle 6).
* **needs_mapping** -- a column layout nothing recognised. The user confirms it
  in the dialog and the answer is saved as a template, so the next file of that
  shape does not ask.

Re-running a job is safe: every write is keyed by ``txn_id``/``doc_id``, so the
second run overwrites with identical values rather than duplicating.
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
import threading
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    ColumnTemplate,
    Document,
    ItemRow,
    Job,
    MerchantRow,
    ObligationRow,
    StagingRow,
    TransactionRow,
    TxnLinkRow,
)
from app.db.session import session_scope
from ingestion.detect import sha256_file
from ingestion.mapping import (
    Template,
    TemplateLibrary,
    header_fingerprint,
    template_from_mapping,
)
from ingestion.pipeline import JobResult, export_job, run_job
from ingestion.schema import (
    JOB_STAGES,
    DocumentStatus,
    JobStage,
    JobStatus,
    make_doc_id,
    make_merchant_id,
)

log = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="finpilot-job")
_LOCK = threading.Lock()
#: passwords live here for the life of the process only, never on disk
_PASSWORDS: dict[str, dict[str, str]] = {}
#: confirmed mappings awaiting a re-run, keyed by job then doc
_CONFIRMED: dict[str, dict[str, Template]] = {}


class JobNotFound(LookupError):
    pass


class DocumentNotFound(LookupError):
    pass


# --------------------------------------------------------------------------
# creating a job
# --------------------------------------------------------------------------


@dataclass
class UploadedFile:
    filename: str
    content: bytes


def job_dir(job_id: str) -> Path:
    return get_settings().uploads_dir / job_id


def export_dir(job_id: str) -> Path:
    return get_settings().exports_dir / job_id


def safe_filename(name: str | None) -> str:
    """The last path component, whichever separator the client used.

    ``Path(name).name`` only splits on the server's separator, so on Linux a
    Windows-style ``..\\..\\x.csv`` would survive whole. Control characters
    are dropped too: the name is shown in the UI and written into reports.
    """
    import re

    base = re.split(r"[\\/]", name or "")[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip().lstrip(".")
    return base[:200] or "upload"


def create_job(uploads: Iterable[UploadedFile], *, user_id: str | None = None) -> str:
    """Save the uploaded bytes and register the job as queued."""
    settings = get_settings()
    settings.ensure_dirs()
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    target = job_dir(job_id)
    target.mkdir(parents=True, exist_ok=True)

    stored: list[tuple[str, Path]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for upload in uploads:
        safe = safe_filename(upload.filename)
        path = target / safe
        counter = 1
        while path.exists():
            path = target / f"{Path(safe).stem}_{counter}{Path(safe).suffix}"
            counter += 1
        path.write_bytes(upload.content)
        digest = sha256_file(path)
        if digest in seen:
            duplicates.append(safe)
            path.unlink()
            continue
        seen.add(digest)
        stored.append((safe, path))

    with session_scope() as session:
        session.add(
            Job(
                job_id=job_id,
                user_id=user_id,
                status=JobStatus.QUEUED.value,
                stage=JobStage.DETECT.value,
                progress=0.0,
            )
        )
        for name, path in stored:
            digest = sha256_file(path)
            session.add(
                Document(
                    doc_id=make_doc_id(digest, job_id),
                    job_id=job_id,
                    filename=name,
                    file_hash=digest,
                    status=DocumentStatus.QUEUED.value,
                    stored_path=str(path),
                )
            )
    if duplicates:
        log.info("job %s: skipped %d duplicate uploads", job_id, len(duplicates))
    return job_id


def start(job_id: str) -> None:
    """Hand the job to a worker thread."""
    _EXECUTOR.submit(_run, job_id)


def run_sync(job_id: str) -> None:
    """Run inline. Used by the tests and by ``--no-background`` runs."""
    _run(job_id)


# --------------------------------------------------------------------------
# the worker
# --------------------------------------------------------------------------

_STAGE_INDEX = {stage: i for i, stage in enumerate(JOB_STAGES)}


def _run(job_id: str) -> None:
    settings = get_settings()
    directory = job_dir(job_id)
    paths = sorted(p for p in directory.rglob("*") if p.is_file())
    if not paths:
        _fail(job_id, "no files were uploaded")
        return

    def on_stage(stage: str, fraction: float) -> None:
        _set_stage(job_id, stage, fraction)

    _set_status(job_id, JobStatus.RUNNING.value)
    try:
        from ingestion.link import LinkSettings

        result = run_job(
            paths,
            job_id=job_id,
            base_dir=directory,
            passwords=_PASSWORDS.get(job_id, {}),
            confirmed_templates=_CONFIRMED.get(job_id, {}),
            link_settings=LinkSettings.from_settings(settings),
            on_stage=on_stage,
        )
    except Exception as exc:  # noqa: BLE001 - a crashed job must still report
        log.exception("job %s failed", job_id)
        _fail(job_id, f"{type(exc).__name__}: {exc}")
        return

    result = scope_to_owner(result, _owner_of(job_id))

    # Persisting and exporting are inside the guard too. They run after the
    # pipeline has already succeeded, and a failure here used to escape into
    # the thread pool -- which swallows it, leaving the job stuck on "running"
    # and the UI polling a job that will never finish.
    try:
        persist(job_id, result)
        export_job(result, export_dir(job_id))
    except Exception as exc:  # noqa: BLE001
        log.exception("job %s produced a ledger but could not be saved", job_id)
        _fail(job_id, f"could not save the results: {type(exc).__name__}: {exc}")
        return

    blocked = [
        d for d in result.documents
        if d.status in (DocumentStatus.NEEDS_PASSWORD.value, DocumentStatus.NEEDS_MAPPING.value)
    ]
    status = JobStatus.NEEDS_INPUT.value if blocked else JobStatus.DONE.value
    _finish(job_id, status, result.report)
    _index_documents(job_id)


def _index_documents(job_id: str) -> None:
    """Embed the job's documents for the query agent. Never fails the job --
    the agent re-checks the index before it searches, so a miss here only
    costs the first question a few seconds."""
    try:
        from app.rag.store import ensure_index

        ensure_index(job_id)
    except Exception:  # noqa: BLE001
        log.exception("job %s: document indexing failed", job_id)


def _owner_of(job_id: str) -> str | None:
    """Whose ledger this job writes into -- None in single-user mode."""
    if not get_settings().auth_required:
        return None
    with session_scope() as session:
        job = session.get(Job, job_id)
        return job.user_id if job else None


def _scoped(owner: str, ident: str | None) -> str | None:
    if not ident:
        return ident
    import hashlib

    prefix = ident.split("_", 1)[0] + "_" if "_" in ident else ""
    return prefix + hashlib.sha256(f"{owner}|{ident}".encode()).hexdigest()[:20]


def scope_to_owner(result: JobResult, owner: str | None) -> JobResult:
    """Give one user's transactions and bills ids no other user can share.

    A transaction id is a hash of account, date, amount and reference, so two
    people who upload the same file -- the demo set, or a statement shared
    between spouses -- would otherwise write the *same* ledger rows, and the
    second upload would take over the first one's links and segments. Hashing
    the owner in keeps every ledger its own while leaving the within-user
    behaviour unchanged: the same payment seen in two of one person's files
    still lands on one row. Deterministic, so a re-run produces the same ids.
    """
    if not owner:
        return result
    result.transactions = [
        t.model_copy(update={"txn_id": _scoped(owner, t.txn_id),
                             "refund_of": _scoped(owner, t.refund_of)})
        for t in result.transactions
    ]
    result.obligations = [
        o.model_copy(update={"bill_id": _scoped(owner, o.bill_id),
                             "paid_txn_id": _scoped(owner, o.paid_txn_id)})
        for o in result.obligations
    ]
    result.items = [i.model_copy(update={"txn_id": _scoped(owner, i.txn_id)}) for i in result.items]
    result.links = [link.model_copy(update={"txn_id": _scoped(owner, link.txn_id)})
                    for link in result.links]
    return result


def _set_stage(job_id: str, stage: str, fraction: float) -> None:
    index = _STAGE_INDEX.get(stage, 0)
    progress = (index + max(0.0, min(fraction, 1.0))) / len(JOB_STAGES)
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.stage = stage
        job.progress = round(min(progress, 1.0), 4)


def _set_status(job_id: str, status: str) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.status = status


def _fail(job_id: str, message: str) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.FAILED.value
            job.error = message
            job.finished_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _finish(job_id: str, status: str, report: dict[str, Any]) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.stage = JobStage.DONE.value
        job.progress = 1.0
        job.report = report
        job.finished_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        if job.expires_at is None:
            from app.retention import first_expiry

            # the 30 days start when the analysis is ready, not at upload
            job.expires_at = first_expiry(job.finished_at)


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def persist(job_id: str, result: JobResult) -> None:
    """Write the job's output. Idempotent -- a re-run replaces, never appends."""
    with session_scope() as session:
        _persist_documents(session, job_id, result)
        _replace_job_rows(session, job_id, result)
        _persist_merchants(session, result)


def _persist_documents(session: Session, job_id: str, result: JobResult) -> None:
    for document in result.documents:
        ctx = document.context
        row = session.get(Document, ctx.doc_id)
        if row is None:
            row = Document(doc_id=ctx.doc_id, job_id=job_id, filename=ctx.filename,
                           file_hash="")
            session.add(row)
        row.job_id = job_id
        row.filename = ctx.filename
        row.document_type = ctx.document_type
        row.issuer = ctx.issuer
        row.account = ctx.account
        row.statement_start = ctx.statement_start
        row.statement_end = ctx.statement_end
        row.opening_balance = ctx.opening_balance
        row.closing_balance = ctx.closing_balance
        row.extraction_method = ctx.extraction_method
        row.status = document.status
        row.issues = [i.as_dict() for i in document.issues]
        row.row_count = len(document.transactions)
        row.template_name = document.template_name
        row.needs_password = bool(
            document.detection is not None and document.detection.needs_password
        )
        row.pending_mapping = (
            document.pending_mapping.as_dict() if document.pending_mapping else None
        )
        if document.detection is not None:
            row.file_type = document.detection.file_type
            row.file_hash = document.detection.file_hash

        session.execute(delete(StagingRow).where(StagingRow.doc_id == ctx.doc_id))
        for staged in document.staging:
            session.add(
                StagingRow(
                    doc_id=ctx.doc_id,
                    source_ref=staged["source_ref"],
                    row_index=staged["row_index"],
                    page=staged.get("page"),
                    payload=staged["payload"],
                )
            )


#: SQLite allows 999 bound parameters per statement.
_CHUNK = 500


def _chunks(values: list[str]):
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


def _replace_job_rows(session: Session, job_id: str, result: JobResult) -> None:
    """Rewrite what this job produced, without disturbing what it did not.

    The ledger is cumulative across jobs, and a transaction's id deliberately
    excludes its source -- so the August statement and the June-to-August one
    describe nineteen of the *same* transactions. That makes the scope of a
    rewrite delicate:

    * links and items are cleared for this job's transactions **and only for
      this job's documents**. Clearing by transaction alone would detach those
      nineteen rows from the statement that first reported them, which is
      silent data loss in another job.
    * ``linked_sources`` is unioned rather than overwritten, so uploading the
      August statement later *adds* it to those rows instead of replacing the
      June statement that was already there.
    """
    doc_ids = [d.context.doc_id for d in result.documents]
    txn_ids = [t.txn_id for t in result.transactions]

    for chunk in _chunks(txn_ids):
        for docs in _chunks(doc_ids):
            session.execute(
                delete(TxnLinkRow)
                .where(TxnLinkRow.txn_id.in_(chunk))
                .where(TxnLinkRow.doc_id.in_(docs))
            )
            session.execute(
                delete(ItemRow)
                .where(ItemRow.txn_id.in_(chunk))
                .where(ItemRow.doc_id.in_(docs))
            )
    session.flush()

    known = _existing_linked_sources(session, txn_ids)
    for txn in result.transactions:
        payload = txn.model_dump()
        payload["amount"] = txn.amount
        payload["linked_sources"] = _union_sources(
            known.get(txn.txn_id, ""), txn.linked_sources
        )
        session.merge(TransactionRow(**payload))
    for obligation in result.obligations:
        session.merge(ObligationRow(**obligation.model_dump(), job_id=job_id))
    for item in result.items:
        session.add(ItemRow(**item.model_dump()))
    for link in result.links:
        # merge, not add: the natural key means a link that is already recorded
        # is simply updated, so a re-run is a no-op rather than a violation
        session.merge(TxnLinkRow(**link.model_dump()))
    session.flush()
    _count_rows_per_document(session, doc_ids)


def _count_rows_per_document(session: Session, doc_ids: list[str]) -> None:
    """How many ledger rows each file describes, counted from the links.

    A document's own extraction count misses rows created later, in linking --
    an order email whose payment no statement covers becomes a row there --
    so an email-only upload used to report "0 rows" with rows in the ledger.
    """
    from sqlalchemy import func

    counts = dict(
        session.execute(
            select(TxnLinkRow.doc_id, func.count(func.distinct(TxnLinkRow.txn_id)))
            .where(TxnLinkRow.doc_id.in_(doc_ids))
            .group_by(TxnLinkRow.doc_id)
        ).all()
    )
    for doc_id in doc_ids:
        document = session.get(Document, doc_id)
        if document is not None and doc_id in counts:
            document.row_count = max(document.row_count or 0, counts[doc_id])


def _existing_linked_sources(session: Session, txn_ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in _chunks(txn_ids):
        rows = session.execute(
            select(TransactionRow.txn_id, TransactionRow.linked_sources).where(
                TransactionRow.txn_id.in_(chunk)
            )
        ).all()
        out.update({txn_id: sources or "" for txn_id, sources in rows})
    return out


def _union_sources(existing: str, incoming: str) -> str:
    """Keep every source that has ever described this transaction.

    A ref's link type cannot be read back out of the joined string, so the
    order is rebuilt from the file kind instead -- statements, then emails,
    receipts, sheets and bills. That is the same order
    :meth:`ingestion.merge.LinkRegistry.render` produces, so a column filled in
    over two uploads reads the same as one filled in over one.
    """
    refs = {r for r in f"{existing}|{incoming}".split("|") if r}
    return "|".join(sorted(refs, key=lambda r: (_rank_hint(r), r)))


def _rank_hint(source_ref: str) -> int:
    """Statements first, then emails, receipts, sheets, bills -- by file kind."""
    from ingestion.merge import LINK_RANK

    name = source_ref.split("#")[0].lower()
    if name.endswith((".eml", ".mbox")):
        return LINK_RANK["email"]
    if name.endswith((".xlsx", ".xls")):
        return LINK_RANK["spreadsheet"]
    if "bill" in name:
        return LINK_RANK["bill"]
    return LINK_RANK["primary"]


def _persist_merchants(session: Session, result: JobResult) -> None:
    names = {t.merchant for t in result.transactions if t.merchant}
    for name in sorted(names):
        merchant_id = make_merchant_id(name)
        if session.get(MerchantRow, merchant_id) is None:
            session.add(
                MerchantRow(merchant_id=merchant_id, canonical_name=name, aliases=[])
            )


# --------------------------------------------------------------------------
# user input: passwords and mappings
# --------------------------------------------------------------------------


def submit_password(job_id: str, doc_id: str, password: str) -> None:
    """Unlock a protected PDF and re-run the job.

    The password is held in memory for this process only and is deliberately
    never written to the database or the report.
    """
    with session_scope() as session:
        document = session.get(Document, doc_id)
        if document is None or document.job_id != job_id:
            raise DocumentNotFound(doc_id)
        filename = document.filename
    with _LOCK:
        _PASSWORDS.setdefault(job_id, {})[filename] = password


def confirm_mapping(
    job_id: str,
    doc_id: str,
    *,
    columns: dict[str, str],
    amount_style: str,
    save_as_template: bool = True,
    template_name: str | None = None,
) -> Template:
    """Accept a user-confirmed column mapping and remember it."""
    with session_scope() as session:
        document = session.get(Document, doc_id)
        if document is None or document.job_id != job_id:
            raise DocumentNotFound(doc_id)
        pending = document.pending_mapping or {}
        headers = pending.get("headers") or list(columns.values())
        issuer = document.issuer
        # Templates are shared by every account (a bank's layout is the same
        # for everyone), so the name must not carry this user's file name.
        name = template_name or f"{issuer or 'Custom'} layout {header_fingerprint(headers)[:6]}"
        document_type = document.document_type or "bank_statement"

    template = template_from_mapping(
        name=name,
        headers=headers,
        columns=columns,
        amount_style=amount_style,
        issuer=issuer,
        document_type=document_type,
    )
    with _LOCK:
        _CONFIRMED.setdefault(job_id, {})[doc_id] = template

    if save_as_template:
        TemplateLibrary().save(template)
        with session_scope() as session:
            session.merge(
                ColumnTemplate(
                    fingerprint=header_fingerprint(headers),
                    name=template.name,
                    issuer=issuer,
                    document_type=document_type,
                    spec=template.to_json(),
                )
            )
    return template


def job_passwords(job_id: str) -> dict[str, str]:
    """filename -> password, for this process only. Used to re-open a PDF for
    indexing; never persisted."""
    with _LOCK:
        return dict(_PASSWORDS.get(job_id, {}))


def clear_job_secrets(job_id: str) -> None:
    with _LOCK:
        _PASSWORDS.pop(job_id, None)
        _CONFIRMED.pop(job_id, None)


# --------------------------------------------------------------------------
# reading a job back
# --------------------------------------------------------------------------


def get_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise JobNotFound(job_id)
    return job


def job_documents(session: Session, job_id: str) -> list[Document]:
    return list(
        session.scalars(
            select(Document).where(Document.job_id == job_id).order_by(Document.filename)
        )
    )


def job_transactions(session: Session, job_id: str) -> list[TransactionRow]:
    doc_ids = [d.doc_id for d in job_documents(session, job_id)]
    if not doc_ids:
        return []
    txn_ids = set(
        session.scalars(select(TxnLinkRow.txn_id).where(TxnLinkRow.doc_id.in_(doc_ids)))
    )
    if not txn_ids:
        return []
    return list(
        session.scalars(
            select(TransactionRow)
            .where(TransactionRow.txn_id.in_(txn_ids))
            .order_by(TransactionRow.date, TransactionRow.txn_id)
        )
    )


def job_obligations(session: Session, job_id: str) -> list[ObligationRow]:
    own = list(
        session.scalars(
            select(ObligationRow).where(ObligationRow.job_id == job_id).order_by(ObligationRow.bill_id)
        )
    )
    if own:
        return own
    # Bills saved before obligations carried a job id: match them by filename,
    # among those legacy rows only. Never fall back to "every bill" -- with
    # accounts that would show one user another user's bills.
    prefixes = tuple(d.filename for d in job_documents(session, job_id))
    if not prefixes:
        return []
    legacy = session.scalars(
        select(ObligationRow).where(ObligationRow.job_id.is_(None)).order_by(ObligationRow.bill_id)
    )
    return [r for r in legacy if any(r.source_ref.startswith(p) or p in r.source_ref for p in prefixes)]


def refresh_exports(session: Session, job_id: str) -> None:
    """Rewrite the two CSVs from what the database holds now."""
    import pandas as pd

    from app.segments import rewrite_export
    from ingestion.schema import OBLIGATION_COLUMNS, frame_to_csv

    rewrite_export(session, job_id, job_transactions(session, job_id))
    bills = job_obligations(session, job_id)
    frame = pd.DataFrame.from_records(
        [{column: getattr(b, column, None) for column in OBLIGATION_COLUMNS} for b in bills],
        columns=list(OBLIGATION_COLUMNS),
    )
    out_dir = export_dir(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_to_csv(frame, out_dir / "obligations.csv", OBLIGATION_COLUMNS)


#: what goes into finpilot_export.zip, if present
BUNDLE_FILES = (
    "transactions.csv",
    "obligations.csv",
    "financial_report.pdf",
    "financial_report.md",
    "segmentation.json",
    "ingestion_report.json",
)


#: a cell starting with one of these is evaluated as a formula by Excel,
#: LibreOffice and Google Sheets
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _defuse(cell: str) -> str:
    if not cell or not cell.startswith(_FORMULA_START):
        return cell
    try:
        float(cell)  # a negative number is data, not a formula
        return cell
    except ValueError:
        return "'" + cell


def spreadsheet_safe_csv(path: Path) -> bytes:
    """The CSV with formula-looking cells defused (OWASP "CSV injection").

    Ledger text comes from statements and emails other people wrote -- a
    narration like ``=HYPERLINK(...)``, or a masked UPI handle ``@okaxis``,
    would otherwise be run as a formula when the user opens the download.
    """
    import csv
    import io

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in rows:
        writer.writerow([_defuse(c) for c in row])
    return out.getvalue().encode("utf-8")


def export_bundle(job_id: str) -> Path:
    import zipfile

    out_dir = export_dir(job_id)
    path = out_dir / "finpilot_export.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in BUNDLE_FILES:
            source = out_dir / name
            if not source.exists():
                continue
            if name.endswith(".csv"):
                bundle.writestr(name, spreadsheet_safe_csv(source))
            else:
                bundle.write(source, arcname=name)
    return path


def delete_job(job_id: str) -> None:
    """Remove a job and everything it produced."""
    from app.privacy import purge_job_rows

    with session_scope() as session:
        # ledger rows, links, items, bills and documents -- not just the job
        # row: deleting an upload has to erase what it put in the database
        purge_job_rows(session, job_id)
        job = session.get(Job, job_id)
        if job is not None:
            session.delete(job)
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
    shutil.rmtree(export_dir(job_id), ignore_errors=True)
    clear_job_secrets(job_id)
    try:
        from app.rag.store import drop_index

        drop_index(job_id)
    except Exception:  # noqa: BLE001
        log.warning("job %s: could not drop its document index", job_id)
