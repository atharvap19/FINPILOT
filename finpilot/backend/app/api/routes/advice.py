"""Ask a question about one job's ledger and segments."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import jobs as job_service
from app.advice.service import ask_job
from app.api.schemas import AskRequest, AskResponse
from app.auth import job_access
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["advice"], dependencies=[Depends(job_access)])


@router.post("/jobs/{job_id}/ask", response_model=AskResponse)
def ask(job_id: str, body: AskRequest, db: Session = Depends(get_db)) -> AskResponse:
    """Recommend from this upload. Uses Gemini when configured, else rules."""
    try:
        job_service.get_job(db, job_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="no such job") from None

    rows = job_service.job_transactions(db, job_id)
    if not rows:
        raise HTTPException(
            status_code=409, detail="this job has no transactions to advise on"
        )

    try:
        result = ask_job(
            db,
            job_id,
            body.question,
            [turn.model_dump() for turn in body.history],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    return AskResponse.model_validate(result)
