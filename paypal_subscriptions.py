import os
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
import models

router = APIRouter(
    prefix="/payments/paypal/subscriptions",
    tags=["PayPal Subscriptions"],
)

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"


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


def get_plan_id_by_level(level: int):
    env_map = {
        1: "PAYPAL_PLAN_ID_LEVEL_1",
        2: "PAYPAL_PLAN_ID_LEVEL_2",
        3: "PAYPAL_PLAN_ID_LEVEL_3",
    }
    return os.getenv(env_map.get(level, "") or "")


# =========================
# PRECIOS MENSUALES RECURRENTES
# =========================
MONTHLY_PRICES = {
    1: 40.00,
    2: 50.00,
    3: 60.00,
}


PLAN_NAMES = {
    1: "Mayu Wellness Club - Nivel 1 Cobre",
    2: "Mayu Wellness Club - Nivel 2 Plata",
    3: "Mayu Wellness Club - Nivel 3 Oro",
}


class CreateSubscriptionRequest(BaseModel):
    user_id: int
    plan_level: int
    start_time: Optional[str] = None


class CreateProductRequest(BaseModel):
    name: str = "Mayu Wellness Club"
    description: str = "Membresías recurrentes Mayu Wellness Club"


class CreatePlanRequest(BaseModel):
    product_id: str
    plan_level: int
    currency: str = "USD"


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


