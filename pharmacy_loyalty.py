import uuid
import io
from html import escape
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
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
import models
import qrcode


router = APIRouter(prefix="/pharmacy-loyalty", tags=["Pharmacy Loyalty"])
security = HTTPBearer()
POINT_VALUE_CENTS = 1000


class PharmacyCustomerRegister(BaseModel):
    name: str
    email: str
    phone: str
    password: str
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
        "qr_url": (
            "https://mayu-wellness-backend-v1.onrender.com"
            f"/pharmacy-loyalty/qr/{card.qr_token}"
        ),
        "qr_image_url": (
            "https://mayu-wellness-backend-v1.onrender.com"
            f"/pharmacy-loyalty/qr/{card.qr_token}/image"
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
    return {
        "created": created,
        "points_earned": transaction.points_delta,
        "card": card_to_dict(customer, card),
    }


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
            <h1>Farmacia Mayu</h1>
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
