import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from cryptography.fernet import Fernet, InvalidToken

from database import get_db
from dependencies import get_current_user
from marketing import notify_admin_member_payment_event, send_welcome_member_notifications
from marketing_contacts import upsert_marketing_contact
from auth import SECRET_KEY, hash_password
from renewal_processing import reconcile_subscription_renewal
from security_alerts import _sanitize, record_security_event
from models import (
    MembershipPayment,
    MonthlySelection,
    NuveiMembershipCard,
    NuveiRecurringAttempt,
    Order,
    Plan,
    User,
    Ambassador,
    AmbassadorReferral,
    MonthlySelectionItem,
    Product,
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

DEFAULT_NUVEI_VAT_PERCENTAGE = Decimal("15")

SUCCESS_STATUS_DETAILS = {"3", 3}
SUCCESS_STATUSES = {"success", "1", 1}


class RegisterTokenRequest(BaseModel):
    user_id: Optional[int] = None
    token: str
    status: str = "valid"
    holder_name: Optional[str] = None
    bin: Optional[str] = None
    last4: Optional[str] = None
    card_type: Optional[str] = None
    expiry_month: Optional[str] = None
    expiry_year: Optional[str] = None
    origin: Optional[str] = None
    transaction_reference: Optional[str] = None
    make_default: bool = True
    charge_initial: bool = False


class BrowserInfo(BaseModel):
    language: Optional[str] = None
    java_enabled: bool = False
    javascript_enabled: bool = True
    color_depth: Optional[int] = None
    screen_height: Optional[int] = None
    screen_width: Optional[int] = None
    timezone_offset: Optional[int] = None


class SecureSignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    phone: str
    cedula: str
    birth_date: Optional[str] = None
    city: str
    address: str
    reference: str
    delivery_notes: str
    phone_secondary: Optional[str] = None
    ambassador_code: Optional[str] = None
    accepted_terms: bool = False
    accepted_privacy_policy: bool = False
    accepted_digital_policy: bool = False
    membership_level: int
    initial_products: list[str] = Field(default_factory=list)
    token: str
    status: str = "valid"
    holder_name: Optional[str] = None
    bin: Optional[str] = None
    last4: Optional[str] = None
    card_type: Optional[str] = None
    expiry_month: Optional[str] = None
    expiry_year: Optional[str] = None
    origin: Optional[str] = None
    transaction_reference: Optional[str] = None
    # Used only for the first debit and never persisted or logged.
    cvc: Optional[str] = Field(default=None, min_length=3, max_length=4)
    browser_info: Optional[BrowserInfo] = None


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


def get_callback_url():
    return os.getenv(
        "NUVEI_CALLBACK_URL",
        "https://mayu-wellness-backend-v1.onrender.com/payments/nuvei/membership/webhook",
    ).strip()


def get_max_retry_attempts():
    return max(1, int(os.getenv("NUVEI_MAX_RETRY_ATTEMPTS", "3")))


def get_sandbox_renewal_interval_days() -> Optional[int]:
    """Return an accelerated renewal interval only outside production."""
    if get_nuvei_mode() in {"production", "live"}:
        return None
    raw = os.getenv("NUVEI_SANDBOX_RENEWAL_INTERVAL_DAYS", "2").strip()
    if not raw:
        return None
    try:
        return max(1, min(int(raw), 28))
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="NUVEI_SANDBOX_RENEWAL_INTERVAL_DAYS debe ser un entero entre 1 y 28",
        ) from exc


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


