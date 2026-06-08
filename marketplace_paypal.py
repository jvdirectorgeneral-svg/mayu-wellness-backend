import os
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

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


def get_base_url():
    return (
        "https://api-m.sandbox.paypal.com"
        if get_paypal_mode() == "sandbox"
        else "https://api-m.paypal.com"
    )


def get_paypal_client_id():
    value = os.getenv("PAYPAL_CLIENT_ID")
    return value.strip() if value else None


def get_paypal_client_secret():
    value = os.getenv("PAYPAL_CLIENT_SECRET")
    return value.strip() if value else None


class MarketplacePayPalCreateOrderRequest(BaseModel):
    item_type: str  # pharmacy | education
    item_id: int
    user_id: int
    quantity: int = 1
    currency: str = "USD"


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


def get_marketplace_item(db: Session, item_type: str, item_id: int):
    clean_type = item_type.strip().lower()

    if clean_type not in {"pharmacy", "education"}:
        raise HTTPException(
            status_code=400,
            detail="item_type debe ser pharmacy o education",
        )

    if clean_type == "pharmacy":
        product = (
            db.query(models.Product)
            .filter(models.Product.id == item_id)
            .first()
        )

        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        if hasattr(product, "active") and product.active is not True:
            raise HTTPException(status_code=400, detail="Producto inactivo")

        price = float(getattr(product, "price", 0) or 0)

        return {
            "source": "pharmacy",
            "title": product.name,
            "price": price,
        }

    resource = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.id == item_id)
        .first()
    )

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido educativo no encontrado")

    if hasattr(resource, "active") and resource.active is not True:
        raise HTTPException(status_code=400, detail="Contenido educativo inactivo")

    price = float(getattr(resource, "price", 0) or 0)

    return {
        "source": "education",
        "title": resource.title,
        "price": price,
    }


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
    if payload.quantity <= 0:
        raise HTTPException(status_code=400, detail="La cantidad debe ser mayor a cero")

    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    item = get_marketplace_item(db, payload.item_type, payload.item_id)

    if item["price"] <= 0:
        raise HTTPException(
            status_code=400,
            detail="El producto o contenido no tiene precio válido",
        )

    total = round(item["price"] * payload.quantity, 2)

    token = get_token()

    paypal_body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": payload.currency,
                    "value": f"{total:.2f}",
                },
                "description": f"Mayu Marketplace - {item['title']}",
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
        user_id=user.id,
        order_id=None,
        paypal_order_id=response["id"],
        amount=total,
        currency=payload.currency,
        status="created",
        provider="paypal",
        payment_type=f"marketplace_{item['source']}",
        payment_reference=response["id"],
        payer_email=user.email,
        raw_payload=json.dumps(
            {
                "paypal": response,
                "marketplace": {
                    "item_type": item["source"],
                    "item_id": payload.item_id,
                    "title": item["title"],
                    "quantity": payload.quantity,
                    "unit_price": item["price"],
                    "total": total,
                },
                "buyer": {
                    "user_id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "phone": user.phone,
                },
            }
        ),
    )

    db.add(payment)
    db.commit()
    db.refresh(payment)

    approval_url = None
    for link in response.get("links", []):
        if link.get("rel") == "approve":
            approval_url = link.get("href")

    return {
        "message": "Orden PayPal marketplace creada",
        "payment_id": payment.id,
        "paypal_order_id": response["id"],
        "item_type": item["source"],
        "item_id": payload.item_id,
        "title": item["title"],
        "quantity": payload.quantity,
        "unit_price": item["price"],
        "amount": total,
        "currency": payload.currency,
        "approval_url": approval_url,
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
