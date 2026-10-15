"""Plans, monthly usage limits, and turning a payment into Pro.

Three things are metered, per calendar month:

* ``chat``   -- an answered question to the agent
* ``report`` -- a generated financial report
* ``file``   -- an uploaded file

Free and Pro differ in their limits (Pro's are a fair-use ceiling that keeps
model costs bounded). Hitting a limit is a ``402 Payment Required`` whose body
says which limit, so the UI can open the upgrade dialog with the right reason.

Payments go through Razorpay Orders: the server creates an order, the browser
opens Razorpay Checkout, and the server verifies the signature Razorpay
returns before extending ``pro_until``. Pro is prepaid -- a month or a year --
rather than an auto-renewing subscription, which keeps refunds and failed
renewals out of scope for now.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Payment, UsageEvent, User

KINDS = ("chat", "report", "file")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


# --------------------------------------------------------------------------
# plans
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanPrice:
    code: str
    label: str
    amount_paise: int
    months: int


def prices() -> dict[str, PlanPrice]:
    s = get_settings()
    return {
        "pro_monthly": PlanPrice("pro_monthly", "Pro · monthly", s.price_pro_monthly_paise, 1),
        "pro_yearly": PlanPrice("pro_yearly", "Pro · yearly", s.price_pro_yearly_paise, 12),
    }


def limits(plan: str) -> dict[str, int]:
    s = get_settings()
    if plan == "pro":
        return {"chat": s.pro_chat_per_month, "report": s.pro_reports_per_month,
                "file": s.pro_files_per_month}
    return {"chat": s.free_chat_per_month, "report": s.free_reports_per_month,
            "file": s.free_files_per_month}


def effective_plan(user: User) -> str:
    if user.plan == "pro" and user.pro_until and user.pro_until > _now():
        return "pro"
    return "free"


def _month_start(now: dt.datetime | None = None) -> dt.datetime:
    now = now or _now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage_this_month(session: Session, user_id: str) -> dict[str, int]:
    rows = session.execute(
        select(UsageEvent.kind, func.coalesce(func.sum(UsageEvent.quantity), 0))
        .where(UsageEvent.user_id == user_id)
        .where(UsageEvent.created_at >= _month_start())
        .group_by(UsageEvent.kind)
    ).all()
    used = {kind: 0 for kind in KINDS}
    used.update({kind: int(total) for kind, total in rows})
    return used


def account_view(session: Session, user: User) -> dict[str, Any]:
    plan = effective_plan(user)
    used = usage_this_month(session, user.user_id)
    caps = limits(plan)
    return {
        "user": {"user_id": user.user_id, "email": user.email, "name": user.name},
        "plan": plan,
        "pro_until": user.pro_until.isoformat() if plan == "pro" and user.pro_until else None,
        "usage": {k: {"used": used[k], "limit": caps[k]} for k in KINDS},
        "auth_required": get_settings().auth_required,
    }


# --------------------------------------------------------------------------
# metering
# --------------------------------------------------------------------------

_FEATURE_TEXT = {
    "chat": "questions to FinPilot",
    "report": "financial reports",
    "file": "uploaded files",
}


def enforce(session: Session, user: User, kind: str, amount: int = 1) -> None:
    """Raise 402 if ``amount`` more of ``kind`` would exceed this month's limit."""
    if not get_settings().auth_required:
        return
    plan = effective_plan(user)
    cap = limits(plan)[kind]
    used = usage_this_month(session, user.user_id)[kind]
    if used + amount <= cap:
        return
    left = max(cap - used, 0)
    if plan == "free":
        message = (
            f"You've used {used} of {cap} {_FEATURE_TEXT[kind]} on the Free plan this month"
            + (f" (only {left} left)" if left else "")
            + ". Upgrade to Pro for more."
        )
    else:
        message = f"You've reached this month's fair-use limit of {cap} {_FEATURE_TEXT[kind]}."
    raise HTTPException(
        status_code=402,
        detail={"code": "limit_reached", "feature": kind, "limit": cap, "used": used,
                "plan": plan, "message": message},
    )


