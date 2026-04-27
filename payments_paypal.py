import os
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
from dependencies import get_current_user
import models

router = APIRouter(prefix="/payments/paypal", tags=["PayPal Payments"])


# =========================
# DB
# =========================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# CONFIG
# =========================
def get_paypal_mode() -> str:
    return os.getenv("PAYPAL_MODE", "sandbox").lower().strip()


def get_paypal_client_id() -> Optional[str]:
    return os.getenv("PAYPAL_CLIENT_ID")


def get_paypal_client_secret() -> Optional[str]:
    return os.getenv("PAYPAL_CLIENT_SECRET")


def get_paypal_base_url() -> str:
    return (
        "https://api-m.sandbox.paypal.com"
        if get_paypal_mode() == "sandbox"
        else "https://api-m.paypal.com"
    )


# =========================
# SCHEMAS
# =========================
class PayPalCreateOrderRequest(BaseModel):
    user_id: int
    amount: float
    currency: str = "USD"
    payment_type: str = "signup"
    order_id: Optional[int] = None


class PayPalCaptureOrderRequest(BaseModel):
    paypal_order_id: str


class AdminVerifyPaymentRequest(BaseModel):
    verification_notes: Optional[str] = None


# =========================
# HELPERS
# =========================
def require_admin_or_superadmin(user):
    if not user or user.role not in {"admin", "superadmin"}:
        raise HTTPException(403, "Solo admin")


def generate_order_code(user_id, month, year):
    return f"MWC-{year}{month:02d}-U{user_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


def get_token():
    auth = base64.b64encode(
        f"{get_paypal_client_id()}:{get_paypal_client_secret()}".encode()
    ).decode()

    req = urllib.request.Request(
        f"{get_paypal_base_url()}/v1/oauth2/token",
        data="grant_type=client_credentials".encode(),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )

    res = urllib.request.urlopen(req)
    return json.loads(res.read())["access_token"]


def paypal_request(method, path, token, body=None):
    req = urllib.request.Request(
        f"{get_paypal_base_url()}{path}",
        data=json.dumps(body).encode() if body else None,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        },
        method=method
    )

    res = urllib.request.urlopen(req)
    return json.loads(res.read())


# =========================
# CREATE ORDER
# =========================
@router.post("/create-order")
def create_order(payload: PayPalCreateOrderRequest, db: Session = Depends(get_db)):
    token = get_token()

    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {
                "currency_code": payload.currency,
                "value": f"{payload.amount:.2f}"
            }
        }]
    }

    response = paypal_request("POST", "/v2/checkout/orders", token, body)

    payment = models.MembershipPayment(
        user_id=payload.user_id,
        order_id=payload.order_id,
        paypal_order_id=response["id"],
        amount=payload.amount,
        status="created"
    )

    db.add(payment)
    db.commit()

    return response


# =========================
# CAPTURE
# =========================
@router.post("/capture-order")
def capture(payload: PayPalCaptureOrderRequest, db: Session = Depends(get_db)):
    token = get_token()

    response = paypal_request(
        "POST",
        f"/v2/checkout/orders/{payload.paypal_order_id}/capture",
        token
    )

    payment = db.query(models.MembershipPayment).filter_by(
        paypal_order_id=payload.paypal_order_id
    ).first()

    payment.status = "paid"
    payment.paid_at = datetime.utcnow()

    db.commit()

    return response


# =========================
# VERIFY + CREAR ORDEN + LIBERAR LOGÍSTICA
# =========================
@router.put("/{payment_id}/verify")
def verify(payment_id: int,
           payload: AdminVerifyPaymentRequest,
           db: Session = Depends(get_db),
           current_user=Depends(get_current_user)):

    require_admin_or_superadmin(current_user)

    payment = db.query(models.MembershipPayment).get(payment_id)

    if not payment or payment.status != "paid":
        raise HTTPException(400, "Pago inválido")

    user = db.query(models.User).get(payment.user_id)

    payment.status = "verified"
    payment.admin_verified = True
    payment.admin_verified_at = datetime.utcnow()
    payment.admin_verified_by = current_user.id

    now = datetime.utcnow()

    # =========================
    # BUSCAR ORDEN EXISTENTE
    # =========================
    order = None

    if payment.order_id:
        order = db.query(models.Order).get(payment.order_id)

    else:
        order = db.query(models.Order).filter_by(
            user_id=user.id,
            month=now.month,
            year=now.year
        ).first()

    # =========================
    # CREAR ORDEN SI NO EXISTE
    # =========================
    if not order:
        monthly = db.query(models.MonthlySelection).filter_by(
            user_id=user.id,
            month=now.month,
            year=now.year
        ).first()

        if not monthly:
            raise HTTPException(400, "No hay selección mensual")

        order = models.Order(
            order_code=generate_order_code(user.id, now.month, now.year),
            user_id=user.id,
            month=now.month,
            year=now.year,
            status="approved_for_logistics",  # 🔥 DIRECTO A LOGÍSTICA
            logistics_notes="Pago verificado - listo para despacho"
        )

        db.add(order)
        db.flush()

        for item in monthly.items:
            db.add(models.OrderItem(
                order_id=order.id,
                product_id=item.product_id,
                product_name_snapshot=item.product.name,
                quantity=item.quantity
            ))

        payment.order_id = order.id

    # =========================
    # SI YA EXISTE → LIBERAR
    # =========================
    else:
        order.status = "approved_for_logistics"
        order.logistics_notes = "Pago verificado - listo para despacho"

    db.commit()

    return {
        "message": "Pago verificado y orden lista para logística",
        "order_status": order.status
    }


# =========================
# WEBHOOK
# =========================
@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    event = await request.json()

    if event.get("event_type") == "PAYMENT.CAPTURE.COMPLETED":
        order_id = event["resource"]["supplementary_data"]["related_ids"]["order_id"]

        payment = db.query(models.MembershipPayment).filter_by(
            paypal_order_id=order_id
        ).first()

        if payment:
            payment.status = "paid"
            payment.paid_at = datetime.utcnow()
            db.commit()

    return {"ok": True}
