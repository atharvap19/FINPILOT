"""Segmentation endpoints: run it, then read what it found.

Segmentation is a separate call rather than part of the upload job. It runs
over the ledger a job produced, so it only becomes meaningful once ingestion
has finished, and it is worth re-running after more files are added without
re-reading a single document.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import jobs as job_service
from app import segments as segment_service
from app.api.schemas import (
    SegmentationResponse,
    SegmentChartData,
    SegmentMonth,
    SegmentPoint,
    SegmentSlice,
    SegmentView,
)
from app.auth import job_access
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["segments"], dependencies=[Depends(job_access)])


def _job_or_404(db: Session, job_id: str):
    try:
        return job_service.get_job(db, job_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="no such job") from None


@router.post("/jobs/{job_id}/segments", response_model=SegmentationResponse)
def run_segmentation(job_id: str, db: Session = Depends(get_db)) -> SegmentationResponse:
    """Cluster this job's ledger and name the clusters.

    Idempotent in effect: every row the job owns is rewritten, so re-running
    after adding files replaces the previous assignment rather than layering
    on top of it.
    """
    _job_or_404(db, job_id)
    if not job_service.job_transactions(db, job_id):
        raise HTTPException(
            status_code=409, detail="this job has no transactions to segment"
        )
    report = segment_service.segment_job(job_id)
    db.expire_all()
    return _response(report)


@router.get("/jobs/{job_id}/segments", response_model=SegmentationResponse)
def get_segmentation(job_id: str, db: Session = Depends(get_db)) -> SegmentationResponse:
    """What the last run found. 409 until segmentation has been run."""
    _job_or_404(db, job_id)
    rows = job_service.job_transactions(db, job_id)
    if not any(row.segment_id is not None for row in rows):
        raise HTTPException(
            status_code=409, detail="this job has not been segmented yet"
        )

    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        if row.segment_id is None:
            continue
        entry = grouped.setdefault(
            row.segment_id,
            {"segment_id": row.segment_id, "name": row.segment_name, "size": 0,
             "total_amount": 0.0, "memberships": []},
        )
        entry["size"] += 1
        entry["total_amount"] += float(row.amount or 0)
        entry["memberships"].append(float(row.segment_probability or 0.0))

    total = len(rows)
    segments = [
        SegmentView(
            segment_id=entry["segment_id"],
            name=entry["name"],
            size=entry["size"],
            share_of_rows=round(entry["size"] / total, 4) if total else 0.0,
            total_amount=round(entry["total_amount"], 2),
            mean_membership=round(
                sum(entry["memberships"]) / len(entry["memberships"]), 4
            )
            if entry["memberships"]
            else 0.0,
        )
        for entry in sorted(grouped.values(), key=lambda e: -e["size"])
    ]
    return SegmentationResponse(
        rows=total,
        segments=len(segments),
        unsegmented_rows=sum(1 for r in rows if r.segment_id is None),
        detail=segments,
    )


#: Above this, the scatter is thinned -- a browser cannot usefully draw more
#: dots than this, and the shape is identical.
MAX_POINTS = 2000


@router.get("/jobs/{job_id}/segments/chart", response_model=SegmentChartData)
def segment_chart_data(job_id: str, db: Session = Depends(get_db)) -> SegmentChartData:
    """Everything the three charts need, in one call.

    Aggregated here rather than in the browser so the page does not have to
    page through the whole ledger to draw a pie.
    """
    _job_or_404(db, job_id)
    rows = job_service.job_transactions(db, job_id)
    if not any(row.segment_id is not None for row in rows):
        raise HTTPException(
            status_code=409, detail="this job has not been segmented yet"
        )

    slices: dict[int, dict[str, Any]] = {}
    months: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.segment_id is None:
            continue
        entry = slices.setdefault(
            row.segment_id,
            {"segment_id": row.segment_id, "name": row.segment_name or "unnamed",
             "size": 0, "total_amount": 0.0},
        )
        entry["size"] += 1
        entry["total_amount"] += float(row.amount or 0)
        key = row.date.strftime("%Y-%m")
        months.setdefault(key, {})
        bucket = months[key]
        bucket[str(row.segment_id)] = bucket.get(str(row.segment_id), 0) + 1

    step = max(1, len(rows) // MAX_POINTS)
    points = [
        SegmentPoint(
            date=row.date, amount=float(row.amount or 0), segment_id=row.segment_id
        )
        for row in rows[::step]
    ]

    return SegmentChartData(
        slices=[
            SegmentSlice(**entry)
            for entry in sorted(slices.values(), key=lambda e: -e["size"])
        ],
        months=[
            SegmentMonth(month=month, counts=counts)
            for month, counts in sorted(months.items())
        ],
        points=points,
        unsegmented_rows=sum(1 for r in rows if r.segment_id is None),
        points_sampled=step > 1,
    )


def _response(report: dict[str, Any]) -> SegmentationResponse:
    return SegmentationResponse(
        rows=report.get("rows", 0),
        segments=report.get("segments", 0),
        unsegmented_rows=report.get("unsegmented_rows", 0),
        named_by_model=report.get("named_by_model", 0),
        named_by_rules=report.get("named_by_rules", 0),
        detail=[
            SegmentView(
                segment_id=entry["segment_id"],
                name=entry["name"],
                rationale=entry.get("rationale", ""),
                named_by=entry.get("named_by"),
                size=entry["size"],
                share_of_rows=entry["share_of_rows"],
                median_amount=entry["median_amount"],
                total_amount=entry["total_amount"],
                mean_membership=entry["mean_membership"],
                profile=entry.get("profile"),
            )
            for entry in report.get("segments_detail", [])
        ],
    )
