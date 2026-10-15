"""Load a job, build the brief, answer the question."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app import jobs as job_service
from app.advice.brief import AdviceBrief, build_brief
from app.advice.gemini import GeminiAdvisor
from app.advice.rules import recommend
from app.config import get_settings

log = logging.getLogger("finpilot.advice")

MAX_QUESTION = 500
MAX_HISTORY = 8


def advisor_from_settings() -> GeminiAdvisor | None:
    settings = get_settings()
    if not settings.naming_available:
        return None
    return GeminiAdvisor(
        api_key=settings.gemini_api_key,
        model=settings.gemini_naming_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def brief_for_job(session: Session, job_id: str) -> AdviceBrief:
    return build_brief(
        documents=job_service.job_documents(session, job_id),
        rows=job_service.job_transactions(session, job_id),
        obligations=job_service.job_obligations(session, job_id),
    )


def ask_job(
    session: Session,
    job_id: str,
    question: str,
    history: list[dict[str, str]] | None = None,
    *,
    advisor: GeminiAdvisor | None = None,
) -> dict[str, Any]:
    """Answer from this job's ledger. Falls back to rules if the model fails."""
    text = question.strip()
    if not text:
        raise ValueError("ask a question about this ledger")
    if len(text) > MAX_QUESTION:
        raise ValueError(f"question is longer than {MAX_QUESTION} characters")

    clean_history: list[dict[str, str]] = []
    for turn in (history or [])[-MAX_HISTORY:]:
        role = turn.get("role", "")
        content = (turn.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        clean_history.append({"role": role, "content": content[:2000]})

    brief = brief_for_job(session, job_id)
    if not brief.period_start and not brief.documents:
        raise ValueError("this job has no ledger to advise on")

    used = "rules"
    payload: dict[str, Any] | None = None
    namer = advisor if advisor is not None else advisor_from_settings()
    if namer is not None:
        try:
            payload = namer.advise(brief, text, clean_history)
        except Exception as exc:  # noqa: BLE001 - advice never fails the job
            log.warning("advice model failed (%s); using rules", exc)
            payload = None
        if payload is not None:
            used = "model"

    if payload is None:
        payload = recommend(brief, text)

    return {
        "answer": payload["answer"],
        "recommendations": payload.get("recommendations") or [],
        "source": used,
        "segmented": brief.segmented,
        "period_start": brief.period_start,
        "period_end": brief.period_end,
    }
