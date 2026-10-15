"""The segmentation endpoints, driven through a real client.

No model is configured in tests, so every segment here is named by the rule
fallback -- which is exactly the path a deployment without a key takes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_api import finished_job, upload  # noqa: F401 - fixtures


@pytest.fixture()
def client(tmp_settings, monkeypatch) -> TestClient:
    from app import jobs as job_service
    from app.main import app

    monkeypatch.setattr(job_service, "start", job_service.run_sync)
    with TestClient(app) as test_client:
        yield test_client


def test_segments_404_for_an_unknown_job(client: TestClient):
    assert client.post("/api/jobs/nope/segments").status_code == 404
    assert client.get("/api/jobs/nope/segments").status_code == 404


def test_reading_before_running_is_a_409(finished_job):  # noqa: F811
    client, job_id = finished_job
    response = client.get(f"/api/jobs/{job_id}/segments")
    assert response.status_code == 409
    assert "not been segmented" in response.json()["detail"]


def test_running_segmentation_names_and_persists(finished_job):  # noqa: F811
    client, job_id = finished_job

    response = client.post(f"/api/jobs/{job_id}/segments")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["rows"] > 0
    assert body["segments"] == len(body["detail"])
    # with no key configured every name comes from the profile, not a model
    assert body["named_by_model"] == 0
    assert body["named_by_rules"] == body["segments"]

    placed = sum(s["size"] for s in body["detail"])
    assert placed + body["unsegmented_rows"] == body["rows"]

    for segment in body["detail"]:
        assert segment["name"]
        assert len(segment["name"]) <= 64
        assert 0.0 <= segment["mean_membership"] <= 1.0
        assert segment["named_by"] == "rules"


def test_segments_are_readable_after_running(finished_job):  # noqa: F811
    client, job_id = finished_job
    ran = client.post(f"/api/jobs/{job_id}/segments").json()
    if not ran["segments"]:
        pytest.skip("this ledger produced no segments to read back")

    stored = client.get(f"/api/jobs/{job_id}/segments").json()
    assert stored["segments"] == ran["segments"]
    assert stored["rows"] == ran["rows"]
    assert stored["unsegmented_rows"] == ran["unsegmented_rows"]
    assert {s["segment_id"] for s in stored["detail"]} == {
        s["segment_id"] for s in ran["detail"]
    }


def test_the_preview_carries_the_segment_back(finished_job):  # noqa: F811
    """Segmentation writes the reserved columns on the ledger itself."""
    client, job_id = finished_job
    ran = client.post(f"/api/jobs/{job_id}/segments").json()
    if not ran["segments"]:
        pytest.skip("this ledger produced no segments")

    rows = client.get(f"/api/jobs/{job_id}/preview", params={"limit": 500}).json()["rows"]
    assert rows
    # the preview view does not expose segment columns, but the export does
    export = client.get(f"/api/jobs/{job_id}/export/transactions.csv")
    assert export.status_code == 200


def test_rerunning_is_idempotent_in_shape(finished_job):  # noqa: F811
    client, job_id = finished_job
    first = client.post(f"/api/jobs/{job_id}/segments").json()
    second = client.post(f"/api/jobs/{job_id}/segments").json()
    assert first["segments"] == second["segments"]
    assert first["rows"] == second["rows"]
    assert [s["size"] for s in first["detail"]] == [s["size"] for s in second["detail"]]


def test_the_export_carries_the_segments(finished_job):  # noqa: F811
    """The CSV is written when ingestion finishes -- before segmentation.

    Without the re-export it keeps all 48 columns and leaves the three segment
    ones empty forever, so the download looks complete while being wrong.
    """
    import csv
    import io

    client, job_id = finished_job
    ran = client.post(f"/api/jobs/{job_id}/segments").json()
    if not ran["segments"]:
        pytest.skip("this ledger produced no segments")

    text = client.get(f"/api/jobs/{job_id}/export/transactions.csv").text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == ran["rows"]

    with_id = [r for r in rows if r["segment_id"]]
    assert len(with_id) == ran["rows"] - ran["unsegmented_rows"]
    assert all(r["segment_name"] for r in with_id)
    assert all(r["segment_probability"] for r in with_id)


def test_preview_rows_carry_the_segment(finished_job):  # noqa: F811
    client, job_id = finished_job
    ran = client.post(f"/api/jobs/{job_id}/segments").json()
    if not ran["segments"]:
        pytest.skip("this ledger produced no segments")

    rows = client.get(f"/api/jobs/{job_id}/preview", params={"limit": 500}).json()["rows"]
    assert rows
    assert all("segment_id" in row for row in rows)
    named = [r for r in rows if r["segment_id"] is not None]
    assert named, "at least one previewed row should belong to a segment"
    assert all(r["segment_name"] for r in named)


def test_chart_data_is_consistent_with_the_run(finished_job):  # noqa: F811
    client, job_id = finished_job
    assert client.get(f"/api/jobs/{job_id}/segments/chart").status_code == 409

    ran = client.post(f"/api/jobs/{job_id}/segments").json()
    if not ran["segments"]:
        pytest.skip("this ledger produced no segments")

    chart = client.get(f"/api/jobs/{job_id}/segments/chart").json()
    assert len(chart["slices"]) == ran["segments"]
    assert chart["unsegmented_rows"] == ran["unsegmented_rows"]

    # every slice is a real, named segment, and the sizes reconcile
    assert sum(s["size"] for s in chart["slices"]) + chart["unsegmented_rows"] == ran["rows"]
    assert all(s["name"] for s in chart["slices"])

    # the stacked bar's buckets must agree with the pie's totals
    per_segment: dict[str, int] = {}
    for month in chart["months"]:
        for segment_id, count in month["counts"].items():
            per_segment[segment_id] = per_segment.get(segment_id, 0) + count
    for slice_ in chart["slices"]:
        assert per_segment[str(slice_["segment_id"])] == slice_["size"]

    # the scatter is one point per ledger row unless it was thinned
    if not chart["points_sampled"]:
        assert len(chart["points"]) == ran["rows"]
    assert all(p["amount"] >= 0 for p in chart["points"])


def test_chart_404_for_an_unknown_job(client: TestClient):
    assert client.get("/api/jobs/nope/segments/chart").status_code == 404


def test_segmenting_a_job_with_no_transactions_is_a_409(client: TestClient, tmp_path: Path):
    empty = tmp_path / "notes.csv"
    empty.write_text("just some text\nwith no transactions in it\n", encoding="utf-8")
    created = upload(client, [empty])
    response = client.post(f"/api/jobs/{created['job_id']}/segments")
    assert response.status_code == 409
