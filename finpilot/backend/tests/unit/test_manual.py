"""Typed expenses: parse, add to the ledger, undo, feed the budget and the agent."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.manual import parse_expenses
from tests.unit.test_api import finished_job, upload  # noqa: F401

TODAY = dt.date(2026, 10, 15)


@pytest.fixture()
def client(tmp_settings, monkeypatch) -> TestClient:
    from app import jobs as job_service
    from app.main import app

    monkeypatch.setattr(job_service, "start", job_service.run_sync)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("lunch 250 cash, petrol 500 upi, yesterday chai 40",
         [("Lunch", 250, 0, "CASH", "debit"), ("Petrol", 500, 0, "UPI", "debit"), ("Chai", 40, -1, "OTHER", "debit")]),
        ("kal swiggy 380 gpay\nauto 60 cash; got salary 85,000",
         [("Swiggy", 380, -1, "UPI", "debit"), ("Auto", 60, 0, "CASH", "debit"), ("Salary", 85000, 0, "OTHER", "credit")]),
        ("2k shopping card and 12/10 movie 450",
         [("Shopping", 2000, 0, "CARD", "debit"), ("Movie", 450, -3, "OTHER", "debit")]),
        ("day before yesterday groceries 1,240 phonepe", [("Groceries", 1240, -2, "UPI", "debit")]),
    ],
)
def test_parse_expenses(text, expected):
    entries, skipped = parse_expenses(text, TODAY)
    got = [(e.label, e.amount, (e.date - TODAY).days, e.mode, e.direction) for e in entries]
    assert got == expected
    assert skipped == []


def test_pieces_without_an_amount_are_reported():
    entries, skipped = parse_expenses("coffee, lunch 200", TODAY)
    assert [e.label for e in entries] == ["Lunch"]
    assert skipped == ["coffee"]


def test_typed_expenses_join_the_ledger_and_can_be_undone(finished_job):  # noqa: F811
    client, job_id = finished_job
    before = client.get(f"/api/jobs/{job_id}/preview?limit=1").json()["total"]
    r = client.post(f"/api/jobs/{job_id}/expenses", json={"text": "lunch 250 cash, petrol 500 upi, hmm"})
    assert r.status_code == 201
    body = r.json()
    assert [a["label"] for a in body["added"]] == ["Lunch", "Petrol"]
    assert body["skipped"] == ["hmm"]
    assert client.get(f"/api/jobs/{job_id}/preview?limit=1").json()["total"] == before + 2

    today = client.get(f"/api/jobs/{job_id}/expenses").json()
    assert today["spent"] == 750 and len(today["entries"]) == 2

    # the budget counts what is already spent today
    plan = client.post(f"/api/jobs/{job_id}/budget", json={"period": "day", "budget": 2000}).json()
    assert plan["spent_total"] == 750
    assert plan["safe_to_spend"] <= 2000 - 750

    lunch = body["added"][0]["txn_id"]
    assert client.delete(f"/api/jobs/{job_id}/expenses/{lunch}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}/expenses").json()["spent"] == 500
    # undo only ever touches typed rows, never a statement row
    statement_row = client.get(f"/api/jobs/{job_id}/preview?limit=1&sort=date").json()["rows"][0]["txn_id"]
    assert client.delete(f"/api/jobs/{job_id}/expenses/{statement_row}").status_code == 404


def test_bad_input(finished_job):  # noqa: F811
    client, job_id = finished_job
    assert client.post(f"/api/jobs/{job_id}/expenses", json={"text": "  "}).status_code == 400
    assert client.post(f"/api/jobs/{job_id}/expenses", json={"text": "x" * 2001}).status_code == 400


def test_a_ledger_without_files(client):
    created = client.post("/api/manual-ledger")
    assert created.status_code == 201
    job_id = created.json()["job_id"]
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "done"
    client.post(f"/api/jobs/{job_id}/expenses", json={"text": "chai 40, auto 80 cash"})
    insights = client.get(f"/api/jobs/{job_id}/insights").json()
    assert insights["kpis"]["money_out"]["value"] == 120


def test_agent_can_log_an_expense(finished_job):  # noqa: F811
    _, job_id = finished_job
    from app.agent.tools import AgentContext, run_tool, summarize_result

    ctx = AgentContext(job_id)
    before = len(ctx.rows)
    result = run_tool(ctx, "log_expense", {"text": "lunch 250, auto 60 cash"})
    assert len(result["added"]) == 2
    assert summarize_result("log_expense", result) == "added 2 entries"
    assert len(ctx.rows) == before + 2  # the agent sees its own change
    assert "error" in run_tool(ctx, "log_expense", {"text": "just lunch"})
