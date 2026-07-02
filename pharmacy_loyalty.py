import uuid
import io
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
import models
from notification_service import safe_send_push_to_user
import qrcode


router = APIRouter(prefix="/pharmacy-loyalty", tags=["Pharmacy Loyalty"])
POINT_VALUE_CENTS = 1000


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


def get_or_create_card(db: Session, user_id: int):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(models.PharmacyLoyaltyCard.user_id == user_id)
        .first()
    )
    if card:
        return user, card

    card = models.PharmacyLoyaltyCard(
        user_id=user_id,
        card_code=f"FAR-MAYU-{user_id:06d}",
        qr_token=str(uuid.uuid4()),
    )
    db.add(card)
    db.flush()
    return user, card


def card_to_dict(user, card, include_transactions=True):
    data = {
        "id": card.id,
        "user_id": card.user_id,
        "customer_name": user.name,
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
    user_id: int,
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

    _, card = get_or_create_card(db, user_id)
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


@router.post("/enroll")
def enroll(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    user, card = get_or_create_card(db, current_user.id)
    db.commit()
    db.refresh(card)
    return card_to_dict(user, card)


@router.get("/me")
def get_my_card(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    user, card = get_or_create_card(db, current_user.id)
    db.commit()
    db.refresh(card)
    return card_to_dict(user, card)


@router.get("/resolve/{identifier}")
def resolve_card(
    identifier: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(
            (models.PharmacyLoyaltyCard.qr_token == identifier)
            | (models.PharmacyLoyaltyCard.card_code == identifier)
        )
        .first()
    )
    if not card or not card.active:
        raise HTTPException(status_code=404, detail="Tarjeta Farmacia no válida")

    user = db.query(models.User).filter(models.User.id == card.user_id).first()
    mode = (
        "credit"
        if current_user.role in {"superadmin", "admin", "pharmacy_admin"}
        else "view"
    )
    if mode == "view" and current_user.id != card.user_id:
        raise HTTPException(status_code=403, detail="Esta tarjeta no te pertenece")

    return {"mode": mode, "card": card_to_dict(user, card)}


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
    if not card or not card.active:
        raise HTTPException(status_code=404, detail="Tarjeta Farmacia no válida")

    reference = payload.reference.strip()
    if not reference:
        raise HTTPException(status_code=400, detail="La factura es obligatoria")

    card, transaction, created = credit_purchase(
        db,
        card.user_id,
        payload.amount,
        "pharmacy_admin",
        reference=reference,
        created_by=current_user.id,
        note=payload.note,
    )
    db.commit()
    db.refresh(card)
    if created:
        safe_send_push_to_user(
            db,
            card.user_id,
            "Puntos Farmacia Mayu",
            (
                f"Ganaste {transaction.points_delta} punto(s). "
                f"Tu saldo es {card.points_balance}."
            ),
        )
    user = db.query(models.User).filter(models.User.id == card.user_id).first()
    return {
        "created": created,
        "points_earned": transaction.points_delta,
        "card": card_to_dict(user, card),
    }


@router.get("/qr/{qr_token}", response_class=HTMLResponse)
def public_card(qr_token: str, db: Session = Depends(get_db)):
    card = (
        db.query(models.PharmacyLoyaltyCard)
        .filter(models.PharmacyLoyaltyCard.qr_token == qr_token)
        .first()
    )
    if not card or not card.active:
        return HTMLResponse("<h1>Tarjeta Farmacia no válida</h1>", status_code=404)
    user = db.query(models.User).filter(models.User.id == card.user_id).first()
    return HTMLResponse(
        f"""
        <html><head><meta name="viewport" content="width=device-width"></head>
        <body style="font-family:Arial;background:#f4f4f1;padding:24px">
          <div style="max-width:520px;margin:auto;background:white;padding:28px;border-radius:24px">
            <h1>Farmacia Mayu</h1>
            <h2>{user.name}</h2>
            <p>Tarjeta: {card.card_code}</p>
            <p style="font-size:42px;font-weight:bold">{card.points_balance} puntos</p>
            <p>Acumulado hacia el próximo punto: ${card.accumulated_cents / 100:.2f}</p>
            <p>Abre la app Mayu para ver el historial o acreditar una compra.</p>
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
    if not card or not card.active:
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
