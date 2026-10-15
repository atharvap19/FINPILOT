"""Accounts: passwords, sessions, and who may see which job.

Sessions are server-side: the browser holds a random token in an HttpOnly
cookie and the database holds its SHA-256. That makes logout real (the row is
deleted) and keeps the token out of reach of page JavaScript. A cookie rather
than a bearer header also means plain download links -- the CSVs, the PDF --
are authenticated with no extra work.

Every route under ``/api/jobs/{job_id}`` passes through :func:`job_access`,
which answers 404 for a job that belongs to someone else. 404 rather than 403,
so job ids cannot be probed for existence.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
import uuid

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuthSession, Job, User
from app.db.session import get_db

COOKIE_NAME = "finpilot_session"
LOCAL_USER_ID = "local"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def create_user(
    session: Session,
    *,
    email: str,
    password: str,
    name: str = "",
    accepted_terms: bool = False,
    age_confirmed: bool = False,
) -> User:
    email = normalize_email(email)
    if not accepted_terms:
        raise ValueError("please accept the Terms and Privacy Policy to create an account")
    if not age_confirmed:
        raise ValueError("FinPilot is for people aged 18 or over")
    if not _EMAIL.match(email):
        raise ValueError("enter a valid email address")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"use a password of at least {MIN_PASSWORD} characters")
    if session.scalar(select(User).where(User.email == email)) is not None:
        raise ValueError("an account with this email already exists")

    first_user = session.scalar(select(User.user_id).limit(1)) is None
    user = User(
        user_id=f"usr_{uuid.uuid4().hex[:12]}",
        email=email,
        name=(name or "").strip()[:120] or email.split("@")[0],
        password_hash=hash_password(password),
        consent_version=get_settings().consent_version,
        consented_at=_now(),
        age_confirmed=True,
    )
    session.add(user)
    session.flush()
    if first_user:
        # Jobs uploaded before accounts existed belong to whoever was running
        # the app then -- the first person to sign up.
        session.execute(update(Job).where(Job.user_id.is_(None)).values(user_id=user.user_id))
    return user


# --------------------------------------------------------------------------
# brute-force protection
# --------------------------------------------------------------------------


class LoginThrottle:
    """Failed sign-ins per account and per client address, in a sliding window.

    In-process on purpose: the API runs as a single worker (see the
    Dockerfile), so one dictionary sees every attempt. A success clears the
    account's count; the address count only decays with time, so trying many
    accounts from one address is slowed too.
    """

    def __init__(self, per_account: int, per_address: int, window_s: int) -> None:
        self.per_account = per_account
        self.per_address = per_address
        self.window = window_s
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        stamps = [t for t in self._fails.get(key, []) if now - t < self.window]
        self._fails[key] = stamps
        return stamps

    def retry_after(self, email: str, address: str) -> int:
        """Seconds to wait, or 0 if an attempt is allowed now."""
        now = time.monotonic()
        with self._lock:
            waits = []
            for key, limit in ((f"a:{email}", self.per_account), (f"ip:{address}", self.per_address)):
                stamps = self._recent(key, now)
                if len(stamps) >= limit:
                    waits.append(int(self.window - (now - stamps[0])) + 1)
            return max(waits, default=0)

    def failed(self, email: str, address: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in (f"a:{email}", f"ip:{address}"):
                self._recent(key, now).append(now)

    def succeeded(self, email: str) -> None:
        with self._lock:
            self._fails.pop(f"a:{email}", None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()


LOGIN_THROTTLE = LoginThrottle(per_account=5, per_address=30, window_s=15 * 60)
#: reset emails: a few per address per hour, so the form can't spam an inbox
RESET_THROTTLE = LoginThrottle(per_account=3, per_address=10, window_s=3600)

audit = logging.getLogger("finpilot.audit")


# --------------------------------------------------------------------------
# password reset
# --------------------------------------------------------------------------

RESET_MINUTES = 30


def request_password_reset(session: Session, email: str, address: str) -> None:
    """Email a reset link if the account exists. Silent either way, so the
    form can't be used to find out whether an email is registered."""
    from app.db.models import PasswordReset
    from app.mailer import send_email

    email = normalize_email(email)
    if RESET_THROTTLE.retry_after(email, address):
        audit.info("reset_throttled ip=%s", address)
        return
    RESET_THROTTLE.failed(email, address)  # counts requests, not failures
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        return
    token = secrets.token_urlsafe(32)
    session.add(
        PasswordReset(
            token_hash=_token_hash(token),
            user_id=user.user_id,
            expires_at=_now() + dt.timedelta(minutes=RESET_MINUTES),
        )
    )
    session.commit()
    link = f"{get_settings().public_url.rstrip('/')}/reset?token={token}"
    send_email(
        user.email,
        "Reset your FinPilot password",
        f"Hi {user.name or 'there'},\n\n"
        f"Someone (hopefully you) asked to reset the password for your FinPilot account.\n"
        f"Open this link within {RESET_MINUTES} minutes to choose a new one:\n\n{link}\n\n"
        "If you didn't ask for this, ignore this email -- your password stays the same.\n",
    )
    audit.info("reset_requested user=%s ip=%s", user.user_id, address)


