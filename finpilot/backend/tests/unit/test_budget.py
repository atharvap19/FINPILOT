"""Today's / this week's budget: safe to spend = budget - planned - commitments - usual."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.budget import parse_items
from tests.unit.test_api import finished_job, upload  # noqa: F401


@pytest.fixture()
def client(tmp_settings, monkeypatch) -> TestClient:
    from app import jobs as job_service
    from app.main import app

    monkeypatch.setattr(job_service, "start", job_service.run_sync)
    with TestClient(app) as test_client:
        yield test_client


def test_parse_items_understands_indian_amounts_and_either_order():
    items = [(i.label, i.amount) for i in parse_items("lunch 200, petrol ₹500\nmovie Rs 350; 1,200 groceries, 2,50,000 car")]
    assert items == [("lunch", 200), ("petrol", 500), ("movie", 350), ("groceries", 1200), ("car", 250000)]
    assert parse_items("") == []
    assert parse_items("no numbers here") == []


def _check_maths(plan: dict) -> None:
    included = lambda group: sum(e["amount"] for e in plan[group] if e["included"])  # noqa: E731
    expected = plan["budget"] - plan["planned_total"] - included("commitments") - included("usual")
    assert plan["safe_to_spend"] == pytest.approx(expected, abs=0.02)


def test_day_plan_shows_its_working(finished_job):  # noqa: F811
    client, job_id = finished_job
    r = client.post(f"/api/jobs/{job_id}/budget", json={
        "period": "day", "budget": 1500, "text": "lunch 200, petrol 500", "start": "2026-10-16",
    })
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["days"] == 1 and plan["planned_total"] == 700
    _check_maths(plan)
    assert plan["history"]["weeks"] > 4
    for entry in plan["usual"] + plan["commitments"]:
        assert entry["basis"]


def test_a_monthly_fixed_payment_lands_on_its_day(finished_job):  # noqa: F811
    client, job_id = finished_job
    # the synthetic tenant pays the same rent on the same day every month
    days = {}
    for day in range(1, 29):
        plan = client.post(f"/api/jobs/{job_id}/budget", json={
            "period": "day", "budget": 50000, "start": f"2026-11-{day:02d}",
        }).json()
        for c in plan["commitments"]:
            days.setdefault(c["label"], set()).add(day)
    assert days, "no recurring commitment found in the synthetic ledger"
    # each commitment shows up on exactly one day of the month
    assert all(len(d) == 1 for d in days.values()), days


def test_week_plan_and_unticking(finished_job):  # noqa: F811
    client, job_id = finished_job
    base = {"period": "week", "budget": 10000, "text": "groceries 1500", "start": "2026-10-15"}
    plan = client.post(f"/api/jobs/{job_id}/budget", json=base).json()
    assert plan["days"] == 7 and plan["end"] == "2026-10-21"
    _check_maths(plan)
    assert plan["per_day"] == pytest.approx(plan["safe_to_spend"] / 7, abs=0.02)

    included = [e for e in plan["usual"] if e["included"]]
    assert included
    dropped = client.post(f"/api/jobs/{job_id}/budget", json={**base, "exclude": [included[0]["key"]]}).json()
    assert dropped["safe_to_spend"] == pytest.approx(plan["safe_to_spend"] + included[0]["amount"], abs=0.02)


def test_planned_items_are_not_counted_twice(finished_job):  # noqa: F811
    client, job_id = finished_job
    plan = client.post(f"/api/jobs/{job_id}/budget", json={
        "period": "week", "budget": 20000, "text": "groceries 1500, petrol 800, food 900", "start": "2026-10-15",
    }).json()
    for entry in plan["usual"]:
        if entry["covered_by"]:
            assert entry["included"] is False


def test_only_a_day_or_a_week(finished_job):  # noqa: F811
    client, job_id = finished_job
    for period in ("month", "year"):
        r = client.post(f"/api/jobs/{job_id}/budget", json={"period": period, "budget": 1000})
        assert r.status_code == 400
        assert "day or a week" in r.json()["detail"]
    assert client.post(f"/api/jobs/{job_id}/budget", json={"period": "day", "budget": -5}).status_code == 400


def test_agent_tool_plans_a_budget(finished_job):  # noqa: F811
    _, job_id = finished_job
    from app.agent.tools import AgentContext, run_tool, summarize_result

    ctx = AgentContext(job_id)
    result = run_tool(ctx, "plan_budget", {"period": "day", "budget": 2000, "planned": "lunch 250",
                                            "start": dt.date(2026, 10, 16).isoformat()})
    assert "safe_to_spend" in result
    assert "left to spend" in summarize_result("plan_budget", result)
    assert "error" in run_tool(ctx, "plan_budget", {"period": "month", "budget": 2000})
