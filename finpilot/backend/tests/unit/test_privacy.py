"""Consent, password reset, erasure, export and retention -- auth ON."""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from tests.unit.test_accounts import new_client, register, secured, upload  # noqa: F401


def _jobs_table_counts(job_id: str | None = None) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.db.models import Document, ObligationRow, TransactionRow, TxnLinkRow
    from app.db.session import session_scope

    with session_scope() as s:
        return {
            "transactions": s.scalar(select(func.count()).select_from(TransactionRow)),
            "links": s.scalar(select(func.count()).select_from(TxnLinkRow)),
            "documents": s.scalar(select(func.count()).select_from(Document)),
            "bills": s.scalar(select(func.count()).select_from(ObligationRow)),
        }


# --------------------------------------------------------------------------
# consent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"accept_terms": False, "confirm_age": True}, "Terms"),
        ({"accept_terms": True, "confirm_age": False}, "18"),
        ({}, "Terms"),
    ],
)
def test_sign_up_requires_consent_and_age(secured, body, message):  # noqa: F811
    with new_client() as client:
        r = client.post("/api/auth/register", json={"email": "c@example.com", "password": "long-enough", **body})
        assert r.status_code == 400
        assert message in r.json()["detail"]


def test_consent_is_recorded(secured):  # noqa: F811
    from app.config import get_settings
    from app.db.models import User
    from app.db.session import session_scope

    with new_client() as client:
        register(client, "consent@example.com")
    with session_scope() as s:
        user = s.query(User).filter_by(email="consent@example.com").one()
        assert user.consent_version == get_settings().consent_version
        assert user.consented_at is not None
        assert user.age_confirmed is True


# --------------------------------------------------------------------------
# password reset
# --------------------------------------------------------------------------


def test_password_reset_end_to_end(secured, monkeypatch):  # noqa: F811
    from app import mailer

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, text: sent.append((to, text)) or True)

    with new_client() as client:
        register(client, "forgetful@example.com")
        # an unknown email gets the same answer and no email
        unknown = client.post("/api/auth/forgot", json={"email": "nobody@example.com"})
        known = client.post("/api/auth/forgot", json={"email": "Forgetful@example.com"})
        assert unknown.status_code == known.status_code == 200
        assert unknown.json() == known.json()
        assert [to for to, _ in sent] == ["forgetful@example.com"]

        token = re.search(r"token=([\w-]+)", sent[0][1]).group(1)
        assert client.post("/api/auth/reset", json={"token": token, "password": "short"}).status_code == 400
        assert client.post("/api/auth/reset", json={"token": token, "password": "brand-new-pass"}).status_code == 200
        # the link is single-use, and the old session is signed out
        assert client.post("/api/auth/reset", json={"token": token, "password": "again-new-pass"}).status_code == 400
        assert client.get("/api/auth/me").status_code == 401

        old = client.post("/api/auth/login", json={"email": "forgetful@example.com", "password": "correct-horse"})
        assert old.status_code == 401
        new = client.post("/api/auth/login", json={"email": "forgetful@example.com", "password": "brand-new-pass"})
        assert new.status_code == 200


def test_expired_reset_links_are_refused(secured, monkeypatch):  # noqa: F811
    from app import mailer
    from app.db.models import PasswordReset
    from app.db.session import session_scope

    sent: list[str] = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, text: sent.append(text) or True)
    with new_client() as client:
        register(client, "late@example.com")
        client.post("/api/auth/forgot", json={"email": "late@example.com"})
        token = re.search(r"token=([\w-]+)", sent[0]).group(1)
        with session_scope() as s:
            for row in s.query(PasswordReset):
                row.expires_at = dt.datetime(2000, 1, 1)
        assert client.post("/api/auth/reset", json={"token": token, "password": "whatever-123"}).status_code == 400


# --------------------------------------------------------------------------
# erasure
# --------------------------------------------------------------------------


def test_deleting_an_upload_erases_its_ledger(secured, synthetic_dir: Path):  # noqa: F811
    before = _jobs_table_counts()
    with new_client() as client:
        register(client, "eraser@example.com")
        files = [synthetic_dir / "hdfc_savings_aug.csv", *sorted((synthetic_dir / "bills").glob("*.pdf"))]
        job_id = upload(client, files).json()["job_id"]
        assert _jobs_table_counts()["transactions"] > before["transactions"]
        assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert _jobs_table_counts() == before


def test_delete_account_erases_everything_and_spares_others(secured, synthetic_dir: Path):  # noqa: F811
    from app import jobs as job_service

    with new_client() as keeper:
        register(keeper, "keeper@example.com")
        kept_job = upload(keeper, [synthetic_dir / "sbi_savings_jun_aug.csv"]).json()["job_id"]
        kept_rows = keeper.get(f"/api/jobs/{kept_job}/preview?limit=1").json()["total"]

    with new_client() as leaver:
        register(leaver, "leaver@example.com")
        job_id = upload(leaver, [synthetic_dir / "hdfc_savings_aug.csv"]).json()["job_id"]
        assert job_service.job_dir(job_id).exists()

        wrong = leaver.request("DELETE", "/api/account", json={"password": "nope-nope", "confirm": "DELETE"})
        assert wrong.status_code == 403
        unconfirmed = leaver.request("DELETE", "/api/account", json={"password": "correct-horse", "confirm": "yes"})
        assert unconfirmed.status_code == 400

        gone = leaver.request("DELETE", "/api/account", json={"password": "correct-horse", "confirm": "DELETE"})
        assert gone.status_code == 200, gone.text
        assert gone.json()["uploads_deleted"] == 1
        assert leaver.get("/api/auth/me").status_code == 401
        assert not job_service.job_dir(job_id).exists()
        assert not job_service.export_dir(job_id).exists()
        login = leaver.post("/api/auth/login", json={"email": "leaver@example.com", "password": "correct-horse"})
        assert login.status_code == 401

    with new_client() as keeper:
        keeper.post("/api/auth/login", json={"email": "keeper@example.com", "password": "correct-horse"})
        assert keeper.get(f"/api/jobs/{kept_job}/preview?limit=1").json()["total"] == kept_rows


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


