"""The REST API, driven through a real client.

Jobs are run synchronously here (``start`` is patched to ``run_sync``) so the
tests are deterministic; the background path itself is the same function, just
handed to a thread.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATEMENT = "hdfc_savings_jun_aug15.csv"
SHEET = "personal_expenses.xlsx"
CARD = "hdfc_card_jul2025.pdf"


@pytest.fixture()
def client(tmp_settings, monkeypatch) -> TestClient:
    """A client whose jobs run inline, against a throwaway database."""
    from app import jobs as job_service
    from app.main import app

    monkeypatch.setattr(job_service, "start", job_service.run_sync)
    with TestClient(app) as test_client:
        yield test_client


def upload(client: TestClient, paths: list[Path]) -> dict:
    files = [("files", (p.name, p.read_bytes())) for p in paths]
    response = client.post("/api/jobs", files=files)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def finished_job(client: TestClient, synthetic_dir: Path) -> tuple[TestClient, str]:
    created = upload(
        client,
        [
            synthetic_dir / STATEMENT,
            synthetic_dir / "hdfc_savings_aug.csv",
            synthetic_dir / "sbi_savings_jun_aug.csv",
            synthetic_dir / CARD,
            synthetic_dir / SHEET,
            *sorted((synthetic_dir / "emails").glob("*.eml")),
            *sorted((synthetic_dir / "bills").glob("*.pdf")),
        ],
    )
    job_id = created["job_id"]
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "done", status.get("error")
    return client, job_id


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------


def test_health_reports_what_is_actually_configured(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["extraction"] == "code", "nothing here calls a model"
    assert isinstance(body["embeddings"], bool)
    assert body["llm"]["enabled"] is False
    # public endpoint: no template names, model ids or payment mode
    assert "templates" not in body
    assert "model" not in body["agent"]


# --------------------------------------------------------------------------
# upload and polling
# --------------------------------------------------------------------------


def test_upload_creates_a_job_and_lists_its_files(client: TestClient, synthetic_dir: Path):
    created = upload(client, [synthetic_dir / STATEMENT, synthetic_dir / SHEET])
    assert created["job_id"].startswith("job_")
    assert {f["filename"] for f in created["files"]} == {STATEMENT, SHEET}


def test_an_empty_upload_is_rejected(client: TestClient):
    response = client.post("/api/jobs", files=[("files", ("empty.csv", b""))])
    assert response.status_code == 400


def test_uploading_the_same_file_twice_stores_it_once(client: TestClient, synthetic_dir: Path):
    blob = (synthetic_dir / STATEMENT).read_bytes()
    response = client.post(
        "/api/jobs",
        files=[("files", (STATEMENT, blob)), ("files", ("a-copy.csv", blob))],
    )
    assert response.status_code == 201
    assert len(response.json()["files"]) == 1, "identical bytes are one document"


def test_status_reports_the_stepper_stages(finished_job):
    client, job_id = finished_job
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["stage"] == "done"
    assert status["progress"] == 1.0
    assert status["stages"] == [
        "detect", "extract", "map", "normalize", "validate", "merge", "link", "done",
    ]
    assert status["totals"]["rows"] == 137


def test_per_file_status_carries_what_the_ui_shows(finished_job):
    client, job_id = finished_job
    files = {f["filename"]: f for f in client.get(f"/api/jobs/{job_id}").json()["files"]}
    statement = files[STATEMENT]
    assert statement["status"] == "processed"
    assert statement["document_type"] == "bank_statement"
    assert statement["issuer"] == "HDFC Bank"
    assert statement["account"] == "HDFC-XX7890"
    assert statement["statement_start"] == "2025-06-01"
    assert statement["extraction_method"] == "template"
    assert statement["rows"] == 83
    assert files[CARD]["document_type"] == "cc_statement"


def test_an_unknown_job_is_a_404(client: TestClient):
    assert client.get("/api/jobs/job_nope").status_code == 404
    assert client.get("/api/jobs/job_nope/preview").status_code == 404
    assert client.get("/api/jobs/job_nope/report").status_code == 404


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------


def test_preview_paginates(finished_job):
    client, job_id = finished_job
    first = client.get(f"/api/jobs/{job_id}/preview?limit=10&offset=0").json()
    assert first["total"] == 137
    assert len(first["rows"]) == 10
    second = client.get(f"/api/jobs/{job_id}/preview?limit=10&offset=10").json()
    assert {r["txn_id"] for r in first["rows"]} & {r["txn_id"] for r in second["rows"]} == set()


def test_preview_offers_the_filter_values(finished_job):
    client, job_id = finished_job
    body = client.get(f"/api/jobs/{job_id}/preview?limit=1").json()
    assert set(body["accounts"]) == {"HDFC-XX7890", "SBI-XX8901", "CARD-XX4417", "CASH"}
    assert "UPI" in body["payment_modes"]


@pytest.mark.parametrize(
    ("query", "check"),
    [
        ("account=CASH", lambda r: r["account"] == "CASH"),
        ("direction=credit", lambda r: r["direction"] == "credit"),
        ("payment_mode=UPI", lambda r: r["payment_mode"] == "UPI"),
        ("date_from=2025-08-01", lambda r: r["date"] >= "2025-08-01"),
        ("date_to=2025-06-30", lambda r: r["date"] <= "2025-06-30"),
        ("search=swiggy", lambda r: "swiggy" in json.dumps(r).lower()),
    ],
)
def test_preview_filters(finished_job, query, check):
    client, job_id = finished_job
    rows = client.get(f"/api/jobs/{job_id}/preview?limit=500&{query}").json()["rows"]
    assert rows
    assert all(check(r) for r in rows)


def test_preview_needs_review_filter_is_a_real_tri_state(finished_job):
    client, job_id = finished_job
    unflagged = client.get(f"/api/jobs/{job_id}/preview?limit=500&needs_review=false").json()
    flagged = client.get(f"/api/jobs/{job_id}/preview?limit=500&needs_review=true").json()
    assert unflagged["total"] == 137
    assert flagged["total"] == 0


@pytest.mark.parametrize("sort", ["date", "amount", "merchant", "account"])
def test_preview_sorting(finished_job, sort):
    client, job_id = finished_job
    ascending = client.get(f"/api/jobs/{job_id}/preview?limit=500&sort={sort}&order=asc").json()
    descending = client.get(
        f"/api/jobs/{job_id}/preview?limit=500&sort={sort}&order=desc"
    ).json()
    assert [r["txn_id"] for r in ascending["rows"]] == [
        r["txn_id"] for r in reversed(descending["rows"])
    ]


def test_an_unsupported_sort_is_rejected(finished_job):
    client, job_id = finished_job
    assert client.get(f"/api/jobs/{job_id}/preview?sort=whatever").status_code == 422


# --------------------------------------------------------------------------
# the row side panel
# --------------------------------------------------------------------------


def test_transaction_detail_shows_every_linked_source(finished_job):
    client, job_id = finished_job
    rows = client.get(f"/api/jobs/{job_id}/preview?limit=500&search=myntra").json()["rows"]
    purchase = next(r for r in rows if r["direction"] == "debit")
    detail = client.get(f"/api/jobs/{job_id}/transactions/{purchase['txn_id']}").json()

    link_types = {link["link_type"] for link in detail["links"]}
    assert "primary" in link_types
    assert "email" in link_types, "the order email is attached"
    assert all(link["filename"] for link in detail["links"])
    assert detail["items"], "the order's line items are listed"
    assert detail["raw_values"], "the raw extracted cells are available"


def test_the_raw_values_in_the_side_panel_are_masked(finished_job):
    client, job_id = finished_job
    rows = client.get(f"/api/jobs/{job_id}/preview?limit=500&search=rent").json()["rows"]
    detail = client.get(f"/api/jobs/{job_id}/transactions/{rows[0]['txn_id']}").json()
    blob = json.dumps(detail)
    for secret in ("50100234567890", "ramesh.kumar@okaxis"):
        assert secret not in blob


def test_an_unknown_transaction_is_a_404(finished_job):
    client, job_id = finished_job
    assert client.get(f"/api/jobs/{job_id}/transactions/t_nope").status_code == 404


# --------------------------------------------------------------------------
# obligations and the summary card
# --------------------------------------------------------------------------


def test_obligations(finished_job):
    client, job_id = finished_job
    bills = client.get(f"/api/jobs/{job_id}/obligations").json()
    assert len(bills) == 3
    by_status = {b["biller"]: b["status"] for b in bills}
    assert by_status == {"Tata Power": "paid", "Airtel": "paid", "ACT Fibernet": "overdue"}
    assert all("X" in b["consumer_no"] for b in bills), "consumer numbers stay masked"


def test_summary_card(finished_job):
    client, job_id = finished_job
    summary = client.get(f"/api/jobs/{job_id}/summary").json()
    assert summary["rows"] == 137
    assert summary["duplicates_merged"] == 19
    assert summary["transfers_found"] == 1
    assert summary["card_payments_found"] == 1
    assert summary["emails_linked"] == 22
    assert summary["bills_found"] == 3
    assert summary["rows_needing_review"] == 0
    assert summary["reconciliation_pass_rate"] == 1.0
    assert len(summary["reconciliations"]) == 4
    assert all(r["ok"] for r in summary["reconciliations"])


def test_the_report_has_per_file_detail(finished_job):
    client, job_id = finished_job
    report = client.get(f"/api/jobs/{job_id}/report").json()
    assert len(report["files"]) == 36
    assert report["totals"]["rows"] == 137


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------


def test_transactions_export_is_the_contract_shape(finished_job):
    from ingestion.schema import ALL_TRANSACTION_COLUMNS

    client, job_id = finished_job
    response = client.get(f"/api/jobs/{job_id}/export/transactions.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(response.text))
    assert reader.fieldnames == list(ALL_TRANSACTION_COLUMNS)
    assert len(list(reader)) == 137


def test_obligations_export(finished_job):
    client, job_id = finished_job
    response = client.get(f"/api/jobs/{job_id}/export/obligations.csv")
    assert response.status_code == 200
    assert len(list(csv.DictReader(io.StringIO(response.text)))) == 3


def test_report_export(finished_job):
    client, job_id = finished_job
    response = client.get(f"/api/jobs/{job_id}/export/ingestion_report.json")
    assert response.status_code == 200
    assert "totals" in response.json()


def test_an_arbitrary_filename_cannot_be_downloaded(finished_job):
    """No path traversal: the name is checked against an allow-list."""
    client, job_id = finished_job
    for name in ("secrets.txt", "../../.env", "..%2F..%2F.env"):
        assert client.get(f"/api/jobs/{job_id}/export/{name}").status_code in (404, 400)


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------


@pytest.fixture()
def locked_pdf(tmp_path: Path, synthetic_dir: Path) -> Path:
    import pikepdf

    locked = tmp_path / "locked_card.pdf"
    with pikepdf.open(synthetic_dir / CARD) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(owner="o", user="hunter2"))
    return locked


def test_a_protected_pdf_pauses_the_job_for_input(client: TestClient, locked_pdf: Path):
    created = upload(client, [locked_pdf])
    job_id = created["job_id"]
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "needs_input"
    assert status["files"][0]["needs_password"] is True
    assert status["files"][0]["rows"] == 0


def test_supplying_the_password_unlocks_and_reruns(client: TestClient, locked_pdf: Path):
    created = upload(client, [locked_pdf])
    job_id = created["job_id"]
    doc_id = client.get(f"/api/jobs/{job_id}").json()["files"][0]["doc_id"]

    response = client.post(
        f"/api/jobs/{job_id}/files/{doc_id}/password", json={"password": "hunter2"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert client.get(f"/api/jobs/{job_id}/preview").json()["total"] == 10


def test_the_password_is_never_persisted(client: TestClient, locked_pdf: Path):
    """Principle 6: it unlocks the file and is not written anywhere."""
    created = upload(client, [locked_pdf])
    job_id = created["job_id"]
    doc_id = client.get(f"/api/jobs/{job_id}").json()["files"][0]["doc_id"]
    client.post(f"/api/jobs/{job_id}/files/{doc_id}/password", json={"password": "hunter2"})

    from app.config import get_settings

    database = Path(get_settings().database_url.split("///", 1)[-1])
    assert b"hunter2" not in database.read_bytes()
    report = client.get(f"/api/jobs/{job_id}/report").text
    assert "hunter2" not in report


def test_a_wrong_password_leaves_the_job_waiting(client: TestClient, locked_pdf: Path):
    created = upload(client, [locked_pdf])
    job_id = created["job_id"]
    doc_id = client.get(f"/api/jobs/{job_id}").json()["files"][0]["doc_id"]
    response = client.post(
        f"/api/jobs/{job_id}/files/{doc_id}/password", json={"password": "wrong"}
    )
    assert response.json()["files"][0]["needs_password"] is True


def test_a_password_for_an_unknown_file_is_a_404(client: TestClient, synthetic_dir: Path):
    job_id = upload(client, [synthetic_dir / STATEMENT])["job_id"]
    response = client.post(
        f"/api/jobs/{job_id}/files/d_nope/password", json={"password": "x"}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------
# column mapping
# --------------------------------------------------------------------------


@pytest.fixture()
def unknown_layout(tmp_path: Path) -> Path:
    """A layout no template or keyword recognises."""
    path = tmp_path / "mystery_bank.csv"
    path.write_text(
        "Col A,Col B,Col C\r\n"
        "01/06/2025,a longer piece of narration text here,-100.00\r\n"
        "02/06/2025,another line of narration text here,-250.50\r\n"
        "03/06/2025,a third line of narration text here,1200.00\r\n",
        encoding="utf-8",
        newline="",
    )
    return path


def test_an_unknown_layout_asks_rather_than_guesses(client: TestClient, unknown_layout: Path):
    job_id = upload(client, [unknown_layout])["job_id"]
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "needs_input"
    assert status["files"][0]["needs_mapping"] is True

    pending = client.get(f"/api/jobs/{job_id}/mappings/pending").json()
    assert len(pending) == 1
    dialog = pending[0]
    assert dialog["headers"] == ["Col A", "Col B", "Col C"]
    assert len(dialog["sample_rows"]) == 3, "the dialog previews the real rows"
    assert dialog["uncertain"]
    assert {f["name"] for f in dialog["available_fields"]} >= {"date", "description", "amount"}
    assert "signed" in dialog["amount_styles"]


def test_confirming_a_mapping_finishes_the_job(client: TestClient, unknown_layout: Path):
    job_id = upload(client, [unknown_layout])["job_id"]
    doc_id = client.get(f"/api/jobs/{job_id}").json()["files"][0]["doc_id"]

    response = client.post(
        f"/api/jobs/{job_id}/mappings/{doc_id}",
        json={
            "columns": {"date": "Col A", "description": "Col B", "amount": "Col C"},
            "amount_style": "signed",
            "save_as_template": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "done"
    rows = client.get(f"/api/jobs/{job_id}/preview").json()
    assert rows["total"] == 3
    assert {r["direction"] for r in rows["rows"]} == {"debit", "credit"}


def test_a_confirmed_mapping_is_saved_as_a_template(
    client: TestClient, unknown_layout: Path, tmp_path: Path, monkeypatch
):
    """The next file of this shape must not ask again."""
    from ingestion import mapping as mapping_module

    monkeypatch.setattr(mapping_module, "TEMPLATES_DIR", tmp_path / "templates")
    job_id = upload(client, [unknown_layout])["job_id"]
    doc_id = client.get(f"/api/jobs/{job_id}").json()["files"][0]["doc_id"]
    client.post(
        f"/api/jobs/{job_id}/mappings/{doc_id}",
        json={
            "columns": {"date": "Col A", "description": "Col B", "amount": "Col C"},
            "amount_style": "signed",
            "save_as_template": True,
            "template_name": "Mystery Bank CSV",
        },
    )
    library = mapping_module.TemplateLibrary(tmp_path / "templates")
    found, how = library.find(["Col A", "Col B", "Col C"])
    assert found is not None and how == "exact"
    assert found.columns["date"] == "Col A"


@pytest.mark.parametrize(
    "body",
    [
        {"columns": {"description": "Col B", "amount": "Col C"}, "amount_style": "signed"},
        {"columns": {"date": "Col A", "description": "Col B"}, "amount_style": "signed"},
        {"columns": {"date": "Col A", "description": "Col B", "amount": "Col C"},
         "amount_style": "nonsense"},
    ],
)
def test_an_incomplete_mapping_is_rejected(client: TestClient, unknown_layout: Path, body):
    job_id = upload(client, [unknown_layout])["job_id"]
    doc_id = client.get(f"/api/jobs/{job_id}").json()["files"][0]["doc_id"]
    response = client.post(f"/api/jobs/{job_id}/mappings/{doc_id}", json=body)
    assert response.status_code == 422


# --------------------------------------------------------------------------
# deletion
# --------------------------------------------------------------------------


def test_deleting_a_job_removes_it(client: TestClient, synthetic_dir: Path):
    job_id = upload(client, [synthetic_dir / STATEMENT])["job_id"]
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------


def test_running_a_job_twice_does_not_duplicate_rows(client: TestClient, synthetic_dir: Path):
    from app import jobs as job_service

    job_id = upload(client, [synthetic_dir / STATEMENT])["job_id"]
    before = client.get(f"/api/jobs/{job_id}/preview").json()["total"]
    job_service.run_sync(job_id)
    after = client.get(f"/api/jobs/{job_id}/preview").json()["total"]
    assert before == after == 83


# --------------------------------------------------------------------------
# two jobs describing the same transactions
# --------------------------------------------------------------------------


def test_a_second_job_sharing_transactions_does_not_collide(
    client: TestClient, synthetic_dir: Path
):
    """Regression: the two HDFC statements overlap by 19 transactions.

    A transaction's id does not include its source, so the same payment
    uploaded in a *different* file produces the same ``txn_id`` under a
    different ``doc_id``. Writing the second job used to violate the uniqueness
    of ``txn_links`` -- and because that happened after the pipeline had
    already succeeded, the job hung on "running" forever instead of reporting.
    """
    first = upload(client, [synthetic_dir / STATEMENT])["job_id"]
    assert client.get(f"/api/jobs/{first}").json()["status"] == "done"

    second = upload(client, [synthetic_dir / "hdfc_savings_aug.csv"])["job_id"]
    status = client.get(f"/api/jobs/{second}").json()
    assert status["status"] == "done", status.get("error")
    assert status["totals"]["rows"] == 33

    # both jobs still read back correctly, each seeing its own file's rows
    assert client.get(f"/api/jobs/{first}/preview").json()["total"] == 83
    assert client.get(f"/api/jobs/{second}/preview").json()["total"] == 33


def test_reuploading_a_corrected_file_replaces_its_links(
    client: TestClient, synthetic_dir: Path, tmp_path: Path
):
    """A corrected export has different bytes, so a different doc_id, but
    describes the same transactions. The newer links must replace the old."""
    # byte-level, so the line endings of the fixture survive untouched: a
    # changed newline would change the parse, not just the hash
    corrected = tmp_path / "hdfc_corrected.csv"
    corrected.write_bytes((synthetic_dir / STATEMENT).read_bytes() + b"\r\n")

    first = upload(client, [synthetic_dir / STATEMENT])["job_id"]
    second = upload(client, [corrected])["job_id"]
    status = client.get(f"/api/jobs/{second}").json()
    assert status["status"] == "done", status.get("error")

    detail_rows = client.get(f"/api/jobs/{second}/preview?limit=1").json()["rows"]
    detail = client.get(f"/api/jobs/{second}/transactions/{detail_rows[0]['txn_id']}").json()
    refs = [link["source_ref"] for link in detail["links"]]
    assert len(refs) == len(set(refs)), "a link is recorded once, not once per upload"
    assert client.get(f"/api/jobs/{first}").json()["status"] == "done"