def reset_password(session: Session, token: str, new_password: str) -> User:
    """Use a reset link. Signs the account out everywhere."""
    from app.db.models import PasswordReset

    row = session.get(PasswordReset, _token_hash(token or ""))
    if row is None or row.used_at is not None or row.expires_at < _now():
        raise ValueError("this reset link is invalid or has expired -- ask for a new one")
    if len(new_password or "") < MIN_PASSWORD:
        raise ValueError(f"use a password of at least {MIN_PASSWORD} characters")
    user = session.get(User, row.user_id)
    if user is None:
        raise ValueError("this reset link is invalid or has expired -- ask for a new one")
    user.password_hash = hash_password(new_password)
    now = _now()
    for other in session.scalars(select(PasswordReset).where(PasswordReset.user_id == user.user_id)):
        other.used_at = other.used_at or now
    session.query(AuthSession).filter(AuthSession.user_id == user.user_id).delete()
    session.commit()
    LOGIN_THROTTLE.succeeded(user.email)
    audit.info("password_reset user=%s", user.user_id)
    return user


def authenticate(session: Session, *, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(password, hash_password("timing-equaliser"))
        return None
    return user if verify_password(password, user.password_hash) else None


def local_user(session: Session) -> User:
    """The single user of an auth-less run (tests, local single-user mode)."""
    user = session.get(User, LOCAL_USER_ID)
    if user is None:
        user = User(
            user_id=LOCAL_USER_ID,
            email="local@finpilot.local",
            name="Local user",
            password_hash="!",
            plan="pro",
        )
        session.add(user)
        session.commit()
    return user


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(session: Session, response: Response, user: User) -> None:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    expires = _now() + dt.timedelta(days=settings.session_days)
    session.add(AuthSession(token_hash=_token_hash(token), user_id=user.user_id, expires_at=expires))
    session.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def end_session(session: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        row = session.get(AuthSession, _token_hash(token))
        if row is not None:
            session.delete(row)
            session.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


def _user_from_cookie(session: Session, request: Request) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    row = session.get(AuthSession, _token_hash(token))
    if row is None:
        return None
    if row.expires_at < _now():
        session.delete(row)
        session.commit()
        return None
    return session.get(User, row.user_id)


# --------------------------------------------------------------------------
# dependencies
# --------------------------------------------------------------------------


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """The signed-in user, or 401. With auth off, the local user."""
    if not get_settings().auth_required:
        return local_user(db)
    user = _user_from_cookie(db, request)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in to continue")
    return user


def job_access(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """Router-level guard: a ``{job_id}`` in the path must be the user's own."""
    job_id = request.path_params.get("job_id")
    if job_id is None:
        return
    job = db.get(Job, job_id)
    if job is None:
        return  # the route's own 404
    if job.user_id == user.user_id:
        return
    if job.user_id is None and not get_settings().auth_required:
        return
    raise HTTPException(status_code=404, detail="no such job")
