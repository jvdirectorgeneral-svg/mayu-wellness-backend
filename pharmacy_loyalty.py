import uuid
import io
import os
import json
import tempfile
import base64
import resend
import requests
from html import escape
from email.utils import format_datetime
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from auth import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    hash_password,
    verify_password,
)
from database import get_db
from dependencies import get_current_user
from marketing import send_push_notification
from member_cards import (
    BASE_PUBLIC_URL,
    build_manifest,
    clean_google_private_key,
    cover_image_to_canvas,
    create_wallet_icon,
    fit_image_to_canvas,
    get_google_wallet_service_account,
    sign_manifest,
    zip_pkpass,
)
import models
import qrcode
import jwt as pyjwt
from pharmacy_assets import TARJETA_SOCIOSFARMACIA_JPG_BASE64


router = APIRouter(prefix="/pharmacy-loyalty", tags=["Pharmacy Loyalty"])
security = HTTPBearer()
POINT_VALUE_CENTS = 1000
PHARMACY_WALLET_AUTH_PREFIX = "mayu-magistral-wallet"

# Apple Wallet y Google Wallet admiten hasta 10 ubicaciones de relevancia por
# tarjeta/objeto. Estas son las ubicaciones iniciales aprobadas para la tarjeta
# de puntos de Farmacia Mayu. Render todavía puede sustituir la lista completa
# mediante PHARMACY_WALLET_LOCATIONS_JSON sin necesidad de publicar código.
DEFAULT_PHARMACY_WALLET_LOCATIONS = [
    {
        "name": "Estás cerca de Farmacia Mayu Quito. Revisa tus puntos y beneficios.",
        "latitude": -0.182462,
        "longitude": -78.482439,
    },
    {
        "name": "Estás cerca de Farmacia Mayu Cuenca. Revisa tus puntos y beneficios.",
        "latitude": -2.902350,
        "longitude": -79.014920,
    },
    {
        "name": "Estás cerca de Megamaxi El Condado. Revisa tus beneficios Mayu.",
        "latitude": -0.104340,
        "longitude": -78.490690,
    },
    {
        "name": "Estás cerca de Megamaxi 6 de Diciembre. Revisa tus beneficios Mayu.",
        "latitude": -0.180370,
        "longitude": -78.477450,
    },
    {
        "name": "Estás cerca de Megamaxi Quicentro Sur. Revisa tus beneficios Mayu.",
        "latitude": -0.285990,
        "longitude": -78.543180,
    },
    {
        "name": "Estás cerca de Megamaxi Scala Shopping. Revisa tus beneficios Mayu.",
        "latitude": -0.207820,
        "longitude": -78.425620,
    },
    {
        "name": "Estás cerca de Megamaxi Mall del Sol. Revisa tus beneficios Mayu.",
        "latitude": -2.155040,
        "longitude": -79.892670,
    },
    {
        "name": "Estás cerca de Megamaxi Los Ceibos. Revisa tus beneficios Mayu.",
        "latitude": -2.173720,
        "longitude": -79.939850,
    },
    {
        "name": "Estás cerca de Supermaxi Las Américas. Revisa tus beneficios Mayu.",
        "latitude": -2.889670,
        "longitude": -79.024260,
    },
    {
        "name": "Estás cerca de Supermaxi La Plaza Shopping. Revisa tus beneficios Mayu.",
        "latitude": 0.345910,
        "longitude": -78.136350,
    },
]


def configured_pharmacy_wallet_locations():
    """Devuelve hasta 10 ubicaciones; Render puede reemplazar las predeterminadas."""
    raw = os.getenv("PHARMACY_WALLET_LOCATIONS_JSON", "").strip()
    if not raw:
        return [dict(item) for item in DEFAULT_PHARMACY_WALLET_LOCATIONS]
    try:
        locations = json.loads(raw)
        if not isinstance(locations, list):
            return []
        clean = []
        for item in locations[:10]:
            if not isinstance(item, dict):
                continue
            latitude = float(item["latitude"])
            longitude = float(item["longitude"])
            name = str(item.get("name") or "Farmacia Magistral Mayu cercana").strip()
            clean.append({
                "latitude": latitude,
                "longitude": longitude,
                "relevantText": name,
            })
        return clean
    except Exception as exc:
        print(f"[pharmacy-wallet] invalid PHARMACY_WALLET_LOCATIONS_JSON: {exc}")
        return []


class PharmacyCustomerRegister(BaseModel):
    name: str
    email: str
    phone: str
    password: str
    birth_date: Optional[str] = None
    cedula: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    reference: Optional[str] = None
    delivery_notes: Optional[str] = None
    accepted_terms: bool
    accepted_privacy_policy: bool
    accepted_digital_policy: bool


class PharmacyCustomerLogin(BaseModel):
    email: str
    password: str


class PharmacyPurchaseCredit(BaseModel):
    amount: float
    reference: str
    note: Optional[str] = None


class PharmacyPointsRedemption(BaseModel):
    note: Optional[str] = None


class PharmacyPushTokenRequest(BaseModel):
    token: str
    platform: Optional[str] = None


class PharmacyRecoverCardRequest(BaseModel):
    email: str
    phone: str


class AppleWalletRegistrationRequest(BaseModel):
    pushToken: str


def pharmacy_customer_admin_dict(customer, card=None):
    return {
        "id": customer.id,
        "name": customer.name,
        "email": customer.email,
        "phone": customer.phone,
        "birth_date": customer.birth_date,
        "city": customer.city,
        "is_active": customer.is_active,
        "created_at": customer.created_at,
        "card": card_to_dict(customer, card, include_transactions=False)
        if card
        else None,
    }


def require_pharmacy_admin(user: models.User):
    if user.role not in {"superadmin", "admin", "pharmacy_admin"}:
        raise HTTPException(
            status_code=403,
            detail="Solo Farmacia Administrador puede acreditar puntos",
        )


def customer_to_dict(customer: models.PharmacyCustomer):
    return {
        "id": customer.id,
        "name": customer.name,
        "email": customer.email,
        "phone": customer.phone,
        "cedula": customer.cedula,
        "birth_date": customer.birth_date,
        "city": customer.city,
        "address": customer.address,
        "reference": customer.reference,
        "delivery_notes": customer.delivery_notes,
        "is_active": customer.is_active,
    }


def get_current_pharmacy_customer(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        token_type = payload.get("type")
        subject = payload.get("sub")
        if token_type != "pharmacy_customer" or not subject:
            raise HTTPException(status_code=401, detail="Token Farmacia inválido")
        prefix = "pharmacy_customer:"
        if not str(subject).startswith(prefix):
            raise HTTPException(status_code=401, detail="Token Farmacia inválido")
        customer_id = int(str(subject).replace(prefix, "", 1))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Token Farmacia inválido")

    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == customer_id)
        .first()
    )
    if not customer or not customer.is_active:
        raise HTTPException(status_code=401, detail="Cliente Farmacia no válido")
    return customer


def parse_birth_date(value: Optional[str]):
    if not value:
        return None
    clean = value.strip()
    if not clean:
        return None
    try:
        return datetime.strptime(clean, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Fecha de nacimiento inválida. Usa formato YYYY-MM-DD",
        )


