"""The FastAPI application.

    uv run uvicorn app.main:app --reload

Everything lives under ``/api``. The frontend polls ``GET /api/jobs/{id}``
once a second while a job runs; there is no websocket, because a poll survives
a page reload and a second browser tab, and a job takes seconds rather than
minutes.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.account import router as account_router
from app.api.routes.agent import router as agent_router
from app.api.routes.insights import router as insights_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.segments import router as segments_router
from app.config import get_settings
from app.db.session import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("finpilot")


def _file_logging() -> None:
    """Daily log files kept for LOG_RETENTION_DAYS (CERT-In: 180 days)."""
    from logging.handlers import TimedRotatingFileHandler
    from pathlib import Path

    settings = get_settings()
    if not settings.log_dir:
        return
    folder = Path(settings.log_dir)
    folder.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        folder / "finpilot.log", when="midnight", backupCount=settings.log_retention_days,
        utc=True, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


_file_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    init_db()
    from app.retention import start_sweeper

    start_sweeper()
    log.info("FinPilot ingestion API ready (data dir: %s)", settings.data_dir)
    yield


app = FastAPI(
    title="FinPilot ingestion",
    version="0.1.0",
    summary=(
        "Turns statements, bills, receipts, emails and sheets into one clean "
        "ledger, entirely with code."
    ),
    lifespan=lifespan,
    docs_url="/docs" if get_settings().api_docs else None,
    redoc_url="/redoc" if get_settings().api_docs else None,
    openapi_url="/openapi.json" if get_settings().api_docs else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(account_router)
app.include_router(jobs_router)
app.include_router(segments_router)
app.include_router(agent_router)
app.include_router(insights_router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """Liveness, plus which capabilities are switched on.

    Public, so it says only what the UI needs: no model ids, no payment mode,
    and no learned column templates (shared across accounts, they can carry
    the shape of other people's files).
    """
    from ingestion import embeddings

    settings = get_settings()
    return {
        "status": "ok",
        "extraction": "code",
        "embeddings": embeddings.available(),
        "llm": {
            "configured": bool(settings.gemini_api_key),
            "enabled": settings.llm_enabled,
        },
        "agent": {"available": settings.agent_available},
        "auth_required": settings.auth_required,
    }


@app.exception_handler(ValueError)
async def value_error_handler(_request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})
