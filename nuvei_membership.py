import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from commissions import ensure_current_month_pending_commissions_for_ambassador, sync_ambassador_wallets
from database import get_db
from dependencies import get_current_user
from marketing import notify_admin_member_payment_event, send_welcome_member_notifications
from models import (
    Ambassador,
    AmbassadorReferral,
    MembershipPayment,
    MonthlySelection,
    NuveiMembershipCard,
    NuveiRecurringAttempt,
    Order,
    Plan,
    User,
)

router = APIRouter(
    prefix="/payments/nuvei/membership",
    tags=["Nuvei Membership"],
)

MONTHLY_PRICES = {
    1: 42.00,
    2: 52.00,
    3: 62.00,
}

SUCCESS_STATUS_DETAILS = {"3", 3}
SUCCESS_STATUSES = {"success", "1", 1, "approved", "APPROVED"}


class RegisterTokenRequest(BaseModel):
    user_id: Optional[int] = None
    token: str
    status: str = "valid"
    holder_name: Optional[str] = None
    bin: Optional[str] = None
    last4: Optional[str] = None
    number: Optional[str] = None
    card_type: Optional[str] = None
    expiry_month: Optional[str] = None
    expiry_year: Optional[str] = None
    origin: Optional[str] = None
    transaction_reference: Optional[str] = None
    make_default: bool = True
    charge_initial: bool = False
    raw_payload: Optional[dict] = None


class AddCardServerRequest(BaseModel):
    user_id: Optional[int] = None
    number: str
    holder_name: str
    expiry_month: int
    expiry_year: int
    cvc: str
    card_type: Optional[str] = None


class DebitRequest(BaseModel):
    user_id: Optional[int] = None
    card_id: Optional[int] = None
    month: Optional[int] = None
    year: Optional[int] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    force: bool = False


class RefundRequest(BaseModel):
    payment_id: Optional[int] = None
    transaction_id: Optional[str] = None
    amount: Optional[float] = None


class VerifyTransactionRequest(BaseModel):
    transaction_id: str
    user_id: Optional[int] = None
    verify_type: str = "BY_OTP"
    value: str
    more_info: bool = True


def require_admin(user: User):
    if not user or user.role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="Acceso solo admin")