def normalize_phone(value: Optional[str]) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def safe_send_pharmacy_email(to_email: str, subject: str, message: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev")
    logo_url = os.getenv(
        "MAYU_EMAIL_LOGO_URL",
        "https://mayuwellnesclub.com/mayu-email-logo.png",
    )

    if not resend_api_key or not to_email:
        return {"sent": False, "detail": "Email no configurado"}

    try:
        resend.api_key = resend_api_key
        resend.Emails.send(
            {
                "from": from_email,
                "to": [to_email],
                "subject": subject,
                "html": f"""
                <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;padding:24px;color:#1d2525">
                    <div style="text-align:center;background:#006054;padding:22px;border-radius:18px 18px 0 0">
                      <img src="{logo_url}" alt="Mayu Salud Funcional" width="210"
                        style="display:block;width:210px;max-width:80%;height:auto;margin:0 auto;border:0" />
                      <div style="color:#ffffff;font-size:18px;font-weight:bold;margin-top:12px">Mayu Magistral</div>
                    </div>
                    <div style="padding:24px;border:1px solid #d8e6e2;border-top:0;border-radius:0 0 18px 18px">
                    <div style="font-size:16px; line-height:1.7; white-space:pre-line;">
                        {message}
                    </div>
                    <br>
                    <p style="color:#00695C;">Equipo Mayu Magistral</p>
                    </div>
                </div>
                """,
            }
        )
        return {"sent": True}
    except Exception as exc:
        return {"sent": False, "detail": str(exc)}


def build_pharmacy_card_email_message(customer, card):
    card_data = card_to_dict(customer, card, include_transactions=False)
    return (
        f"Hola {customer.name},\n\n"
        "Tu Tarjeta Mayu Magistral está lista.\n\n"
        f"Código de tarjeta: {card.card_code}\n"
        f"Puntos actuales: {card.points_balance}\n\n"
        "Descargar para iPhone / Apple Wallet:\n"
        f"{card_data['apple_wallet_url']}\n\n"
        "Descargar para Android / Google Wallet:\n"
        f"{card_data['google_wallet_url']}\n\n"
        "También puedes mostrar tu QR desde la app para que Farmacia Mayu registre tus compras."
    )


def deactivate_invalid_pharmacy_token(push_token, error_text: str):
    invalid_markers = [
        "UNREGISTERED",
        "INVALID_ARGUMENT",
        "registration token is not a valid",
        "Requested entity was not found",
    ]
    if any(marker in error_text for marker in invalid_markers):
        push_token.is_active = False
        push_token.updated_at = datetime.utcnow()


def safe_send_push_to_pharmacy_customer(
    db: Session,
    pharmacy_customer_id: int,
    title: str,
    message: str,
    image_url: Optional[str] = None,
):
    push_token = (
        db.query(models.PharmacyPushNotificationToken)
        .filter(
            models.PharmacyPushNotificationToken.pharmacy_customer_id
            == pharmacy_customer_id,
            models.PharmacyPushNotificationToken.is_active == True,
        )
        .order_by(models.PharmacyPushNotificationToken.updated_at.desc())
        .first()
    )
    if not push_token:
        return {"sent": False, "detail": "Socio farmacia sin token push activo"}

    try:
        result = send_push_notification(
            token=push_token.token,
            title=title,
            message=message,
            image_url=image_url,
        )
        return {"sent": True, "token_id": push_token.id, "firebase": result}
    except Exception as exc:
        deactivate_invalid_pharmacy_token(push_token, str(exc))
        return {"sent": False, "token_id": push_token.id, "detail": str(exc)}


def get_or_create_card(db: Session, customer_id: int):
    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == customer_id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente Farmacia no encontrado")

    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(models.PharmacyLoyaltyCard.pharmacy_customer_id == customer_id)
        .first()
    )
    if card:
        return customer, card

    card = models.PharmacyLoyaltyCard(
        pharmacy_customer_id=customer_id,
        card_code=f"FAR-MAYU-{customer_id:06d}",
        qr_token=str(uuid.uuid4()),
    )
    db.add(card)
    db.flush()
    return customer, card


def card_to_dict(customer, card, include_transactions=True):
    data = {
        "id": card.id,
        "pharmacy_customer_id": card.pharmacy_customer_id,
        "customer_name": customer.name,
        "card_code": card.card_code,
        "qr_token": card.qr_token,
        "points_balance": card.points_balance,
        "lifetime_points": card.lifetime_points,
        "accumulated_cents": card.accumulated_cents,
        "amount_until_next_point_cents": (
            0
            if card.accumulated_cents == 0
            else POINT_VALUE_CENTS - card.accumulated_cents
        ),
        "active": card.active,
        "qr_url": f"{BASE_PUBLIC_URL}/pharmacy-loyalty/qr/{card.qr_token}",
        "qr_image_url": (
            f"{BASE_PUBLIC_URL}/pharmacy-loyalty/qr/{card.qr_token}/image"
        ),
        "apple_wallet_url": (
            f"{BASE_PUBLIC_URL}/pharmacy-loyalty/wallet/apple/{card.qr_token}"
        ),
        "google_wallet_url": (
            f"{BASE_PUBLIC_URL}/pharmacy-loyalty/wallet/google/{card.qr_token}"
        ),
    }
    if include_transactions:
        data["transactions"] = [
            {
                "id": item.id,
                "purchase_amount_cents": item.purchase_amount_cents,
                "points_delta": item.points_delta,
                "remainder_after_cents": item.remainder_after_cents,
                "source": item.source,
                "reference": item.reference,
                "note": item.note,
                "created_at": item.created_at,
            }
            for item in card.transactions
        ]
    return data


def _amount_to_cents(amount) -> int:
    cents = int(
        (Decimal(str(amount)) * Decimal("100")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    if cents <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor a cero")
    return cents


def calculate_points(previous_cents: int, purchase_cents: int):
    accumulated = previous_cents + purchase_cents
    return accumulated // POINT_VALUE_CENTS, accumulated % POINT_VALUE_CENTS


def credit_purchase(
    db: Session,
    pharmacy_customer_id: int,
    amount,
    source: str,
    reference: Optional[str] = None,
    marketplace_order_id: Optional[int] = None,
    created_by: Optional[int] = None,
    note: Optional[str] = None,
):
    if reference:
        existing = (
            db.query(models.PharmacyPointsTransaction)
            .filter(models.PharmacyPointsTransaction.reference == reference)
            .first()
        )
        if existing:
            return existing.card, existing, False

    if marketplace_order_id:
        existing = (
            db.query(models.PharmacyPointsTransaction)
            .filter(
                models.PharmacyPointsTransaction.marketplace_order_id
                == marketplace_order_id
            )
            .first()
        )
        if existing:
            return existing.card, existing, False

    _, card = get_or_create_card(db, pharmacy_customer_id)
    amount_cents = _amount_to_cents(amount)
    points, remainder = calculate_points(card.accumulated_cents, amount_cents)

    card.points_balance += points
    card.lifetime_points += points
    card.accumulated_cents = remainder
    card.updated_at = datetime.utcnow()

    transaction = models.PharmacyPointsTransaction(
        card_id=card.id,
        marketplace_order_id=marketplace_order_id,
        purchase_amount_cents=amount_cents,
        points_delta=points,
        remainder_after_cents=remainder,
        source=source,
        reference=reference,
        note=note,
        created_by=created_by,
    )
    db.add(transaction)
    db.flush()
    return card, transaction, True


def _clean_phone(value: Optional[str]) -> str:
    if not value:
        return ""
    return "".join(ch for ch in str(value) if ch.isdigit())


def _phones_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    # Ecuador: permite enlazar 098... con 59398... sin mezclar registros distintos.
    if len(left) >= 9 and len(right) >= 9:
        return left[-9:] == right[-9:]
    return False


def _identifier_variants(value: Optional[str]) -> set:
    if not value:
        return set()
    raw = str(value).strip()
    if not raw:
        return set()
    without_query = raw.split("?", 1)[0].split("#", 1)[0].strip()
    last_segment = without_query.rstrip("/").split("/")[-1].strip()
    variants = {raw, without_query, last_segment}
    variants.update({item.upper() for item in list(variants) if item})
    variants.update({item.lower() for item in list(variants) if item})
    variants.discard("")
    return variants


def find_pharmacy_customer_for_marketplace_order(db: Session, order):
    identifiers = _identifier_variants(
        getattr(order, "pharmacy_loyalty_identifier", None)
        or getattr(order, "mayu_magistral_identifier", None)
        or getattr(order, "pharmacy_card_code", None)
    )
    if identifiers:
        card = (
            db.query(models.PharmacyLoyaltyCard)
            .filter(
                models.PharmacyLoyaltyCard.active == True,
                models.PharmacyLoyaltyCard.pharmacy_customer_id.isnot(None),
                (
                    models.PharmacyLoyaltyCard.qr_token.in_(identifiers)
                    | models.PharmacyLoyaltyCard.card_code.in_(identifiers)
                ),
            )
            .first()
        )
        if card and card.pharmacy_customer_id:
            return (
                db.query(models.PharmacyCustomer)
                .filter(
                    models.PharmacyCustomer.id == card.pharmacy_customer_id,
                    models.PharmacyCustomer.is_active == True,
                )
                .first()
            )

    emails = {
        (getattr(order, "customer_email", None) or "").strip().lower(),
        (getattr(order, "billing_email", None) or "").strip().lower(),
    }
    emails.discard("")

    phones = {
        _clean_phone(getattr(order, "customer_phone", None)),
        _clean_phone(getattr(order, "billing_phone", None)),
    }
    phones.discard("")

    candidates = []
    if emails:
        candidates.extend(
            db.query(models.PharmacyCustomer)
            .filter(
                models.PharmacyCustomer.is_active == True,
                models.PharmacyCustomer.email.in_(emails),
            )
            .all()
        )

    if phones:
        for customer in (
            db.query(models.PharmacyCustomer)
            .filter(models.PharmacyCustomer.is_active == True)
            .all()
        ):
            customer_phone = _clean_phone(customer.phone)
            if any(_phones_match(customer_phone, phone) for phone in phones):
                candidates.append(customer)

    seen = set()
    unique_candidates = []
    for customer in candidates:
        if customer.id in seen:
            continue
        seen.add(customer.id)
        unique_candidates.append(customer)

    if not unique_candidates:
        return None

    for customer in unique_candidates:
        email_match = customer.email.strip().lower() in emails
        customer_phone = _clean_phone(customer.phone)
        phone_match = any(_phones_match(customer_phone, phone) for phone in phones)
        if email_match and phone_match:
            return customer

    return unique_candidates[0]


def sync_marketplace_loyalty_wallet_after_commit(
    db: Session,
    loyalty_result: Optional[dict],
    order_code: Optional[str] = None,
):
    if not loyalty_result or not loyalty_result.get("credited"):
        return loyalty_result

    points_earned = loyalty_result.get("points_earned") or 0
    card_id = loyalty_result.get("card_id")
    customer_id = loyalty_result.get("customer_id")
    if not card_id or not customer_id or points_earned <= 0:
        return loyalty_result

    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(models.PharmacyLoyaltyCard.id == card_id)
        .first()
    )
    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == customer_id)
        .first()
    )
    if not card or not customer:
        return loyalty_result

    push_result = safe_send_push_to_pharmacy_customer(
        db=db,
        pharmacy_customer_id=customer.id,
        title="⭐ Puntos Mayu Magistral acreditados",
        message=(
            f"Tu compra online {order_code or ''} sumó "
            f"{points_earned} punto(s). "
            f"Saldo actual: {card.points_balance} punto(s)."
        ),
    )
    wallet_sync = {
        "apple": safe_send_apple_wallet_update_pushes(db, card),
        "google": safe_update_google_wallet_object(customer, card),
        "google_notification": safe_notify_google_wallet_points(
            customer,
            card,
            "Puntos Mayu Magistral acreditados",
            f"Tu compra {order_code or ''} sumó {points_earned} punto(s). Saldo actual: {card.points_balance} punto(s).",
        ),
    }
    loyalty_result["push"] = push_result
    loyalty_result["wallet_sync"] = wallet_sync
    loyalty_result["points_balance"] = card.points_balance
    return loyalty_result


