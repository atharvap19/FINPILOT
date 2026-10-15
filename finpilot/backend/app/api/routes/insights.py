"""The dashboard's data, and a one-click demo upload."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

from app import jobs as job_service
from app.api.schemas import ApiModel
from app.auth import current_user, job_access
from app.db.models import User
from app.db.session import get_db
from app.insights import build_insights

router = APIRouter(prefix="/api", tags=["insights"], dependencies=[Depends(job_access)])


@router.get("/jobs/{job_id}/insights")
def insights(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """KPIs, money flow, calendar, weekday x hour, merchants, segment trends."""
    try:
        job_service.get_job(db, job_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="no such job") from None
    return build_insights(job_id)


@router.post("/demo", status_code=201)
def demo(user: User = Depends(current_user)) -> dict[str, str]:
    """Upload FinPilot's synthetic sample set as a job of the user's own.

    Three months of one fictional person's statements, card statement, bills,
    order emails and a spending sheet. Not counted against the plan: it is how
    a new user sees what the product does before trusting it with real data.
    """
    from pathlib import Path

    from app.config import BACKEND_DIR

    fixtures = BACKEND_DIR / "tests" / "fixtures" / "synthetic"
    if not (fixtures / "hdfc_savings_aug.csv").exists():
        from scripts.generate_synthetic import generate

        generate(fixtures)
    paths = [
        p for p in sorted(Path(fixtures).rglob("*"))
        if p.is_file() and not p.name.startswith("expected_")
    ]
    uploads = [job_service.UploadedFile(filename=p.name, content=p.read_bytes()) for p in paths]
    job_id = job_service.create_job(uploads, user_id=user.user_id)
    job_service.start(job_id)
    return {"job_id": job_id}


class BudgetItem(ApiModel):
    label: str
    amount: float


class BudgetRequest(ApiModel):
    period: str = "day"
    budget: float
    #: either structured items...
    items: list[BudgetItem] = Field(default_factory=list)
    #: ...or what the user typed, e.g. "lunch 200, petrol 500"
    text: str = ""
    start: dt.date | None = None
    #: keys of commitments / usual spends the user unticked
    exclude: list[str] = Field(default_factory=list)


@router.post("/jobs/{job_id}/budget")
def budget(job_id: str, body: BudgetRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Today's or this week's safe-to-spend, from the budget, the plan and the ledger."""
    from app.agent.tools import AgentContext
    from app.budget import PlannedItem, parse_items, plan

    try:
        job_service.get_job(db, job_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="no such job") from None
    if body.budget > 1e9 or any(i.amount < 0 or i.amount > 1e9 for i in body.items):
        raise HTTPException(status_code=400, detail="those amounts don't look right")
    items = [PlannedItem(label=i.label.strip()[:60] or "Planned", amount=round(i.amount, 2)) for i in body.items]
    items += parse_items(body.text)
    try:
        return plan(AgentContext(job_id), period=body.period, budget=body.budget, items=items[:50],
                    start=body.start, exclude=set(body.exclude))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


# --------------------------------------------------------------------------
# typed expenses
# --------------------------------------------------------------------------


class ExpenseRequest(ApiModel):
    #: what the user typed, e.g. "lunch 250 cash, petrol 500 upi, yesterday chai 40"
    text: str


@router.post("/jobs/{job_id}/expenses", status_code=201)
def log_expenses(job_id: str, body: ExpenseRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Parse and add typed expenses to this upload's ledger."""
    from app.manual import add_entries, parse_expenses

    try:
        job_service.get_job(db, job_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="no such job") from None
    text = (body.text or "").strip()
    if not text or len(text) > 2000:
        raise HTTPException(status_code=400, detail="type what you spent, e.g. lunch 250 cash")
    entries, skipped = parse_expenses(text)
    if len(entries) > 50:
        raise HTTPException(status_code=400, detail="that's a lot at once -- add up to 50 at a time")
    added = add_entries(db, job_id, entries) if entries else []
    db.commit()
    return {"added": added, "skipped": skipped}


@router.get("/jobs/{job_id}/expenses")
def typed_expenses(job_id: str, day: dt.date | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """What was typed for a day (today by default)."""
    from app.manual import entries_on

    try:
        job_service.get_job(db, job_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="no such job") from None
    day = day or dt.date.today()
    entries = entries_on(db, job_id, day)
    spent = sum(e["amount"] for e in entries if e["direction"] == "debit")
    return {"date": day.isoformat(), "entries": entries, "spent": round(spent, 2)}


@router.delete("/jobs/{job_id}/expenses/{txn_id}", status_code=204)
def undo_expense(job_id: str, txn_id: str, db: Session = Depends(get_db)) -> None:
    """Remove one typed entry. Statement rows can't be removed this way."""
    from app.manual import delete_entry

    if not delete_entry(db, job_id, txn_id):
        raise HTTPException(status_code=404, detail="no such typed expense")
    db.commit()


@router.post("/manual-ledger", status_code=201)
def start_manual_ledger(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    """A ledger with no files, for people who'd rather just type what they spend."""
    from app.manual import create_manual_ledger

    job_id = create_manual_ledger(db, user.user_id)
    db.commit()
    return {"job_id": job_id}