def resolve_user(db: Session, current_user: User, user_id: Optional[int] = None) -> User:
    target_id = user_id or current_user.id
    if current_user.role not in {"admin", "superadmin"} and target_id != current_user.id:
        raise HTTPException(status_code=403, detail="No puedes acceder a otro socio")

    user = db.query(User).filter(User.id == target_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if user.role != "member":
        raise HTTPException(status_code=400, detail="Nuvei recurrente aplica solo a socios")
    return user


def get_nuvei_mode():
    return os.getenv("NUVEI_MODE", "sandbox").lower().strip()


def get_cards_base_url():
    custom = os.getenv("NUVEI_CARDS_BASE_URL")
    if custom:
        return custom.rstrip("/")
    return (
        "https://ccapi-stg.paymentez.com"
        if get_nuvei_mode() != "production"
        else "https://ccapi.paymentez.com"
    )


def get_server_app_code():
    value = os.getenv("NUVEI_SERVER_APP_CODE")
    return value.strip() if value else None


def get_server_app_key():
    value = os.getenv("NUVEI_SERVER_APP_KEY")
    return value.strip() if value else None


def get_client_app_code():
    value = os.getenv("NUVEI_CLIENT_APP_CODE")
    return value.strip() if value else None


def get_client_app_key():
    value = os.getenv("NUVEI_CLIENT_APP_KEY")
    return value.strip() if value else None


def get_cron_secret():
    return os.getenv("NUVEI_CRON_SECRET") or os.getenv("MARKETING_CRON_SECRET")


def auth_token():
    app_code = get_server_app_code()
    app_key = get_server_app_key()
    if not app_code or not app_key:
        raise HTTPException(
            status_code=500,
            detail="Faltan NUVEI_SERVER_APP_CODE o NUVEI_SERVER_APP_KEY",
        )

    timestamp = str(int(time.time()))
    uniq_hash = hashlib.sha256(f"{app_key}{timestamp}".encode("utf-8")).hexdigest()
    raw = f"{app_code};{timestamp};{uniq_hash}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


def nuvei_request(method: str, path: str, body: Optional[dict] = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{get_cards_base_url()}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Auth-Token": auth_token(),
        },
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw
        raise HTTPException(
            status_code=502,
            detail={"message": f"Error Nuvei HTTP {exc.code}", "nuvei": detail},
        )
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo conectar con Nuvei: {exc}")


def monthly_amount_for_user(user: User) -> float:
    level = int(user.membership_level or 0)
    if level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Socio sin nivel válido")
    return MONTHLY_PRICES[level]


def current_cycle():
    now = datetime.utcnow()
    return now.month, now.year


def cycle_from_date(value: date):
    return value.month, value.year


def add_months(value: date, months: int = 1) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def latest_successful_membership_payment(db: Session, user_id: int):
    return (
        db.query(MembershipPayment)
        .filter(
            MembershipPayment.user_id == user_id,
            MembershipPayment.provider.in_(["nuvei", "paypal"]),
            MembershipPayment.payment_type.in_(["subscription", "subscription_renewal", "membership_initial"]),
            MembershipPayment.status.in_(["paid", "verified", "subscription_paid", "subscription_active"]),
        )
        .order_by(MembershipPayment.paid_at.desc().nullslast(), MembershipPayment.created_at.desc())
        .first()
    )


def next_debit_date_for_user(db: Session, user: User) -> date:
    fixed_day = os.getenv("NUVEI_RECURRING_FIXED_DAY")
    if fixed_day:
        day = max(1, min(28, int(fixed_day)))
        today = datetime.utcnow().date()
        candidate = date(today.year, today.month, day)
        if candidate < today:
            return add_months(candidate, 1)
        return candidate

    latest = latest_successful_membership_payment(db, user.id)
    base_dt = latest.paid_at or latest.created_at if latest else user.created_at
    base_date = (base_dt or datetime.utcnow()).date()
    candidate = add_months(base_date, 1)
    today = datetime.utcnow().date()
    while candidate < today:
        candidate = add_months(candidate, 1)
    return candidate


def has_successful_payment_for_cycle(db: Session, user_id: int, month: int, year: int):
    return (
        db.query(MembershipPayment)
        .filter(
            MembershipPayment.user_id == user_id,
            MembershipPayment.provider == "nuvei",
            MembershipPayment.payment_type == "subscription_renewal",
            MembershipPayment.status.in_(["subscription_paid", "verified"]),
            MembershipPayment.monthly_selection.has(
                MonthlySelection.month == month,
                MonthlySelection.year == year,
            ),
        )
        .first()
        is not None
    )


def get_default_card(db: Session, user_id: int):
    return (
        db.query(NuveiMembershipCard)
        .filter(
            NuveiMembershipCard.user_id == user_id,
            NuveiMembershipCard.is_active == True,
            NuveiMembershipCard.status.in_(["valid", "review"]),
        )
        .order_by(NuveiMembershipCard.is_default.desc(), NuveiMembershipCard.id.desc())
        .first()
    )


def get_or_create_monthly_selection(db: Session, user: User, month: int, year: int):
    selection = (
        db.query(MonthlySelection)
        .filter(
            MonthlySelection.user_id == user.id,
            MonthlySelection.month == month,
            MonthlySelection.year == year,
        )
        .first()
    )
    if selection:
        return selection

    plan = db.query(Plan).filter(Plan.level == user.membership_level).first()
    if not plan:
        return None

    selection = MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=month,
        year=year,
        status="draft",
        editable=True,
    )
    db.add(selection)
    db.flush()
    return selection


def is_nuvei_success(response: dict):
    transaction = response.get("transaction") or response
    status = transaction.get("status")
    status_detail = transaction.get("status_detail")
    return status in SUCCESS_STATUSES and status_detail in SUCCESS_STATUS_DETAILS