def credit_marketplace_order_if_paid(db: Session, order, sync_wallet: bool = True):
    payment_status = (getattr(order, "payment_status", "") or "").strip().lower()
    if payment_status != "paid":
        return {"credited": False, "detail": "El pedido aún no está pagado"}

    loyalty_identifier = (
        getattr(order, "pharmacy_loyalty_identifier", None)
        or getattr(order, "mayu_magistral_identifier", None)
        or getattr(order, "pharmacy_card_code", None)
    )
    if not str(loyalty_identifier or "").strip():
        return {
            "credited": False,
            "not_applicable": True,
            "detail": "Compra directa sin código de tarjeta Farmacia; no genera puntos.",
        }

    existing = (
        db.query(models.PharmacyPointsTransaction)
        .filter(models.PharmacyPointsTransaction.marketplace_order_id == order.id)
        .first()
    )
    if existing:
        return {
            "credited": False,
            "already_credited": True,
            "points_earned": existing.points_delta,
            "transaction_id": existing.id,
        }

    customer = find_pharmacy_customer_for_marketplace_order(db, order)
    if not customer:
        return {
            "credited": False,
            "detail": "No existe socio Mayu Magistral con el correo/teléfono del pedido",
        }

    card, transaction, created = credit_purchase(
        db,
        customer.id,
        getattr(order, "total", 0) or 0,
        "marketplace_online",
        reference=f"marketplace:{order.order_code}",
        marketplace_order_id=order.id,
        note="Compra online Marketplace Farmacia pagada",
    )
    db.flush()

    result = {
        "credited": created,
        "customer_id": customer.id,
        "card_id": card.id,
        "points_earned": transaction.points_delta,
        "points_balance": card.points_balance,
        "transaction_id": transaction.id,
        "push": None,
        "wallet_sync": {
            "apple": {"registered_devices": safe_get_apple_wallet_registration_count(db, card.id)},
            "google": None,
        },
    }
    if sync_wallet:
        result = sync_marketplace_loyalty_wallet_after_commit(
            db,
            result,
            getattr(order, "order_code", None),
        )
    return result


@router.post("/register")
def register_pharmacy_customer(
    payload: PharmacyCustomerRegister,
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    cedula = payload.cedula.strip() if payload.cedula else None
    now = datetime.utcnow()
    birth_date = parse_birth_date(payload.birth_date)

    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    if not payload.phone.strip():
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")
    if len(payload.password.strip()) < 6:
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe tener al menos 6 caracteres",
        )
    if not payload.accepted_terms:
        raise HTTPException(status_code=400, detail="Debes aceptar términos")
    if not payload.accepted_privacy_policy:
        raise HTTPException(status_code=400, detail="Debes aceptar privacidad")
    if not payload.accepted_digital_policy:
        raise HTTPException(status_code=400, detail="Debes aceptar notificaciones")

    if db.query(models.PharmacyCustomer).filter_by(email=email).first():
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    if cedula and db.query(models.PharmacyCustomer).filter_by(cedula=cedula).first():
        raise HTTPException(status_code=400, detail="La cédula ya está registrada")

    customer = models.PharmacyCustomer(
        name=payload.name.strip(),
        email=email,
        password=hash_password(payload.password.strip()),
        phone=payload.phone.strip(),
        cedula=cedula,
        birth_date=birth_date,
        city=payload.city.strip() if payload.city else None,
        address=payload.address.strip() if payload.address else None,
        reference=payload.reference.strip() if payload.reference else None,
        delivery_notes=(
            payload.delivery_notes.strip() if payload.delivery_notes else None
        ),
        accepted_terms=True,
        accepted_privacy_policy=True,
        accepted_digital_policy=True,
        accepted_terms_at=now,
        accepted_privacy_policy_at=now,
        accepted_digital_policy_at=now,
        is_active=True,
    )
    db.add(customer)
    db.flush()
    _, card = get_or_create_card(db, customer.id)
    db.commit()
    db.refresh(customer)
    db.refresh(card)

    welcome_email = safe_send_pharmacy_email(
        customer.email,
        "Tu Tarjeta Mayu Magistral está lista",
        build_pharmacy_card_email_message(customer, card),
    )

    access_token = create_access_token(
        {"sub": f"pharmacy_customer:{customer.id}", "type": "pharmacy_customer"}
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "customer": customer_to_dict(customer),
        "card": card_to_dict(customer, card),
        "welcome_email": welcome_email,
    }


@router.post("/login")
def login_pharmacy_customer(
    payload: PharmacyCustomerLogin,
    db: Session = Depends(get_db),
):
    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.email == payload.email.strip().lower())
        .first()
    )
    if not customer or not verify_password(payload.password, customer.password):
        raise HTTPException(status_code=401, detail="Credenciales Farmacia inválidas")
    if not customer.is_active:
        raise HTTPException(status_code=403, detail="Cliente Farmacia desactivado")

    _, card = get_or_create_card(db, customer.id)
    db.commit()
    db.refresh(card)
    access_token = create_access_token(
        {"sub": f"pharmacy_customer:{customer.id}", "type": "pharmacy_customer"}
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "customer": customer_to_dict(customer),
        "card": card_to_dict(customer, card),
    }


