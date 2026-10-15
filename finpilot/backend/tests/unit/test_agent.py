"""The query agent, the report writer and the document index.

The model is replaced by a scripted fake client that returns real
``google.genai`` types, so the loop, the tool dispatch, the streaming API and
the saved report are all exercised exactly as in production -- only the
decisions are canned.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from tests.unit.test_api import finished_job, upload  # noqa: F401


@pytest.fixture()
def client(tmp_settings, monkeypatch) -> TestClient:
    from app import jobs as job_service
    from app.main import app

    monkeypatch.setattr(job_service, "start", job_service.run_sync)
    with TestClient(app) as test_client:
        yield test_client


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(list(contents))
        parts = self.script.pop(0)
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
        )


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


def call(name: str, **args) -> types.Part:
    return types.Part(function_call=types.FunctionCall(id=f"c-{name}", name=name, args=args))


def text(value: str) -> types.Part:
    return types.Part(text=value)


def thought(value: str) -> types.Part:
    return types.Part(text=value, thought=True)


@pytest.fixture()
def agent_on(monkeypatch):
    """Pretend a key is configured, and route make_client to the fake."""
    from app.agent import runner
    from app.config import reload_settings

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_ENABLED", "true")
    reload_settings()
    holder: dict = {}

    def install(script):
        holder["client"] = FakeClient(script)
        monkeypatch.setattr(runner, "make_client", lambda: holder["client"])
        return holder["client"]

    yield install
    reload_settings()


def events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


# --------------------------------------------------------------------------
# the index
# --------------------------------------------------------------------------


def test_every_document_kind_is_indexed_and_searchable(finished_job):  # noqa: F811
    client, job_id = finished_job
    status = client.post(f"/api/jobs/{job_id}/index").json()
    assert status["documents"] == status["documents_total"]
    assert status["passages"] >= status["documents"]

    from app.rag import store

    hits = store.search(job_id, "airtel bill amount due", k=3)
    assert hits and any("airtel" in h["filename"] for h in hits)
    bills_only = store.search(job_id, "amount payable", document_type="bill")
    assert bills_only and all(h["document_type"] == "bill" for h in bills_only)


def test_indexing_is_incremental(finished_job):  # noqa: F811
    client, job_id = finished_job
    first = client.post(f"/api/jobs/{job_id}/index").json()
    again = client.post(f"/api/jobs/{job_id}/index").json()
    assert again["passages"] == first["passages"]
    rebuilt = client.post(f"/api/jobs/{job_id}/index?rebuild=true").json()
    assert rebuilt["passages"] == first["passages"]


def test_passages_are_masked():
    from app.rag.chunking import mask_text_block

    masked = mask_text_block(
        "Consumer Number: 9820045612\nmail aarav.sharma@gmail.com\nCard 4477 1234 5678 4417\n"
        "Total Amount Payable 799.00"
    )
    assert "9820045612" not in masked
    assert "aarav.sharma" not in masked
    assert "4477 1234 5678" not in masked
    assert "799.00" in masked


# --------------------------------------------------------------------------
# the tools
# --------------------------------------------------------------------------


def test_tools_compute_from_the_ledger(finished_job):  # noqa: F811
    _, job_id = finished_job
    from app.agent.tools import AgentContext, run_tool

    ctx = AgentContext(job_id)
    overview = run_tool(ctx, "get_financial_overview", {})
    assert overview["transactions"] > 0
    assert overview["total_spent_excluding_transfers"] > 0

    by_month = run_tool(ctx, "aggregate_spending", {"group_by": "month"})
    assert round(by_month["grand_total"], 2) == overview["total_spent_excluding_transfers"]

    swiggy = run_tool(ctx, "search_transactions", {"merchant": "swiggy", "limit": 5})
    assert swiggy["matched"] >= 1
    assert all("swiggy" in t["merchant"].lower() for t in swiggy["transactions"])

    assert run_tool(ctx, "get_bills", {})["bills"]
    assert "error" in run_tool(ctx, "aggregate_spending", {"group_by": "planet"})
    assert "error" in run_tool(ctx, "no_such_tool", {})


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------


def test_chat_needs_a_model(finished_job):  # noqa: F811
    client, job_id = finished_job
    response = client.post(f"/api/jobs/{job_id}/chat", json={"message": "hi"})
    assert response.status_code == 503


def test_chat_runs_tools_then_answers(finished_job, agent_on):  # noqa: F811
    client, job_id = finished_job
    fake = agent_on(
        [
            [thought("Plan: totals first, then the bill."), call("get_financial_overview")],
            [call("search_documents", query="airtel bill due date")],
            [text("You spent a lot. Your Airtel bill [bill_airtel_jul2025.pdf, page 1] was paid.")],
        ]
    )
    response = client.post(
        f"/api/jobs/{job_id}/chat",
        json={"message": "How am I doing and what did the Airtel bill say?"},
    )
    assert response.status_code == 200
    stream = events(response)
    kinds = [e["type"] for e in stream]
    assert kinds[0] == "start"
    assert "thought" in kinds
    assert [e["name"] for e in stream if e["type"] == "tool_call"] == [
        "get_financial_overview",
        "search_documents",
    ]
    answer = stream[-1]
    assert answer["type"] == "answer"
    assert answer["tools_used"] == ["get_financial_overview", "search_documents"]
    # only the cited file is listed as a source
    assert [s["filename"] for s in answer["sources"]] == ["bill_airtel_jul2025.pdf"]

    # the tool results went back to the model as function responses
    last_turn = fake.models.calls[-1][-1]
    assert last_turn.parts[0].function_response.name == "search_documents"


def test_chat_stops_at_the_step_budget(finished_job, agent_on, monkeypatch):  # noqa: F811
    client, job_id = finished_job
    monkeypatch.setenv("AGENT_MAX_STEPS", "2")
    from app.config import reload_settings

    reload_settings()
    agent_on(
        [
            [call("list_segments")],
            [call("list_documents")],
            [text("Here is what I found so far.")],  # the forced, tool-less final round
        ]
    )
    stream = events(client.post(f"/api/jobs/{job_id}/chat", json={"message": "dig deep"}))
    assert stream[-1]["type"] == "answer"
    assert stream[-1]["steps"] == 2


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def test_report_is_generated_saved_and_downloadable(finished_job, agent_on):  # noqa: F811
    client, job_id = finished_job
    agent_on(
        [
            [call("get_financial_overview"), call("list_segments")],
            [call("get_bills")],
            [
                text(
                    "# Personal Financial Report\n\n## Executive summary\n- You spent **₹1,000**.\n\n"
                    "| Month | Spent |\n|---|---|\n| 2025-06 | ₹500 |\n\n## Recommendations\n1. Cook more."
                )
            ],
        ]
    )
    assert client.get(f"/api/jobs/{job_id}/financial-report").status_code == 404

    stream = events(client.post(f"/api/jobs/{job_id}/financial-report"))
    final = stream[-1]
    assert final["type"] == "report", stream
    assert "Appendix" in final["markdown"]
    assert final["meta"]["pdf"] is True

    saved = client.get(f"/api/jobs/{job_id}/financial-report").json()
    assert saved["markdown"].startswith("# Personal Financial Report")

    pdf = client.get(f"/api/jobs/{job_id}/financial-report/pdf")
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"

    bundle = client.get(f"/api/jobs/{job_id}/export/finpilot_export.zip")
    assert bundle.status_code == 200
    import io
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(bundle.content)).namelist()
    assert {"transactions.csv", "obligations.csv", "financial_report.pdf"} <= set(names)


def test_csv_exports_are_rebuilt_with_segments(finished_job):  # noqa: F811
    client, job_id = finished_job
    ran = client.post(f"/api/jobs/{job_id}/segments").json()
    csv = client.get(f"/api/jobs/{job_id}/export/transactions.csv").text
    header = csv.splitlines()[0].split(",")
    assert "segment_name" in header
    if ran["segments"]:
        assert any(line.split(",")[header.index("segment_id")] not in ("", "-1")
                   for line in csv.splitlines()[1:])
    bills = client.get(f"/api/jobs/{job_id}/export/obligations.csv").text
    assert len(bills.splitlines()) > 1


# --------------------------------------------------------------------------
# chart tags
# --------------------------------------------------------------------------


def test_chat_keeps_only_drawable_chart_tags(finished_job, agent_on):  # noqa: F811
    client, job_id = finished_job
    agent_on([[text(
        "Here is the trend. [[chart:monthly]] Habits: [[chart:segment_split]] [[chart:bogus]] [[chart:segment:7]]"
    )]])
    answer = events(client.post(f"/api/jobs/{job_id}/chat", json={"message": "show me"}))[-1]
    assert answer["type"] == "answer"
    # not segmented yet: only the ledger chart survives, on its own line
    assert "\n\n[[chart:monthly]]\n\n" in answer["text"]
    assert "segment_split" not in answer["text"]
    assert "bogus" not in answer["text"]
    assert "segment:7" not in answer["text"]


def test_report_draws_segment_charts_and_captions_the_markdown(finished_job, agent_on):  # noqa: F811
    client, job_id = finished_job
    ran = client.post(f"/api/jobs/{job_id}/segments").json()
    if not ran["segments"]:
        pytest.skip("synthetic ledger produced no segments")
    seg_id = ran["detail"][0]["segment_id"]
    agent_on([[text(
        "# Personal Financial Report\n\n## Where your money goes\n[[chart:segment_split]]\n\n"
        f"### A habit\nText. [[chart:segment:{seg_id}]]\n\n[[chart:monthly]]\n"
    )]])
    report = events(client.post(f"/api/jobs/{job_id}/financial-report"))[-1]
    assert report["type"] == "report", report
    assert report["meta"]["pdf"] is True
    assert "[[chart:segment_split]]" in report["markdown"]
    assert f"[[chart:segment:{seg_id}]]" in report["markdown"]

    from app.agent.charts import pdf_flowables
    from app.insights import build_insights

    data = build_insights(job_id)
    for tag, arg in (("segment_split", None), ("segment", str(seg_id)), ("monthly", None),
                     ("money_flow", None), ("calendar", None), ("kpis", None)):
        assert pdf_flowables(tag, arg, data, "Helvetica", 480), tag

    md = client.get(f"/api/jobs/{job_id}/financial-report/md").text
    assert "[[chart:" not in md
    assert "see the PDF" in md
