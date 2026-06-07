import os
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
import models

router = APIRouter(
    prefix="/payments/paypal/marketplace",
    tags=["PayPal Marketplace"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_paypal_mode():
    return os.getenv("PAYPAL_MODE", "sandbox").lower().strip()


def get_paypal_client_id():
    value = os.getenv("PAYPAL_CLIENT_ID")
    return value.strip() if value else None


def get_paypal_client_secret():
    value = os.getenv("PAYPAL_CLIENT_SECRET")
    return value.strip() if value else None


def get_base_url():
    if get_paypal_mode() == "sandbox":
        return "https://api-m.sandbox.paypal.com"
    return "https://api-m.paypal.com"


class MarketplacePayPalItem(BaseModel):
    item_type: str  # pharmacy | education
    item_id: int
    title: str
    quantity: int = 1
    unit_price: float


class MarketplacePayPalCreateOrderRequest(BaseModel):
    buyer_name: str
    buyer_email: Optional[str] = None
    buyer_phone: Optional[str] = None
    currency: str = "USD"
    source: str = "pharmacy"  # pharmacy | education
    items: List[MarketplacePayPalItem]


class MarketplacePayPalCaptureRequest(BaseModel):
    paypal_order_id: str


def get_token():
    client_id = get_paypal_client_id()
    client_secret = get_paypal_client_secret()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Faltan PAYPAL_CLIENT_ID o PAYPAL_CLIENT_SECRET",
        )

    auth = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("utf-8")

    try:
        req = urllib.request.Request(
            f"{get_base_url()}/v1/oauth2/token",
            data="grant_type=client_credentials".encode("utf-8"),
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )

        with urllib.request.urlopen(req) as res:
            payload = json.loads(res.read().decode("utf-8"))
            return payload["access_token"]

    except urllib.error.HTTPError as e:
        error = e.read().decode("utf-8")
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo token PayPal: HTTP Error {e.code}: {error}",
        )


def paypal_request(method: str, path: str, token: str, body=None):
    try:
        req = urllib.request.Request(
            f"{get_base_url()}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method=method,
        )

        with urllib.request.urlopen(req) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    except urllib.error.HTTPError as e:
        error = e.read().decode("utf-8")
        raise HTTPException(
            status_code=500,
            detail=f"Error PayPal: HTTP Error {e.code}: {error}",
        )


def validate_items(payload: MarketplacePayPalCreateOrderRequest):
    if not payload.items:
        raise HTTPException(status_code=400, detail="El carrito está vacío")

    if payload.source not in {"pharmacy", "education"}:
        raise HTTPException(
            status_code=400,
            detail="source debe ser pharmacy o education",
        )

    total = 0.0

    for item in payload.items:
        if item.item_type not in {"pharmacy", "education"}:
            raise HTTPException(
                status_code=400,
                detail="item_type debe ser pharmacy o education",
            )

        if item.quantity <= 0:
            raise HTTPException(
                status_code=400,
                detail="La cantidad debe ser mayor a cero",
            )

        if item.unit_price < 0:
            raise HTTPException(
                status_code=400,
                detail="El precio no puede ser negativo",
            )

        total += item.quantity * item.unit_price

    return round(total, 2)


def build_description(payload: MarketplacePayPalCreateOrderRequest):
    if payload.source == "education":
        return "Compra Marketplace Mayu Educación"

    return "Compra Marketplace Farmacia Magistral Mayu"


def serialize_items(payload: MarketplacePayPalCreateOrderRequest):
    return [
        {
            "item_type": item.item_type,
            "item_id": item.item_id,
            "title": item.title,
            "quantity": item.quantity,
            "unit_price": round(item.unit_price, 2),
            "total": round(item.quantity * item.unit_price, 2),
        }
        for item in payload.items
    ]


@router.get("/debug-config")
def debug_config():
    client_id = get_paypal_client_id()
    client_secret = get_paypal_client_secret()

    return {
        "paypal_mode": get_paypal_mode(),
        "paypal_base_url": get_base_url(),
        "has_client_id": bool(client_id),
        "has_client_secret": bool(client_secret),
        "client_id_prefix": client_id[:12] if client_id else None,
        "client_secret_length": len(client_secret) if client_secret else 0,
    }