@router.post("/push-token")
def save_pharmacy_push_token(
    payload: PharmacyPushTokenRequest,
    db: Session = Depends(get_db),
    current_customer: models.PharmacyCustomer = Depends(get_current_pharmacy_customer),
):
    if not payload.token or not payload.token.strip():
        raise HTTPException(status_code=400, detail="Token push requerido")

    clean_token = payload.token.strip()

    old_tokens = (
        db.query(models.PharmacyPushNotificationToken)
        .filter(
            models.PharmacyPushNotificationToken.pharmacy_customer_id
            == current_customer.id,
            models.PharmacyPushNotificationToken.token != clean_token,
        )
        .all()
    )
    for item in old_tokens:
        item.is_active = False
        item.updated_at = datetime.utcnow()

    existing = (
        db.query(models.PharmacyPushNotificationToken)
        .filter(models.PharmacyPushNotificationToken.token == clean_token)
        .first()
    )

    created = False
    if existing:
        existing.pharmacy_customer_id = current_customer.id
        existing.platform = payload.platform
        existing.is_active = True
        existing.updated_at = datetime.utcnow()
        push_token = existing
    else:
        created = True
        push_token = models.PharmacyPushNotificationToken(
            pharmacy_customer_id=current_customer.id,
            token=clean_token,
            platform=payload.platform,
            is_active=True,
        )
        db.add(push_token)
        db.flush()

    welcome_push = safe_send_push_to_pharmacy_customer(
        db=db,
        pharmacy_customer_id=current_customer.id,
        title="✅ Tarjeta Farmacia Mayu activada",
        message=f"Hola {current_customer.name}, tu tarjeta de puntos ya está lista.",
    )

    db.commit()
    db.refresh(push_token)

    return {
        "message": "Token push farmacia guardado",
        "token_id": push_token.id,
        "created": created,
        "welcome_push": welcome_push,
    }


@router.post("/recover-card")
def recover_pharmacy_card(
    payload: PharmacyRecoverCardRequest,
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    phone = normalize_phone(payload.phone)
    if not email or not phone:
        raise HTTPException(status_code=400, detail="Correo y teléfono son obligatorios")

    customer = (
        db.query(models.PharmacyCustomer)
        .filter(
            models.PharmacyCustomer.email == email,
            models.PharmacyCustomer.is_active == True,
        )
        .first()
    )
    if not customer or normalize_phone(customer.phone) != phone:
        raise HTTPException(
            status_code=404,
            detail="No encontramos una Tarjeta Mayu Magistral con ese correo y teléfono",
        )

    customer, card = get_or_create_card(db, customer.id)
    db.commit()
    db.refresh(card)

    email_result = safe_send_pharmacy_email(
        customer.email,
        "Recupera tu Tarjeta Mayu Magistral",
        build_pharmacy_card_email_message(customer, card),
    )

    return {
        "message": "Tarjeta encontrada. Te enviamos los enlaces de descarga si el correo está configurado.",
        "email_sent": email_result.get("sent", False),
        "email": email_result,
        "card": card_to_dict(customer, card),
    }


@router.get("/me")
def get_my_card(
    db: Session = Depends(get_db),
    current_customer: models.PharmacyCustomer = Depends(get_current_pharmacy_customer),
):
    customer, card = get_or_create_card(db, current_customer.id)
    db.commit()
    db.refresh(card)
    return card_to_dict(customer, card)


@router.get("/resolve/{identifier}")
def resolve_card(
    identifier: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(
            (models.PharmacyLoyaltyCard.qr_token == identifier)
            | (models.PharmacyLoyaltyCard.card_code == identifier)
        )
        .first()
    )
    if not card or not card.active or not card.pharmacy_customer_id:
        raise HTTPException(status_code=404, detail="Tarjeta Farmacia no válida")

    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == card.pharmacy_customer_id)
        .first()
    )
    return {"mode": "credit", "card": card_to_dict(customer, card)}


@router.get("/admin/customers")
def list_pharmacy_customers(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    customers = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.is_active == True)
        .order_by(models.PharmacyCustomer.created_at.desc())
        .all()
    )
    card_by_customer_id = {
        card.pharmacy_customer_id: card
        for card in db.query(models.PharmacyLoyaltyCard)
        .filter(models.PharmacyLoyaltyCard.pharmacy_customer_id.isnot(None))
        .all()
    }
    return {
        "total": len(customers),
        "customers": [
            pharmacy_customer_admin_dict(
                customer,
                card_by_customer_id.get(customer.id),
            )
            for customer in customers
        ],
    }


@router.post("/admin/test-push/{identifier}")
def test_push_by_pharmacy(
    identifier: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(
            (models.PharmacyLoyaltyCard.qr_token == identifier)
            | (models.PharmacyLoyaltyCard.card_code == identifier)
        )
        .first()
    )
    if not card or not card.active or not card.pharmacy_customer_id:
        raise HTTPException(status_code=404, detail="Tarjeta Mayu Magistral no válida")

    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == card.pharmacy_customer_id)
        .first()
    )
    firebase = safe_send_push_to_pharmacy_customer(
        db=db,
        pharmacy_customer_id=card.pharmacy_customer_id,
        title="🔔 Prueba Mayu Magistral",
        message="Esta es una prueba de notificaciones de tu Tarjeta Mayu Magistral.",
    )
    apple = safe_send_apple_wallet_update_pushes(db, card)
    google_update = safe_update_google_wallet_object(customer, card)
    google_notification = safe_notify_google_wallet_points(
        customer,
        card,
        "Prueba Mayu Magistral",
        "Las notificaciones y el saldo de tu Tarjeta Mayu Magistral están sincronizados.",
    )
    db.commit()
    return {
        "firebase": firebase,
        "apple_wallet": apple,
        "google_wallet": google_update,
        "google_notification": google_notification,
        "proximity_locations": {
            "configured": len(configured_pharmacy_wallet_locations()),
            "apple_max_distance_m": 100,
        },
    }


@router.post("/admin/redeem-all/{identifier}")
def redeem_all_points_by_pharmacy(
    identifier: str,
    payload: PharmacyPointsRedemption,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(
            (models.PharmacyLoyaltyCard.qr_token == identifier)
            | (models.PharmacyLoyaltyCard.card_code == identifier)
        )
        .first()
    )
    if not card or not card.active or not card.pharmacy_customer_id:
        raise HTTPException(status_code=404, detail="Tarjeta Mayu Magistral no válida")

    points_used = int(card.points_balance or 0)
    if points_used <= 0:
        raise HTTPException(status_code=409, detail="La tarjeta ya tiene saldo de 0 puntos")

    transaction = models.PharmacyPointsTransaction(
        card_id=card.id,
        purchase_amount_cents=0,
        points_delta=-points_used,
        remainder_after_cents=card.accumulated_cents or 0,
        source="pharmacy_redemption",
        reference=f"redemption:{card.id}:{int(datetime.utcnow().timestamp())}",
        note=(payload.note or "Puntos utilizados en Farmacia Mayu").strip(),
        created_by=current_user.id,
    )
    card.points_balance = 0
    card.updated_at = datetime.utcnow()
    db.add(transaction)
    db.commit()
    db.refresh(card)

    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == card.pharmacy_customer_id)
        .first()
    )
    push_result = safe_send_push_to_pharmacy_customer(
        db=db,
        pharmacy_customer_id=card.pharmacy_customer_id,
        title="🎁 Puntos Mayu Magistral utilizados",
        message=(
            f"Utilizaste {points_used} punto(s) en Farmacia Mayu. "
            "Tu saldo actual es 0 puntos."
        ),
    )
    wallet_sync = {
        "apple": safe_send_apple_wallet_update_pushes(db, card),
        "google": safe_update_google_wallet_object(customer, card) if customer else None,
    }
    wallet_sync["google_notification"] = (
        safe_notify_google_wallet_points(
            customer,
            card,
            "Puntos Mayu Magistral utilizados",
            f"Utilizaste {points_used} punto(s). Tu saldo actual es 0 puntos.",
        )
        if customer
        else None
    )
    db.commit()
    return {
        "redeemed": True,
        "points_used": points_used,
        "points_balance": 0,
        "transaction_id": transaction.id,
        "card": card_to_dict(customer, card),
        "push": push_result,
        "wallet_sync": wallet_sync,
    }


def safe_get_apple_wallet_registration_count(db: Session, card_id: int):
    try:
        return (
            db.query(models.PharmacyAppleWalletRegistration)
            .filter(models.PharmacyAppleWalletRegistration.card_id == card_id)
            .count()
        )
    except SQLAlchemyError as exc:
        return {"error": str(exc)}