def record(
    session: Session,
    user_id: str,
    kind: str,
    *,
    quantity: int = 1,
    job_id: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> None:
    session.add(
        UsageEvent(user_id=user_id, kind=kind, quantity=quantity, job_id=job_id,
                   tokens_in=tokens_in, tokens_out=tokens_out)
    )
    session.commit()


# --------------------------------------------------------------------------
# payments
# --------------------------------------------------------------------------


class PaymentError(ValueError):
    pass


def _razorpay_order(price: PlanPrice, user: User) -> dict[str, Any]:
    """POST /v1/orders. Plain HTTP so there is no SDK to install."""
    import httpx

    s = get_settings()
    auth = base64.b64encode(f"{s.razorpay_key_id}:{s.razorpay_key_secret}".encode()).decode()
    response = httpx.post(
        "https://api.razorpay.com/v1/orders",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        content=json.dumps({
            "amount": price.amount_paise,
            "currency": "INR",
            "receipt": f"fp_{uuid.uuid4().hex[:16]}",
            "notes": {"user_id": user.user_id, "plan_code": price.code},
        }),
        timeout=20,
    )
    if response.status_code >= 300:
        raise PaymentError(f"Razorpay refused the order ({response.status_code})")
    return response.json()


def create_checkout(session: Session, user: User, plan_code: str) -> dict[str, Any]:
    price = prices().get(plan_code)
    if price is None:
        raise PaymentError(f"unknown plan {plan_code}")
    s = get_settings()
    if s.razorpay_enabled:
        order = _razorpay_order(price, user)
        provider, order_id = "razorpay", order["id"]
    elif s.payments_dev_mode:
        provider, order_id = "dev", f"dev_order_{uuid.uuid4().hex[:14]}"
    else:
        raise PaymentError(
            "Payments are not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET "
            "(or PAYMENTS_DEV_MODE=true for local testing)."
        )
    session.add(Payment(order_id=order_id, user_id=user.user_id, provider=provider,
                        plan_code=plan_code, amount_paise=price.amount_paise))
    session.commit()
    return {
        "provider": provider,
        "order_id": order_id,
        "key_id": s.razorpay_key_id if provider == "razorpay" else None,
        "amount_paise": price.amount_paise,
        "currency": "INR",
        "label": price.label,
        "email": user.email,
        "name": user.name,
    }


def _signature_ok(order_id: str, payment_id: str, signature: str) -> bool:
    secret = get_settings().razorpay_key_secret.encode()
    expected = hmac.new(secret, f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _pending(session: Session, user: User, order_id: str) -> Payment:
    payment = session.get(Payment, order_id)
    if payment is None or payment.user_id != user.user_id:
        raise PaymentError("unknown order")
    return payment


def _activate(session: Session, user: User, payment: Payment, payment_id: str) -> None:
    if payment.status == "paid":
        return  # idempotent: a double-submitted verify must not add a second month
    months = prices()[payment.plan_code].months
    start = max(_now(), user.pro_until or _now())
    user.plan = "pro"
    user.pro_until = _add_months(start, months)
    payment.status = "paid"
    payment.payment_id = payment_id
    payment.paid_at = _now()
    session.commit()


def _add_months(when: dt.datetime, months: int) -> dt.datetime:
    month = when.month - 1 + months
    year = when.year + month // 12
    month = month % 12 + 1
    import calendar

    day = min(when.day, calendar.monthrange(year, month)[1])
    return when.replace(year=year, month=month, day=day)


def verify_razorpay(session: Session, user: User, *, order_id: str, payment_id: str,
                    signature: str) -> None:
    payment = _pending(session, user, order_id)
    if payment.provider != "razorpay":
        raise PaymentError("not a Razorpay order")
    if not _signature_ok(order_id, payment_id, signature):
        payment.status = "failed"
        session.commit()
        raise PaymentError("payment signature did not verify")
    _activate(session, user, payment, payment_id)


def complete_dev(session: Session, user: User, order_id: str) -> None:
    if not get_settings().payments_dev_mode:
        raise PaymentError("dev payments are disabled")
    payment = _pending(session, user, order_id)
    if payment.provider != "dev":
        raise PaymentError("not a dev order")
    _activate(session, user, payment, f"dev_pay_{uuid.uuid4().hex[:10]}")
