"""Every upload is erased 30 days after its analysis, unless it is extended.

FinPilot holds people's bank transactions, so it keeps them only as long as
they are useful: when an upload finishes, the user chooses to delete it right
away or keep it for UPLOAD_RETENTION_DAYS (30). On the expiry date the whole
upload goes -- original files, ledger rows, bills, habits, reports and its
search index (:func:`app.jobs.delete_job`). Pro users can extend by another
30 days, as many times as they like.

So nobody is surprised, the owner is emailed 7 days and 1 day before the
deletion (with a link to keep it) and once more when it is done.

A daemon thread runs :func:`sweep` every hour; ``now`` is injectable for tests.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job, User
from app.db.session import session_scope

log = logging.getLogger("finpilot.retention")
SWEEP_EVERY_S = 3600
#: reminder emails, in days before the deletion
REMINDERS = (7, 1)
_TERMINAL = ("done", "needs_input", "failed")
_started = threading.Event()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def retention_days() -> int:
    return get_settings().upload_retention_days


def first_expiry(now: dt.datetime | None = None) -> dt.datetime | None:
    days = retention_days()
    return (now or _now()) + dt.timedelta(days=days) if days > 0 else None


def extend(job: Job, now: dt.datetime | None = None) -> None:
    """Push the deletion date out by one retention period (Pro)."""
    now = now or _now()
    base = max(job.expires_at or now, now)
    job.expires_at = base + dt.timedelta(days=retention_days())
    job.retention_choice = "keep"
    job.reminder_7d_at = None
    job.reminder_1d_at = None
    job.extensions = (job.extensions or 0) + 1


def view(job: Job, now: dt.datetime | None = None) -> dict[str, Any]:
    """What the dashboard shows about an upload's lifetime."""
    now = now or _now()
    days_left = None
    if job.expires_at is not None:
        days_left = max(0, int((job.expires_at - now).total_seconds() // 86400))
    return {
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
        "days_left": days_left,
        "choice": job.retention_choice,
        "extensions": job.extensions or 0,
        "retention_days": retention_days(),
    }


# --------------------------------------------------------------------------
# emails
# --------------------------------------------------------------------------


def _describe(job: Job) -> str:
    when = job.created_at.strftime("%d %b %Y") if job.created_at else "recently"
    return f"the files you uploaded on {when}"


def _reminder_text(user: User, job: Job, days: int) -> tuple[str, str]:
    app = get_settings().public_url.rstrip("/")
    when = job.expires_at.strftime("%d %b %Y") if job.expires_at else "soon"
    subject = (
        "Your FinPilot data will be deleted tomorrow"
        if days <= 1
        else f"Your FinPilot data will be deleted in {days} days"
    )
    body = (
        f"Hi {user.name or 'there'},\n\n"
        f"To keep your bank transactions safe, FinPilot only keeps an upload for "
        f"{retention_days()} days. The analysis of {_describe(job)} -- your transactions, "
        f"spending habits, charts and reports -- will be permanently deleted on {when}.\n\n"
        f"Want to keep it? Open FinPilot and choose \"Extend 30 days\" (FinPilot Pro):\n{app}\n\n"
        "Need a copy first? Use Account -> Download my data.\n\n"
        "If you don't need it any more, you don't have to do anything.\n"
    )
    return subject, body


def _deleted_text(user: User, job: Job) -> tuple[str, str]:
    subject = "Your FinPilot data has been deleted"
    body = (
        f"Hi {user.name or 'there'},\n\n"
        f"As promised, we have permanently deleted the analysis of {_describe(job)}: the original "
        "files, your transactions, spending habits, charts, reports and search index.\n\n"
        "You can upload again any time.\n"
    )
    return subject, body


def _owner(session, job: Job) -> User | None:
    return session.get(User, job.user_id) if job.user_id else None


# --------------------------------------------------------------------------
# the sweep
# --------------------------------------------------------------------------


def sweep(now: dt.datetime | None = None) -> dict[str, int]:
    """Give finished uploads a date, send due reminders, erase what expired."""
    from app import jobs as job_service
    from app.mailer import send_email

    if retention_days() <= 0:
        return {"dated": 0, "reminded": 0, "deleted": 0}
    now = now or _now()
    stats = {"dated": 0, "reminded": 0, "deleted": 0}
    expired: list[tuple[str, User | None, Job]] = []

    with session_scope() as session:
        for job in session.scalars(select(Job).where(Job.status.in_(_TERMINAL))):
            if job.expires_at is None:
                # uploads from before retention existed get a full period
                # from today rather than vanishing on the first sweep
                job.expires_at = first_expiry(now)
                stats["dated"] += 1
                continue
            left = job.expires_at - now
            owner = _owner(session, job)
            if left <= dt.timedelta(0):
                session.expunge(job)
                expired.append((job.job_id, owner, job))
                continue
            for days, column in zip(REMINDERS, ("reminder_7d_at", "reminder_1d_at"), strict=True):
                if left <= dt.timedelta(days=days) and getattr(job, column) is None:
                    # one email per threshold: a 1-day reminder also covers 7
                    job.reminder_7d_at = job.reminder_7d_at or now
                    setattr(job, column, now)
                    if owner is not None:
                        send_email(owner.email, *_reminder_text(owner, job, days if days == 1 else max(1, left.days)))
                        stats["reminded"] += 1
                    break

    for job_id, owner, job in expired:
        job_service.delete_job(job_id)
        stats["deleted"] += 1
        if owner is not None:
            send_email(owner.email, *_deleted_text(owner, job))
        logging.getLogger("finpilot.audit").info("upload_expired job=%s user=%s", job_id, job.user_id)

    if any(stats.values()):
        log.info("retention sweep: %s", stats)
    return stats


def start_sweeper() -> None:
    """Start the hourly sweep once per process."""
    if retention_days() <= 0 or _started.is_set():
        return
    _started.set()

    def loop() -> None:
        while True:
            try:
                sweep()
            except Exception:  # noqa: BLE001 - a failed sweep retries next hour
                log.exception("retention sweep failed")
            threading.Event().wait(SWEEP_EVERY_S)

    threading.Thread(target=loop, name="finpilot-retention", daemon=True).start()