def get_wallet_certs_dir():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    certs_dir = os.path.join(base_dir, "certs")
    if not os.path.exists(certs_dir):
        certs_dir = os.path.join(os.getcwd(), "certs")
    return certs_dir


def build_apple_wallet_push_cert_files(temp_dir: str):
    certs_dir = get_wallet_certs_dir()
    p12_path = os.path.join(certs_dir, "mayu_wallet.p12")
    password = os.getenv("APPLE_WALLET_P12_PASSWORD") or os.getenv(
        "APPLE_WALLET_CERT_PASSWORD"
    )
    if not os.path.exists(p12_path):
        raise Exception("No existe certs/mayu_wallet.p12")
    if not password:
        raise Exception("Falta APPLE_WALLET_P12_PASSWORD")

    with open(p12_path, "rb") as f:
        private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
            f.read(),
            password.encode(),
        )
    if not private_key or not certificate:
        raise Exception("Certificado Apple Wallet inválido")

    cert_path = os.path.join(temp_dir, "apple_wallet_push_cert.pem")
    key_path = os.path.join(temp_dir, "apple_wallet_push_key.pem")

    with open(cert_path, "wb") as f:
        f.write(certificate.public_bytes(Encoding.PEM))
        for item in additional_certificates or []:
            f.write(item.public_bytes(Encoding.PEM))

    with open(key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                Encoding.PEM,
                PrivateFormat.PKCS8,
                NoEncryption(),
            )
        )

    return cert_path, key_path


def safe_send_apple_wallet_update_pushes(db: Session, card):
    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID")
    if not pass_type_id:
        return {"sent": 0, "errors": [{"detail": "Falta APPLE_PASS_TYPE_ID"}]}

    registrations = (
        db.query(models.PharmacyAppleWalletRegistration)
        .filter(models.PharmacyAppleWalletRegistration.card_id == card.id)
        .all()
    )
    if not registrations:
        return {"sent": 0, "errors": [], "detail": "Sin dispositivos Apple Wallet registrados"}

    try:
        import httpx

        temp_dir = tempfile.mkdtemp(prefix=f"mayu_magistral_apns_{card.id}_")
        cert_path, key_path = build_apple_wallet_push_cert_files(temp_dir)
        apns_host = os.getenv("APPLE_APNS_HOST", "https://api.push.apple.com")
        sent = 0
        errors = []
        successes = []
        with httpx.Client(http2=True, cert=(cert_path, key_path), timeout=20) as client:
            for registration in registrations:
                try:
                    response = client.post(
                        f"{apns_host}/3/device/{registration.push_token}",
                        headers={
                            "apns-topic": pass_type_id,
                            "apns-push-type": "background",
                            "apns-priority": "10",
                        },
                        json={},
                    )
                    if response.status_code in {200, 201}:
                        sent += 1
                        successes.append(
                            {
                                "registration_id": registration.id,
                                "status_code": response.status_code,
                                "apns_id": response.headers.get("apns-id"),
                            }
                        )
                    else:
                        errors.append(
                            {
                                "registration_id": registration.id,
                                "status_code": response.status_code,
                                "detail": response.text[:300],
                            }
                        )
                except Exception as exc:
                    errors.append(
                        {
                            "registration_id": registration.id,
                            "detail": str(exc),
                        }
                    )
        print(
            json.dumps(
                {
                    "event": "mayu_magistral_apple_wallet_push",
                    "card_id": card.id,
                    "points_balance": card.points_balance,
                    "sent": sent,
                    "registered": len(registrations),
                    "successes": successes,
                    "errors": errors,
                },
                default=str,
            ),
            flush=True,
        )
        return {
            "sent": sent,
            "errors": errors,
            "registered": len(registrations),
            "successes": successes,
        }
    except Exception as exc:
        return {"sent": 0, "errors": [{"detail": str(exc)}], "registered": len(registrations)}


@router.post("/admin/credit/{identifier}")
def credit_by_pharmacy(
    identifier: str,
    payload: PharmacyPurchaseCredit,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)
    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(
            (models.PharmacyLoyaltyCard.qr_token == identifier)
            | (models.PharmacyLoyaltyCard.card_code == identifier)
        )
        .first()
    )
    if not card or not card.active or not card.pharmacy_customer_id:
        raise HTTPException(status_code=404, detail="Tarjeta Farmacia no válida")

    reference = payload.reference.strip()
    if not reference:
        raise HTTPException(status_code=400, detail="La factura es obligatoria")

    card, transaction, created = credit_purchase(
        db,
        card.pharmacy_customer_id,
        payload.amount,
        "pharmacy_admin",
        reference=reference,
        created_by=current_user.id,
        note=payload.note,
    )
    db.commit()
    db.refresh(card)
    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == card.pharmacy_customer_id)
        .first()
    )
    push_result = None
    wallet_sync = {
        "apple": {"registered_devices": safe_get_apple_wallet_registration_count(db, card.id)},
        "google": None,
    }
    if created and transaction.points_delta > 0:
        push_result = safe_send_push_to_pharmacy_customer(
            db=db,
            pharmacy_customer_id=card.pharmacy_customer_id,
            title="⭐ Puntos Farmacia Mayu acreditados",
            message=(
                f"Sumaste {transaction.points_delta} punto(s). "
                f"Saldo actual: {card.points_balance} punto(s)."
            ),
        )
        wallet_sync["apple"] = safe_send_apple_wallet_update_pushes(db, card)
        wallet_sync["google"] = safe_update_google_wallet_object(customer, card)
        wallet_sync["google_notification"] = safe_notify_google_wallet_points(
            customer,
            card,
            "Puntos Mayu Magistral acreditados",
            f"Sumaste {transaction.points_delta} punto(s). Saldo actual: {card.points_balance} punto(s).",
        )
        db.commit()

    return {
        "created": created,
        "points_earned": transaction.points_delta,
        "card": card_to_dict(customer, card),
        "push": push_result,
        "wallet_sync": wallet_sync,
    }


def process_pharmacy_birthday_notifications(db: Session):
    today = datetime.utcnow().date()
    customers = (
        db.query(models.PharmacyCustomer)
        .filter(
            models.PharmacyCustomer.is_active == True,
            models.PharmacyCustomer.birth_date.isnot(None),
        )
        .all()
    )

    total_candidates = 0
    total_sent = 0
    total_errors = 0
    skipped_existing = 0

    for customer in customers:
        if (
            customer.birth_date.month != today.month
            or customer.birth_date.day != today.day
        ):
            continue

        total_candidates += 1

        push_token = (
            db.query(models.PharmacyPushNotificationToken)
            .filter(
                models.PharmacyPushNotificationToken.pharmacy_customer_id
                == customer.id,
                models.PharmacyPushNotificationToken.is_active == True,
            )
            .order_by(models.PharmacyPushNotificationToken.updated_at.desc())
            .first()
        )
        if not push_token:
            total_errors += 1
            continue

        if (
            push_token.birthday_last_sent_at
            and push_token.birthday_last_sent_at.date() == today
        ):
            skipped_existing += 1
            continue

        result = safe_send_push_to_pharmacy_customer(
            db=db,
            pharmacy_customer_id=customer.id,
            title="🎉 Feliz cumpleaños",
            message=(
                f"Hola {customer.name}, Farmacia Mayu te desea un día lleno "
                "de salud y bienestar."
            ),
        )
        if result.get("sent"):
            push_token.birthday_last_sent_at = datetime.utcnow()
            push_token.updated_at = datetime.utcnow()
            total_sent += 1
        else:
            total_errors += 1

    return {
        "birthday_customers": total_candidates,
        "push_success": total_sent,
        "errors": total_errors,
        "skipped_existing": skipped_existing,
    }


def run_pharmacy_birthday_cron_job(
    secret: str,
    db: Session,
):
    cron_secret = os.getenv("MARKETING_CRON_SECRET")
    if not cron_secret:
        raise HTTPException(
            status_code=500,
            detail="Falta MARKETING_CRON_SECRET en Render",
        )
    if secret != cron_secret:
        raise HTTPException(status_code=401, detail="No autorizado")

    result = process_pharmacy_birthday_notifications(db)
    db.commit()
    return {
        "message": "Cumpleaños Farmacia procesados correctamente",
        "result": result,
    }


