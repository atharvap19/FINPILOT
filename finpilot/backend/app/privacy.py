"""A user's rights over their data: erase it, take it with them.

India's DPDP Act gives every user the right to have their personal data
erased once they withdraw consent, and to know what is held about them. Both
are implemented over the same inventory of what one account owns:

* the account row, its sessions, password-reset tokens and usage meters;
* every upload (job): the raw files, the ledger rows, links, line items and
  bills it produced, its exports and reports, and its document index.

Payments are the one exception to erasure: a paid order is a financial
record that has to be kept for tax and accounting (GST/income-tax records are
kept for years). The order keeps only an opaque user id, an amount and a date
-- no email, name or document -- and is listed in the export.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    AuthSession,
    Document,
    ItemRow,
    Job,
    ObligationRow,
    PasswordReset,
    Payment,
    StagingRow,
    TransactionRow,
    TxnLinkRow,
    UsageEvent,
    User,
)
from app.db.session import session_scope

log = logging.getLogger("finpilot.privacy")
audit = logging.getLogger("finpilot.audit")

_CHUNK = 500


def _chunks(values: list[str]):
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


# --------------------------------------------------------------------------
# erasure
# --------------------------------------------------------------------------


def purge_job_rows(session: Session, job_id: str) -> dict[str, int]:
    """Delete every database row one upload produced.

    A transaction can be described by several uploads (the same payment in
    two overlapping statements), so a row is only deleted when no remaining
    document still links to it -- deleting an old upload never takes rows
    away from a newer one.
    """
    from app import jobs as job_service

    documents = job_service.job_documents(session, job_id)
    doc_ids = [d.doc_id for d in documents]
    txn_ids: set[str] = set()
    for chunk in _chunks(doc_ids):
        txn_ids.update(session.scalars(select(TxnLinkRow.txn_id).where(TxnLinkRow.doc_id.in_(chunk))))

    bills = [b for b in job_service.job_obligations(session, job_id) if b.job_id in (None, job_id)]
    counts = {"documents": len(doc_ids), "bills": len(bills), "transactions": 0}
    for bill in bills:
        session.delete(bill)

    for chunk in _chunks(doc_ids):
        session.execute(delete(TxnLinkRow).where(TxnLinkRow.doc_id.in_(chunk)))
        session.execute(delete(ItemRow).where(ItemRow.doc_id.in_(chunk)))
        session.execute(delete(StagingRow).where(StagingRow.doc_id.in_(chunk)))
    session.flush()

    ids = sorted(txn_ids)
    still_linked: set[str] = set()
    for chunk in _chunks(ids):
        still_linked.update(session.scalars(select(TxnLinkRow.txn_id).where(TxnLinkRow.txn_id.in_(chunk))))
    orphans = [t for t in ids if t not in still_linked]
    for chunk in _chunks(orphans):
        session.execute(delete(TransactionRow).where(TransactionRow.txn_id.in_(chunk)))
        session.execute(delete(ItemRow).where(ItemRow.txn_id.in_(chunk)))
    session.execute(delete(ObligationRow).where(ObligationRow.job_id == job_id))
    counts["transactions"] = len(orphans)

    for document in documents:
        session.delete(document)
    return counts


def delete_account(user_id: str) -> dict[str, Any]:
    """Erase an account and everything it uploaded. Irreversible."""
    from app import jobs as job_service

    with session_scope() as session:
        job_ids = list(session.scalars(select(Job.job_id).where(Job.user_id == user_id)))
    for job_id in job_ids:
        job_service.delete_job(job_id)

    with session_scope() as session:
        session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
        session.execute(delete(PasswordReset).where(PasswordReset.user_id == user_id))
        session.execute(delete(UsageEvent).where(UsageEvent.user_id == user_id))
        kept_payments = session.query(Payment).filter(Payment.user_id == user_id).count()
        user = session.get(User, user_id)
        if user is not None:
            session.delete(user)

    shutil.rmtree(_account_dir(user_id), ignore_errors=True)
    audit.info("account_deleted user=%s uploads=%d payments_retained=%d", user_id, len(job_ids), kept_payments)
    return {"uploads_deleted": len(job_ids), "payment_records_retained": kept_payments}


# --------------------------------------------------------------------------
# portability
# --------------------------------------------------------------------------


def _account_dir(user_id: str) -> Path:
    return get_settings().exports_dir / "_accounts" / user_id


def _iso(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return value


def export_account(user_id: str) -> Path:
    """Everything held about one account, as a zip the user can keep.

    Written fresh on every request into the account's own folder and
    removed by the caller after it has been sent.
    """
    from app import jobs as job_service

    out_dir = _account_dir(user_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"finpilot-data-{dt.date.today().isoformat()}.zip"

    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise LookupError(user_id)
        account = {
            "email": user.email,
            "name": user.name,
            "created_at": _iso(user.created_at),
            "plan": user.plan,
            "pro_until": _iso(user.pro_until),
            "consent": {
                "version": user.consent_version,
                "given_at": _iso(user.consented_at),
                "confirmed_18_or_over": user.age_confirmed,
            },
        }
        payments = [
            {"order_id": p.order_id, "plan": p.plan_code, "amount_paise": p.amount_paise,
             "status": p.status, "created_at": _iso(p.created_at), "paid_at": _iso(p.paid_at)}
            for p in session.scalars(select(Payment).where(Payment.user_id == user_id))
        ]
        jobs = list(session.scalars(select(Job).where(Job.user_id == user_id)))
        uploads = []
        for job in jobs:
            docs = job_service.job_documents(session, job.job_id)
            uploads.append({
                "upload_id": job.job_id,
                "created_at": _iso(job.created_at),
                "status": job.status,
                "files": [{"name": d.filename, "type": d.document_type, "issuer": d.issuer,
                           "period": [_iso(d.statement_start), _iso(d.statement_end)],
                           "original_still_stored": bool(d.stored_path and Path(d.stored_path).exists())}
                          for d in docs],
            })
            job_service.refresh_exports(session, job.job_id)

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("README.txt", _README)
        bundle.writestr("account.json", json.dumps(account, indent=2))
        bundle.writestr("payments.json", json.dumps(payments, indent=2))
        bundle.writestr("uploads.json", json.dumps(uploads, indent=2))
        for upload in uploads:
            job_id = upload["upload_id"]
            exports = job_service.export_dir(job_id)
            for name in job_service.BUNDLE_FILES:
                source = exports / name
                if not source.exists():
                    continue
                arc = f"uploads/{job_id}/{name}"
                if name.endswith(".csv"):
                    bundle.writestr(arc, job_service.spreadsheet_safe_csv(source))
                else:
                    bundle.write(source, arc)
            raw = job_service.job_dir(job_id)
            if raw.exists():
                for file in sorted(p for p in raw.rglob("*") if p.is_file()):
                    bundle.write(file, f"uploads/{job_id}/original_files/{file.relative_to(raw).as_posix()}")
    audit.info("account_exported user=%s uploads=%d", user_id, len(uploads))
    return path


_README = """FinPilot -- your data

account.json   your account, plan and the consent you gave at sign-up
payments.json  your payments (kept for tax records even if you delete your account)
uploads.json   each upload and the files in it
uploads/<id>/  per upload: the cleaned ledger (transactions.csv), bills
               (obligations.csv), spending habits (segmentation.json), the
               processing report, any financial report you generated, and the
               original files if they are still within the retention period.
"""


def raw_upload_age_days(job: Job, now: dt.datetime | None = None) -> float:
    now = now or dt.datetime.now(dt.UTC).replace(tzinfo=None)
    return (now - job.created_at).total_seconds() / 86400


def documents_for(session: Session, job_id: str) -> list[Document]:
    return list(session.scalars(select(Document).where(Document.job_id == job_id)))
