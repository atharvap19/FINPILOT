"""The query agent, the financial report and the document index.

Chat and report generation stream newline-delimited JSON: one event per line
(``thought``, ``tool_call``, ``tool_result``, then ``answer`` or ``report``, or
``error``). The browser renders the agent's steps as they happen, which is
both better feedback for a 30-second report and the honest way to show what
the agent actually did.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import Field
from sqlalchemy.orm import Session

from app import billing
from app import jobs as job_service
from app.agent import report as report_service
from app.agent.prompts import chat_system
from app.agent.runner import run_agent
from app.agent.tools import AgentContext
from app.api.schemas import ApiModel, ChatTurn
from app.auth import current_user, job_access
from app.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.rag import store

router = APIRouter(prefix="/api", tags=["agent"], dependencies=[Depends(job_access)])

MAX_MESSAGE = 2000


class ChatRequest(ApiModel):
    message: str
    history: list[ChatTurn] = Field(default_factory=list)


def _job_or_404(db: Session, job_id: str) -> None:
    try:
        job_service.get_job(db, job_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="no such job") from None


def _require_ledger(db: Session, job_id: str) -> None:
    _job_or_404(db, job_id)
    if not job_service.job_transactions(db, job_id):
        raise HTTPException(status_code=409, detail="this job has no transactions yet")


def _require_model() -> None:
    if not get_settings().agent_available:
        raise HTTPException(
            status_code=503,
            detail="The FinPilot agent needs a Gemini model. Set GEMINI_API_KEY (and AGENT_ENABLED=true).",
        )


def _metered(
    events: Iterator[dict[str, Any]], *, user_id: str, kind: str, job_id: str, done: str
) -> Iterator[dict[str, Any]]:
    """Pass events through; record one unit of usage when the run succeeds.

    Only a delivered answer or report counts against the plan -- a run the
    model failed, or the user stopped, costs the user nothing."""
    from app.db.session import session_scope

    for event in events:
        if event["type"] == done:
            tokens = event.get("tokens") or (event.get("meta") or {}).get("tokens") or {}
            with session_scope() as session:
                billing.record(
                    session, user_id, kind, job_id=job_id,
                    tokens_in=tokens.get("input"),
                    tokens_out=(tokens.get("output") or 0) + (tokens.get("thinking") or 0),
                )
        yield event


def _with_clean_charts(events: Iterator[dict[str, Any]], ctx: AgentContext) -> Iterator[dict[str, Any]]:
    """Drop chart tags that can't be drawn for this job before the UI sees them."""
    from app.agent.charts import clean_for

    for event in events:
        if event["type"] == "answer":
            event = {**event, "text": clean_for(event["text"], ctx)}
        yield event


def _ndjson(events: Iterator[dict[str, Any]]) -> StreamingResponse:
    def lines() -> Iterator[bytes]:
        try:
            for event in events:
                yield (json.dumps(event, default=str, ensure_ascii=False) + "\n").encode("utf-8")
        except Exception as exc:  # noqa: BLE001 - the stream must end with an event
            yield (json.dumps({"type": "error", "message": f"{type(exc).__name__}: {exc}"}) + "\n").encode()

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------


@router.post("/jobs/{job_id}/chat")
def chat(
    job_id: str,
    body: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> StreamingResponse:
    """Ask the agent anything about this job's money. Streams its work."""
    _require_ledger(db, job_id)
    _require_model()
    billing.enforce(db, user, "chat")
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="ask a question")
    if len(message) > MAX_MESSAGE:
        raise HTTPException(status_code=400, detail=f"keep it under {MAX_MESSAGE} characters")

    ctx = AgentContext(job_id)
    run = _with_clean_charts(
        run_agent(
            job_id,
            system=chat_system(ctx),
            message=message,
            history=[t.model_dump() for t in body.history],
            context=ctx,
        ),
        ctx,
    )
    return _ndjson(_metered(run, user_id=user.user_id, kind="chat", job_id=job_id, done="answer"))


# --------------------------------------------------------------------------
# financial report
# --------------------------------------------------------------------------


@router.post("/jobs/{job_id}/financial-report")
def generate_financial_report(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> StreamingResponse:
    """Have the agent investigate the ledger and write the report. Streams."""
    _require_ledger(db, job_id)
    _require_model()
    billing.enforce(db, user, "report")
    run = report_service.generate_report(job_id)
    return _ndjson(_metered(run, user_id=user.user_id, kind="report", job_id=job_id, done="report"))


@router.get("/jobs/{job_id}/financial-report")
def get_financial_report(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The last report generated for this job. 404 until one exists."""
    _job_or_404(db, job_id)
    saved = report_service.load_report(job_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="no report has been generated for this job yet")
    return saved


@router.get("/jobs/{job_id}/financial-report/{fmt}")
def download_financial_report(job_id: str, fmt: str, db: Session = Depends(get_db)) -> FileResponse:
    """``pdf`` or ``md``."""
    _job_or_404(db, job_id)
    names = {"pdf": (report_service.PDF_NAME, "application/pdf"),
             "md": (report_service.MD_NAME, "text/markdown")}
    if fmt not in names:
        raise HTTPException(status_code=404, detail="choose pdf or md")
    name, media = names[fmt]
    path = job_service.export_dir(job_id) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="no report has been generated for this job yet")
    if fmt == "md":
        # chart tags only mean something to the app; in a plain file they
        # become captions pointing at the PDF
        from app.agent.charts import captions_for_markdown

        names_by_id = {
            r["segment_id"]: r["segment_name"] or f"segment {r['segment_id']}"
            for r in AgentContext(job_id).rows
            if r["segment_id"] is not None
        }
        text = captions_for_markdown(path.read_text(encoding="utf-8"), names_by_id)
        return Response(
            content=text,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="finpilot_{name}"'},
        )
    return FileResponse(path, media_type=media, filename=f"finpilot_{name}")


# --------------------------------------------------------------------------
# document index
# --------------------------------------------------------------------------


@router.get("/jobs/{job_id}/index")
def get_index(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """How many passages of which documents are searchable."""
    _job_or_404(db, job_id)
    return store.index_status(job_id)


@router.post("/jobs/{job_id}/index")
def build_index(job_id: str, rebuild: bool = False, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Index what is missing (or everything, with ``rebuild=true``)."""
    _job_or_404(db, job_id)
    return store.ensure_index(job_id, rebuild=rebuild)