def normalize_card_payload(payload: dict):
    card = payload.get("card") or payload
    return {
        "token": str(card.get("token") or "").strip(),
        "status": str(card.get("status") or "valid").strip(),
        "holder_name": card.get("holder_name"),
        "bin": card.get("bin"),
        "last4": card.get("number") or card.get("last4"),
        "card_type": card.get("type"),
        "expiry_month": str(card.get("expiry_month") or "") or None,
        "expiry_year": str(card.get("expiry_year") or "") or None,
        "origin": card.get("origin"),
        "transaction_reference": card.get("transaction_reference"),
    }


def card_to_dict(card: NuveiMembershipCard):
    return {
        "id": card.id,
        "user_id": card.user_id,
        "status": card.status,
        "is_default": card.is_default,
        "is_active": card.is_active,
        "holder_name": card.holder_name,
        "bin": card.bin,
        "last4": card.last4,
        "card_type": card.card_type,
        "expiry_month": card.expiry_month,
        "expiry_year": card.expiry_year,
        "origin": card.origin,
        "transaction_reference": card.transaction_reference,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
    }


def attempt_to_dict(attempt: NuveiRecurringAttempt):
    return {
        "id": attempt.id,
        "user_id": attempt.user_id,
        "card_id": attempt.card_id,
        "membership_payment_id": attempt.membership_payment_id,
        "dev_reference": attempt.dev_reference,
        "transaction_id": attempt.transaction_id,
        "authorization_code": attempt.authorization_code,
        "amount": attempt.amount,
        "currency": attempt.currency,
        "month": attempt.month,
        "year": attempt.year,
        "attempt_type": attempt.attempt_type,
        "status": attempt.status,
        "status_detail": attempt.status_detail,
        "response_message": attempt.response_message,
        "processed_at": attempt.processed_at,
        "created_at": attempt.created_at,
    }


def save_card_from_payload(
    db: Session,
    user: User,
    payload: dict,
    make_default: bool = True,
):
    card_data = normalize_card_payload(payload)
    token = card_data.pop("token")
    if not token:
        raise HTTPException(status_code=400, detail="Nuvei no devolvió token de tarjeta")

    existing = db.query(NuveiMembershipCard).filter(NuveiMembershipCard.token == token).first()
    if existing and existing.user_id != user.id:
        raise HTTPException(status_code=409, detail="Token Nuvei ya pertenece a otro socio")

    if make_default:
        (
            db.query(NuveiMembershipCard)
            .filter(NuveiMembershipCard.user_id == user.id)
            .update({"is_default": False})
        )

    card = existing or NuveiMembershipCard(user_id=user.id, token=token)
    for key, value in card_data.items():
        if value is not None:
            setattr(card, key, value)
    card.is_active = True
    card.is_default = make_default or card.is_default
    card.raw_payload = json.dumps(payload)

    if not existing:
        db.add(card)

    db.flush()
    return card


def create_payment_from_success(
    db: Session,
    user: User,
    attempt: NuveiRecurringAttempt,
    response: dict,
):
    transaction = response.get("transaction") or response
    transaction_id = str(transaction.get("id") or attempt.dev_reference)

    existing = (
        db.query(MembershipPayment)
        .filter(MembershipPayment.paypal_order_id == transaction_id)
        .first()
    )
    if existing:
        attempt.membership_payment_id = existing.id
        return existing

    selection = get_or_create_monthly_selection(db, user, attempt.month, attempt.year)

    payment = MembershipPayment(
        user_id=user.id,
        order_id=None,
        monthly_selection_id=selection.id if selection else None,
        payment_type=attempt.attempt_type or "subscription_renewal",
        provider="nuvei",
        paypal_order_id=transaction_id,
        amount=float(transaction.get("amount") or attempt.amount),
        currency=attempt.currency,
        status="subscription_paid",
        payer_email=user.email,
        payment_reference=attempt.dev_reference,
        raw_payload=json.dumps(response),
        admin_verified=False,
        paid_at=datetime.utcnow(),
    )
    db.add(payment)
    db.flush()

    attempt.membership_payment_id = payment.id
    user.membership_active = True
    user.is_active = True
    user.status = "active"

    return payment


