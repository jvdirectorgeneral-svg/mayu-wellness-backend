import os
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
from dependencies import get_current_user
import models

router = APIRouter(prefix="/payments/paypal", tags=["PayPal Payments"])


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
    return (
        "https://api-m.sandbox.paypal.com"
        if get_paypal_mode() == "sandbox"
        else "https://api-m.paypal.com"
    )


class PayPalCreateOrderRequest(BaseModel):
    user_id: int
    amount: float
    currency: str = "USD"
    order_id: Optional[int] = None


class PayPalCaptureOrderRequest(BaseModel):
    paypal_order_id: str


class AdminVerifyPaymentRequest(BaseModel):
    verification_notes: Optional[str] = None


def require_admin(user):
    if not user or user.role not in ["admin", "superadmin"]:
        raise HTTPException(status_code=403, detail="Acceso solo admin")


def generate_order_code(user_id, month, year):
    return f"MWC-{year}{month:02d}-U{user_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


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


def get_token():
    client_id = get_paypal_client_id()
    client_secret = get_paypal_client_secret()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Faltan PAYPAL_CLIENT_ID o PAYPAL_CLIENT_SECRET",
        )

    try:
        auth = base64.b64encode(
            f"{client_id}:{client_secret}".encode("utf-8")
        ).decode("utf-8")

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
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo token PayPal: {str(e)}",
        )


def paypal_request(method, path, token, body=None):
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


@router.get("")
def list_payments(db: Session = Depends(get_db)):
    payments = db.query(models.MembershipPayment).all()

    return {
        "items": [
            {
                "id": p.id,
                "user_id": p.user_id,
                "order_id": p.order_id,
                "amount": p.amount,
                "status": p.status,
                "paypal_order_id": p.paypal_order_id,
                "payer_email": p.payer_email,
                "admin_verified": p.admin_verified,
                "created_at": p.created_at,
                "paid_at": p.paid_at,
            }
            for p in payments
        ]
    }


@router.get("/order/{paypal_order_id}")
def get_payment_by_paypal_order_id(
    paypal_order_id: str,
    db: Session = Depends(get_db),
):
    payment = db.query(models.MembershipPayment).filter_by(
        paypal_order_id=paypal_order_id
    ).first()

    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    return {
        "id": payment.id,
        "user_id": payment.user_id,
        "order_id": payment.order_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "paypal_order_id": payment.paypal_order_id,
        "paypal_capture_id": payment.paypal_capture_id,
        "payer_email": payment.payer_email,
        "admin_verified": payment.admin_verified,
        "paid_at": payment.paid_at,
    }


@router.post("/create-order")
def create_order(
    payload: PayPalCreateOrderRequest,
    db: Session = Depends(get_db),
):
    user = db.query(models.User).get(payload.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    token = get_token()

    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": payload.currency,
                    "value": f"{payload.amount:.2f}",
                },
                "description": "Mayu Wellness Club - Membresía",
            }
        ],
        "application_context": {
            "brand_name": "Mayu Wellness Club",
            "user_action": "PAY_NOW",
            "return_url": "https://mayu-wellness-backend-v1.onrender.com/payments/paypal/success",
            "cancel_url": "https://mayu-wellness-backend-v1.onrender.com/payments/paypal/cancel",
        },
    }

    response = paypal_request("POST", "/v2/checkout/orders", token, body)

    payment = models.MembershipPayment(
        user_id=payload.user_id,
        order_id=payload.order_id,
        paypal_order_id=response["id"],
        amount=payload.amount,
        currency=payload.currency,
        status="created",
        provider="paypal",
        payment_type="signup",
        payment_reference=response["id"],
        raw_payload=json.dumps(response),
    )

    db.add(payment)
    db.commit()
    db.refresh(payment)

    return {
        "payment_id": payment.id,
        "paypal_order_id": response["id"],
        "links": response.get("links", []),
    }


