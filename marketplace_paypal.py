import os
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from typing import Optional, List
import secrets
import string

import resend
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
import models
from notification_service import (
    add_tracking_history,
    notify_customer_order,
    safe_send_push_to_roles,
)
from marketplace import (
    build_marketplace_whatsapp_message,
    credit_marketplace_doctor_if_paid,
    sync_marketplace_doctor_wallet_after_commit,
    validate_doctor_prescriber_identifier,
    validate_member_discount_code,
)
from pharmacy_loyalty import (
    credit_marketplace_order_if_paid,
    sync_marketplace_loyalty_wallet_after_commit,
)

router = APIRouter(
    prefix="/payments/paypal/marketplace",
    tags=["PayPal Marketplace"],
)


def get_mayu_app_public_url():
    return os.getenv("MAYU_APP_PUBLIC_URL", "http://127.0.0.1:5186").strip()


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
    user_id: Optional[int] = None
    quantity: int = 1
    currency: str = "USD"
    buyer_name: Optional[str] = None
    buyer_phone: Optional[str] = None
    buyer_email: Optional[str] = None


class MarketplaceCartItemCreate(BaseModel):
    product_id: int
    quantity: int = 1


class MarketplacePayPalCreateCartOrderRequest(BaseModel):
    item_type: str = "pharmacy"
    user_id: int
    buyer_name: Optional[str] = None
    buyer_phone: Optional[str] = None
    buyer_email: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    delivery_notes: Optional[str] = None

    billing_name: Optional[str] = None
    billing_identification: Optional[str] = None
    billing_email: Optional[str] = None
    billing_phone: Optional[str] = None
    billing_address: Optional[str] = None

    discount_code: Optional[str] = None
    pharmacy_loyalty_identifier: Optional[str] = None
    doctor_prescriber_identifier: Optional[str] = None
    currency: str = "USD"
    items: List[MarketplaceCartItemCreate]


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


def generate_marketplace_order_code():
    now = datetime.utcnow()
    random_code = "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6)
    )
    return f"MP-MAYU-{now.strftime('%Y%m%d%H%M%S')}-{random_code}"


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
            db.query(models.MarketplaceProduct)
            .filter(models.MarketplaceProduct.id == item_id)
            .first()
        )

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


