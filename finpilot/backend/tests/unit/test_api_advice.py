"""The /ask endpoint, through a real client. LLM is off, so rules answer."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_api import finished_job, upload  # noqa: F401


@pytest.fixture()
def client(tmp_settings, monkeypatch) -> TestClient:
    from app import jobs as job_service
    from app.main import app

    monkeypatch.setattr(job_service, "start", job_service.run_sync)
    with TestClient(app) as test_client:
        yield test_client


def test_ask_404_for_an_unknown_job(client: TestClient):
    response = client.post(
        "/api/jobs/nope/ask", json={"question": "how do I spend less next month"}
    )
    assert response.status_code == 404


def test_ask_rejects_an_empty_question(finished_job):  # noqa: F811
    client, job_id = finished_job
    response = client.post(f"/api/jobs/{job_id}/ask", json={"question": "  "})
    assert response.status_code == 400


def test_ask_uses_the_ledger_without_a_model(finished_job):  # noqa: F811
    client, job_id = finished_job
    response = client.post(
        f"/api/jobs/{job_id}/ask",
        json={"question": "How do I spend less money next month?"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "rules"
    assert body["answer"]
    assert body["period_start"]
    assert body["period_end"]
    assert isinstance(body["recommendations"], list)


def test_ask_names_segments_after_clustering(finished_job):  # noqa: F811
    client, job_id = finished_job
    ran = client.post(f"/api/jobs/{job_id}/segments").json()
    if not ran["segments"]:
        return

    body = client.post(
        f"/api/jobs/{job_id}/ask",
        json={"question": "How do I spend less money next month?"},
    ).json()
    assert body["segmented"] is True
    named = {s["name"] for s in ran["detail"] if s["name"]}
    blob = body["answer"] + " ".join(r["title"] for r in body["recommendations"])
    assert any(name in blob for name in named)