def sync_referral_commission_for_payment(db: Session, user: User, month: int, year: int):
    referral = (
        db.query(AmbassadorReferral)
        .filter(
            AmbassadorReferral.user_id == user.id,
            AmbassadorReferral.status == "active",
        )
        .first()
    )
    if not referral:
        return {"referral_found": False}

    ambassador = (
        db.query(Ambassador)
        .filter(Ambassador.id == referral.ambassador_id)
        .first()
    )
    if not ambassador or not ambassador.is_active or ambassador.status != "active":
        return {"referral_found": True, "ambassador_active": False}

    created = ensure_current_month_pending_commissions_for_ambassador(
        db,
        ambassador,
        month=month,
        year=year,
    )
    db.flush()
    wallet_sync = sync_ambassador_wallets(db, ambassador.id)
    return {
        "referral_found": True,
        "ambassador_id": ambassador.id,
        "created_count": len(created),
        "wallet_sync": wallet_sync,
    }


def run_nuvei_debit(
    db: Session,
    user: User,
    card: NuveiMembershipCard,
    month: int,
    year: int,
    amount: Optional[float] = None,
    description: Optional[str] = None,
    force: bool = False,
    attempt_type: str = "subscription_renewal",
):
    if attempt_type == "subscription_renewal" and not force and has_successful_payment_for_cycle(db, user.id, month, year):
        return {
            "skipped": True,
            "reason": "Ya existe pago Nuvei exitoso para este ciclo",
            "user_id": user.id,
            "month": month,
            "year": year,
        }

    amount = round(float(amount if amount is not None else monthly_amount_for_user(user)), 2)
    dev_reference = f"MWC-NUVEI-{user.id}-{year}{month:02d}-{int(time.time())}"
    request_body = {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "phone": user.phone,
        },
        "order": {
            "amount": amount,
            "description": description or f"Mayu Wellness Club mensualidad {month}/{year}",
            "dev_reference": dev_reference,
            "vat": 0.00,
        },
        "card": {
            "token": card.token,
        },
    }

    attempt = NuveiRecurringAttempt(
        user_id=user.id,
        card_id=card.id,
        dev_reference=dev_reference,
        amount=amount,
        currency="USD",
        month=month,
        year=year,
        attempt_type=attempt_type,
        status="created",
        raw_request=json.dumps(request_body),
    )
    db.add(attempt)
    db.flush()

    response = nuvei_request("POST", "/v2/transaction/debit/", request_body)
    transaction = response.get("transaction") or response
    attempt.raw_response = json.dumps(response)
    attempt.transaction_id = transaction.get("id")
    attempt.authorization_code = transaction.get("authorization_code")
    attempt.status = str(transaction.get("status") or "unknown")
    attempt.status_detail = str(transaction.get("status_detail") or "")
    attempt.response_message = transaction.get("message")
    attempt.processed_at = datetime.utcnow()

    payment = None
    commission_sync = None
    admin_email_sync = None

    if is_nuvei_success(response):
        payment = create_payment_from_success(db, user, attempt, response)
        commission_sync = sync_referral_commission_for_payment(db, user, month, year)
        try:
            trigger = (
                "nuvei_subscription_activation"
                if attempt_type == "subscription"
                else "nuvei_subscription_renewal"
            )
            admin_email_sync = notify_admin_member_payment_event(
                db=db,
                user=user,
                payment=payment,
                order=None,
                trigger=trigger,
            )
        except Exception as exc:
            admin_email_sync = {"sent": False, "error": str(exc)}
    else:
        user.membership_active = False
        attempt.next_retry_at = datetime.utcnow() + timedelta(days=1)

    db.commit()

    return {
        "skipped": False,
        "success": is_nuvei_success(response),
        "attempt": attempt_to_dict(attempt),
        "payment_id": payment.id if payment else None,
        "commission_sync": commission_sync,
        "admin_email_notification": admin_email_sync,
        "nuvei_response": response,
    }