def test_export_my_data(secured, synthetic_dir: Path):  # noqa: F811
    with new_client() as client:
        register(client, "porter@example.com")
        job_id = upload(client, [synthetic_dir / "hdfc_savings_aug.csv"]).json()["job_id"]
        r = client.get("/api/account/export")
        assert r.status_code == 200
        bundle = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(bundle.namelist())
        assert {"account.json", "payments.json", "uploads.json", "README.txt"} <= names
        assert f"uploads/{job_id}/transactions.csv" in names
        assert any(n.startswith(f"uploads/{job_id}/original_files/") for n in names)
        account = json.loads(bundle.read("account.json"))
        assert account["email"] == "porter@example.com"
        assert account["consent"]["confirmed_18_or_over"] is True


# --------------------------------------------------------------------------
# retention
# --------------------------------------------------------------------------


@pytest.fixture()
def retention(secured, monkeypatch):  # noqa: F811
    """Retention on (30 days) and every email captured instead of sent."""
    from app import mailer
    from app.config import reload_settings

    monkeypatch.setenv("UPLOAD_RETENTION_DAYS", "30")
    reload_settings()
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, text: sent.append((to, subject, text)) or True)
    yield sent


def _expiry(job_id: str) -> dt.datetime:
    from app.db.models import Job
    from app.db.session import session_scope

    with session_scope() as s:
        return s.get(Job, job_id).expires_at


def test_an_upload_expires_30_days_after_its_analysis(retention, synthetic_dir: Path):
    with new_client() as client:
        register(client, "expiry@example.com")
        job_id = upload(client, [synthetic_dir / "hdfc_savings_aug.csv"]).json()["job_id"]
        status = client.get(f"/api/jobs/{job_id}").json()
        r = status["retention"]
        assert r["choice"] is None and r["days_left"] in (29, 30)
        finished = dt.datetime.fromisoformat(status["finished_at"])
        assert abs((_expiry(job_id) - finished) - dt.timedelta(days=30)) < dt.timedelta(seconds=5)

        kept = client.post(f"/api/jobs/{job_id}/retention/keep").json()
        assert kept["choice"] == "keep"
        listed = client.get("/api/jobs").json()
        assert listed[0]["expires_at"]


def test_only_pro_can_extend(retention, synthetic_dir: Path):
    with new_client() as client:
        register(client, "extender@example.com")
        job_id = upload(client, [synthetic_dir / "hdfc_savings_aug.csv"]).json()["job_id"]
        before = _expiry(job_id)

        free = client.post(f"/api/jobs/{job_id}/retention/extend")
        assert free.status_code == 402
        assert free.json()["detail"]["code"] == "pro_required"
        assert _expiry(job_id) == before

        order = client.post("/api/billing/checkout", json={"plan_code": "pro_monthly"}).json()
        client.post("/api/billing/dev-complete", json={"order_id": order["order_id"]})
        extended = client.post(f"/api/jobs/{job_id}/retention/extend")
        assert extended.status_code == 200
        assert extended.json()["extensions"] == 1
        assert abs((_expiry(job_id) - before) - dt.timedelta(days=30)) < dt.timedelta(seconds=5)


def test_reminders_then_deletion(retention, synthetic_dir: Path):
    from app import jobs as job_service
    from app.retention import sweep

    sent = retention
    before = _jobs_table_counts()
    with new_client() as client:
        register(client, "reminded@example.com")
        job_id = upload(client, [synthetic_dir / "hdfc_savings_aug.csv"]).json()["job_id"]
    expires = _expiry(job_id)

    assert sweep(now=expires - dt.timedelta(days=10))["reminded"] == 0
    assert sweep(now=expires - dt.timedelta(days=6))["reminded"] == 1
    assert sweep(now=expires - dt.timedelta(days=5))["reminded"] == 0  # once only
    assert "deleted in" in sent[-1][1] and "Extend 30 days" in sent[-1][2]
    assert sweep(now=expires - dt.timedelta(hours=20))["reminded"] == 1
    assert "tomorrow" in sent[-1][1]

    result = sweep(now=expires + dt.timedelta(minutes=1))
    assert result["deleted"] == 1
    assert sent[-1][0] == "reminded@example.com" and "has been deleted" in sent[-1][1]
    assert not job_service.job_dir(job_id).exists()
    assert _jobs_table_counts() == before

    with new_client() as client:
        client.post("/api/auth/login", json={"email": "reminded@example.com", "password": "correct-horse"})
        assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_older_uploads_get_a_full_period_not_instant_deletion(retention, synthetic_dir: Path):
    from app.db.models import Job
    from app.db.session import session_scope
    from app.retention import sweep

    with new_client() as client:
        register(client, "legacy@example.com")
        job_id = upload(client, [synthetic_dir / "hdfc_savings_aug.csv"]).json()["job_id"]
    with session_scope() as s:
        s.get(Job, job_id).expires_at = None  # as if uploaded before retention existed
    now = dt.datetime.now() + dt.timedelta(days=400)
    assert sweep(now=now)["dated"] == 1
    assert abs((_expiry(job_id) - now) - dt.timedelta(days=30)) < dt.timedelta(seconds=5)


def test_reports_carry_the_disclaimer():
    from app.agent.report import DISCLAIMER

    assert "not financial, tax or investment advice" in DISCLAIMER
    assert "SEBI" in DISCLAIMER