def paypal_request(method: str, path: str, token: str, body=None):
    try:
        req = urllib.request.Request(
            f"{get_base_url()}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "Prefer": "return=representation",
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


def first_day_next_month_utc():
    now = datetime.now(timezone.utc)

    if now.month == 12:
        year = now.year + 1
        month = 1
    else:
        year = now.year
        month = now.month + 1

    return datetime(year, month, 1, 5, 0, 0, tzinfo=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def extract_approve_url(links):
    if not isinstance(links, list):
        return None

    for link in links:
        if link.get("rel") == "approve":
            return link.get("href")

    return None


def safe_set(obj, attr, value):
    if hasattr(obj, attr):
        setattr(obj, attr, value)


@router.get("/debug")
def debug_subscriptions():
    return {
        "paypal_mode": get_paypal_mode(),
        "paypal_base_url": get_base_url(),
        "has_client_id": bool(get_paypal_client_id()),
        "has_client_secret": bool(get_paypal_client_secret()),
        "plan_level_1": bool(get_plan_id_by_level(1)),
        "plan_level_2": bool(get_plan_id_by_level(2)),
        "plan_level_3": bool(get_plan_id_by_level(3)),
        "monthly_prices": MONTHLY_PRICES,
    }


@router.post("/create-product")
def create_product(payload: CreateProductRequest):
    token = get_token()

    body = {
        "name": payload.name,
        "description": payload.description,
        "type": "SERVICE",
        "category": "SOFTWARE",
    }

    response = paypal_request("POST", "/v1/catalogs/products", token, body)

    return {
        "message": "Producto PayPal creado correctamente",
        "product_id": response.get("id"),
        "response": response,
    }


@router.post("/create-plan")
def create_plan(payload: CreatePlanRequest):
    if payload.plan_level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Nivel inválido")

    token = get_token()

    price = MONTHLY_PRICES[payload.plan_level]
    plan_name = PLAN_NAMES[payload.plan_level]

    body = {
        "product_id": payload.product_id,
        "name": plan_name,
        "description": f"Mensualidad recurrente {plan_name}",
        "status": "ACTIVE",
        "billing_cycles": [
            {
                "frequency": {
                    "interval_unit": "MONTH",
                    "interval_count": 1,
                },
                "tenure_type": "REGULAR",
                "sequence": 1,
                "total_cycles": 0,
                "pricing_scheme": {
                    "fixed_price": {
                        "value": f"{price:.2f}",
                        "currency_code": payload.currency,
                    }
                },
            }
        ],
        "payment_preferences": {
            "auto_bill_outstanding": True,
            "setup_fee_failure_action": "CONTINUE",
            "payment_failure_threshold": 1,
        },
    }

    response = paypal_request("POST", "/v1/billing/plans", token, body)

    return {
        "message": "Plan PayPal creado correctamente",
        "plan_level": payload.plan_level,
        "monthly_price": price,
        "plan_id": response.get("id"),
        "response": response,
    }


@router.post("/create")
def create_subscription(
    payload: CreateSubscriptionRequest,
    db: Session = Depends(get_db),
):
    if payload.plan_level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Nivel inválido")

    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    plan_id = get_plan_id_by_level(payload.plan_level)

    if not plan_id:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Falta configurar PAYPAL_PLAN_ID_LEVEL_{payload.plan_level} "
                "en Render. Primero crea el producto y el plan en Swagger."
            ),
        )

    token = get_token()
    start_time = payload.start_time or first_day_next_month_utc()

    body = {
        "plan_id": plan_id,
        "start_time": start_time,
        "subscriber": {
            "name": {
                "given_name": user.name or "Socio",
                "surname": "Mayu",
            },
            "email_address": user.email,
        },
        "application_context": {
            "brand_name": "Mayu Wellness Club",
            "locale": "es-EC",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "SUBSCRIBE_NOW",
            "payment_method": {
                "payer_selected": "PAYPAL",
                "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED",
            },
            "return_url": f"{BASE_PUBLIC_URL}/payments/paypal/subscriptions/return",
            "cancel_url": f"{BASE_PUBLIC_URL}/payments/paypal/subscriptions/cancel",
        },
    }

    response = paypal_request("POST", "/v1/billing/subscriptions", token, body)

    subscription_id = response.get("id")
    approve_url = extract_approve_url(response.get("links", []))
    monthly_amount = MONTHLY_PRICES[payload.plan_level]

    user.membership_level = payload.plan_level

    payment = models.MembershipPayment(
        user_id=user.id,
        order_id=None,
        paypal_order_id=subscription_id,
        amount=monthly_amount,
        currency="USD",
        status="subscription_created",
    )

    safe_set(payment, "provider", "paypal")
    safe_set(payment, "payment_type", "subscription")
    safe_set(payment, "payment_reference", subscription_id)
    safe_set(payment, "raw_payload", json.dumps(response))

    db.add(payment)
    db.commit()
    db.refresh(payment)

    return {
        "message": "Suscripción PayPal creada",
        "payment_id": payment.id,
        "user_id": user.id,
        "plan_level": payload.plan_level,
        "monthly_amount": monthly_amount,
        "start_time": start_time,
        "paypal_subscription_id": subscription_id,
        "approve_url": approve_url,
        "links": response.get("links", []),
    }


@router.get("/return", response_class=HTMLResponse)
def subscription_return():
    return HTMLResponse(
        content="""
        <html>
            <head>
                <title>Suscripción activada</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: Arial; background:#0f172a; color:white; text-align:center; padding:40px;">
                <h1>Mensualidad automática activada</h1>
                <p>Puedes volver a Mayu Wellness Club y continuar.</p>
            </body>
        </html>
        """
    )


@router.get("/cancel", response_class=HTMLResponse)
def subscription_cancel():
    return HTMLResponse(
        content="""
        <html>
            <head>
                <title>Suscripción cancelada</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: Arial; background:#111; color:white; text-align:center; padding:40px;">
                <h1>Proceso cancelado</h1>
                <p>No se activó la mensualidad automática.</p>
            </body>
        </html>
        """
    )