@router.get("/debug")
def debug(current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    return {
        "mode": get_nuvei_mode(),
        "cards_base_url": get_cards_base_url(),
        "has_server_app_code": bool(get_server_app_code()),
        "has_server_app_key": bool(get_server_app_key()),
        "has_client_app_code": bool(get_client_app_code()),
        "has_client_app_key": bool(get_client_app_key()),
        "has_cron_secret": bool(get_cron_secret()),
        "allow_server_card_add": os.getenv("NUVEI_ALLOW_SERVER_CARD_ADD") == "true",
        "monthly_prices": MONTHLY_PRICES,
        "callback_url": "https://mayu-wellness-backend-v1.onrender.com/payments/nuvei/membership/webhook",
    }


@router.get("/client-config")
def client_config(current_user: User = Depends(get_current_user)):
    if current_user.role != "member":
        raise HTTPException(status_code=403, detail="Solo socios pueden registrar tarjeta")
    return {
        "mode": get_nuvei_mode(),
        "client_app_code": get_client_app_code(),
        "client_app_key": get_client_app_key(),
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "phone": current_user.phone,
        },
        "callback_url": "https://mayu-wellness-backend-v1.onrender.com/payments/nuvei/membership/webhook",
    }


@router.post("/cards/register-token")
def register_token(
    payload: RegisterTokenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = resolve_user(db, current_user, payload.user_id)
    raw = payload.raw_payload or {
        "card": {
            "token": payload.token,
            "status": payload.status,
            "holder_name": payload.holder_name,
            "bin": payload.bin,
            "number": payload.last4 or payload.number,
            "type": payload.card_type,
            "expiry_month": payload.expiry_month,
            "expiry_year": payload.expiry_year,
            "origin": payload.origin,
            "transaction_reference": payload.transaction_reference,
        }
    }
    card = save_card_from_payload(db, user, raw, make_default=payload.make_default)

    db.commit()
    db.refresh(card)

    initial_debit = None
    welcome_sync = None
    if payload.charge_initial:
        month, year = current_cycle()
        initial_debit = run_nuvei_debit(
            db=db,
            user=user,
            card=card,
            month=month,
            year=year,
            description=f"Mayu Wellness Club primer débito Nivel {user.membership_level}",
            force=False,
            attempt_type="subscription",
        )
        if initial_debit.get("success"):
            try:
                welcome_sync = send_welcome_member_notifications(
                    db=db,
                    user=user,
                    trigger="nuvei_subscription_activation",
                )
                db.commit()
            except Exception as exc:
                welcome_sync = {"sent": False, "error": str(exc)}

    return {
        "message": "Tarjeta Nuvei asociada al socio",
        "card": card_to_dict(card),
        "next_debit_date": next_debit_date_for_user(db, user).isoformat(),
        "monthly_amount": monthly_amount_for_user(user),
        "initial_debit": initial_debit,
        "welcome_notifications": welcome_sync,
    }


@router.post("/cards/add-server")
def add_card_server(
    payload: AddCardServerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if os.getenv("NUVEI_ALLOW_SERVER_CARD_ADD") != "true":
        raise HTTPException(
            status_code=403,
            detail="Add Card directo está apagado. Usar SDK Nuvei para tokenizar sin tocar PAN/CVV en MAYU.",
        )
    user = resolve_user(db, current_user, payload.user_id)
    body = {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "phone": user.phone,
            "fiscal_number": user.cedula,
        },
        "card": {
            "number": payload.number,
            "holder_name": payload.holder_name,
            "expiry_month": payload.expiry_month,
            "expiry_year": payload.expiry_year,
            "cvc": payload.cvc,
            "type": payload.card_type,
        },
    }
    response = nuvei_request("POST", "/v2/card/add", body)
    card = save_card_from_payload(db, user, response, make_default=True)
    db.commit()
    db.refresh(card)
    return {"message": "Tarjeta agregada en Nuvei", "card": card_to_dict(card), "nuvei": response}


@router.get("/cards")
def list_cards(
    user_id: Optional[int] = None,
    sync_remote: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = resolve_user(db, current_user, user_id)
    remote = None
    if sync_remote:
        query = urllib.parse.urlencode({"uid": str(user.id)})
        remote = nuvei_request("GET", f"/v2/card/list?{query}")
        for raw_card in remote.get("cards") or []:
            save_card_from_payload(db, user, {"card": raw_card}, make_default=False)
        db.commit()

    cards = (
        db.query(NuveiMembershipCard)
        .filter(NuveiMembershipCard.user_id == user.id)
        .order_by(NuveiMembershipCard.is_default.desc(), NuveiMembershipCard.id.desc())
        .all()
    )
    return {
        "items": [card_to_dict(card) for card in cards],
        "remote": remote,
        "next_debit_date": next_debit_date_for_user(db, user).isoformat(),
        "monthly_amount": monthly_amount_for_user(user),
    }


@router.delete("/cards/{card_id}")
def delete_card(
    card_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    card = db.query(NuveiMembershipCard).filter(NuveiMembershipCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    user = resolve_user(db, current_user, card.user_id)
    response = nuvei_request(
        "POST",
        "/v2/card/delete/",
        {
            "card": {"token": card.token},
            "user": {"id": str(user.id)},
        },
    )
    card.is_active = False
    card.is_default = False
    card.deleted_at = datetime.utcnow()
    card.raw_payload = json.dumps({"delete_response": response})
    db.commit()
    return {"message": "Tarjeta eliminada", "card_id": card.id, "nuvei": response}


@router.post("/debit/run")
def debit_run(
    payload: DebitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    user = resolve_user(db, current_user, payload.user_id)
    month = payload.month or datetime.utcnow().month
    year = payload.year or datetime.utcnow().year
    card = (
        db.query(NuveiMembershipCard)
        .filter(NuveiMembershipCard.id == payload.card_id)
        .first()
        if payload.card_id
        else get_default_card(db, user.id)
    )
    if not card:
        raise HTTPException(status_code=400, detail="Socio sin tarjeta Nuvei activa")
    return run_nuvei_debit(
        db=db,
        user=user,
        card=card,
        month=month,
        year=year,
        amount=payload.amount,
        description=payload.description,
        force=payload.force,
    )


@router.post("/cron/run")
def cron_run(
    secret: str = Query(...),
    dry_run: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    expected = get_cron_secret()
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Secret inválido")

    today = datetime.utcnow().date()
    users = (
        db.query(User)
        .filter(
            User.role == "member",
            User.is_active == True,
            User.membership_active == True,
            User.membership_level.in_([1, 2, 3]),
        )
        .order_by(User.id.asc())
        .limit(limit)
        .all()
    )

    results = []
    for user in users:
        card = get_default_card(db, user.id)
        due_date = next_debit_date_for_user(db, user)
        month, year = cycle_from_date(due_date)
        item = {
            "user_id": user.id,
            "user_name": user.name,
            "due_date": due_date.isoformat(),
            "monthly_amount": monthly_amount_for_user(user),
            "has_card": card is not None,
            "due_today": due_date <= today,
        }
        if not card:
            item["status"] = "skipped_no_card"
        elif due_date > today:
            item["status"] = "skipped_not_due"
        elif dry_run:
            item["status"] = "dry_run_due"
        else:
            item["result"] = run_nuvei_debit(
                db=db,
                user=user,
                card=card,
                month=month,
                year=year,
            )
            item["status"] = "processed"
        results.append(item)

    return {"message": "Cron Nuvei procesado", "dry_run": dry_run, "items": results}


@router.get("/cron/run")
def cron_run_get(secret: str = Query(...), dry_run: bool = False, limit: int = 50, db: Session = Depends(get_db)):
    return cron_run(secret=secret, dry_run=dry_run, limit=limit, db=db)


@router.post("/refund")
def refund(
    payload: RefundRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    payment = None
    transaction_id = payload.transaction_id

    if payload.payment_id:
        payment = db.query(MembershipPayment).filter(MembershipPayment.id == payload.payment_id).first()
        if not payment:
            raise HTTPException(status_code=404, detail="Pago no encontrado")
        if payment.provider != "nuvei":
            raise HTTPException(status_code=400, detail="Solo se reversan pagos Nuvei aquí")
        transaction_id = payment.paypal_order_id or payload.transaction_id

    if not transaction_id:
        raise HTTPException(status_code=400, detail="transaction_id requerido")

    body = {"transaction": {"id": transaction_id}}
    if payload.amount is not None:
        body["order"] = {"amount": round(float(payload.amount), 2)}

    response = nuvei_request("POST", "/v2/transaction/refund/", body)

    if payment:
        payment.status = "refunded"
        payment.raw_payload = json.dumps({"last_refund": response})
        db.commit()

    return {"message": "Reverso solicitado a Nuvei", "nuvei": response}


@router.post("/transaction/verify")
def verify_transaction(
    payload: VerifyTransactionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    user_id = payload.user_id or current_user.id
    body = {
        "transaction": {"id": payload.transaction_id},
        "user": {"id": str(user_id)},
        "type": payload.verify_type,
        "value": payload.value,
        "more_info": payload.more_info,
    }
    response = nuvei_request("POST", "/v2/transaction/verify", body)
    return {"message": "Verificación enviada a Nuvei", "nuvei": response}


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    event = await request.json()
    transaction = event.get("transaction") or {}
    user_payload = event.get("user") or {}
    transaction_id = str(transaction.get("id") or "")
    dev_reference = str(transaction.get("dev_reference") or "")
    user_id = user_payload.get("id")

    if os.getenv("NUVEI_VALIDATE_WEBHOOK_STOKEN") == "true":
        stoken = transaction.get("stoken")
        app_code = transaction.get("application_code") or get_server_app_code()
        app_key = get_server_app_key()
        expected = None
        if transaction_id and app_code and user_id and app_key:
            expected = hashlib.md5(
                f"{transaction_id}_{app_code}_{user_id}_{app_key}".encode("utf-8")
            ).hexdigest()
        if not expected or stoken != expected:
            raise HTTPException(status_code=403, detail="stoken Nuvei inválido")

    attempt = None
    if dev_reference:
        attempt = (
            db.query(NuveiRecurringAttempt)
            .filter(NuveiRecurringAttempt.dev_reference == dev_reference)
            .first()
        )
    if not attempt and transaction_id:
        attempt = (
            db.query(NuveiRecurringAttempt)
            .filter(NuveiRecurringAttempt.transaction_id == transaction_id)
            .first()
        )

    user = None
    if attempt:
        user = db.query(User).filter(User.id == attempt.user_id).first()
        attempt.raw_response = json.dumps(event)
        attempt.transaction_id = transaction_id or attempt.transaction_id
        attempt.authorization_code = transaction.get("authorization_code")
        attempt.status = str(transaction.get("status") or attempt.status)
        attempt.status_detail = str(transaction.get("status_detail") or attempt.status_detail or "")
        attempt.response_message = transaction.get("message")
        attempt.processed_at = datetime.utcnow()
    elif user_id:
        try:
            user = db.query(User).filter(User.id == int(user_id)).first()
        except Exception:
            user = None

    payment = None
    if user and is_nuvei_success({"transaction": transaction}):
        if not attempt:
            month, year = current_cycle()
            attempt = NuveiRecurringAttempt(
                user_id=user.id,
                card_id=None,
                dev_reference=dev_reference or f"NUVEI-WEBHOOK-{transaction_id}",
                transaction_id=transaction_id,
                amount=float(transaction.get("amount") or monthly_amount_for_user(user)),
                currency="USD",
                month=month,
                year=year,
                attempt_type="subscription_renewal",
                status=str(transaction.get("status") or "success"),
                status_detail=str(transaction.get("status_detail") or "3"),
                raw_response=json.dumps(event),
                processed_at=datetime.utcnow(),
            )
            db.add(attempt)
            db.flush()
        payment = create_payment_from_success(db, user, attempt, {"transaction": transaction})
        sync_referral_commission_for_payment(db, user, attempt.month, attempt.year)
        try:
            notify_admin_member_payment_event(
                db=db,
                user=user,
                payment=payment,
                order=None,
                trigger="nuvei_webhook_payment",
            )
        except Exception:
            pass
    elif user and attempt:
        user.membership_active = False

    db.commit()
    return {
        "status": "ok",
        "matched_attempt": attempt.id if attempt else None,
        "payment_id": payment.id if payment else None,
        "transaction_id": transaction_id or None,
        "success": is_nuvei_success({"transaction": transaction}),
    }
