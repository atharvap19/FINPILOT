"""Sign-up, sign-in, the account summary, and upgrading to Pro."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import auth, billing
from app.api.schemas import ApiModel
from app.config import get_settings
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["account"])


class RegisterRequest(ApiModel):
    email: str
    password: str
    name: str = ""
    #: the Terms and Privacy Policy checkbox
    accept_terms: bool = False
    #: the "I am 18 or older" checkbox
    confirm_age: bool = False


class ForgotRequest(ApiModel):
    email: str


class ResetRequest(ApiModel):
    token: str
    password: str


class DeleteAccountRequest(ApiModel):
    password: str
    #: the user types DELETE to confirm
    confirm: str


class LoginRequest(ApiModel):
    email: str
    password: str


class CheckoutRequest(ApiModel):
    plan_code: str


class VerifyRequest(ApiModel):
    order_id: str
    payment_id: str
    signature: str


class DevCompleteRequest(ApiModel):
    order_id: str


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


@router.post("/auth/register", status_code=201)
def register(body: RegisterRequest, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        user = auth.create_user(
            db,
            email=body.email,
            password=body.password,
            name=body.name,
            accepted_terms=body.accept_terms,
            age_confirmed=body.confirm_age,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from None
    db.commit()
    auth.start_session(db, response, user)
    auth.audit.info("signup user=%s consent=%s", user.user_id, user.consent_version)
    return billing.account_view(db, user)


@router.post("/auth/login")
def login(
    body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    email = auth.normalize_email(body.email)
    address = request.client.host if request.client else "unknown"
    wait = auth.LOGIN_THROTTLE.retry_after(email, address)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many sign-in attempts. Try again in {max(1, wait // 60)} minute(s).",
            headers={"Retry-After": str(wait)},
        )
    user = auth.authenticate(db, email=email, password=body.password)
    if user is None:
        auth.LOGIN_THROTTLE.failed(email, address)
        auth.audit.info("login_failed ip=%s", address)
        raise HTTPException(status_code=401, detail="wrong email or password")
    auth.LOGIN_THROTTLE.succeeded(email)
    auth.start_session(db, response, user)
    auth.audit.info("login user=%s ip=%s", user.user_id, address)
    return billing.account_view(db, user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    auth.end_session(db, request, response)


@router.post("/auth/forgot")
def forgot_password(body: ForgotRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Always the same answer, whether or not the email has an account."""
    address = request.client.host if request.client else "unknown"
    auth.request_password_reset(db, body.email, address)
    return {"ok": True, "message": "If an account exists for that email, a reset link is on its way."}


@router.post("/auth/reset")
def reset_password(body: ResetRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        auth.reset_password(db, body.token, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}


@router.get("/account/export")
def export_my_data(user: User = Depends(auth.current_user)) -> FileResponse:
    """Everything FinPilot holds about this account, as a zip."""
    from starlette.background import BackgroundTask

    from app.privacy import export_account

    path = export_account(user.user_id)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.delete("/account")
def delete_my_account(
    body: DeleteAccountRequest,
    request: Request,
    response: Response,
    user: User = Depends(auth.current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Erase the account and every upload. Needs the password and "DELETE"."""
    from app.privacy import delete_account

    if body.confirm.strip().upper() != "DELETE":
        raise HTTPException(status_code=400, detail='type DELETE to confirm')
    if not auth.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=403, detail="that password is not correct")
    user_id = user.user_id
    db.close()
    result = delete_account(user_id)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True, **result}


@router.get("/auth/me")
def me(user: User = Depends(auth.current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Who is signed in, their plan, and this month's usage against it."""
    return billing.account_view(db, user)


# --------------------------------------------------------------------------
# billing
# --------------------------------------------------------------------------


@router.get("/billing/plans")
def plans() -> dict[str, Any]:
    s = get_settings()
    return {
        "prices": [
            {"code": p.code, "label": p.label, "amount_paise": p.amount_paise, "months": p.months}
            for p in billing.prices().values()
        ],
        "limits": {"free": billing.limits("free"), "pro": billing.limits("pro")},
        "provider": "razorpay" if s.razorpay_enabled else ("dev" if s.payments_dev_mode else None),
    }


@router.post("/billing/checkout")
def checkout(body: CheckoutRequest, user: User = Depends(auth.current_user),
             db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return billing.create_checkout(db, user, body.plan_code)
    except billing.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/billing/verify")
def verify(body: VerifyRequest, user: User = Depends(auth.current_user),
           db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        billing.verify_razorpay(db, user, order_id=body.order_id, payment_id=body.payment_id,
                                signature=body.signature)
    except billing.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return billing.account_view(db, user)


@router.post("/billing/dev-complete")
def dev_complete(body: DevCompleteRequest, user: User = Depends(auth.current_user),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        billing.complete_dev(db, user, body.order_id)
    except billing.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return billing.account_view(db, user)