@router.post("/birthday/cron/run")
def run_pharmacy_birthday_cron(
    secret: str,
    db: Session = Depends(get_db),
):
    return run_pharmacy_birthday_cron_job(secret=secret, db=db)


@router.get("/birthday/cron/run")
def run_pharmacy_birthday_cron_get(
    secret: str,
    db: Session = Depends(get_db),
):
    return run_pharmacy_birthday_cron_job(secret=secret, db=db)


def get_valid_pharmacy_card_by_token(db: Session, qr_token: str):
    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(models.PharmacyLoyaltyCard.qr_token == qr_token)
        .first()
    )
    if not card or not card.active or not card.pharmacy_customer_id:
        raise HTTPException(status_code=404, detail="Tarjeta Mayu Magistral no válida")

    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == card.pharmacy_customer_id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Socio farmacia no encontrado")

    return customer, card


def pharmacy_apple_serial(card) -> str:
    return f"mayu-magistral-{card.id}"


def pharmacy_wallet_auth_token(card) -> str:
    return f"{PHARMACY_WALLET_AUTH_PREFIX}-{card.qr_token}"


def pharmacy_apple_last_updated(card) -> str:
    updated_at = card.updated_at or card.created_at or datetime.utcnow()
    return updated_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def pharmacy_apple_last_modified(card) -> str:
    updated_at = card.updated_at or card.created_at or datetime.utcnow()
    return format_datetime(updated_at.replace(tzinfo=timezone.utc), usegmt=True)


def extract_wallet_auth_token(request: FastAPIRequest) -> str:
    authorization = request.headers.get("authorization") or ""
    prefix = "ApplePass "
    if authorization.startswith(prefix):
        return authorization.replace(prefix, "", 1).strip()
    return ""


def get_pharmacy_card_by_apple_serial(db: Session, serial_number: str):
    prefix = "mayu-magistral-"
    if not serial_number.startswith(prefix):
        raise HTTPException(status_code=404, detail="Pase Mayu Magistral no válido")
    try:
        card_id = int(serial_number.replace(prefix, "", 1))
    except ValueError:
        raise HTTPException(status_code=404, detail="Pase Mayu Magistral no válido")

    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(models.PharmacyLoyaltyCard.id == card_id)
        .first()
    )
    if not card or not card.active or not card.pharmacy_customer_id:
        raise HTTPException(status_code=404, detail="Pase Mayu Magistral no válido")
    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == card.pharmacy_customer_id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Socio Mayu Magistral no válido")
    return customer, card


def verify_pharmacy_wallet_request(request: FastAPIRequest, card):
    if extract_wallet_auth_token(request) != pharmacy_wallet_auth_token(card):
        raise HTTPException(status_code=401, detail="No autorizado")


def pharmacy_card_asset_bytes():
    return base64.b64decode(TARJETA_SOCIOSFARMACIA_JPG_BASE64)


@router.get("/assets/tarjeta_sociosfarmacia.jpg")
def get_pharmacy_wallet_asset():
    return Response(
        content=pharmacy_card_asset_bytes(),
        media_type="image/jpeg",
    )


def copy_or_create_pharmacy_wallet_images(pass_dir: str):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(base_dir, "assets", "logo_mayu.png")
    wallet_image_path = os.path.join(base_dir, "assets", "tarjeta_sociosfarmacia.jpg")
    bg_color = (0, 96, 84)

    for filename, size in [
        ("icon.png", (29, 29)),
        ("icon@2x.png", (58, 58)),
    ]:
        target = os.path.join(pass_dir, filename)
        if os.path.exists(logo_path):
            fit_image_to_canvas(logo_path, target, size, bg_color)
        else:
            create_wallet_icon(target)

    for filename, size in [
        ("logo.png", (70, 26)),
        ("logo@2x.png", (140, 52)),
    ]:
        target = os.path.join(pass_dir, filename)
        if os.path.exists(logo_path):
            fit_image_to_canvas(logo_path, target, size, bg_color)
        else:
            create_wallet_icon(target)

    if not os.path.exists(wallet_image_path):
        wallet_image_path = os.path.join(pass_dir, "tarjeta_sociosfarmacia.jpg")
        with open(wallet_image_path, "wb") as f:
            f.write(pharmacy_card_asset_bytes())

    if os.path.exists(wallet_image_path):
        cover_image_to_canvas(
            wallet_image_path,
            os.path.join(pass_dir, "strip.png"),
            (375, 123),
            bg_color,
        )
        cover_image_to_canvas(
            wallet_image_path,
            os.path.join(pass_dir, "strip@2x.png"),
            (750, 246),
            bg_color,
        )


def build_pharmacy_apple_wallet_file(customer, card):
    pass_type_id = os.getenv("APPLE_PASS_TYPE_ID")
    team_id = os.getenv("APPLE_TEAM_ID")
    organization_name = os.getenv("APPLE_ORGANIZATION_NAME", "Mayu Magistral")

    if not pass_type_id:
        raise HTTPException(status_code=500, detail="Falta APPLE_PASS_TYPE_ID")
    if not team_id:
        raise HTTPException(status_code=500, detail="Falta APPLE_TEAM_ID")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    certs_dir = os.path.join(base_dir, "certs")
    if not os.path.exists(certs_dir):
        certs_dir = os.path.join(os.getcwd(), "certs")

    temp_dir = tempfile.mkdtemp(prefix=f"mayu_magistral_pkpass_{card.id}_")
    pass_dir = os.path.join(temp_dir, "pass")
    os.makedirs(pass_dir, exist_ok=True)

    try:
        public_url = f"{BASE_PUBLIC_URL}/pharmacy-loyalty/qr/{card.qr_token}"
        pass_json = {
            "formatVersion": 1,
            "passTypeIdentifier": pass_type_id,
            "serialNumber": pharmacy_apple_serial(card),
            "teamIdentifier": team_id,
            "organizationName": organization_name,
            "description": "Tarjeta Mayu Magistral",
            "logoText": "MAYU MAGISTRAL",
            "webServiceURL": f"{BASE_PUBLIC_URL}/pharmacy-loyalty/wallet/apple",
            "authenticationToken": pharmacy_wallet_auth_token(card),
            "foregroundColor": "rgb(255,255,255)",
            "backgroundColor": "rgb(0,96,84)",
            "labelColor": "rgb(210,245,238)",
            "suppressStripShine": True,
            "sharingProhibited": False,
            "storeCard": {
                "primaryFields": [
                    {
                        "key": "points",
                        "label": "BALANCE EN PUNTOS",
                        "value": str(card.points_balance),
                        "changeMessage": "Tu saldo Mayu Magistral cambió a %@ puntos.",
                    }
                ],
                "secondaryFields": [
                    {
                        "key": "name",
                        "label": "TARJETA DE",
                        "value": customer.name,
                    },
                    {
                        "key": "benefit",
                        "label": "RECLAMA",
                        "value": "Beneficios Mayu Magistral",
                    },
                ],
                "auxiliaryFields": [
                    {
                        "key": "code",
                        "label": "CÓDIGO",
                        "value": card.card_code,
                    }
                ],
                "backFields": [
                    {"key": "email", "label": "Correo", "value": customer.email},
                    {"key": "phone", "label": "Teléfono", "value": customer.phone},
                    {"key": "city", "label": "Ciudad", "value": customer.city or "-"},
                    {"key": "web", "label": "Tarjeta web", "value": public_url},
                ],
            },
            "barcode": {
                "format": "PKBarcodeFormatQR",
                "message": public_url,
                "messageEncoding": "iso-8859-1",
                "altText": card.card_code,
            },
        }
        wallet_locations = configured_pharmacy_wallet_locations()
        if wallet_locations:
            pass_json["locations"] = wallet_locations
            pass_json["maxDistance"] = 100

        with open(os.path.join(pass_dir, "pass.json"), "w", encoding="utf-8") as f:
            json.dump(pass_json, f, ensure_ascii=False, separators=(",", ":"))

        copy_or_create_pharmacy_wallet_images(pass_dir)
        build_manifest(pass_dir)
        sign_manifest(pass_dir, certs_dir)

        output_path = os.path.join(temp_dir, f"tarjeta_mayu_magistral_{card.id}.pkpass")
        zip_pkpass(pass_dir, output_path)
        return output_path

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error generando Apple Wallet Farmacia: {str(exc)}",
        )