@router.post("/webhook")
async def subscription_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    event = await request.json()
    event_type = event.get("event_type")
    resource = event.get("resource", {})

    subscription_id = (
        resource.get("billing_agreement_id")
        or resource.get("id")
        or resource.get("subscription_id")
    )

    if not subscription_id:
        return {"status": "ignored", "reason": "No subscription id"}

    payment = db.query(models.MembershipPayment).filter(
        models.MembershipPayment.paypal_order_id == subscription_id,
        models.MembershipPayment.payment_type == "subscription",
    ).first()

    user = None

    if payment:
        user = db.query(models.User).filter(
            models.User.id == payment.user_id
        ).first()

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        if payment:
            payment.status = "subscription_active"
            safe_set(payment, "admin_verified", True)
            safe_set(payment, "admin_verified_at", datetime.utcnow())
            safe_set(payment, "raw_payload", json.dumps(event))

        if user:
            user.membership_active = True

        db.commit()

        return {
            "status": "ok",
            "event": event_type,
            "subscription_id": subscription_id,
        }

    if event_type in [
        "PAYMENT.SALE.COMPLETED",
        "PAYMENT.CAPTURE.COMPLETED",
    ]:
        if payment:
            payment.status = "subscription_paid"
            payment.paid_at = datetime.utcnow()
            safe_set(payment, "admin_verified", True)
            safe_set(payment, "admin_verified_at", datetime.utcnow())
            safe_set(payment, "raw_payload", json.dumps(event))

        if user:
            user.membership_active = True

        db.commit()

        return {
            "status": "ok",
            "event": event_type,
            "subscription_id": subscription_id,
        }

    if event_type in [
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
        "BILLING.SUBSCRIPTION.EXPIRED",
        "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
    ]:
        if payment:
            payment.status = "subscription_inactive"
            safe_set(payment, "raw_payload", json.dumps(event))

        if user:
            user.membership_active = False

        db.commit()

        return {
            "status": "ok",
            "event": event_type,
            "subscription_id": subscription_id,
        }

    return {
        "status": "ignored",
        "event": event_type,
        "subscription_id": subscription_id,
    }


@router.get("/status/{user_id}")
def get_subscription_status(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.user_id == user_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .order_by(models.MembershipPayment.id.desc())
        .first()
    )

    if not payment:
        return {
            "membership_active": user.membership_active,
            "subscription_status": "NONE",
            "local_payment_status": "NONE",
            "next_billing_time": None,
            "plan_level": user.membership_level,
            "paypal_subscription_id": None,
        }

    subscription_id = payment.paypal_order_id

    if not subscription_id:
        return {
            "membership_active": user.membership_active,
            "subscription_status": payment.status,
            "local_payment_status": payment.status,
            "next_billing_time": None,
            "plan_level": user.membership_level,
            "paypal_subscription_id": None,
        }

    try:
        token = get_token()

        response = paypal_request(
            "GET",
            f"/v1/billing/subscriptions/{subscription_id}",
            token,
        )

        paypal_status = response.get("status")
        billing_info = response.get("billing_info", {})
        next_billing_time = billing_info.get("next_billing_time") if billing_info else None

        if paypal_status == "ACTIVE":
            payment.status = "subscription_active"
            safe_set(payment, "admin_verified", True)
            safe_set(payment, "admin_verified_at", datetime.utcnow())
            user.membership_active = True
            db.commit()

        elif paypal_status in ["SUSPENDED", "CANCELLED", "EXPIRED"]:
            payment.status = "subscription_inactive"
            user.membership_active = False
            db.commit()

        return {
            "membership_active": user.membership_active,
            "subscription_status": paypal_status,
            "local_payment_status": payment.status,
            "next_billing_time": next_billing_time,
            "plan_level": user.membership_level,
            "paypal_subscription_id": subscription_id,
            "payment_id": payment.id,
            "admin_verified": payment.admin_verified,
            "admin_verified_at": payment.admin_verified_at,
        }

    except Exception as e:
        return {
            "membership_active": user.membership_active,
            "subscription_status": payment.status,
            "local_payment_status": payment.status,
            "next_billing_time": None,
            "plan_level": user.membership_level,
            "paypal_subscription_id": subscription_id,
            "payment_id": payment.id,
            "admin_verified": payment.admin_verified,
            "admin_verified_at": payment.admin_verified_at,
            "error": str(e),
        }
