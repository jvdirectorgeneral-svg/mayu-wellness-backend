import os
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional
import secrets
import string

import resend
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
    item_type: str
    item_id: int
    user_id: int
    quantity: int = 1
    currency: str = "USD"
    buyer_name: Optional[str] = None
    buyer_phone: Optional[str] = None
    buyer_email: Optional[str] = None


class MarketplacePayPalCaptureRequest(BaseModel):
    paypal_order_id: str


def generate_access_code():
    alphabet = string.ascii_uppercase + string.digits
    return "MAYU-EDU-" + "".join(secrets.choice(alphabet) for _ in range(10))


def generate_education_order_code():
    now = datetime.utcnow()
    random_code = "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6)
    )
    return f"EDU-MAYU-{now.strftime('%Y%m%d%H%M%S')}-{random_code}"


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
        product = db.query(models.Product).filter(models.Product.id == item_id).first()

        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        if hasattr(product, "active") and product.active is not True:
            raise HTTPException(status_code=400, detail="Producto inactivo")

        return {
            "source": "pharmacy",
            "title": product.name,
            "price": float(product.price or 0),
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

    return {
        "source": "education",
        "title": resource.title,
        "price": float(resource.price or 0),
    }


def safe_json_loads(value):
    try:
        if not value:
            return {}
        if isinstance(value, dict):
            return value
        return json.loads(value)
    except Exception:
        return {}


def get_payment_payload(payment: models.MembershipPayment):
    payload = safe_json_loads(payment.raw_payload)

    if "previous_payload" in payload:
        previous = safe_json_loads(payload.get("previous_payload"))
        if previous:
            payload["previous_payload_decoded"] = previous

    return payload


def get_original_payment_payload(payment: models.MembershipPayment):
    payload = safe_json_loads(payment.raw_payload)

    if "previous_payload" in payload:
        previous = safe_json_loads(payload.get("previous_payload"))
        return previous or payload

    return payload


def send_education_access_email(
    to_email: str,
    buyer_name: str,
    resource_title: str,
    access_code: str,
    view_url: str,
):
    resend_api_key = os.getenv("RESEND_API_KEY")
    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev")

    if not resend_api_key:
        raise Exception("Falta RESEND_API_KEY en Render")

    if not to_email:
        raise Exception("Email comprador vacío")

    resend.api_key = resend_api_key

    resend.Emails.send({
        "from": from_email,
        "to": [to_email],
        "subject": "Tu código de acceso Mayu Educación",
        "html": f"""
        <div style="font-family:Arial,sans-serif; max-width:620px; margin:auto; padding:24px;">
            <h2>Mayu Educación</h2>
            <p>Hola {buyer_name or "estudiante Mayu"},</p>
            <p>Tu pago fue confirmado correctamente.</p>
            <p><strong>Contenido:</strong> {resource_title}</p>
            <p><strong>Código de acceso:</strong></p>
            <div style="font-size:22px; font-weight:bold; background:#f3f3f3; padding:14px; border-radius:10px;">
                {access_code}
            </div>
            <p>Enlace de acceso:</p>
            <p><a href="{view_url}">{view_url}</a></p>
            <p>Este código permite hasta 30 ingresos.</p>
            <br>
            <p>Equipo Mayu Educación</p>
        </div>
        """,
    })


def fulfill_education_payment_if_needed(payment: models.MembershipPayment, db: Session):
    if payment.payment_type != "marketplace_education":
        return None

    original_payload = get_original_payment_payload(payment)
    marketplace = original_payload.get("marketplace", {}) or {}
    buyer = original_payload.get("buyer", {}) or {}

    item_id = marketplace.get("item_id")
    quantity = int(marketplace.get("quantity") or 1)

    if not item_id:
        return None

    existing_order = (
        db.query(models.EducationOrder)
        .filter(models.EducationOrder.raw_payment_payload.contains(payment.paypal_order_id))
        .first()
    )

    if existing_order:
        first_item = existing_order.items[0] if existing_order.items else None
        return {
            "education_order_id": existing_order.id,
            "education_order_code": existing_order.order_code,
            "access_code": first_item.access_code if first_item else None,
            "view_url": (
                f"/education/resources/{first_item.resource_id}/view?access_code={first_item.access_code}"
                if first_item and first_item.access_code
                else None
            ),
            "email_sent": True,
            "already_created": True,
        }

    resource = (
        db.query(models.EducationResource)
        .filter(models.EducationResource.id == int(item_id))
        .filter(models.EducationResource.active == True)
        .first()
    )

    if not resource:
        raise HTTPException(status_code=404, detail="Contenido educativo no encontrado")

    buyer_name = (buyer.get("name") or "Comprador Mayu Educación").strip()
    buyer_phone = (buyer.get("phone") or "").strip()
    buyer_email = (buyer.get("email") or payment.payer_email or "").strip()

    if not buyer_email:
        raise HTTPException(
            status_code=400,
            detail="No existe email del comprador para enviar el código educativo",
        )

    unit_price = float(resource.price or 0)
    total = round(unit_price * quantity, 2)
    access_code = generate_access_code()

    access = models.EducationAccessCode(
        resource_id=resource.id,
        code=access_code,
        buyer_name=buyer_name,
        buyer_email=buyer_email,
        buyer_phone=buyer_phone,
        max_uses=30,
        uses_count=0,
        status="active",
        created_at=datetime.utcnow(),
    )

    db.add(access)
    db.flush()

    order = models.EducationOrder(
        order_code=generate_education_order_code(),
        user_id=payment.user_id,
        buyer_name=buyer_name,
        buyer_phone=buyer_phone or "No registrado",
        buyer_email=buyer_email,
        subtotal=total,
        total=total,
        currency=payment.currency,
        payment_method="paypal",
        payment_status="paid",
        status="paid",
        whatsapp_message=None,
        raw_payment_payload=json.dumps({
            "paypal_order_id": payment.paypal_order_id,
            "membership_payment_id": payment.id,
            "marketplace": marketplace,
            "buyer": buyer,
        }),
        paid_at=datetime.utcnow(),
    )

    db.add(order)
    db.flush()

    order_item = models.EducationOrderItem(
        order_id=order.id,
        resource_id=resource.id,
        resource_title_snapshot=resource.title,
        resource_type_snapshot=resource.resource_type,
        unit_price_snapshot=unit_price,
        quantity=quantity,
        total_snapshot=total,
        access_code=access_code,
    )

    db.add(order_item)
    db.flush()

    view_url = (
        f"https://mayu-wellness-backend-v1.onrender.com"
        f"/education/resources/{resource.id}/view?access_code={access_code}"
    )

    email_sent = False
    email_error = None

    try:
        send_education_access_email(
            to_email=buyer_email,
            buyer_name=buyer_name,
            resource_title=resource.title,
            access_code=access_code,
            view_url=view_url,
        )
        email_sent = True
    except Exception as e:
        email_error = str(e)

    return {
        "education_order_id": order.id,
        "education_order_code": order.order_code,
        "access_code": access_code,
        "view_url": view_url,
        "email_sent": email_sent,
        "email_error": email_error,
        "already_created": False,
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
        "has_resend_api_key": bool(os.getenv("RESEND_API_KEY")),
        "from_email": os.getenv("FROM_EMAIL", "onboarding@resend.dev"),
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

    buyer_email = (payload.buyer_email or user.email or "").strip()
    buyer_name = (payload.buyer_name or user.name or "").strip()
    buyer_phone = (payload.buyer_phone or user.phone or "").strip()

    if item["source"] == "education" and not buyer_email:
        raise HTTPException(
            status_code=400,
            detail="El email es obligatorio para compras de Mayu Educación",
        )

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

    response = paypal_request("POST", "/v2/checkout/orders", token, paypal_body)

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
        payer_email=buyer_email,
        raw_payload=json.dumps({
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
                "name": buyer_name,
                "email": buyer_email,
                "phone": buyer_phone,
            },
        }),
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
        "buyer_name": buyer_name,
        "buyer_phone": buyer_phone,
        "buyer_email": buyer_email,
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

    fulfillment = None

    if payment.status in {"paid", "verified"}:
        fulfillment = fulfill_education_payment_if_needed(payment, db)
        db.commit()
        db.refresh(payment)
        return payment, fulfillment

    token = get_token()

    response = paypal_request(
        "POST",
        f"/v2/checkout/orders/{paypal_order_id}/capture",
        token,
        body={},
    )

    capture_id = None
    paypal_payer_email = None

    try:
        capture_id = response["purchase_units"][0]["payments"]["captures"][0]["id"]
        paypal_payer_email = response.get("payer", {}).get("email_address")
    except Exception:
        pass

    previous_payload = payment.raw_payload

    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    payment.paypal_capture_id = capture_id
    payment.payer_email = payment.payer_email or paypal_payer_email
    payment.raw_payload = json.dumps({
        "previous_payload": previous_payload,
        "capture": response,
    })

    db.flush()

    fulfillment = fulfill_education_payment_if_needed(payment, db)

    db.commit()
    db.refresh(payment)

    return payment, fulfillment


@router.post("/capture-order")
def capture_marketplace_order(
    payload: MarketplacePayPalCaptureRequest,
    db: Session = Depends(get_db),
):
    payment, fulfillment = capture_marketplace_payment(payload.paypal_order_id, db)

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
        "education_fulfillment": fulfillment,
    }


@router.get("/success")
def paypal_marketplace_success(
    token: str,
    db: Session = Depends(get_db),
):
    payment, fulfillment = capture_marketplace_payment(token, db)

    return {
        "status": "paid",
        "message": "Pago marketplace capturado correctamente. Puedes volver a Mayu Wellness Club.",
        "payment_id": payment.id,
        "payment_type": payment.payment_type,
        "paypal_order_id": payment.paypal_order_id,
        "payer_email": payment.payer_email,
        "education_fulfillment": fulfillment,
    }


@router.get("/cancel")
def paypal_marketplace_cancel():
    return {
        "status": "cancelled",
        "message": "Pago marketplace cancelado por el usuario",
    }