def nuvei_vat_included_in_total(amount: float) -> float:
    """Return the VAT portion already included in the final membership price."""
    raw_percentage = os.getenv("NUVEI_VAT_PERCENTAGE", "15").strip() or "15"
    try:
        percentage = Decimal(raw_percentage)
    except InvalidOperation as exc:
        raise RuntimeError("NUVEI_VAT_PERCENTAGE debe ser un número válido") from exc
    if percentage <= 0:
        raise RuntimeError("NUVEI_VAT_PERCENTAGE debe ser mayor que cero")

    total = Decimal(str(amount))
    vat = total * percentage / (Decimal("100") + percentage)
    return float(vat.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def nuvei_order(amount: float, description: str, dev_reference: str) -> dict:
    return {
        "amount": amount,
        "description": description,
        "dev_reference": dev_reference,
        # Nuvei Ecuador requires a positive VAT amount when the application has
        # tax_percentage configured. This is the tax portion of the price, not
        # a surcharge: order.amount remains exactly USD 40, 50 or 60.
        "vat": nuvei_vat_included_in_total(amount),
    }


def current_cycle():
    now = datetime.utcnow()
    return now.month, now.year


def _three_ds_state(dev_reference: str, expires_in: int = 900) -> str:
    expires_at = int(time.time()) + expires_in
    payload = f"{dev_reference}|{expires_at}"
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{encoded}.{signature}"


def _parse_three_ds_state(state: str) -> str:
    try:
        encoded, signature = state.split(".", 1)
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        dev_reference, expires_at = payload.rsplit("|", 1)
        expected = hmac.new(
            SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        if int(expires_at) < int(time.time()):
            raise ValueError("expired")
        return dev_reference
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Estado 3DS inválido o expirado") from exc


def _public_api_base() -> str:
    configured = (os.getenv("PUBLIC_API_BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    callback = get_callback_url()
    marker = "/payments/nuvei/membership/"
    return callback.split(marker, 1)[0].rstrip("/")


def _three_ds_term_url(state: str) -> str:
    return f"{_public_api_base()}/payments/nuvei/membership/3ds/term/{state}"


def _browser_payload(request: Request, info: Optional[BrowserInfo]) -> dict:
    provided = info or BrowserInfo()
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    client_ip = forwarded or (request.client.host if request.client else "")
    return {
        "ip": client_ip,
        "language": provided.language or request.headers.get("accept-language", "es-EC").split(",", 1)[0],
        "java_enabled": provided.java_enabled,
        "javascript_enabled": provided.javascript_enabled,
        "color_depth": provided.color_depth or 24,
        "screen_height": provided.screen_height or 0,
        "screen_width": provided.screen_width or 0,
        "timezone_offset": provided.timezone_offset or 0,
        "user_agent": request.headers.get("user-agent", "")[:500],
        "accept_header": request.headers.get("accept", "*/*")[:500],
    }


def _three_ds_artifact(response: dict, name: str) -> Optional[str]:
    transaction = response.get("transaction") or response
    candidates = [
        transaction,
        response,
        transaction.get("threeDS2_data") or {},
        transaction.get("three_ds") or {},
        response.get("threeDS2_data") or {},
        response.get("three_ds") or {},
        response.get("3ds") or {},
    ]
    for candidate in candidates:
        value = candidate.get(name) if isinstance(candidate, dict) else None
        if value:
            return str(value)
    return None


def _three_ds_action(attempt: NuveiRecurringAttempt, state: str, response: dict) -> dict:
    detail = status_detail_as_int(attempt.status_detail)
    action = "frictionless_wait" if detail == 35 else "challenge"
    return {
        "success": False,
        "payment_pending": True,
        "status": attempt.status,
        "status_detail": detail,
        "next_action": action,
        "state": state,
        "content_url": f"{_public_api_base()}/payments/nuvei/membership/3ds/content/{state}",
        "continue_url": f"{_public_api_base()}/payments/nuvei/membership/3ds/continue/{state}",
        "expires_in_seconds": 900,
    }


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
    sandbox_days = get_sandbox_renewal_interval_days()
    if sandbox_days:
        latest = latest_successful_membership_payment(db, user.id)
        base_dt = latest.paid_at or latest.created_at if latest else datetime.utcnow()
        return (base_dt or datetime.utcnow()).date() + timedelta(days=sandbox_days)

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
            # La deduplicacion es por ciclo de membresia, no por proveedor.
            # Esto evita cobrar Nuvei en un mes que ya fue pagado por PayPal
            # durante la migracion del metodo de pago.
            MembershipPayment.provider.in_(["nuvei", "paypal"]),
            MembershipPayment.payment_type.in_(
                ["subscription", "subscription_renewal", "membership_initial"]
            ),
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


def signup_nuvei_user_id(email: str, phone: str):
    normalized_email = email.strip().lower()
    return "signup-" + hashlib.sha256(
        f"{normalized_email}:{phone.strip()}".encode("utf-8")
    ).hexdigest()[:24]


def nuvei_user_id_for_card(user: User, card: NuveiMembershipCard):
    try:
        metadata = json.loads(card.raw_payload or "{}")
        stored = metadata.get("nuvei_user_id")
        if stored:
            return str(stored)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return str(user.id)


def status_detail_as_int(value):
    try:
        return int(value) if value is not None and str(value) != "" else None
    except (TypeError, ValueError):
        return None


def validate_webhook_signature(transaction: dict, user_id):
    transaction_id = str(transaction.get("id") or "")
    stoken = transaction.get("stoken")
    configured_app_code = get_server_app_code()
    app_code = transaction.get("application_code")
    app_key = get_server_app_key()
    if configured_app_code and app_code != configured_app_code:
        raise HTTPException(status_code=203, detail="application_code Nuvei invalido")
    expected = None
    if transaction_id and app_code and user_id and app_key:
        expected = hashlib.md5(
            f"{transaction_id}_{app_code}_{user_id}_{app_key}".encode("utf-8")
        ).hexdigest()
    if not expected or not stoken or not hmac.compare_digest(str(stoken), expected):
        raise HTTPException(status_code=203, detail="stoken Nuvei inválido")


def normalize_card_payload(payload: dict):
    card = payload.get("card") or payload
    raw_last4 = str(card.get("last4") or "")
    last4 = "".join(character for character in raw_last4 if character.isdigit())[-4:]
    raw_bin = str(card.get("bin") or "")
    card_bin = "".join(character for character in raw_bin if character.isdigit())[:8]
    return {
        "token": str(card.get("token") or "").strip(),
        "status": str(card.get("status") or "valid").strip(),
        "holder_name": card.get("holder_name"),
        "bin": card_bin or None,
        "last4": last4 or None,
        "card_type": card.get("type"),
        "expiry_month": str(card.get("expiry_month") or "") or None,
        "expiry_year": str(card.get("expiry_year") or "") or None,
        "origin": card.get("origin"),
        "transaction_reference": card.get("transaction_reference"),
    }


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_cipher() -> Optional[Fernet]:
    key = (os.getenv("NUVEI_TOKEN_ENCRYPTION_KEY") or "").strip()
    if not key:
        if get_nuvei_mode() in {"production", "live"}:
            raise RuntimeError("Falta NUVEI_TOKEN_ENCRYPTION_KEY en producción")
        return None
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("NUVEI_TOKEN_ENCRYPTION_KEY no es una clave Fernet válida") from exc


def _encrypt_card_token(token: str) -> str:
    cipher = _token_cipher()
    if not cipher:
        return token
    return "enc:v1:" + cipher.encrypt(token.encode("utf-8")).decode("ascii")


def token_for_provider(card: NuveiMembershipCard) -> str:
    stored = str(card.token or "")
    if not stored.startswith("enc:v1:"):
        cipher = _token_cipher()
        if cipher:
            card.token_fingerprint = _token_fingerprint(stored)
            card.token = _encrypt_card_token(stored)
        return stored
    cipher = _token_cipher()
    if not cipher:
        raise RuntimeError("No se puede descifrar el token Nuvei")
    try:
        return cipher.decrypt(stored.removeprefix("enc:v1:").encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Token Nuvei cifrado inválido") from exc


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
        "next_debit_at": card.next_debit_at,
        "last_debit_at": card.last_debit_at,
        "failed_attempts": card.failed_attempts,
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
        "response_message": attempt.message,
        "processed_at": attempt.charged_at,
        "next_retry_at": attempt.next_retry_at,
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

    fingerprint = _token_fingerprint(token)
    existing = db.query(NuveiMembershipCard).filter(
        (NuveiMembershipCard.token_fingerprint == fingerprint)
        | (NuveiMembershipCard.token == token)
    ).first()
    if existing and existing.user_id != user.id:
        raise HTTPException(status_code=409, detail="Token Nuvei ya pertenece a otro socio")

    if make_default:
        (
            db.query(NuveiMembershipCard)
            .filter(NuveiMembershipCard.user_id == user.id)
            .update({"is_default": False})
        )

    card = existing or NuveiMembershipCard(
        user_id=user.id,
        token=_encrypt_card_token(token),
        token_fingerprint=fingerprint,
    )
    card.token_fingerprint = fingerprint
    for key, value in card_data.items():
        if value is not None:
            setattr(card, key, value)
    card.is_active = True
    card.is_default = make_default or card.is_default
    # Persistimos únicamente metadatos permitidos. PAN nunca llega al backend;
    # el CVC del primer débito se usa en memoria y nunca pasa por esta función.
    card.raw_payload = json.dumps({"card": {"token_received": True, **card_data}})

    if not existing:
        db.add(card)

    db.flush()
    if not card.next_debit_at:
        card.next_debit_at = datetime.combine(next_debit_date_for_user(db, user), datetime.min.time())
    return card


def advance_card_after_success(card: NuveiMembershipCard, charged_at: Optional[datetime] = None):
    charged_at = charged_at or datetime.utcnow()
    scheduled = card.next_debit_at.date() if card.next_debit_at else charged_at.date()
    if scheduled < charged_at.date():
        scheduled = charged_at.date()
    card.last_debit_at = charged_at
    sandbox_days = get_sandbox_renewal_interval_days()
    next_date = (
        charged_at.date() + timedelta(days=sandbox_days)
        if sandbox_days
        else add_months(scheduled, 1)
    )
    card.next_debit_at = datetime.combine(next_date, datetime.min.time())
    card.failed_attempts = 0


def schedule_card_retry(card: NuveiMembershipCard):
    card.failed_attempts = int(card.failed_attempts or 0) + 1
    card.next_debit_at = datetime.combine(
        datetime.utcnow().date() + timedelta(days=1),
        datetime.min.time(),
    )


def monthly_due_date(card: NuveiMembershipCard, scheduled: date) -> date:
    """Protect live renewals while allowing accelerated sandbox tests."""
    if card.last_debit_at and not get_sandbox_renewal_interval_days():
        return max(scheduled, add_months(card.last_debit_at.date(), 1))
    return scheduled


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
        raw_payload=json.dumps(_sanitize(response)),
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
    if not force and has_successful_payment_for_cycle(db, user.id, month, year):
        return {
            "skipped": True,
            "reason": "Ya existe un pago exitoso para este ciclo",
            "user_id": user.id,
            "month": month,
            "year": year,
        }

    expected_amount = monthly_amount_for_user(user)
    if amount is not None and float(amount) != expected_amount:
        raise HTTPException(status_code=400, detail="El monto debe coincidir con la mensualidad del plan")
    amount = expected_amount
    dev_reference = f"MWC-NUVEI-{user.id}-{year}{month:02d}-{int(time.time())}"
    request_body = {
        "user": {
            "id": nuvei_user_id_for_card(user, card),
            "email": user.email,
            "phone": user.phone,
        },
        "order": nuvei_order(
            amount,
            description or f"Mayu Wellness Club mensualidad {month}/{year}",
            dev_reference,
        ),
        "card": {
            "token": token_for_provider(card),
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
        request_payload=json.dumps(_sanitize(request_body)),
        due_date=datetime(year, month, 1),
    )
    db.add(attempt)
    db.flush()

    response = nuvei_request("POST", "/v2/transaction/debit/", request_body)
    transaction = response.get("transaction") or response
    attempt.response_payload = json.dumps(_sanitize(response))
    attempt.transaction_id = transaction.get("id")
    attempt.authorization_code = transaction.get("authorization_code")
    attempt.status = str(transaction.get("status") or "unknown")
    attempt.status_detail = status_detail_as_int(transaction.get("status_detail"))
    attempt.message = transaction.get("message")
    attempt.charged_at = datetime.utcnow()

    payment = None
    renewal_sync = None
    admin_email_sync = None

    if is_nuvei_success(response):
        payment = create_payment_from_success(db, user, attempt, response)
        advance_card_after_success(card, attempt.charged_at)
        if payment.payment_type == "subscription_renewal":
            # Nuvei debe recorrer exactamente el mismo circuito operativo que
            # una renovación PayPal: socio activo, orden de logística,
            # comisión del embajador, Wallet y notificaciones.
            renewal_sync = reconcile_subscription_renewal(
                db=db,
                payment=payment,
                sync_wallet=True,
            )
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
                order=(
                    db.query(Order).filter(Order.id == payment.order_id).first()
                    if payment.order_id
                    else None
                ),
                trigger=trigger,
            )
        except Exception as exc:
            admin_email_sync = {"sent": False, "error": str(exc)}
    else:
        # Un rechazo no cancela la membresia en el primer intento. El cron la
        # reintenta y el estado final se administra con la politica de cobro.
        attempt.next_retry_at = datetime.utcnow() + timedelta(days=1)
        schedule_card_retry(card)
        if card.failed_attempts >= 3:
            record_security_event(
                db,
                event_type="nuvei_repeated_payment_failure",
                severity="critical",
                source="nuvei_membership_renewal",
                reference=dev_reference,
                summary="La membresía alcanzó tres o más intentos de cobro fallidos.",
                metadata={
                    "user_id": user.id,
                    "failed_attempts": card.failed_attempts,
                    "status": attempt.status,
                    "status_detail": attempt.status_detail,
                },
            )

    db.commit()

    return {
        "skipped": False,
        "success": is_nuvei_success(response),
        "attempt": attempt_to_dict(attempt),
        "payment_id": payment.id if payment else None,
        "renewal_processing": renewal_sync,
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
        "monthly_prices": MONTHLY_PRICES,
        "sandbox_renewal_interval_days": get_sandbox_renewal_interval_days(),
        "callback_url": get_callback_url(),
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
        "callback_url": get_callback_url(),
    }


@router.get("/signup/client-config")
def signup_client_config(email: EmailStr, phone: str):
    """Configuracion publica de tokenizacion; no crea ningun registro MAYU."""
    normalized_email = email.strip().lower()
    temporary_id = signup_nuvei_user_id(normalized_email, phone)
    return {
        "mode": get_nuvei_mode(),
        "client_app_code": get_client_app_code(),
        "client_app_key": get_client_app_key(),
        "user": {"id": temporary_id, "email": normalized_email, "phone": phone.strip()},
        "callback_url": get_callback_url(),
    }


def _complete_initial_signup(
    db: Session,
    *,
    user: User,
    card: NuveiMembershipCard,
    attempt: NuveiRecurringAttempt,
    response: dict,
) -> MembershipPayment:
    if attempt.membership_payment_id:
        return db.query(MembershipPayment).filter(
            MembershipPayment.id == attempt.membership_payment_id
        ).first()

    metadata = json.loads(attempt.request_payload or "{}")
    payment = create_payment_from_success(db, user, attempt, response)
    advance_card_after_success(card, datetime.utcnow())

    selection = get_or_create_monthly_selection(db, user, attempt.month, attempt.year)
    if selection:
        for product_name in dict.fromkeys(metadata.get("initial_products") or []):
            product = db.query(Product).filter(Product.name == product_name).first()
            if product:
                exists = db.query(MonthlySelectionItem).filter(
                    MonthlySelectionItem.monthly_selection_id == selection.id,
                    MonthlySelectionItem.product_id == product.id,
                ).first()
                if not exists:
                    db.add(MonthlySelectionItem(
                        monthly_selection_id=selection.id,
                        product_id=product.id,
                        quantity=1,
                    ))
        selection.status = "confirmed"
        selection.editable = True

    ambassador_code = (metadata.get("ambassador_code") or "").strip()
    if ambassador_code:
        ambassador = db.query(Ambassador).filter(
            Ambassador.ambassador_code == ambassador_code
        ).first()
        existing_referral = db.query(AmbassadorReferral).filter(
            AmbassadorReferral.user_id == user.id
        ).first()
        if ambassador and not existing_referral:
            db.add(AmbassadorReferral(
                ambassador_id=ambassador.id,
                user_id=user.id,
                referral_code=ambassador_code,
                status="active",
            ))

    upsert_marketing_contact(
        db,
        name=user.name,
        email=user.email,
        phone=user.phone,
        city=user.city,
        source="mayu_wellness",
        user_id=user.id,
        marketing_consent=True,
        consent_source="digital_policy",
    )
    return payment


def _apply_three_ds_result(
    db: Session,
    attempt: NuveiRecurringAttempt,
    response: dict,
) -> tuple[Optional[MembershipPayment], Optional[dict]]:
    transaction = response.get("transaction") or response
    attempt.response_payload = json.dumps(_sanitize(response))
    attempt.transaction_id = transaction.get("id") or attempt.transaction_id
    attempt.authorization_code = transaction.get("authorization_code") or attempt.authorization_code
    attempt.status = str(transaction.get("status") or attempt.status)
    attempt.status_detail = status_detail_as_int(transaction.get("status_detail"))
    attempt.message = transaction.get("message")

    if is_nuvei_success(response):
        user = db.query(User).filter(User.id == attempt.user_id).first()
        card = db.query(NuveiMembershipCard).filter(
            NuveiMembershipCard.id == attempt.card_id
        ).first()
        if not user or not card:
            raise HTTPException(status_code=409, detail="Alta 3DS incompleta")
        attempt.charged_at = datetime.utcnow()
        return _complete_initial_signup(
            db, user=user, card=card, attempt=attempt, response=response
        ), None

    if attempt.status_detail in {35, 36}:
        state = _three_ds_state(attempt.dev_reference)
        return None, _three_ds_action(attempt, state, response)

    return None, None


@router.post("/signup/activate", status_code=201)
def secure_signup(
    payload: SecureSignupRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Creates a pending member and completes activation only after Nuvei/3DS approval."""
    email = payload.email.strip().lower()
    cedula = payload.cedula.strip()
    required = {
        "nombre": payload.name,
        "telefono": payload.phone,
        "cedula": cedula,
        "ciudad": payload.city,
        "direccion": payload.address,
        "referencia": payload.reference,
        "datos de facturacion": payload.delivery_notes,
        "contrasena": payload.password,
        "token Nuvei": payload.token,
    }
    missing = [label for label, value in required.items() if not str(value or "").strip()]
    if missing:
        raise HTTPException(status_code=400, detail=f"Falta completar: {', '.join(missing)}")
    if not (payload.accepted_terms and payload.accepted_privacy_policy and payload.accepted_digital_policy):
        raise HTTPException(status_code=400, detail="Debes aceptar todas las politicas obligatorias")
    if payload.membership_level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Nivel de membresia invalido")
    if get_nuvei_mode() in {"production", "live"} and not payload.cvc:
        raise HTTPException(status_code=400, detail="CVC requerido para el primer pago 3DS")
    if get_nuvei_mode() in {"production", "live"} and not payload.browser_info:
        raise HTTPException(status_code=400, detail="Información del navegador requerida para 3DS")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="El correo ya esta registrado")
    if db.query(User).filter(User.cedula == cedula).first():
        raise HTTPException(status_code=409, detail="La cedula ya esta registrada")

    ambassador = None
    ambassador_code = (payload.ambassador_code or "").strip() or None
    if ambassador_code:
        ambassador = db.query(Ambassador).filter(Ambassador.ambassador_code == ambassador_code).first()
        if not ambassador:
            raise HTTPException(status_code=400, detail="Codigo de embajador invalido")

    now = datetime.utcnow()
    birth_date = None
    if payload.birth_date:
        try:
            birth_date = datetime.fromisoformat(payload.birth_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Fecha de nacimiento invalida")
    try:
        user = User(
            name=payload.name.strip(), email=email, password=hash_password(payload.password),
            phone=payload.phone.strip(), cedula=cedula, city=payload.city.strip(),
            address=payload.address.strip(), reference=payload.reference.strip(),
            birth_date=birth_date,
            delivery_notes=payload.delivery_notes.strip(),
            phone_secondary=(payload.phone_secondary or "").strip() or None,
            role="member", is_active=False, status="payment_pending",
            membership_level=payload.membership_level, membership_active=False,
            accepted_terms=True, accepted_privacy_policy=True, accepted_digital_policy=True,
            accepted_terms_at=now, accepted_privacy_policy_at=now, accepted_digital_policy_at=now,
        )
        db.add(user)
        db.flush()
        raw_card = {"card": {
            "token": payload.token, "status": payload.status,
            "holder_name": payload.holder_name, "bin": payload.bin, "last4": payload.last4,
            "type": payload.card_type, "expiry_month": payload.expiry_month,
            "expiry_year": payload.expiry_year, "origin": payload.origin,
            "transaction_reference": payload.transaction_reference,
        }}
        card = save_card_from_payload(db, user, raw_card, make_default=True)
        external_nuvei_user_id = signup_nuvei_user_id(email, payload.phone)
        card.raw_payload = json.dumps({
            "card": {"token_received": True},
            "nuvei_user_id": external_nuvei_user_id,
        })
        month, year = current_cycle()
        amount = MONTHLY_PRICES[payload.membership_level]
        dev_reference = f"MWC-NUVEI-SIGNUP-{user.id}-{int(time.time())}"
        state = _three_ds_state(dev_reference)
        request_body = {
            "user": {"id": external_nuvei_user_id, "email": user.email, "phone": user.phone},
            "order": nuvei_order(
                amount,
                f"Mayu Wellness Club primer debito Nivel {payload.membership_level}",
                dev_reference,
            ),
            "card": {
                "token": token_for_provider(card),
                **({"cvc": payload.cvc} if payload.cvc else {}),
            },
            "extra_params": {
                "threeDS2_data": {
                    "term_url": _three_ds_term_url(state),
                    "device_type": "browser",
                    "browser_info": _browser_payload(request, payload.browser_info),
                }
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
            attempt_type="subscription",
            status="created",
            request_payload=json.dumps({
                "initial_products": payload.initial_products,
                "ambassador_code": ambassador_code,
                "three_ds": {"term_url_created": True, "browser_info_received": True},
            }),
            due_date=now,
        )
        db.add(attempt)
        db.flush()
        response = nuvei_request("POST", "/v2/transaction/debit/", request_body)
        transaction = response.get("transaction") or response
        payment, next_action = _apply_three_ds_result(db, attempt, response)

        # Erase the only in-memory reference containing the CVC as soon as the
        # provider call returns. It is never written to PostgreSQL or logs.
        request_body["card"].pop("cvc", None)

        if next_action:
            db.commit()
            return JSONResponse(status_code=202, content=next_action)

        if not payment:
            db.rollback()
            raise HTTPException(
                status_code=402,
                detail={
                    "message": "Nuvei no aprobo el pago. No se registro ningun usuario.",
                    "status": transaction.get("status"),
                    "status_detail": transaction.get("status_detail"),
                },
            )

        db.commit()
        db.refresh(user)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    welcome = None
    try:
        welcome = send_welcome_member_notifications(db=db, user=user, trigger="nuvei_subscription_activation")
        db.commit()
    except Exception as exc:
        db.rollback()
        welcome = {"sent": False, "error": str(exc)}
    return {
        "id": user.id, "name": user.name, "email": user.email,
        "membership_level": user.membership_level, "membership_active": user.membership_active,
        "payment_id": payment.id, "transaction_id": transaction.get("id"),
        "authorization_code": transaction.get("authorization_code"),
        "welcome_notifications": welcome,
    }


def _attempt_from_state(db: Session, state: str) -> NuveiRecurringAttempt:
    dev_reference = _parse_three_ds_state(state)
    attempt = db.query(NuveiRecurringAttempt).filter(
        NuveiRecurringAttempt.dev_reference == dev_reference,
        NuveiRecurringAttempt.attempt_type == "subscription",
    ).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Transacción 3DS no encontrada")
    return attempt


def _verify_three_ds(db: Session, attempt: NuveiRecurringAttempt, verify_type: str, value: str):
    if attempt.membership_payment_id:
        return {"already_completed": True, "payment_id": attempt.membership_payment_id}
    card = db.query(NuveiMembershipCard).filter(
        NuveiMembershipCard.id == attempt.card_id
    ).first()
    user = db.query(User).filter(User.id == attempt.user_id).first()
    if not card or not user or not attempt.transaction_id:
        raise HTTPException(status_code=409, detail="Transacción 3DS incompleta")
    body = {
        "transaction": {"id": attempt.transaction_id},
        "user": {"id": nuvei_user_id_for_card(user, card)},
        "type": verify_type,
        "value": value,
        "more_info": True,
    }
    response = nuvei_request("POST", "/v2/transaction/verify", body)
    payment, next_action = _apply_three_ds_result(db, attempt, response)
    db.commit()
    if payment:
        try:
            send_welcome_member_notifications(
                db=db, user=user, trigger="nuvei_subscription_activation"
            )
            db.commit()
        except Exception:
            db.rollback()
        return {"success": True, "payment_id": payment.id, "status_detail": 3}
    if next_action:
        return next_action
    record_security_event(
        db,
        event_type="nuvei_3ds_failed",
        severity="critical",
        source="nuvei_membership_3ds",
        reference=attempt.dev_reference,
        summary="Nuvei no aprobó la verificación 3DS inicial.",
        metadata={"status": attempt.status, "status_detail": attempt.status_detail},
    )
    db.commit()
    raise HTTPException(status_code=402, detail="Verificación 3DS no aprobada")


@router.get("/3ds/content/{state}", response_class=HTMLResponse)
def three_ds_content(state: str, db: Session = Depends(get_db)):
    attempt = _attempt_from_state(db, state)
    if attempt.membership_payment_id:
        return HTMLResponse("<p>Pago ya confirmado.</p>")
    response = json.loads(attempt.response_payload or "{}")
    artifact = (
        _three_ds_artifact(response, "challenge_request")
        or _three_ds_artifact(response, "hidden_iframe")
        or _three_ds_artifact(response, "html")
        or _three_ds_artifact(response, "content")
    )
    if not artifact:
        # Some sandbox processors complete status 36 asynchronously through
        # the webhook and do not return challenge HTML. Keep the bank window
        # clean and wait for the authoritative server-side result.
        status_url = f"{_public_api_base()}/payments/nuvei/membership/3ds/status/{state}"
        return HTMLResponse(f"""<!doctype html><html lang=\"es\"><head>
<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<style>body{{font-family:Arial,sans-serif;display:grid;place-items:center;min-height:85vh;margin:0;color:#063f38}}.box{{text-align:center;padding:28px}}.spin{{width:42px;height:42px;margin:0 auto 20px;border:4px solid #d9efec;border-top-color:#2bb3ad;border-radius:50%;animation:s 1s linear infinite}}@keyframes s{{to{{transform:rotate(360deg)}}}}</style>
</head><body><div class=\"box\"><div class=\"spin\"></div><h2>Confirmando el pago seguro</h2><p>Espera un momento. No cierres esta ventana.</p></div>
<script>const u={json.dumps(status_url)};const timer=setInterval(async()=>{{try{{const r=await fetch(u,{{headers:{{Accept:'application/json'}}}});const d=await r.json();if(d.success===true){{clearInterval(timer);parent.postMessage({{type:'mayu-nuvei-3ds',success:true}},location.origin)}}else if(d.failed===true){{clearInterval(timer);document.querySelector('p').textContent='El banco no aprobó el pago.'}}}}catch(e){{}}}},1500);</script></body></html>""")
    return HTMLResponse(artifact)


@router.get("/3ds/status/{state}")
def three_ds_status(state: str, db: Session = Depends(get_db)):
    attempt = _attempt_from_state(db, state)
    if attempt.membership_payment_id:
        return {"success": True, "payment_id": attempt.membership_payment_id}
    if attempt.status_detail not in {35, 36} and attempt.status not in {"created", "pending"}:
        return {"success": False, "failed": True}
    return {"success": False, "payment_pending": True}


@router.post("/3ds/continue/{state}")
def continue_three_ds(state: str, db: Session = Depends(get_db)):
    attempt = _attempt_from_state(db, state)
    if attempt.status_detail != 35 and not attempt.membership_payment_id:
        raise HTTPException(status_code=409, detail="La transacción no espera continuación frictionless")
    return _verify_three_ds(db, attempt, "AUTHENTICATION_CONTINUE", "")


@router.post("/3ds/term/{state}", response_class=HTMLResponse)
async def complete_three_ds_challenge(
    state: str,
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    cres = str(form.get("cres") or form.get("CRes") or "").strip()
    if not cres:
        raise HTTPException(status_code=400, detail="CRes requerido")
    attempt = _attempt_from_state(db, state)
    result = _verify_three_ds(db, attempt, "BY_CRES", cres)
    success = bool(result.get("success") or result.get("already_completed"))
    message = "Pago confirmado correctamente." if success else "El banco todavía está verificando el pago."
    return HTMLResponse(
        "<!doctype html><html><body>"
        f"<p>{message}</p>"
        "<script>const p=window.location.ancestorOrigins&&window.location.ancestorOrigins[0];"
        "const a=['https://mayuwellnesclub.com','https://www.mayuwellnesclub.com'];"
        "if(a.includes(p))window.parent.postMessage({type:'mayu-nuvei-3ds',success:"
        f"{str(success).lower()}"
        "},p);</script>"
        "</body></html>"
    )


@router.post("/cards/register-token")
def register_token(
    payload: RegisterTokenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = resolve_user(db, current_user, payload.user_id)
    raw = {
        "card": {
            "token": payload.token,
            "status": payload.status,
            "holder_name": payload.holder_name,
            "bin": payload.bin,
            "last4": payload.last4,
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
        "next_debit_date": card.next_debit_at.date().isoformat(),
        "monthly_amount": monthly_amount_for_user(user),
        "initial_debit": initial_debit,
        "welcome_notifications": welcome_sync,
    }


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
    default_card = get_default_card(db, user.id)
    return {
        "items": [card_to_dict(card) for card in cards],
        "remote": remote,
        "next_debit_date": (
            default_card.next_debit_at.date().isoformat()
            if default_card and default_card.next_debit_at
            else next_debit_date_for_user(db, user).isoformat()
        ),
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
            "card": {"token": token_for_provider(card)},
            "user": {"id": str(user.id)},
        },
    )
    card.is_active = False
    card.is_default = False
    card.deleted_at = datetime.utcnow()
    card.raw_payload = json.dumps({"delete_response": _sanitize(response)})
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
    x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret"),
    dry_run: bool = False,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    expected = get_cron_secret()
    if not expected or not x_cron_secret or not hmac.compare_digest(x_cron_secret, expected):
        raise HTTPException(status_code=403, detail="Secret inválido")

    today = datetime.utcnow().date()
    safe_limit = max(1, min(int(limit), 500))
    due_cards = (
        db.query(NuveiMembershipCard)
        .join(User, User.id == NuveiMembershipCard.user_id)
        .filter(
            NuveiMembershipCard.is_active == True,
            NuveiMembershipCard.is_default == True,
            NuveiMembershipCard.status.in_(["valid", "review"]),
            User.role == "member",
            User.is_active == True,
            User.membership_active == True,
            User.membership_level.in_([1, 2, 3]),
        )
        .order_by(
            NuveiMembershipCard.next_debit_at.asc().nullsfirst(),
            NuveiMembershipCard.id.asc(),
        )
        .limit(safe_limit)
        .all()
    )

    results = []
    for card in due_cards:
        user = card.user
        due_date = (
            card.next_debit_at.date()
            if card.next_debit_at
            else next_debit_date_for_user(db, user)
        )
        # Never use an old accelerated sandbox date before a full month
        # has passed since the last successful debit. Failed attempts do not
        # update last_debit_at, so daily retries after the due date still work.
        due_date = monthly_due_date(card, due_date)
        month, year = cycle_from_date(due_date)
        item = {
            "user_id": user.id,
            "user_name": user.name,
            "due_date": due_date.isoformat(),
            "monthly_amount": monthly_amount_for_user(user),
            "has_card": True,
            "due_today": due_date <= today,
        }
        if int(card.failed_attempts or 0) >= get_max_retry_attempts():
            item["status"] = "skipped_retry_limit"
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
                # En Sandbox, una prueba acelerada puede tener más de un
                # débito dentro del mismo mes calendario. En producción la
                # deduplicación mensual permanece obligatoria.
                force=bool(get_sandbox_renewal_interval_days()),
            )
            item["status"] = "processed"
        results.append(item)

    return {"message": "Cron Nuvei procesado", "dry_run": dry_run, "items": results}


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

    if payment and is_nuvei_success(response):
        payment.status = "refunded"
        payment.raw_payload = json.dumps({"last_refund": _sanitize(response)})
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

    try:
        validate_webhook_signature(transaction, user_id)
    except HTTPException:
        record_security_event(
            db,
            event_type="nuvei_invalid_webhook_signature",
            severity="critical",
            source="nuvei_webhook",
            reference=dev_reference or transaction_id or None,
            summary="Nuvei webhook rechazado por firma o application_code inválidos.",
            metadata={"transaction_id": transaction_id, "dev_reference": dev_reference},
        )
        db.commit()
        raise

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

    if not attempt:
        # Este endpoint procesa exclusivamente cobros de membresia creados por
        # MAYU. Otros productos Nuvei no pueden activar socios por callback.
        record_security_event(
            db,
            event_type="nuvei_unknown_callback",
            severity="warning",
            source="nuvei_webhook",
            reference=dev_reference or transaction_id or None,
            summary="Se recibió un callback que no corresponde a una membresía creada por Mayu.",
            metadata={"transaction_id": transaction_id, "dev_reference": dev_reference},
        )
        db.commit()
        return {
            "status": "ignored",
            "reason": "membership_attempt_not_found",
            "transaction_id": transaction_id or None,
        }

    user = db.query(User).filter(User.id == attempt.user_id).first()
    callback_was_already_processed = False
    if user:
        callback_was_already_processed = bool(
            attempt.response_payload and attempt.next_retry_at
        )
        attempt.response_payload = json.dumps(_sanitize(event))
        attempt.transaction_id = transaction_id or attempt.transaction_id
        attempt.authorization_code = transaction.get("authorization_code")
        attempt.status = str(transaction.get("status") or attempt.status)
        attempt.status_detail = status_detail_as_int(
            transaction.get("status_detail") or attempt.status_detail
        )
        attempt.message = transaction.get("message")
        attempt.charged_at = datetime.utcnow()

    payment = None
    if user and is_nuvei_success({"transaction": transaction}):
        callback_amount = round(float(transaction.get("amount") or 0), 2)
        if callback_amount != round(float(attempt.amount), 2):
            record_security_event(
                db,
                event_type="nuvei_amount_mismatch",
                severity="critical",
                source="nuvei_webhook",
                reference=attempt.dev_reference,
                summary="El valor reportado por el callback no coincide con la membresía esperada.",
                metadata={"expected_amount": attempt.amount, "callback_amount": callback_amount},
            )
            db.commit()
            raise HTTPException(status_code=409, detail="Monto del webhook no coincide")
        was_already_reconciled = attempt.membership_payment_id is not None
        payment = create_payment_from_success(db, user, attempt, {"transaction": transaction})
        if attempt.card and not was_already_reconciled:
            advance_card_after_success(attempt.card, attempt.charged_at)
        if payment.payment_type == "subscription_renewal" and not payment.admin_verified:
            reconcile_subscription_renewal(db, payment, sync_wallet=True)
        try:
            notify_admin_member_payment_event(
                db=db,
                user=user,
                payment=payment,
                order=(
                    db.query(Order).filter(Order.id == payment.order_id).first()
                    if payment.order_id
                    else None
                ),
                trigger="nuvei_webhook_payment",
            )
        except Exception:
            pass
    elif user and attempt:
        attempt.next_retry_at = datetime.utcnow() + timedelta(days=1)
        if attempt.card and not callback_was_already_processed:
            schedule_card_retry(attempt.card)

    db.commit()
    return {
        "status": "ok",
        "matched_attempt": attempt.id if attempt else None,
        "payment_id": payment.id if payment else None,
        "transaction_id": transaction_id or None,
        "success": is_nuvei_success({"transaction": transaction}),
    }
