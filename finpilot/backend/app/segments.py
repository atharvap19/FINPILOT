"""Running segmentation against a stored job.

:mod:`segmentation` is deliberately database-free -- it takes a frame and
returns assignments. This module is the thin layer that loads a job's ledger,
hands it over, and writes the three reserved columns back.

Re-running is safe and is the intended way to re-segment after more files are
added to a job: every row the job owns is rewritten, so a row that stops
belonging to a segment has its name cleared rather than keeping a stale one.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import TransactionRow
from app.db.session import session_scope
from segmentation.naming import GeminiNamer
from segmentation.service import SegmentationResult, segment_frame

log = logging.getLogger("finpilot.segments")

#: Ledger columns the feature builder and profiler read.
FRAME_COLUMNS: tuple[str, ...] = (
    "txn_id",
    "date",
    "time",
    "amount",
    "direction",
    "account",
    "account_type",
    "source_type",
    "merchant",
    "counterparty_type",
    "payment_mode",
    "channel",
    "is_transfer",
    "is_refund",
    "is_fee",
    "is_cash_withdrawal",
    "emi_flag",
)


def frame_from_rows(rows: list[TransactionRow]) -> pd.DataFrame:
    """ORM rows -> the frame segmentation expects."""
    records = [
        {column: getattr(row, column, None) for column in FRAME_COLUMNS} for row in rows
    ]
    frame = pd.DataFrame.from_records(records, columns=list(FRAME_COLUMNS))
    if frame.empty:
        return frame
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["time"] = frame["time"].map(lambda v: v.isoformat() if v is not None else None)
    return frame


def namer_from_settings() -> GeminiNamer | None:
    """A namer, or ``None`` when the model is off or unconfigured.

    ``None`` is not an error: :func:`segmentation.naming.name_segments` then
    names every segment from its profile with rules instead.
    """
    settings = get_settings()
    if not settings.naming_available:
        log.info("segment naming: no model configured, using rules")
        return None
    return GeminiNamer(
        api_key=settings.gemini_api_key,
        model=settings.gemini_naming_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_calls=settings.llm_max_calls_per_job,
    )


def apply(session: Session, result: SegmentationResult) -> int:
    """Write the three reserved columns back. Returns rows touched."""
    updates = result.rows()
    if not updates:
        return 0
    session.bulk_update_mappings(TransactionRow, updates)
    return len(updates)


def rewrite_export(session: Session, job_id: str, rows: list[TransactionRow]) -> None:
    """Re-write ``transactions.csv`` so the download carries the segments.

    The export is written once, when ingestion finishes -- before this stage has
    run. Without this the downloaded CSV keeps all 48 columns but leaves the
    three segment ones empty forever, however many times the job is segmented,
    and the file looks complete while being wrong.
    """
    from app import jobs as job_service
    from ingestion.schema import ALL_TRANSACTION_COLUMNS, frame_to_csv

    if not rows:
        return
    frame = pd.DataFrame.from_records(
        [
            {column: getattr(row, column, None) for column in ALL_TRANSACTION_COLUMNS}
            for row in rows
        ],
        columns=list(ALL_TRANSACTION_COLUMNS),
    )
    out_dir = job_service.export_dir(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_to_csv(frame, out_dir / "transactions.csv", ALL_TRANSACTION_COLUMNS)


def segment_job(job_id: str, *, namer: GeminiNamer | None = None) -> dict[str, Any]:
    """Segment one job's ledger and persist the result. Returns the report."""
    from app import jobs as job_service

    settings = get_settings()
    with session_scope() as session:
        rows = job_service.job_transactions(session, job_id)
        frame = frame_from_rows(rows)

        result = segment_frame(
            frame,
            namer=namer if namer is not None else namer_from_settings(),
            min_cluster_size=settings.segment_min_cluster_size,
            min_samples=settings.segment_min_samples,
        )
        touched = apply(session, result)
        session.flush()
        # bulk_update_mappings goes round the identity map, so objects already
        # loaded in this session still hold the pre-segmentation values.
        session.expire_all()
        rewrite_export(session, job_id, job_service.job_transactions(session, job_id))

    report = dict(result.report)
    report["rows_updated"] = touched
    save_report(job_id, report)
    return report


def save_report(job_id: str, report: dict[str, Any]) -> None:
    """Keep the last run's names, rationales and profiles. The ledger only has
    room for the name; the agent and the financial report want the why."""
    import json

    from app import jobs as job_service

    out_dir = job_service.export_dir(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "segmentation.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )


def load_report(job_id: str) -> dict[str, Any] | None:
    import json

    from app import jobs as job_service

    path = job_service.export_dir(job_id) / "segmentation.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