def fulfill_pharmacy_payment_if_needed(payment: models.MembershipPayment, db: Session):
    if payment.payment_type != "marketplace_pharmacy":
        return None

    existing_order = (
        db.query(models.MarketplaceOrder)
        .filter(models.MarketplaceOrder.raw_payment_payload.contains(payment.paypal_order_id))
        .first()
    )

    if existing_order:
        loyalty_result = credit_marketplace_order_if_paid(
            db,
            existing_order,
            sync_wallet=False,
        )
        doctor_result = credit_marketplace_doctor_if_paid(
            db,
            existing_order,
            sync_wallet=False,
        )
        return {
            "marketplace_order_id": existing_order.id,
            "marketplace_order_code": existing_order.order_code,
            "loyalty": loyalty_result,
            "doctor_commission": doctor_result,
            "already_created": True,
        }

    original_payload = get_original_payment_payload(payment)
    marketplace = original_payload.get("marketplace", {}) or {}
    buyer = original_payload.get("buyer", {}) or {}
    billing = original_payload.get("billing", {}) or {}

    items_payload = marketplace.get("items") or []

    if not items_payload:
        item_id = marketplace.get("item_id")
        quantity = int(marketplace.get("quantity") or 1)

        if item_id:
            items_payload = [
                {
                    "product_id": item_id,
                    "quantity": quantity,
                }
            ]

    if not items_payload:
        return None

    buyer_name = (buyer.get("name") or "Cliente Mayu").strip()
    buyer_phone = (buyer.get("phone") or "No registrado").strip()
    buyer_email = (buyer.get("email") or payment.payer_email or "").strip()

    city = (buyer.get("city") or "").strip() or None
    address = (buyer.get("address") or "").strip() or None
    delivery_notes = (buyer.get("delivery_notes") or "").strip() or None

    billing_name = (billing.get("name") or buyer_name).strip() or None
    billing_identification = (billing.get("identification") or "").strip() or None
    billing_email = (billing.get("email") or buyer_email).strip() or None
    billing_phone = (billing.get("phone") or buyer_phone).strip() or None
    billing_address = (billing.get("address") or address or "").strip() or None

    subtotal = 0.0
    order_items_data = []

    for item in items_payload:
        product_id = int(item.get("product_id") or item.get("item_id") or 0)
        quantity = int(item.get("quantity") or 1)

        if product_id <= 0:
            raise HTTPException(status_code=400, detail="Producto inválido en carrito")

        if quantity <= 0:
            raise HTTPException(status_code=400, detail="Cantidad inválida en carrito")

        product = (
            db.query(models.MarketplaceProduct)
            .filter(models.MarketplaceProduct.id == product_id)
            .first()
        )

        if not product:
            raise HTTPException(status_code=404, detail=f"Producto {product_id} no encontrado")

        if product.stock < quantity:
            raise HTTPException(status_code=400, detail=f"Stock insuficiente para {product.name}")

        unit_price = float(product.price or 0)
        line_total = round(unit_price * quantity, 2)
        subtotal += line_total

        order_items_data.append({
            "product": product,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total,
        })

    subtotal = round(subtotal, 2)
    discount_code = marketplace.get("discount_code")
    pharmacy_loyalty_identifier = (
        marketplace.get("pharmacy_loyalty_identifier")
        or marketplace.get("mayu_magistral_identifier")
        or marketplace.get("pharmacy_card_code")
    )
    doctor_prescriber_identifier = marketplace.get("doctor_prescriber_identifier")
    discount_percent = float(marketplace.get("discount_percent") or 0)
    discount_amount = float(marketplace.get("discount_amount") or 0)
    total = round(subtotal - discount_amount, 2)

    payment_provider = (getattr(payment, "provider", None) or "paypal").strip() or "paypal"
    payment_reference = (
        getattr(payment, "payment_reference", None)
        or getattr(payment, "paypal_order_id", None)
        or str(payment.id)
    )

    order = models.MarketplaceOrder(
        order_code=generate_marketplace_order_code(),
        user_id=payment.user_id,
        customer_name=buyer_name,
        customer_phone=buyer_phone,
        customer_email=buyer_email,
        city=city,
        address=address,
        delivery_notes=delivery_notes,
        billing_name=billing_name,
        billing_identification=billing_identification,
        billing_email=billing_email,
        billing_phone=billing_phone,
        billing_address=billing_address,
        subtotal=subtotal,
        discount_code=discount_code,
        pharmacy_loyalty_identifier=(
            str(pharmacy_loyalty_identifier).strip()
            if pharmacy_loyalty_identifier and str(pharmacy_loyalty_identifier).strip()
            else None
        ),
        doctor_prescriber_identifier=(
            str(doctor_prescriber_identifier).strip()
            if doctor_prescriber_identifier and str(doctor_prescriber_identifier).strip()
            else None
        ),
        discount_percent=discount_percent,
        discount_amount=discount_amount,
        total=total,
        currency=payment.currency,
        payment_method=payment_provider,
        payment_status="paid",
        status="paid",
        whatsapp_message=None,
        raw_payment_payload=json.dumps({
            "payment_provider": payment_provider,
            "payment_reference": payment_reference,
            "paypal_order_id": payment.paypal_order_id,
            "membership_payment_id": payment.id,
            "marketplace": marketplace,
            "buyer": buyer,
            "billing": billing,
        }),
        paid_at=datetime.utcnow(),
    )

    db.add(order)
    db.flush()

    created_items = []

    for item_data in order_items_data:
        product = item_data["product"]
        quantity = item_data["quantity"]
        unit_price = item_data["unit_price"]
        line_total = item_data["line_total"]

        order_item = models.MarketplaceOrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name_snapshot=product.name,
            unit_price_snapshot=unit_price,
            quantity=quantity,
            total_snapshot=line_total,
        )

        product.stock = product.stock - quantity
        db.add(order_item)

        created_items.append({
            "product_id": product.id,
            "product_name": product.name,
            "quantity": quantity,
            "unit_price": unit_price,
            "total": line_total,
        })

    db.flush()
    order.whatsapp_message = build_marketplace_whatsapp_message(order)
    db.flush()

    add_tracking_history(
        db,
        order,
        "paid",
        "Pago confirmado y pedido recibido por Farmacia Mayu.",
        payment.user_id,
    )
    notify_customer_order(
        db,
        order,
        "Compra confirmada",
        f"Tu pedido {order.order_code} fue recibido por Farmacia Mayu.",
        include_summary=True,
    )
    safe_send_push_to_roles(
        db,
        {"pharmacy_admin", "admin", "superadmin"},
        "Nueva compra Farmacia",
        f"Pedido {order.order_code} pagado por {buyer_name}.",
    )

    loyalty_result = credit_marketplace_order_if_paid(db, order, sync_wallet=False)
    doctor_result = credit_marketplace_doctor_if_paid(db, order, sync_wallet=False)

    return {
        "marketplace_order_id": order.id,
        "marketplace_order_code": order.order_code,
        "items": created_items,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "total": total,
        "loyalty": loyalty_result,
        "doctor_commission": doctor_result,
        "already_created": False,
    }


