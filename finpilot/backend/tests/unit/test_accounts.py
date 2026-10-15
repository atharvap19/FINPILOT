"""Accounts, per-user job isolation, plan limits and payments -- auth ON."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def secured(tmp_settings, monkeypatch):
    """The real thing: auth required, small free limits, dev payments on."""
    from app import jobs as job_service
    from app.config import reload_settings
    from app.db.session import reset_engine

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("FREE_FILES_PER_MONTH", "5")
    monkeypatch.setenv("FREE_CHAT_PER_MONTH", "2")
    monkeypatch.setenv("PAYMENTS_DEV_MODE", "true")
    reload_settings()
    reset_engine()
    monkeypatch.setattr(job_service, "start", job_service.run_sync)
    yield
    reload_settings()


def new_client() -> TestClient:
    from app.main import app

    return TestClient(app)


def register(client: TestClient, email: str, password: str = "correct-horse") -> dict:
    response = client.post("/api/auth/register", json={"email": email, "password": password, "accept_terms": True, "confirm_age": True})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client: TestClient, paths: list[Path]):
    files = [("files", (p.name, p.read_bytes())) for p in paths]
    return client.post("/api/jobs", files=files)


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


def test_everything_needs_a_session(secured):
    with new_client() as client:
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/jobs").status_code == 401
        assert client.get("/api/jobs/job_whatever").status_code == 401
        assert client.get("/api/health").status_code == 200


def test_register_login_logout(secured):
    with new_client() as client:
        account = register(client, "Asha@Example.com")
        assert account["user"]["email"] == "asha@example.com"
        assert account["plan"] == "free"
        assert client.get("/api/auth/me").status_code == 200

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401

        bad = client.post("/api/auth/login", json={"email": "asha@example.com", "password": "nope-nope"})
        assert bad.status_code == 401
        good = client.post("/api/auth/login", json={"email": "ASHA@example.com", "password": "correct-horse"})
        assert good.status_code == 200
        assert client.get("/api/auth/me").json()["user"]["email"] == "asha@example.com"


@pytest.mark.parametrize(
    ("email", "password", "message"),
    [("not-an-email", "long-enough", "valid email"), ("a@b.co", "short", "at least")],
)
def test_register_validates(secured, email, password, message):
    with new_client() as client:
        response = client.post("/api/auth/register", json={"email": email, "password": password, "accept_terms": True, "confirm_age": True})
        assert response.status_code == 400
        assert message in response.json()["detail"]


def test_duplicate_email_is_refused(secured):
    with new_client() as client:
        register(client, "dup@example.com")
    with new_client() as client:
        response = client.post("/api/auth/register", json={"email": "dup@example.com", "password": "another-one", "accept_terms": True, "confirm_age": True})
        assert response.status_code == 400


def test_passwords_are_not_stored():
    from app.auth import hash_password, verify_password

    stored = hash_password("correct-horse")
    assert "correct-horse" not in stored
    assert verify_password("correct-horse", stored)
    assert not verify_password("wrong-horse", stored)


# --------------------------------------------------------------------------
# isolation
# --------------------------------------------------------------------------


def test_users_cannot_see_each_others_jobs(secured, synthetic_dir: Path):
    with new_client() as alice:
        register(alice, "alice@example.com")
        created = upload(alice, [synthetic_dir / "hdfc_savings_aug.csv"])
        assert created.status_code == 201, created.text
        job_id = created.json()["job_id"]
        assert alice.get(f"/api/jobs/{job_id}").status_code == 200
        assert [j["job_id"] for j in alice.get("/api/jobs").json()] == [job_id]

    with new_client() as bob:
        register(bob, "bob@example.com")
        assert bob.get("/api/jobs").json() == []
        for path in ("", "/summary", "/preview", "/obligations", "/export/transactions.csv",
                     "/segments", "/index", "/financial-report"):
            assert bob.get(f"/api/jobs/{job_id}{path}").status_code == 404, path
        assert bob.delete(f"/api/jobs/{job_id}").status_code == 404
        assert bob.post(f"/api/jobs/{job_id}/chat", json={"message": "hi"}).status_code == 404


def test_first_account_adopts_jobs_from_before_accounts(secured):
    from app.db.models import Job
    from app.db.session import session_scope

    with session_scope() as session:
        session.add(Job(job_id="job_legacy0001", status="done"))
    with new_client() as client:
        register(client, "owner@example.com")
        assert "job_legacy0001" in [j["job_id"] for j in client.get("/api/jobs").json()]
    with new_client() as client:
        register(client, "second@example.com")
        assert client.get("/api/jobs/job_legacy0001").status_code == 404


# --------------------------------------------------------------------------
# limits
# --------------------------------------------------------------------------


def test_free_upload_limit_returns_402(secured, synthetic_dir: Path):
    emails = sorted((synthetic_dir / "emails").glob("*.eml"))
    with new_client() as client:
        register(client, "limits@example.com")
        assert upload(client, emails[:4]).status_code == 201
        blocked = upload(client, emails[4:6])
        assert blocked.status_code == 402
        detail = blocked.json()["detail"]
        assert detail["code"] == "limit_reached"
        assert detail["feature"] == "file"
        assert (detail["limit"], detail["used"]) == (5, 4)
        usage = client.get("/api/auth/me").json()["usage"]["file"]
        assert usage == {"used": 4, "limit": 5}


def test_chat_is_metered_per_answer(secured, synthetic_dir: Path, monkeypatch):
    from app.agent import runner
    from app.config import reload_settings
    from tests.unit.test_agent import FakeClient, text

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_ENABLED", "true")
    reload_settings()
    monkeypatch.setattr(runner, "make_client", lambda: FakeClient([[text("one")], [text("two")]]))

    with new_client() as client:
        register(client, "chatty@example.com")
        job_id = upload(client, [synthetic_dir / "hdfc_savings_aug.csv"]).json()["job_id"]
        for _ in range(2):
            response = client.post(f"/api/jobs/{job_id}/chat", json={"message": "how am I doing"})
            assert response.status_code == 200
            assert '"type": "answer"' in response.text
        assert client.get("/api/auth/me").json()["usage"]["chat"]["used"] == 2
        blocked = client.post(f"/api/jobs/{job_id}/chat", json={"message": "and now?"})
        assert blocked.status_code == 402
        assert blocked.json()["detail"]["feature"] == "chat"


# --------------------------------------------------------------------------
# payments
# --------------------------------------------------------------------------


def test_dev_checkout_upgrades_to_pro(secured):
    with new_client() as client:
        register(client, "payer@example.com")
        plans = client.get("/api/billing/plans").json()
        assert plans["provider"] == "dev"
        assert {p["code"] for p in plans["prices"]} == {"pro_monthly", "pro_yearly"}

        order = client.post("/api/billing/checkout", json={"plan_code": "pro_yearly"}).json()
        assert order["provider"] == "dev"
        account = client.post("/api/billing/dev-complete", json={"order_id": order["order_id"]}).json()
        assert account["plan"] == "pro"
        assert account["usage"]["chat"]["limit"] == 300
        # completing the same order twice must not add another year
        again = client.post("/api/billing/dev-complete", json={"order_id": order["order_id"]}).json()
        assert again["pro_until"] == account["pro_until"]


def test_razorpay_signature_is_verified(secured, monkeypatch):
    from app import billing
    from app.config import reload_settings

    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s3cret")
    reload_settings()
    monkeypatch.setattr(billing, "_razorpay_order", lambda price, user: {"id": "order_TEST123"})

    with new_client() as client:
        register(client, "rzp@example.com")
        order = client.post("/api/billing/checkout", json={"plan_code": "pro_monthly"}).json()
        assert order == order | {"provider": "razorpay", "order_id": "order_TEST123", "key_id": "rzp_test_key"}

        forged = client.post("/api/billing/verify", json={
            "order_id": "order_TEST123", "payment_id": "pay_1", "signature": "deadbeef"})
        assert forged.status_code == 400
        assert client.get("/api/auth/me").json()["plan"] == "free"

        signature = hmac.new(b"s3cret", b"order_TEST123|pay_1", hashlib.sha256).hexdigest()
        # a failed attempt marks the order failed; the genuine one still
        # completes because verify re-checks the signature, not the status
        ok = client.post("/api/billing/verify", json={
            "order_id": "order_TEST123", "payment_id": "pay_1", "signature": signature})
        assert ok.status_code == 200, ok.text
        assert ok.json()["plan"] == "pro"


def test_checkout_without_any_provider_is_refused(secured, monkeypatch):
    from app.config import reload_settings

    monkeypatch.setenv("PAYMENTS_DEV_MODE", "false")
    reload_settings()
    with new_client() as client:
        register(client, "nopay@example.com")
        response = client.post("/api/billing/checkout", json={"plan_code": "pro_monthly"})
        assert response.status_code == 400
        assert "not configured" in response.json()["detail"]


# --------------------------------------------------------------------------
# shared files, separate ledgers
# --------------------------------------------------------------------------


def _ledger(client: TestClient, job_id: str) -> tuple[int, int]:
    preview = client.get(f"/api/jobs/{job_id}/preview?limit=1").json()
    bills = client.get(f"/api/jobs/{job_id}/obligations").json()
    return preview["total"], len(bills)


def test_same_files_uploaded_by_two_users_stay_separate(secured, synthetic_dir: Path, monkeypatch):
    monkeypatch.setenv("FREE_FILES_PER_MONTH", "50")
    from app.config import reload_settings

    reload_settings()
    files = [synthetic_dir / "hdfc_savings_aug.csv", *sorted((synthetic_dir / "bills").glob("*.pdf"))]

    with new_client() as alice:
        register(alice, "alice2@example.com")
        job_a = upload(alice, files).json()["job_id"]
        before = _ledger(alice, job_a)
        assert before[0] > 0 and before[1] == 3
        alice.post(f"/api/jobs/{job_a}/segments")

        with new_client() as bob:
            register(bob, "bob2@example.com")
            job_b = upload(bob, files).json()["job_id"]
            assert _ledger(bob, job_b) == before
            # bob's upload (and his segmentation) leaves alice's ledger untouched
            bob.post(f"/api/jobs/{job_b}/segments")

        assert _ledger(alice, job_a) == before


def test_demo_upload_is_free_and_owned(secured):
    with new_client() as client:
        register(client, "demo-user@example.com")
        created = client.post("/api/demo")
        assert created.status_code == 201
        job_id = created.json()["job_id"]
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "done"
        assert client.get("/api/auth/me").json()["usage"]["file"]["used"] == 0
        insights = client.get(f"/api/jobs/{job_id}/insights").json()
        assert insights["kpis"]["money_out"]["value"] > 0
        assert insights["flow"]["sources"] and insights["flow"]["targets"]


# --------------------------------------------------------------------------
# hardening found by the black-box suite
# --------------------------------------------------------------------------


def test_repeated_wrong_passwords_are_throttled(secured):
    with new_client() as client:
        register(client, "throttle@example.com")
        client.post("/api/auth/logout")
        codes = [
            client.post("/api/auth/login", json={"email": "throttle@example.com", "password": f"wrong-{i}"}).status_code
            for i in range(6)
        ]
        assert codes[:5] == [401] * 5
        assert codes[5] == 429
        # even the right password waits out the lockout
        right = client.post("/api/auth/login", json={"email": "throttle@example.com", "password": "correct-horse"})
        assert right.status_code == 429
        assert int(right.headers["Retry-After"]) > 0


def test_csv_downloads_defuse_formulas(secured, tmp_path):
    from app import jobs as job_service

    path = tmp_path / "t.csv"
    path.write_text(
        'a,b,c\n=HYPERLINK("x"),@okaxis,-120.50\nplain,+91 98,\n', encoding="utf-8"
    )
    text = job_service.spreadsheet_safe_csv(path).decode()
    assert "'=HYPERLINK" in text
    assert "'@okaxis" in text
    assert ",-120.50" in text  # numbers are left alone
    assert "'+91 98" in text


def test_uploaded_filenames_are_reduced_to_a_bare_name():
    from app.jobs import safe_filename

    assert safe_filename("../../../etc/passwd.csv") == "passwd.csv"
    assert safe_filename("..\\..\\win.csv") == "win.csv"
    assert safe_filename(".hidden.csv") == "hidden.csv"
    assert safe_filename("") == "upload"


def test_health_is_minimal(secured):
    with new_client() as client:
        body = client.get("/api/health").json()
        assert "templates" not in body and "payments" not in body
        assert "model" not in body["agent"]