@router.get("/wallet/apple/{qr_token}")
def pharmacy_apple_wallet(qr_token: str, db: Session = Depends(get_db)):
    customer, card = get_valid_pharmacy_card_by_token(db, qr_token)
    output_path = build_pharmacy_apple_wallet_file(customer, card)
    return FileResponse(
        path=output_path,
        media_type="application/vnd.apple.pkpass",
        filename=f"tarjeta_mayu_magistral_{card.id}.pkpass",
    )


@router.post(
    "/wallet/apple/v1/devices/{device_library_identifier}/registrations/{pass_type_identifier}/{serial_number}"
)
def register_apple_wallet_device(
    device_library_identifier: str,
    pass_type_identifier: str,
    serial_number: str,
    payload: AppleWalletRegistrationRequest,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
):
    customer, card = get_pharmacy_card_by_apple_serial(db, serial_number)
    verify_pharmacy_wallet_request(request, card)

    if not payload.pushToken or not payload.pushToken.strip():
        raise HTTPException(status_code=400, detail="pushToken requerido")

    existing = (
        db.query(models.PharmacyAppleWalletRegistration)
        .filter(
            models.PharmacyAppleWalletRegistration.card_id == card.id,
            models.PharmacyAppleWalletRegistration.device_library_identifier
            == device_library_identifier,
            models.PharmacyAppleWalletRegistration.serial_number == serial_number,
        )
        .first()
    )

    created = False
    if existing:
        existing.pass_type_identifier = pass_type_identifier
        existing.push_token = payload.pushToken.strip()
        existing.authentication_token = pharmacy_wallet_auth_token(card)
        existing.updated_at = datetime.utcnow()
    else:
        created = True
        existing = models.PharmacyAppleWalletRegistration(
            card_id=card.id,
            device_library_identifier=device_library_identifier,
            pass_type_identifier=pass_type_identifier,
            serial_number=serial_number,
            push_token=payload.pushToken.strip(),
            authentication_token=pharmacy_wallet_auth_token(card),
        )
        db.add(existing)

    db.commit()
    return Response(status_code=201 if created else 200)


@router.delete(
    "/wallet/apple/v1/devices/{device_library_identifier}/registrations/{pass_type_identifier}/{serial_number}"
)
def unregister_apple_wallet_device(
    device_library_identifier: str,
    pass_type_identifier: str,
    serial_number: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
):
    customer, card = get_pharmacy_card_by_apple_serial(db, serial_number)
    verify_pharmacy_wallet_request(request, card)
    (
        db.query(models.PharmacyAppleWalletRegistration)
        .filter(
            models.PharmacyAppleWalletRegistration.card_id == card.id,
            models.PharmacyAppleWalletRegistration.device_library_identifier
            == device_library_identifier,
            models.PharmacyAppleWalletRegistration.pass_type_identifier
            == pass_type_identifier,
            models.PharmacyAppleWalletRegistration.serial_number == serial_number,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return Response(status_code=200)


@router.get(
    "/wallet/apple/v1/devices/{device_library_identifier}/registrations/{pass_type_identifier}"
)
def get_apple_wallet_updated_serials(
    device_library_identifier: str,
    pass_type_identifier: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
    passesUpdatedSince: Optional[str] = None,
):
    registrations = (
        db.query(models.PharmacyAppleWalletRegistration)
        .filter(
            models.PharmacyAppleWalletRegistration.device_library_identifier
            == device_library_identifier,
            models.PharmacyAppleWalletRegistration.pass_type_identifier
            == pass_type_identifier,
        )
        .all()
    )
    if registrations:
        token = extract_wallet_auth_token(request)
        if token not in {item.authentication_token for item in registrations}:
            print(
                json.dumps(
                    {
                        "event": "mayu_magistral_apple_wallet_update_auth_mismatch",
                        "device": device_library_identifier,
                        "pass_type": pass_type_identifier,
                        "registrations": len(registrations),
                        "auth_present": bool(token),
                    },
                    default=str,
                ),
                flush=True,
            )

    updated_items = []
    for item in registrations:
        if not item.card:
            continue
        last_updated = pharmacy_apple_last_updated(item.card)
        if passesUpdatedSince and last_updated <= passesUpdatedSince:
            continue
        updated_items.append((item, last_updated))

    if not updated_items:
        print(
            json.dumps(
                {
                    "event": "mayu_magistral_apple_wallet_no_updates",
                    "device": device_library_identifier,
                    "pass_type": pass_type_identifier,
                    "passesUpdatedSince": passesUpdatedSince,
                    "registrations": len(registrations),
                },
                default=str,
            ),
            flush=True,
        )
        return Response(status_code=204)

    response_payload = {
        "lastUpdated": max(last_updated for _, last_updated in updated_items),
        "serialNumbers": [item.serial_number for item, _ in updated_items],
    }
    print(
        json.dumps(
            {
                "event": "mayu_magistral_apple_wallet_updates",
                "device": device_library_identifier,
                "pass_type": pass_type_identifier,
                "passesUpdatedSince": passesUpdatedSince,
                "response": response_payload,
            },
            default=str,
        ),
        flush=True,
    )
    return {
        **response_payload,
    }


@router.get("/wallet/apple/v1/passes/{pass_type_identifier}/{serial_number}")
def get_updated_apple_wallet_pass(
    pass_type_identifier: str,
    serial_number: str,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
):
    customer, card = get_pharmacy_card_by_apple_serial(db, serial_number)
    verify_pharmacy_wallet_request(request, card)
    print(
        json.dumps(
            {
                "event": "mayu_magistral_apple_wallet_pass_requested",
                "serial_number": serial_number,
                "pass_type": pass_type_identifier,
                "card_id": card.id,
                "points_balance": card.points_balance,
                "updated_at": card.updated_at,
            },
            default=str,
        ),
        flush=True,
    )
    output_path = build_pharmacy_apple_wallet_file(customer, card)
    return FileResponse(
        path=output_path,
        media_type="application/vnd.apple.pkpass",
        filename=f"tarjeta_mayu_magistral_{card.id}.pkpass",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Last-Modified": pharmacy_apple_last_modified(card),
        },
    )


@router.post("/wallet/apple/v1/log")
def apple_wallet_log(payload: dict):
    return {"message": "Apple Wallet log recibido", "payload": payload}


@router.post("/wallet/apple/v1/v1/log")
def apple_wallet_legacy_double_v1_log(payload: dict):
    return {"message": "Apple Wallet log recibido", "payload": payload}


def build_pharmacy_google_wallet_save_url(customer, card):
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    class_suffix = os.getenv(
        "GOOGLE_WALLET_PHARMACY_CLASS_SUFFIX",
        "mayu_magistral_pharmacy_generic_v2",
    )

    if not issuer_id:
        raise HTTPException(status_code=500, detail="Falta GOOGLE_WALLET_ISSUER_ID en Render")

    service_account = get_google_wallet_service_account()
    client_email = service_account.get("client_email")
    private_key = service_account.get("private_key")

    if not client_email or not private_key:
        raise HTTPException(status_code=500, detail="JSON de Google Wallet incompleto")

    private_key = clean_google_private_key(private_key)

    class_id = f"{issuer_id}.{class_suffix}"
    ensure_pharmacy_google_wallet_class(service_account, class_id)
    generic_object = build_pharmacy_google_wallet_object(customer, card, issuer_id, class_id)

    claims = {
        "iss": client_email,
        "aud": "google",
        "typ": "savetowallet",
        "payload": {"genericObjects": [generic_object]},
    }

    token = pyjwt.encode(claims, private_key, algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{token}"


def ensure_pharmacy_google_wallet_class(service_account_info: dict, class_id: str):
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
    )
    credentials.refresh(GoogleAuthRequest())
    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }
    url = f"https://walletobjects.googleapis.com/walletobjects/v1/genericClass/{class_id}"
    existing = requests.get(url, headers=headers, timeout=20)
    if existing.status_code == 200:
        return
    if existing.status_code != 404:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo verificar clase Google Wallet: {existing.text[:500]}",
        )

    class_body = {
        "id": class_id,
        "issuerName": "Mayu Magistral",
        "reviewStatus": "UNDER_REVIEW",
        "hexBackgroundColor": "#006054",
        "localizedIssuerName": {
            "defaultValue": {"language": "es", "value": "Mayu Magistral"}
        },
        "homepageUri": {
            "uri": BASE_PUBLIC_URL,
            "description": "Mayu Magistral",
        },
    }
    created = requests.post(
        "https://walletobjects.googleapis.com/walletobjects/v1/genericClass",
        headers=headers,
        json=class_body,
        timeout=20,
    )
    if created.status_code not in {200, 201}:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo crear clase Google Wallet: {created.text[:500]}",
        )