@router.post("/create-order")
def create_marketplace_paypal_order(
    payload: MarketplacePayPalCreateOrderRequest,
    db: Session = Depends(get_db),
):
    total = validate_items(payload)
    token = get_token()

    paypal_body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": payload.currency,
                    "value": f"{total:.2f}",
                },
                "description": build_description(payload),
            }
        ],
        "application_context": {
            "brand_name": "Mayu Wellness Club",
            "user_action": "PAY_NOW",
            "return_url": "https://mayu-wellness-backend-v1.onrender.com/payments/paypal/marketplace/success",
            "cancel_url": "https://mayu-wellness-backend-v1.onrender.com/payments/paypal/marketplace/cancel",
        },
    }

    response = paypal_request(
        "POST",
        "/v2/checkout/orders",
        token,
        paypal_body,
    )

    payment = models.MembershipPayment(
        user_id=None,
        order_id=None,
        paypal_order_id=response["id"],
        amount=total,
        currency=payload.currency,
        status="created",
        provider="paypal",
        payment_type=f"marketplace_{payload.source}",
        payment_reference=response["id"],
        payer_email=payload.buyer_email,
        raw_payload=json.dumps(
            {
                "paypal": response,
                "buyer": {
                    "name": payload.buyer_name,
                    "email": payload.buyer_email,
                    "phone": payload.buyer_phone,
                },
                "source": payload.source,
                "items": serialize_items(payload),
            }
        ),
    )

    db.add(payment)
    db.commit()
    db.refresh(payment)

    return {
        "message": "Orden PayPal marketplace creada",
        "payment_id": payment.id,
        "paypal_order_id": response["id"],
        "source": payload.source,
        "amount": total,
        "currency": payload.currency,
        "links": response.get("links", []),
    }


def capture_marketplace_payment(paypal_order_id: str, db: Session):
    payment = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.paypal_order_id == paypal_order_id)
        .first()
    )

    if not payment:
        raise HTTPException(status_code=404, detail="Pago marketplace no encontrado")

    if payment.status in {"paid", "verified"}:
        return payment

    token = get_token()

    response = paypal_request(
        "POST",
        f"/v2/checkout/orders/{paypal_order_id}/capture",
        token,
        body={},
    )

    capture_id = None
    payer_email = None

    try:
        capture_id = response["purchase_units"][0]["payments"]["captures"][0]["id"]
        payer_email = response.get("payer", {}).get("email_address")
    except Exception:
        pass

    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    payment.paypal_capture_id = capture_id
    payment.payer_email = payer_email or payment.payer_email
    payment.raw_payload = json.dumps(
        {
            "previous_payload": payment.raw_payload,
            "capture": response,
        }
    )

    db.commit()
    db.refresh(payment)

    return payment


@router.post("/capture-order")
def capture_marketplace_order(
    payload: MarketplacePayPalCaptureRequest,
    db: Session = Depends(get_db),
):
    payment = capture_marketplace_payment(payload.paypal_order_id, db)

    return {
        "message": "Pago marketplace capturado",
        "payment_id": payment.id,
        "status": payment.status,
        "payment_type": payment.payment_type,
        "paypal_order_id": payment.paypal_order_id,
        "paypal_capture_id": payment.paypal_capture_id,
        "payer_email": payment.payer_email,
        "amount": payment.amount,
        "currency": payment.currency,
    }


@router.get("/success")
def paypal_marketplace_success(
    token: str,
    db: Session = Depends(get_db),
):
    payment = capture_marketplace_payment(token, db)

    return {
        "status": "paid",
        "message": "Pago marketplace capturado correctamente. Puedes volver a Mayu Wellness Club.",
        "payment_id": payment.id,
        "payment_type": payment.payment_type,
        "paypal_order_id": payment.paypal_order_id,
        "payer_email": payment.payer_email,
    }


@router.get("/cancel")
def paypal_marketplace_cancel():
    return {
        "status": "cancelled",
        "message": "Pago marketplace cancelado por el usuario",
    }