def capture_payment_by_order_id(paypal_order_id: str, db: Session):
    payment = db.query(models.MembershipPayment).filter_by(
        paypal_order_id=paypal_order_id
    ).first()

    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    if payment.status in ["paid", "verified"]:
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
    payment.payer_email = payer_email
    payment.raw_payload = json.dumps(response)

    db.commit()
    db.refresh(payment)

    return payment


@router.post("/capture-order")
def capture(
    payload: PayPalCaptureOrderRequest,
    db: Session = Depends(get_db),
):
    payment = capture_payment_by_order_id(payload.paypal_order_id, db)

    return {
        "message": "Pago capturado",
        "payment_id": payment.id,
        "status": payment.status,
        "paypal_capture_id": payment.paypal_capture_id,
        "payer_email": payment.payer_email,
    }


@router.get("/success")
def paypal_success(
    token: str,
    db: Session = Depends(get_db),
):
    payment = capture_payment_by_order_id(token, db)

    return {
        "status": "paid",
        "message": "Pago PayPal capturado correctamente. Puedes volver a Mayu Wellness Club.",
        "payment_id": payment.id,
        "paypal_order_id": payment.paypal_order_id,
        "payer_email": payment.payer_email,
    }


@router.get("/cancel")
def paypal_cancel():
    return {
        "status": "cancelled",
        "message": "Pago cancelado por el usuario",
    }


@router.put("/{payment_id}/verify")
def verify(
    payment_id: int,
    payload: AdminVerifyPaymentRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_admin(current_user)

    payment = db.query(models.MembershipPayment).get(payment_id)

    if not payment or payment.status not in ["paid", "verified"]:
        raise HTTPException(status_code=400, detail="Pago inválido")

    user = db.query(models.User).get(payment.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    now = datetime.utcnow()

    payment.status = "verified"
    payment.admin_verified = True
    payment.admin_verified_at = now
    payment.admin_verified_by = current_user.id

    order = None

    if payment.order_id:
        order = db.query(models.Order).get(payment.order_id)
    else:
        order = db.query(models.Order).filter_by(
            user_id=user.id,
            month=now.month,
            year=now.year,
        ).first()

    if not order:
        monthly = db.query(models.MonthlySelection).filter_by(
            user_id=user.id,
            month=now.month,
            year=now.year,
        ).first()

        if not monthly or not monthly.items:
            raise HTTPException(status_code=400, detail="No hay selección mensual")

        order = models.Order(
            order_code=generate_order_code(user.id, now.month, now.year),
            user_id=user.id,
            month=now.month,
            year=now.year,
            membership_level_snapshot=user.membership_level,
            user_status_snapshot="active" if user.membership_active else "inactive",
            city_snapshot=user.city,
            address_snapshot=user.address,
            reference_snapshot=user.reference,
            delivery_notes_snapshot=user.delivery_notes,
            status="approved_for_logistics",
            logistics_notes="✔ Pago verificado - listo para despacho",
        )

        db.add(order)
        db.flush()

        for item in monthly.items:
            db.add(
                models.OrderItem(
                    order_id=order.id,
                    product_id=item.product_id,
                    product_name_snapshot=item.product.name,
                    quantity=item.quantity,
                )
            )

        payment.order_id = order.id
    else:
        order.status = "approved_for_logistics"
        order.logistics_notes = "✔ Pago verificado - listo para despacho"

    db.commit()

    return {
        "message": "Pago verificado y orden lista",
        "payment_id": payment.id,
        "order_id": order.id,
        "order_status": order.status,
    }


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    event = await request.json()

    if event.get("event_type") == "PAYMENT.CAPTURE.COMPLETED":
        order_id = (
            event.get("resource", {})
            .get("supplementary_data", {})
            .get("related_ids", {})
            .get("order_id")
        )

        if order_id:
            payment = db.query(models.MembershipPayment).filter_by(
                paypal_order_id=order_id
            ).first()

            if payment:
                payment.status = "paid"
                payment.paid_at = datetime.utcnow()
                db.commit()

    return {"status": "ok"}