def pharmacy_google_object_id(card, issuer_id: Optional[str] = None) -> str:
    issuer = issuer_id or os.getenv("GOOGLE_WALLET_ISSUER_ID")
    if not issuer:
        raise HTTPException(status_code=500, detail="Falta GOOGLE_WALLET_ISSUER_ID en Render")
    object_suffix = f"mayu_magistral_{card.card_code}_{card.id}".replace("-", "_").lower()
    return f"{issuer}.{object_suffix}"


def build_pharmacy_google_wallet_object(customer, card, issuer_id: str, class_id: str):
    object_id = pharmacy_google_object_id(card, issuer_id)
    public_url = f"{BASE_PUBLIC_URL}/pharmacy-loyalty/qr/{card.qr_token}"
    qr_image_url = f"{BASE_PUBLIC_URL}/pharmacy-loyalty/qr/{card.qr_token}/image"
    logo_url = f"{BASE_PUBLIC_URL}/member-cards/assets/logo_mayu.png"
    pharmacy_card_asset_url = (
        f"{BASE_PUBLIC_URL}/pharmacy-loyalty/assets/tarjeta_sociosfarmacia.jpg"
    )

    generic_object = {
        "id": object_id,
        "classId": class_id,
        "state": "ACTIVE",
        "hexBackgroundColor": "#006054",
        "logo": {
            "sourceUri": {"uri": logo_url},
            "contentDescription": {"defaultValue": {"language": "es", "value": "Mayu Magistral"}},
        },
        "heroImage": {
            "sourceUri": {"uri": pharmacy_card_asset_url},
            "contentDescription": {"defaultValue": {"language": "es", "value": "Tarjeta Mayu Magistral"}},
        },
        "imageModulesData": [
            {
                "id": "pharmacy_card_design",
                "mainImage": {
                    "sourceUri": {"uri": pharmacy_card_asset_url},
                    "contentDescription": {
                        "defaultValue": {
                            "language": "es",
                            "value": "Diseño Tarjeta Mayu Magistral",
                        }
                    },
                },
            }
        ],
        "cardTitle": {"defaultValue": {"language": "es", "value": "Tarjeta Mayu Magistral"}},
        "header": {"defaultValue": {"language": "es", "value": customer.name}},
        "subheader": {"defaultValue": {"language": "es", "value": f"{card.points_balance} puntos · {card.card_code}"}},
        "barcode": {
            "type": "QR_CODE",
            "value": public_url,
            "alternateText": card.card_code,
        },
        "textModulesData": [
            {"id": "points", "header": "Balance en puntos", "body": str(card.points_balance)},
            {"id": "code", "header": "Código", "body": card.card_code},
            {"id": "rule", "header": "Regla", "body": "1 punto por cada $10 acumulados"},
        ],
        "linksModuleData": {
            "uris": [
                {"id": "web", "uri": public_url, "description": "Ver tarjeta"},
            ]
        },
    }
    locations = configured_pharmacy_wallet_locations()
    if locations:
        generic_object["merchantLocations"] = [
            {"latitude": item["latitude"], "longitude": item["longitude"]}
            for item in locations
        ]
    return generic_object


def safe_notify_google_wallet_points(customer, card, header: str, body: str):
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    if not issuer_id:
        return {"sent": False, "detail": "Falta GOOGLE_WALLET_ISSUER_ID"}
    try:
        service_account_info = get_google_wallet_service_account()
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
        )
        credentials.refresh(GoogleAuthRequest())
        object_id = pharmacy_google_object_id(card, issuer_id)
        response = requests.post(
            f"https://walletobjects.googleapis.com/walletobjects/v1/genericObject/{object_id}/addMessage",
            headers={
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json",
            },
            json={
                "message": {
                    "header": header,
                    "body": body,
                    "id": f"points_{card.id}_{int(datetime.utcnow().timestamp())}",
                    "messageType": "TEXT_AND_NOTIFY",
                }
            },
            timeout=20,
        )
        if response.status_code >= 300:
            return {"sent": False, "status_code": response.status_code, "detail": response.text[:500]}
        return {"sent": True, "object_id": object_id}
    except Exception as exc:
        return {"sent": False, "detail": str(exc)}


def safe_update_google_wallet_object(customer, card):
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    class_suffix = os.getenv(
        "GOOGLE_WALLET_PHARMACY_CLASS_SUFFIX",
        "mayu_magistral_pharmacy_generic_v2",
    )
    if not issuer_id:
        return {"updated": False, "detail": "Falta GOOGLE_WALLET_ISSUER_ID"}

    try:
        service_account_info = get_google_wallet_service_account()
        class_id = f"{issuer_id}.{class_suffix}"
        ensure_pharmacy_google_wallet_class(service_account_info, class_id)
        object_body = build_pharmacy_google_wallet_object(
            customer,
            card,
            issuer_id,
            class_id,
        )
        object_id = object_body["id"]
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
        )
        credentials.refresh(GoogleAuthRequest())
        response = requests.patch(
            f"https://walletobjects.googleapis.com/walletobjects/v1/genericObject/{object_id}",
            headers={
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json",
            },
            json=object_body,
            timeout=20,
        )
        if response.status_code == 404:
            return {
                "updated": False,
                "detail": "El socio aún no ha guardado la tarjeta Google Wallet",
            }
        if response.status_code >= 300:
            return {
                "updated": False,
                "status_code": response.status_code,
                "detail": response.text[:500],
            }
        return {"updated": True, "object_id": object_id}
    except Exception as exc:
        return {"updated": False, "detail": str(exc)}


@router.get("/wallet/google/{qr_token}")
def pharmacy_google_wallet(qr_token: str, db: Session = Depends(get_db)):
    customer, card = get_valid_pharmacy_card_by_token(db, qr_token)
    save_url = build_pharmacy_google_wallet_save_url(customer, card)
    return RedirectResponse(url=save_url)


@router.get("/qr/{qr_token}", response_class=HTMLResponse)
def public_card(qr_token: str, db: Session = Depends(get_db)):
    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(models.PharmacyLoyaltyCard.qr_token == qr_token)
        .first()
    )
    if not card or not card.active or not card.pharmacy_customer_id:
        return HTMLResponse("<h1>Tarjeta Farmacia no válida</h1>", status_code=404)
    customer = (
        db.query(models.PharmacyCustomer)
        .filter(models.PharmacyCustomer.id == card.pharmacy_customer_id)
        .first()
    )
    customer_name = escape(customer.name or "Socio Farmacia")
    card_code = escape(card.card_code)
    return HTMLResponse(
        f"""
        <html><head><meta name="viewport" content="width=device-width"></head>
        <body style="font-family:Arial;background:#f4f4f1;padding:24px">
          <div style="max-width:520px;margin:auto;background:white;padding:28px;border-radius:24px">
            <h1>Tarjeta Mayu Magistral</h1>
            <h2>{customer_name}</h2>
            <p>Tarjeta: {card_code}</p>
            <p style="font-size:42px;font-weight:bold">{card.points_balance} puntos</p>
            <p>Acumulado hacia el próximo punto: ${card.accumulated_cents / 100:.2f}</p>
            <p>Abre la app Mayu Farmacia para ver historial o acreditar una compra.</p>
          </div>
        </body></html>
        """
    )


@router.get("/qr/{qr_token}/image")
def public_card_qr_image(qr_token: str, db: Session = Depends(get_db)):
    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(models.PharmacyLoyaltyCard.qr_token == qr_token)
        .first()
    )
    if not card or not card.active or not card.pharmacy_customer_id:
        raise HTTPException(status_code=404, detail="Tarjeta Farmacia no válida")

    url = (
        "https://mayu-wellness-backend-v1.onrender.com"
        f"/pharmacy-loyalty/qr/{card.qr_token}"
    )
    image = qrcode.make(url)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return StreamingResponse(output, media_type="image/png")