def fulfill_education_payment_if_needed(payment: models.MembershipPayment, db: Session):
    if payment.payment_type != "marketplace_education":
        return None

    original_payload = get_original_payment_payload(payment)
    marketplace = original_payload.get("marketplace", {}) or {}
    buyer = original_payload.get("buyer", {}) or {}

    items_payload = marketplace.get("items") or []

    if not items_payload:
        item_id = marketplace.get("item_id")
        quantity = int(marketplace.get("quantity") or 1)

        if item_id:
            items_payload = [
                {
                    "resource_id": item_id,
                    "quantity": quantity,
                }
            ]

    if not items_payload:
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

    buyer_name = (buyer.get("name") or "Comprador Mayu Educación").strip()
    buyer_phone = (buyer.get("phone") or "").strip()
    buyer_email = (buyer.get("email") or payment.payer_email or "").strip()

    if not buyer_email:
        raise HTTPException(
            status_code=400,
            detail="No existe email del comprador para enviar el código educativo",
        )

    order_items_data = []
    subtotal = 0.0

    for item in items_payload:
        resource_id = int(
            item.get("resource_id")
            or item.get("product_id")
            or item.get("item_id")
            or 0
        )
        quantity = int(item.get("quantity") or 1)

        if resource_id <= 0:
            raise HTTPException(status_code=400, detail="Contenido educativo inválido")

        if quantity <= 0:
            raise HTTPException(status_code=400, detail="Cantidad inválida")

        resource = (
            db.query(models.EducationResource)
            .filter(models.EducationResource.id == resource_id)
            .filter(models.EducationResource.active == True)
            .first()
        )

        if not resource:
            raise HTTPException(status_code=404, detail=f"Contenido educativo {resource_id} no encontrado")

        unit_price = float(resource.price or 0)
        line_total = round(unit_price * quantity, 2)
        subtotal += line_total

        order_items_data.append({
            "resource": resource,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total,
        })

    total = round(subtotal, 2)

    order = models.EducationOrder(
        order_code=generate_education_order_code(),
        user_id=payment.user_id,
        buyer_name=buyer_name,
        buyer_phone=buyer_phone or "No registrado",
        buyer_email=buyer_email,
        subtotal=total,
        total=total,
        currency=payment.currency,
        payment_method=(getattr(payment, "provider", None) or "paypal"),
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

    public_url = os.getenv(
        "MAYU_APP_PUBLIC_URL",
        "https://mayu-wellness-backend-v1.onrender.com",
    ).rstrip("/")

    created_items = []
    email_results = []

    for item_data in order_items_data:
        resource = item_data["resource"]
        quantity = item_data["quantity"]
        unit_price = item_data["unit_price"]
        line_total = item_data["line_total"]
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

        order_item = models.EducationOrderItem(
            order_id=order.id,
            resource_id=resource.id,
            resource_title_snapshot=resource.title,
            resource_type_snapshot=resource.resource_type,
            unit_price_snapshot=unit_price,
            quantity=quantity,
            total_snapshot=line_total,
            access_code=access_code,
        )

        db.add(order_item)
        db.flush()

        view_url = (
            f"{public_url}"
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

        created_items.append({
            "resource_id": resource.id,
            "resource_title": resource.title,
            "quantity": quantity,
            "unit_price": unit_price,
            "total": line_total,
            "access_code": access_code,
            "view_url": view_url,
        })
        email_results.append({
            "resource_id": resource.id,
            "email_sent": email_sent,
            "email_error": email_error,
        })

    return {
        "education_order_id": order.id,
        "education_order_code": order.order_code,
        "items": created_items,
        "access_code": created_items[0]["access_code"] if created_items else None,
        "view_url": created_items[0]["view_url"] if created_items else None,
        "email_sent": all(item["email_sent"] for item in email_results) if email_results else False,
        "email_results": email_results,
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

    item = get_marketplace_item(db, payload.item_type, payload.item_id)

    user = None

    if payload.user_id:
        user = db.query(models.User).filter(models.User.id == payload.user_id).first()

        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if item["source"] == "pharmacy" and not user:
        raise HTTPException(
            status_code=400,
            detail="El usuario es obligatorio para compras de farmacia",
        )

    public_user_id = int(os.getenv("MAYU_PUBLIC_USER_ID", "1"))
    payment_user_id = user.id if user else public_user_id

    buyer_email = (payload.buyer_email or (user.email if user else "") or "").strip()
    buyer_name = (payload.buyer_name or (user.name if user else "") or "Comprador Mayu").strip()
    buyer_phone = (payload.buyer_phone or (user.phone if user else "") or "").strip()

    if item["source"] == "education":
        if not buyer_name:
            raise HTTPException(status_code=400, detail="El nombre es obligatorio")
        if not buyer_phone:
            raise HTTPException(status_code=400, detail="El teléfono es obligatorio")
        if not buyer_email:
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
            "brand_name": "Mayu Magistral",
            "user_action": "PAY_NOW",
            "return_url": "https://mayu-wellness-backend-v1.onrender.com/payments/paypal/marketplace/success",
            "cancel_url": "https://mayu-wellness-backend-v1.onrender.com/payments/paypal/marketplace/cancel",
        },
    }

    response = paypal_request("POST", "/v2/checkout/orders", token, paypal_body)

    payment = models.MembershipPayment(
        user_id=payment_user_id,
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
                "user_id": payment_user_id,
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


@router.post("/create-cart-order")
def create_marketplace_paypal_cart_order(
    payload: MarketplacePayPalCreateCartOrderRequest,
    db: Session = Depends(get_db),
):
    clean_item_type = payload.item_type.strip().lower()

    if clean_item_type not in {"pharmacy", "education"}:
        raise HTTPException(status_code=400, detail="item_type debe ser pharmacy o education")

    if not payload.items:
        raise HTTPException(status_code=400, detail="El carrito está vacío")

    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    buyer_name = (payload.buyer_name or user.name or "").strip()
    buyer_phone = (payload.buyer_phone or user.phone or "").strip()
    buyer_email = (payload.buyer_email or user.email or "").strip()

    if not buyer_name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")

    if not buyer_phone:
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")

    if clean_item_type == "education" and not buyer_email:
        raise HTTPException(status_code=400, detail="El email es obligatorio para compras de Mayu Educación")

    subtotal = 0.0
    items_data = []

    for item in payload.items:
        if item.quantity <= 0:
            raise HTTPException(status_code=400, detail="La cantidad debe ser mayor a cero")

        if clean_item_type == "education":
            resource = (
                db.query(models.EducationResource)
                .filter(models.EducationResource.id == item.product_id)
                .filter(models.EducationResource.active == True)
                .first()
            )

            if not resource:
                raise HTTPException(status_code=404, detail=f"Contenido educativo {item.product_id} no encontrado")

            unit_price = float(resource.price or 0)
            line_total = round(unit_price * item.quantity, 2)
            subtotal += line_total

            items_data.append({
                "resource_id": resource.id,
                "title": resource.title,
                "quantity": item.quantity,
                "unit_price": unit_price,
                "total": line_total,
            })
            continue

        product = (
            db.query(models.MarketplaceProduct)
            .filter(models.MarketplaceProduct.id == item.product_id)
            .filter(models.MarketplaceProduct.active == True)
            .first()
        )

        if not product:
            raise HTTPException(status_code=404, detail=f"Producto {item.product_id} no encontrado")

        if product.stock < item.quantity:
            raise HTTPException(status_code=400, detail=f"Stock insuficiente para {product.name}")

        unit_price = float(product.price or 0)
        line_total = round(unit_price * item.quantity, 2)
        subtotal += line_total

        items_data.append({
            "product_id": product.id,
            "title": product.name,
            "quantity": item.quantity,
            "unit_price": unit_price,
            "total": line_total,
        })

    subtotal = round(subtotal, 2)
    discount_code = (
        payload.discount_code.strip()
        if clean_item_type == "pharmacy" and payload.discount_code and payload.discount_code.strip()
        else None
    )
    discount_info = validate_member_discount_code(db, discount_code) if clean_item_type == "pharmacy" else None
    doctor_info = (
        validate_doctor_prescriber_identifier(db, payload.doctor_prescriber_identifier)
        if clean_item_type == "pharmacy"
        else None
    )
    discount_percent = 0.0
    discount_amount = 0.0

    if discount_info:
        discount_code = discount_info["discount_code"]
        discount_percent = discount_info["discount_percent"]
        discount_amount = round(subtotal * (discount_percent / 100), 2)

    total = round(subtotal - discount_amount, 2)

    if total <= 0:
        raise HTTPException(status_code=400, detail="El total debe ser mayor a cero")

    token = get_token()

    paypal_body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": payload.currency,
                    "value": f"{total:.2f}",
                },
                "description": (
                    f"Mayu Educación - {len(items_data)} contenidos"
                    if clean_item_type == "education"
                    else f"Mayu Marketplace Farmacia - {len(items_data)} productos"
                ),
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
        payment_type=f"marketplace_{clean_item_type}",
        payment_reference=response["id"],
        payer_email=buyer_email,
        raw_payload=json.dumps({
            "paypal": response,
            "marketplace": {
                "item_type": clean_item_type,
                "items": [
                    {
                        "product_id": item.get("product_id") or item.get("resource_id"),
                        "resource_id": item.get("resource_id"),
                        "title": item["title"],
                        "quantity": item["quantity"],
                        "unit_price": item["unit_price"],
                        "total": item["total"],
                    }
                    for item in items_data
                ],
                "quantity": sum(item["quantity"] for item in items_data),
                "subtotal": subtotal,
                "discount_code": discount_code,
                "pharmacy_loyalty_identifier": (
                    payload.pharmacy_loyalty_identifier.strip()
                    if payload.pharmacy_loyalty_identifier and payload.pharmacy_loyalty_identifier.strip()
                    else None
                ),
                "doctor_prescriber_identifier": (
                    doctor_info["doctor_prescriber_identifier"] if doctor_info else None
                ),
                "discount_percent": discount_percent,
                "discount_amount": discount_amount,
                "total": total,
            },
            "buyer": {
                "user_id": user.id,
                "name": buyer_name,
                "email": buyer_email,
                "phone": buyer_phone,
                "city": payload.city,
                "address": payload.address,
                "delivery_notes": payload.delivery_notes,
            },
            "billing": {
                "name": payload.billing_name,
                "identification": payload.billing_identification,
                "email": payload.billing_email,
                "phone": payload.billing_phone,
                "address": payload.billing_address,
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
        "message": (
            "Orden PayPal carrito educación creada"
            if clean_item_type == "education"
            else "Orden PayPal carrito farmacia creada"
        ),
        "payment_id": payment.id,
        "paypal_order_id": response["id"],
        "item_type": clean_item_type,
        "items": items_data,
        "subtotal": subtotal,
        "discount_code": discount_code,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
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

    education_fulfillment = None
    pharmacy_fulfillment = None

    if payment.status in {"paid", "verified"}:
        education_fulfillment = fulfill_education_payment_if_needed(payment, db)
        pharmacy_fulfillment = fulfill_pharmacy_payment_if_needed(payment, db)
        db.commit()
        db.refresh(payment)
        if pharmacy_fulfillment and pharmacy_fulfillment.get("loyalty"):
            pharmacy_fulfillment["loyalty"] = (
                sync_marketplace_loyalty_wallet_after_commit(
                    db,
                    pharmacy_fulfillment["loyalty"],
                    pharmacy_fulfillment.get("marketplace_order_code"),
                )
            )
        if pharmacy_fulfillment and pharmacy_fulfillment.get("doctor_commission"):
            pharmacy_fulfillment["doctor_commission"] = (
                sync_marketplace_doctor_wallet_after_commit(
                    db,
                    pharmacy_fulfillment["doctor_commission"],
                    pharmacy_fulfillment.get("marketplace_order_code"),
                )
            )
        return payment, education_fulfillment, pharmacy_fulfillment

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

    education_fulfillment = fulfill_education_payment_if_needed(payment, db)
    pharmacy_fulfillment = fulfill_pharmacy_payment_if_needed(payment, db)

    db.commit()
    db.refresh(payment)
    if pharmacy_fulfillment and pharmacy_fulfillment.get("loyalty"):
        pharmacy_fulfillment["loyalty"] = sync_marketplace_loyalty_wallet_after_commit(
            db,
            pharmacy_fulfillment["loyalty"],
            pharmacy_fulfillment.get("marketplace_order_code"),
        )
    if pharmacy_fulfillment and pharmacy_fulfillment.get("doctor_commission"):
        pharmacy_fulfillment["doctor_commission"] = (
            sync_marketplace_doctor_wallet_after_commit(
                db,
                pharmacy_fulfillment["doctor_commission"],
                pharmacy_fulfillment.get("marketplace_order_code"),
            )
        )

    return payment, education_fulfillment, pharmacy_fulfillment


@router.post("/capture-order")
def capture_marketplace_order(
    payload: MarketplacePayPalCaptureRequest,
    db: Session = Depends(get_db),
):
    payment, education_fulfillment, pharmacy_fulfillment = capture_marketplace_payment(
        payload.paypal_order_id,
        db,
    )

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
        "education_fulfillment": education_fulfillment,
        "pharmacy_fulfillment": pharmacy_fulfillment,
    }

@router.get("/success", response_class=HTMLResponse)
def paypal_marketplace_success(
    token: str,
    db: Session = Depends(get_db),
):
    payment, education_fulfillment, pharmacy_fulfillment = capture_marketplace_payment(
        token,
        db,
    )

    if payment.payment_type == "marketplace_education":
        return RedirectResponse(
            url="https://mayuwellnesclub.com/#/education-marketplace?payment=success",
            status_code=302,
        )

    if payment.payment_type == "marketplace_pharmacy":
        app_url = get_mayu_app_public_url().rstrip("/")
        query = {
            "paypal_order_id": payment.paypal_order_id,
            "payment_id": payment.id,
            "payment": "success",
        }
        app_return_url = (
            f"{app_url}/marketplace/paypal-success?{urllib.parse.urlencode(query)}"
        )

        return HTMLResponse(
            content=f"""
            <html>
                <head>
                    <title>Compra Farmacia confirmada</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                </head>
                <body style="font-family: Arial; background:#0f172a; color:white; text-align:center; padding:40px;">
                    <h1>Pago aprobado en PayPal</h1>
                    <p>Estamos regresando a Mayu para cerrar tu compra de Farmacia.</p>
                    <p style="margin-top:28px;">
                        <a href="{app_return_url}" style="background:#14b8a6;color:white;padding:14px 22px;border-radius:10px;text-decoration:none;font-weight:bold;">
                            Volver a Mayu
                        </a>
                    </p>
                    <script>
                        setTimeout(function() {{
                            window.location.href = "{app_return_url}";
                        }}, 1800);
                    </script>
                </body>
            </html>
            """
        )

    return RedirectResponse(
        url="https://mayuwellnesclub.com/?payment=success",
        status_code=302,
    )


@router.get("/cancel")
def paypal_marketplace_cancel():
    return {
        "status": "cancelled",
        "message": "Pago marketplace cancelado por el usuario",
    }
