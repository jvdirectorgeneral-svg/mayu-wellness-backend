import uuid
import io
import os
import json
import tempfile
from html import escape
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
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


router = APIRouter(prefix="/pharmacy-loyalty", tags=["Pharmacy Loyalty"])
security = HTTPBearer()
POINT_VALUE_CENTS = 1000


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


class PharmacyPushTokenRequest(BaseModel):
    token: str
    platform: Optional[str] = None


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

    access_token = create_access_token(
        {"sub": f"pharmacy_customer:{customer.id}", "type": "pharmacy_customer"}
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "customer": customer_to_dict(customer),
        "card": card_to_dict(customer, card),
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
        db.commit()

    return {
        "created": created,
        "points_earned": transaction.points_delta,
        "card": card_to_dict(customer, card),
        "push": push_result,
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


def copy_or_create_pharmacy_wallet_images(pass_dir: str):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(base_dir, "assets", "logo_mayu.png")
    wallet_image_path = os.path.join(base_dir, "assets", "wallet_oro.png")
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


@router.get("/wallet/apple/{qr_token}")
def pharmacy_apple_wallet(qr_token: str, db: Session = Depends(get_db)):
    customer, card = get_valid_pharmacy_card_by_token(db, qr_token)

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
            "serialNumber": f"{card.card_code}-{card.id}-{card.points_balance}-{uuid.uuid4()}",
            "teamIdentifier": team_id,
            "organizationName": organization_name,
            "description": "Tarjeta Mayu Magistral",
            "logoText": "MAYU MAGISTRAL",
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

        with open(os.path.join(pass_dir, "pass.json"), "w", encoding="utf-8") as f:
            json.dump(pass_json, f, ensure_ascii=False, separators=(",", ":"))

        copy_or_create_pharmacy_wallet_images(pass_dir)
        build_manifest(pass_dir)
        sign_manifest(pass_dir, certs_dir)

        output_path = os.path.join(temp_dir, f"tarjeta_mayu_magistral_{card.id}.pkpass")
        zip_pkpass(pass_dir, output_path)

        return FileResponse(
            path=output_path,
            media_type="application/vnd.apple.pkpass",
            filename=f"tarjeta_mayu_magistral_{card.id}.pkpass",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error generando Apple Wallet Farmacia: {str(exc)}",
        )


def build_pharmacy_google_wallet_save_url(customer, card):
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    class_suffix = os.getenv(
        "GOOGLE_WALLET_PHARMACY_CLASS_SUFFIX",
        "mayu_magistral_pharmacy",
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
    object_suffix = f"mayu_magistral_{card.card_code}_{card.id}".replace("-", "_").lower()
    object_id = f"{issuer_id}.{object_suffix}"
    public_url = f"{BASE_PUBLIC_URL}/pharmacy-loyalty/qr/{card.qr_token}"
    qr_image_url = f"{BASE_PUBLIC_URL}/pharmacy-loyalty/qr/{card.qr_token}/image"
    logo_url = f"{BASE_PUBLIC_URL}/member-cards/assets/logo_mayu.png"

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
            "sourceUri": {"uri": qr_image_url},
            "contentDescription": {"defaultValue": {"language": "es", "value": "QR Tarjeta Mayu Magistral"}},
        },
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

    claims = {
        "iss": client_email,
        "aud": "google",
        "typ": "savetowallet",
        "payload": {"genericObjects": [generic_object]},
    }

    token = pyjwt.encode(claims, private_key, algorithm="RS256")
    return f"https://pay.google.com/gp/v/save/{token}"


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
