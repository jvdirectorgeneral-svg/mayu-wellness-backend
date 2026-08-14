import os
import time
import json
import html
from datetime import datetime
from typing import Optional, Literal, List

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
from dependencies import get_current_user
import models
from member_cards import get_or_create_card
from marketplace_paypal import (
    fulfill_education_payment_if_needed,
    fulfill_pharmacy_payment_if_needed,
    get_original_payment_payload,
    resolve_marketplace_buyer_user,
)
from pharmacy_loyalty import sync_marketplace_loyalty_wallet_after_commit
from marketplace import (
    sync_marketplace_doctor_wallet_after_commit,
    validate_doctor_prescriber_identifier,
    validate_member_discount_code,
)
from notification_service import (
    add_tracking_history,
    mayu_email_header,
    safe_send_email,
    safe_send_push_to_roles,
)

router = APIRouter(prefix="/payphone", tags=["payphone"])


PAYPHONE_TOKEN = os.getenv("PAYPHONE_TOKEN")
PAYPHONE_STORE_ID = os.getenv("PAYPHONE_STORE_ID")
PAYPHONE_BASE_URL = os.getenv(
    "PAYPHONE_BASE_URL",
    "https://pay.payphonetodoesposible.com/api",
)
PAYPHONE_RESPONSE_URL = os.getenv(
    "PAYPHONE_RESPONSE_URL",
    "https://mayu-wellness-backend-v1.onrender.com/payphone/response",
)
PAYPHONE_WEB_RETURN_URL = os.getenv(
    "PAYPHONE_WEB_RETURN_URL",
    "https://mayuwellnesclub.com/marketplacefarmaciamayu",
)

DEFAULT_PHARMACY_ORDER_ALERT_EMAILS = (
    "auxfarmaciaquito@gmail.com,"
    "auxiliarcontablefarmacia@gmail.com"
)


def pharmacy_order_product_rows(items: list) -> str:
    return "".join(
        f"<li>{html.escape(str(item.get('title') or 'Producto'))} "
        f"x{int(item.get('quantity') or 0)} — "
        f"${float(item.get('total') or 0):.2f} USD</li>"
        for item in items
    )


def send_pharmacy_order_received_email(
    client_transaction_id: str,
    buyer_name: str,
    buyer_email: str,
    total: float,
    items: list,
    city: Optional[str],
    address: Optional[str],
    doctor_identifier: Optional[str] = None,
    pharmacy_loyalty_identifier: Optional[str] = None,
    wellness_code: Optional[str] = None,
    discount_amount: float = 0,
):
    """Correo comercial para el comprador, sin instrucciones administrativas."""
    if not buyer_email or not buyer_email.strip():
        return {"sent": False, "detail": "Compra sin correo del cliente"}

    product_rows = pharmacy_order_product_rows(items)
    delivery_rows = ""
    if city or address:
        delivery_rows = f"""
        <h3>Datos de entrega</h3>
        <table style="width:100%;border-collapse:collapse">
          <tr><td><strong>Ciudad</strong></td><td>{html.escape(city or '-')}</td></tr>
          <tr><td><strong>Dirección</strong></td><td>{html.escape(address or '-')}</td></tr>
        </table>
        """
    benefit_rows = []
    if doctor_identifier:
        benefit_rows.append(
            "<li><strong>Doctor Prescriptor:</strong> 22% por pago con "
            "PayPhone/tarjeta. Se acredita al doctor cuando Farmacia confirma "
            "el pago; no reduce el total del cliente.</li>"
        )
    if pharmacy_loyalty_identifier:
        benefit_rows.append(
            "<li><strong>Tarjeta de puntos Mayu:</strong> los puntos se acreditan "
            "cuando Farmacia confirma el pago.</li>"
        )
    if wellness_code:
        benefit_rows.append(
            f"<li><strong>Socio Mayu Wellness Club:</strong> descuento del 10% "
            f"aplicado (${float(discount_amount or 0):.2f} USD).</li>"
        )
    benefits_section = (
        "<h3>Beneficios aplicados</h3><ul>" + "".join(benefit_rows) + "</ul>"
        if benefit_rows
        else "<p><strong>Modalidad:</strong> compra directa sin afiliaciones.</p>"
    )
    body = f"""
    <div style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#17201f">
      {mayu_email_header("Compra recibida · Marketplace Mayu")}
      <div style="padding:24px;border:1px solid #d9e4e1;border-top:0">
        <h2 style="margin-top:0">Hemos recibido tu compra</h2>
        <p>Hola {html.escape(buyer_name or 'Cliente')},</p>
        <p>Estamos verificando el pago de tu pedido. Te enviaremos un nuevo correo
        cuando la compra esté confirmada y entre a preparación.</p>
        <table style="width:100%;border-collapse:collapse">
          <tr><td><strong>Pedido</strong></td><td>{html.escape(client_transaction_id)}</td></tr>
          <tr><td><strong>Estado</strong></td><td>Pago en verificación</td></tr>
          <tr><td><strong>Total</strong></td><td>${total:.2f} USD</td></tr>
        </table>
        <h3>Resumen de tu compra</h3><ul>{product_rows}</ul>
        {benefits_section}
        {delivery_rows}
        <p>Cuando el pedido sea enviado recibirás la transportadora, el número de
        guía y el enlace de seguimiento.</p>
        <p>Gracias por comprar en Marketplace Mayu.</p>
      </div>
    </div>
    """
    return safe_send_email(
        buyer_email.strip(),
        f"Recibimos tu compra · {client_transaction_id}",
        body,
    )


def send_pharmacy_payphone_request_alert(
    client_transaction_id: str,
    buyer_name: str,
    buyer_email: str,
    buyer_phone: str,
    total: float,
    items: list,
    doctor_identifier: Optional[str],
):
    recipients = [
        email.strip()
        for email in os.getenv(
            "PHARMACY_ORDER_ALERT_EMAILS",
            DEFAULT_PHARMACY_ORDER_ALERT_EMAILS,
        ).split(",")
        if email.strip()
    ]
    product_rows = pharmacy_order_product_rows(items)
    body = f"""
    <div style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#17201f">
      {mayu_email_header("Nueva solicitud PayPhone · Farmacia")}
      <div style="padding:24px;border:1px solid #d9e4e1;border-top:0">
        <h2 style="margin-top:0">Tienes una compra reciente por verificar</h2>
        <p>Se generó una solicitud PayPhone para Marketplace Farmacia.</p>
        <p><strong>No la confirmes todavía:</strong> primero comprueba que la referencia
        figure efectivamente cobrada en PayPhone Business.</p>
        <table style="width:100%;border-collapse:collapse">
          <tr><td><strong>Referencia</strong></td><td>{html.escape(client_transaction_id)}</td></tr>
          <tr><td><strong>Cliente</strong></td><td>{html.escape(buyer_name)}</td></tr>
          <tr><td><strong>Correo</strong></td><td>{html.escape(buyer_email)}</td></tr>
          <tr><td><strong>Teléfono</strong></td><td>{html.escape(buyer_phone)}</td></tr>
          <tr><td><strong>Total</strong></td><td>${total:.2f} USD</td></tr>
          <tr><td><strong>Doctor</strong></td><td>{html.escape(doctor_identifier or 'No vinculado')}</td></tr>
        </table>
        <h3>Productos</h3><ul>{product_rows}</ul>
        <p>Después de verificar el cobro, ingresa al panel Administrador Farmacia
        y pulsa <strong>Confirmar</strong> sobre esta misma referencia para procesar
        el pedido y enviarlo a Logística.</p>
      </div>
    </div>
    """
    return {
        recipient: safe_send_email(
            recipient,
            f"Compra PayPhone por verificar · {client_transaction_id}",
            body,
        )
        for recipient in recipients
    }


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_payphone_pharmacy_admin(user: models.User):
    if not user or not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario no autorizado")
    if user.role not in {"superadmin", "admin", "pharmacy_admin"}:
        raise HTTPException(status_code=403, detail="Acceso solo para Farmacia Mayu")


class PayphoneCreateLinkRequest(BaseModel):
    amount: float
    description: str
    payment_type: Literal[
        "membership_initial",
        "membership_monthly",
        "marketplace_farmacy",
        "marketplace_education",
    ]
    reference_id: Optional[int] = None
    buyer_email: Optional[str] = None
    buyer_name: Optional[str] = None


class PayphoneMembershipInitialRequest(BaseModel):
    user_id: int
    plan_level: int
    accepted_recurring_debit: bool = True


class PayphoneConfirmRequest(BaseModel):
    id: Optional[int] = None
    clientTransactionId: Optional[str] = None
    transactionId: Optional[str] = None


class PharmacyTestOrdersCleanupRequest(BaseModel):
    confirmation: str


class PayphoneMarketplaceCartItem(BaseModel):
    product_id: int
    quantity: int = 1


class PayphoneMarketplaceCartRequest(BaseModel):
    item_type: str = "pharmacy"
    user_id: Optional[int] = None
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
    items: List[PayphoneMarketplaceCartItem]


def cents(amount: float) -> int:
    return int(round(float(amount) * 100))


def generate_client_transaction_id(prefix: str = "MW") -> str:
    raw = f"{prefix}{int(time.time() * 1000)}"
    return raw[:15]


def payphone_headers():
    if not PAYPHONE_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="PAYPHONE_TOKEN no configurado en Render",
        )

    return {
        "Authorization": f"Bearer {PAYPHONE_TOKEN}",
        "Content-Type": "application/json",
    }


def get_monthly_amount_by_level(level: int) -> float:
    prices = {
        1: 40.00,
        2: 50.00,
        3: 60.00,
    }

    if level not in prices:
        raise HTTPException(status_code=400, detail="Nivel de plan inválido")

    return prices[level]


def get_signup_amount_by_level(level: int) -> float:
    return 5.00


def get_first_payment_amount_by_level(level: int) -> float:
    return get_monthly_amount_by_level(level) + get_signup_amount_by_level(level)


def get_ambassador_commission_amount(level: int) -> float:
    return round(get_monthly_amount_by_level(level) * 0.14, 2)


def safe_set(obj, attr, value):
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def extract_payphone_link(data):
    if isinstance(data, str):
        value = data.strip()
        return value if value.startswith(("http://", "https://")) else None

    if isinstance(data, dict):
        link_keys = {
            "link", "url", "paymentUrl", "payment_url", "approvalUrl",
            "approval_url", "shortUrl", "short_url", "paymentLink", "payment_link",
            "payWithPayPhone", "payWithCard",
        }
        for key in link_keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
                return value.strip()
        for value in data.values():
            nested = extract_payphone_link(value)
            if nested:
                return nested

    if isinstance(data, list):
        for value in data:
            nested = extract_payphone_link(value)
            if nested:
                return nested

    return None


def create_payphone_link(
    amount: float,
    description: str,
    client_transaction_id: str,
    buyer_email: Optional[str] = None,
    buyer_name: Optional[str] = None,
):
    if not PAYPHONE_STORE_ID:
        raise HTTPException(
            status_code=500,
            detail="PAYPHONE_STORE_ID no configurado en Render",
        )

    subtotal = cents(amount)
    safe_reference = (description or "Mayu Wellness Club")[:100]

    body = {
        "amount": subtotal,
        "amountWithoutTax": subtotal,
        "amountWithTax": 0,
        "tax": 0,
        "service": 0,
        "tip": 0,
        "currency": "USD",
        "reference": safe_reference,
        "clientTransactionId": client_transaction_id,
        "storeId": str(PAYPHONE_STORE_ID),
        "additionalData": description,
        "oneTime": True,
        "expireIn": 0,
        "isAmountEditable": False,
    }

    url = f"{PAYPHONE_BASE_URL}/Links"

    try:
        response = requests.post(
            url,
            json=body,
            headers=payphone_headers(),
            timeout=30,
        )
    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error conectando con PayPhone: {str(e)}",
        )

    if response.status_code not in [200, 201]:
        provider_message = response.text.lower()
        if response.status_code in {401, 403} or "no está autorizada" in provider_message:
            raise HTTPException(
                status_code=503,
                detail=(
                    "PayPhone requiere credenciales WEB habilitadas para completar "
                    "el pago y regresar automáticamente a Mayu. Contacta al administrador."
                ),
            )
        raise HTTPException(
            status_code=502,
            detail="PayPhone no pudo iniciar el pago. Intenta nuevamente.",
        )

    try:
        data = response.json()
    except Exception:
        data = response.text

    return {
        "raw": data,
        "link": extract_payphone_link(data),
        "clientTransactionId": client_transaction_id,
        "sent_body": body,
    }


def confirm_payphone_button_transaction(payphone_id: int, client_transaction_id: str):
    try:
        response = requests.post(
            f"{PAYPHONE_BASE_URL}/button/V2/Confirm",
            json={"id": payphone_id, "clientTxId": client_transaction_id},
            headers=payphone_headers(),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Error confirmando PayPhone: {exc}")

    if response.status_code not in [200, 201]:
        provider_message = response.text.lower()
        if response.status_code in {401, 403} or "no está autorizada" in provider_message:
            raise HTTPException(
                status_code=503,
                detail=(
                    "PayPhone requiere credenciales WEB habilitadas para completar "
                    "el pago y regresar automáticamente a Mayu. Contacta al administrador."
                ),
            )
        raise HTTPException(
            status_code=502,
            detail="PayPhone no pudo confirmar la transacción. Intenta nuevamente.",
        )
    try:
        return response.json()
    except Exception:
        raise HTTPException(status_code=502, detail="PayPhone devolvió una confirmación inválida")


def is_payphone_paid(data: dict) -> bool:
    status = str(
        data.get("status")
        or data.get("transactionStatus")
        or data.get("paymentStatus")
        or data.get("state")
        or ""
    ).lower()

    if status in [
        "approved",
        "success",
        "paid",
        "completed",
        "aprobado",
        "aprobada",
        "approved_for_capture",
    ]:
        return True

    return False


def get_plan_for_user(db: Session, user: models.User):
    level = getattr(user, "membership_level", None)

    if not level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene membership_level asignado",
        )

    plan = (
        db.query(models.Plan)
        .filter(
            models.Plan.level == level,
            models.Plan.active == True,
        )
        .first()
    )

    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"No existe plan activo para el nivel {level}",
        )

    return plan


def create_initial_monthly_selection_if_possible(db: Session, user: models.User):
    now = datetime.utcnow()
    plan = get_plan_for_user(db, user)

    existing = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.month == now.month,
            models.MonthlySelection.year == now.year,
        )
        .first()
    )

    if existing:
        existing.plan_id = plan.id
        existing.editable = True
        db.flush()
        return existing

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=now.month,
        year=now.year,
        status="draft",
        editable=True,
    )

    db.add(selection)
    db.flush()

    return selection


def find_local_payment(
    db: Session,
    client_transaction_id: Optional[str] = None,
    transaction_id: Optional[str] = None,
    payphone_id: Optional[int] = None,
):
    payment = None

    if client_transaction_id:
        payment = (
            db.query(models.MembershipPayment)
            .filter(models.MembershipPayment.payment_reference == client_transaction_id)
            .first()
        )

        if not payment:
            payment = (
                db.query(models.MembershipPayment)
                .filter(models.MembershipPayment.paypal_order_id == client_transaction_id)
                .first()
            )

    if not payment and transaction_id:
        payment = (
            db.query(models.MembershipPayment)
            .filter(models.MembershipPayment.payment_reference == transaction_id)
            .first()
        )

    if not payment and payphone_id:
        search_text = str(payphone_id)
        payments = (
            db.query(models.MembershipPayment)
            .filter(models.MembershipPayment.provider == "payphone")
            .order_by(models.MembershipPayment.id.desc())
            .limit(50)
            .all()
        )

        for item in payments:
            if item.raw_payload and search_text in item.raw_payload:
                payment = item
                break

    return payment


def activate_membership_from_payment(
    db: Session,
    payment: models.MembershipPayment,
    payphone_payload: dict,
):
    user = db.query(models.User).filter(models.User.id == payment.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    now = datetime.utcnow()

    payment.status = "verified"
    payment.paid_at = now
    payment.admin_verified = True
    payment.admin_verified_at = now
    payment.raw_payload = json.dumps(payphone_payload)

    payment_plan_level = getattr(payment, "plan_level", None)

    if payment_plan_level:
        user.membership_level = payment_plan_level

    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="No se puede activar membresía porque el usuario no tiene plan asignado",
        )

    user.membership_active = True
    user.is_active = True

    db.flush()

    create_initial_monthly_selection_if_possible(db, user)

    user, card = get_or_create_card(db, user.id)

    db.commit()
    db.refresh(payment)
    db.refresh(user)
    db.refresh(card)

    return {
        "success": True,
        "message": "Primer pago confirmado. Socio activado, selección mensual creada y tarjeta generada.",
        "payment_id": payment.id,
        "payment_status": payment.status,
        "user_id": user.id,
        "membership_active": user.membership_active,
        "membership_level": user.membership_level,
        "member_card_id": card.id,
        "member_code": card.member_code,
        "qr_token": card.qr_token,
        "payphone": payphone_payload,
    }


def process_membership_initial_confirmation(
    payload: PayphoneConfirmRequest,
    db: Session,
):
    client_transaction_id = payload.clientTransactionId
    transaction_id = payload.transactionId
    payphone_id = payload.id

    payment = find_local_payment(
        db=db,
        client_transaction_id=client_transaction_id,
        transaction_id=transaction_id,
        payphone_id=payphone_id,
    )

    if not payment:
        raise HTTPException(
            status_code=404,
            detail="Pago local no encontrado para este identificador",
        )

    if payment.status == "verified":
        user = db.query(models.User).filter(models.User.id == payment.user_id).first()
        card = None

        if user:
            user, card = get_or_create_card(db, user.id)
            db.commit()
            db.refresh(user)
            db.refresh(card)

        return {
            "success": True,
            "message": "El pago ya estaba confirmado anteriormente.",
            "payment_id": payment.id,
            "payment_status": payment.status,
            "user_id": payment.user_id,
            "membership_active": user.membership_active if user else None,
            "membership_level": user.membership_level if user else None,
            "member_card_id": card.id if card else None,
            "member_code": card.member_code if card else None,
            "qr_token": card.qr_token if card else None,
        }

    payphone_payload = {
        "id": payphone_id,
        "clientTransactionId": client_transaction_id,
        "transactionId": transaction_id,
        "source": "manual_confirm_after_link_payment",
        "note": (
            "API Link de PayPhone no retorna confirmación automática al sistema. "
            "Se confirma contra el pago local creado y el comprobante/link pagado."
        ),
    }

    return activate_membership_from_payment(
        db=db,
        payment=payment,
        payphone_payload=payphone_payload,
    )


def build_marketplace_payphone_payment(
    payload: PayphoneMarketplaceCartRequest,
    db: Session,
):
    clean_item_type = payload.item_type.strip().lower()

    if clean_item_type not in {"pharmacy", "education"}:
        raise HTTPException(status_code=400, detail="item_type debe ser pharmacy o education")

    if not payload.items:
        raise HTTPException(status_code=400, detail="El carrito está vacío")

    user = resolve_marketplace_buyer_user(
        db,
        payload.user_id,
        payload.buyer_name,
        payload.buyer_phone,
        payload.buyer_email,
    )

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
        validate_doctor_prescriber_identifier(
            db,
            payload.doctor_prescriber_identifier,
        )
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

    client_transaction_id = generate_client_transaction_id("MP")
    description = (
        f"Mayu Educación - {len(items_data)} contenidos"
        if clean_item_type == "education"
        else f"Mayu Marketplace Farmacia - {len(items_data)} productos"
    )

    payphone_data = create_payphone_link(
        amount=total,
        description=description,
        client_transaction_id=client_transaction_id,
        buyer_email=buyer_email,
        buyer_name=buyer_name,
    )

    payment = models.MembershipPayment(
        user_id=user.id,
        order_id=None,
        amount=total,
        currency=payload.currency,
        status="created",
        provider="payphone",
        payment_type=f"marketplace_{clean_item_type}",
        payment_reference=client_transaction_id,
        payer_email=buyer_email,
        raw_payload=json.dumps({
            "payphone": payphone_data,
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

    safe_set(payment, "paypal_order_id", client_transaction_id)

    db.add(payment)
    db.commit()
    db.refresh(payment)

    pharmacy_alerts = None
    buyer_order_email = None
    if clean_item_type == "pharmacy":
        buyer_order_email = send_pharmacy_order_received_email(
            client_transaction_id=client_transaction_id,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            total=total,
            items=items_data,
            city=payload.city,
            address=payload.address,
            doctor_identifier=(
                doctor_info["doctor_prescriber_identifier"] if doctor_info else None
            ),
            pharmacy_loyalty_identifier=(
                payload.pharmacy_loyalty_identifier.strip()
                if payload.pharmacy_loyalty_identifier
                and payload.pharmacy_loyalty_identifier.strip()
                else None
            ),
            wellness_code=discount_code,
            discount_amount=discount_amount,
        )
        pharmacy_alerts = send_pharmacy_payphone_request_alert(
            client_transaction_id=client_transaction_id,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_phone=buyer_phone,
            total=total,
            items=items_data,
            doctor_identifier=(
                doctor_info["doctor_prescriber_identifier"] if doctor_info else None
            ),
        )

    return {
        "message": (
            "Link PayPhone carrito educación creado"
            if clean_item_type == "education"
            else "Link PayPhone carrito farmacia creado"
        ),
        "payment_id": payment.id,
        "clientTransactionId": client_transaction_id,
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
        "buyer_order_email": buyer_order_email,
        "pharmacy_alerts": pharmacy_alerts,
        "payment_url": payphone_data.get("link"),
        "payphone": payphone_data,
    }


def confirm_marketplace_payphone_payment(
    payload: PayphoneConfirmRequest,
    db: Session,
    approved_by: Optional[int] = None,
):
    payment = find_local_payment(
        db=db,
        client_transaction_id=payload.clientTransactionId,
        transaction_id=payload.transactionId,
        payphone_id=payload.id,
    )

    if not payment:
        raise HTTPException(status_code=404, detail="Pago marketplace PayPhone no encontrado")

    if payment.payment_type not in {"marketplace_pharmacy", "marketplace_education"}:
        raise HTTPException(status_code=400, detail="Este pago no pertenece a Marketplace")

    if payment.status not in ["paid", "verified"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "PayPhone todavía no confirmó el pago. Completa el checkout "
                "en PayPhone y espera la confirmación automática."
            ),
        )

    payphone_payload = {
        "id": payload.id,
        "clientTransactionId": payload.clientTransactionId or payment.payment_reference,
        "transactionId": payload.transactionId,
        "source": "manual_confirm_after_link_payment",
        "note": (
            "Confirmación manual del link PayPhone. "
            "Se procesa el pedido marketplace, puntos y Wallet."
        ),
    }

    pharmacy_fulfillment = fulfill_pharmacy_payment_if_needed(payment, db)
    education_fulfillment = fulfill_education_payment_if_needed(payment, db)

    # Cuando Farmacia confirma manualmente un cobro PayPhone, esa misma acción
    # constituye la aprobación administrativa. La orden debe quedar visible de
    # inmediato en Logística, sin exigir un segundo botón oculto en otro panel.
    logistics_notified = False
    if pharmacy_fulfillment and approved_by:
        order_id = pharmacy_fulfillment.get("marketplace_order_id")
        order = (
            db.query(models.MarketplaceOrder)
            .filter(models.MarketplaceOrder.id == order_id)
            .first()
        )
        if order:
            was_approved = bool(order.admin_verified) and order.status == "admin_approved"
            order.admin_verified = True
            order.admin_verified_at = datetime.utcnow()
            order.admin_verified_by = approved_by
            order.approved_at = order.approved_at or datetime.utcnow()
            order.payment_status = "paid"
            order.status = "admin_approved"
            if not was_approved:
                add_tracking_history(
                    db,
                    order,
                    "admin_approved",
                    "Pago PayPhone verificado por Farmacia. Pedido enviado a Logística.",
                    approved_by,
                )
                safe_send_push_to_roles(
                    db,
                    {"pharmacy_logistics", "logistics"},
                    "Pedido listo para preparar",
                    f"El pedido {order.order_code} fue aprobado por Farmacia.",
                )
                logistics_notified = True

    db.commit()
    db.refresh(payment)

    if pharmacy_fulfillment and pharmacy_fulfillment.get("loyalty"):
        pharmacy_fulfillment["loyalty"] = sync_marketplace_loyalty_wallet_after_commit(
            db,
            pharmacy_fulfillment.get("loyalty"),
            pharmacy_fulfillment.get("marketplace_order_code"),
        )
    if pharmacy_fulfillment and pharmacy_fulfillment.get("doctor_commission"):
        pharmacy_fulfillment["doctor_commission"] = (
            sync_marketplace_doctor_wallet_after_commit(
                db,
                pharmacy_fulfillment.get("doctor_commission"),
                pharmacy_fulfillment.get("marketplace_order_code"),
            )
        )

    return {
        "success": True,
        "message": "Pago PayPhone confirmado y pedido marketplace procesado.",
        "payment_id": payment.id,
        "payment_status": payment.status,
        "payment_type": payment.payment_type,
        "clientTransactionId": payload.clientTransactionId or payment.paypal_order_id,
        "transactionId": payload.transactionId,
        "amount": float(payment.amount or 0),
        "currency": payment.currency,
        "pharmacy_fulfillment": pharmacy_fulfillment,
        "education_fulfillment": education_fulfillment,
        "logistics_notified": logistics_notified,
    }


@router.get("/health")
def payphone_health():
    return {
        "status": "ok",
        "provider": "PayPhone",
        "store_id_configured": bool(PAYPHONE_STORE_ID),
        "token_configured": bool(PAYPHONE_TOKEN),
        "base_url": PAYPHONE_BASE_URL,
        "response_url": PAYPHONE_RESPONSE_URL,
    }


@router.post("/create-link")
def create_payment_link(
    payload: PayphoneCreateLinkRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    client_transaction_id = generate_client_transaction_id("MW")

    data = create_payphone_link(
        amount=payload.amount,
        description=payload.description,
        client_transaction_id=client_transaction_id,
        buyer_email=payload.buyer_email,
        buyer_name=payload.buyer_name,
    )

    return {
        "success": True,
        "payment_type": payload.payment_type,
        "amount": payload.amount,
        "clientTransactionId": client_transaction_id,
        "reference_id": payload.reference_id,
        "payphone": data,
    }


@router.post("/membership/create-initial-payment")
def create_membership_initial_payment(
    payload: PayphoneMembershipInitialRequest,
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if payload.plan_level not in [1, 2, 3]:
        raise HTTPException(status_code=400, detail="Nivel de plan inválido")

    if not payload.accepted_recurring_debit:
        raise HTTPException(
            status_code=400,
            detail="Debe aceptar el acuerdo de afiliación y débito mensual recurrente",
        )

    signup_amount = get_signup_amount_by_level(payload.plan_level)
    monthly_amount = get_monthly_amount_by_level(payload.plan_level)
    first_payment_amount = get_first_payment_amount_by_level(payload.plan_level)
    ambassador_commission = get_ambassador_commission_amount(payload.plan_level)

    client_transaction_id = generate_client_transaction_id("MW")
    description = f"MWC Primer Pago Nivel {payload.plan_level}"

    user.membership_level = payload.plan_level
    safe_set(user, "accepted_recurring_debit", True)
    safe_set(user, "recurring_debit_provider", "payphone")
    safe_set(user, "recurring_debit_accepted_at", datetime.utcnow())
    safe_set(user, "monthly_amount", monthly_amount)

    payphone_data = create_payphone_link(
        amount=first_payment_amount,
        description=description,
        client_transaction_id=client_transaction_id,
        buyer_email=user.email,
        buyer_name=user.name,
    )

    payment = models.MembershipPayment(
        user_id=user.id,
        order_id=None,
        amount=first_payment_amount,
        currency="USD",
        status="created",
        provider="payphone",
        payment_type="membership_initial",
        payment_reference=client_transaction_id,
        raw_payload=json.dumps(payphone_data),
    )

    safe_set(payment, "paypal_order_id", client_transaction_id)
    safe_set(payment, "signup_amount", signup_amount)
    safe_set(payment, "monthly_amount", monthly_amount)
    safe_set(payment, "plan_level", payload.plan_level)
    safe_set(payment, "accepted_recurring_debit", True)

    db.add(payment)
    db.commit()
    db.refresh(payment)

    return {
        "success": True,
        "message": "Link de primer pago PayPhone creado",
        "payment_id": payment.id,
        "user_id": user.id,
        "plan_level": payload.plan_level,
        "signup_amount": signup_amount,
        "monthly_amount": monthly_amount,
        "first_payment_amount": first_payment_amount,
        "ambassador_commission_monthly_14_percent": ambassador_commission,
        "accepted_recurring_debit": True,
        "clientTransactionId": client_transaction_id,
        "payphone": payphone_data,
    }


@router.post("/marketplace/create-cart-order")
def create_marketplace_payphone_cart_order(
    payload: PayphoneMarketplaceCartRequest,
    db: Session = Depends(get_db),
):
    return build_marketplace_payphone_payment(payload, db)


@router.post("/marketplace/confirm-order")
def confirm_marketplace_payphone_order(
    payload: PayphoneConfirmRequest,
    db: Session = Depends(get_db),
):
    return confirm_marketplace_payphone_payment(payload, db)


@router.get("/marketplace/admin/pending-pharmacy")
def list_pending_pharmacy_payphone_payments(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_payphone_pharmacy_admin(current_user)
    payments = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.provider == "payphone",
            models.MembershipPayment.payment_type == "marketplace_pharmacy",
            models.MembershipPayment.status.in_(["created", "pending"]),
        )
        .order_by(models.MembershipPayment.created_at.desc())
        .limit(100)
        .all()
    )
    items = []
    for payment in payments:
        original = get_original_payment_payload(payment)
        buyer = original.get("buyer", {}) or {}
        marketplace = original.get("marketplace", {}) or {}
        items.append({
            "payment_id": payment.id,
            "client_transaction_id": payment.payment_reference or payment.paypal_order_id,
            "created_at": payment.created_at,
            "amount": float(payment.amount or 0),
            "currency": payment.currency,
            "buyer_name": buyer.get("name"),
            "buyer_phone": buyer.get("phone"),
            "buyer_email": buyer.get("email"),
            "doctor_prescriber_identifier": marketplace.get("doctor_prescriber_identifier"),
            "items": marketplace.get("items") or [],
        })
    return {"items": items, "total": len(items)}


@router.delete("/marketplace/admin/test-orders")
def delete_pharmacy_marketplace_test_orders(
    payload: PharmacyTestOrdersCleanupRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_payphone_pharmacy_admin(current_user)
    if payload.confirmation.strip().upper() != "ELIMINAR PEDIDOS FARMACIA":
        raise HTTPException(
            status_code=400,
            detail="Escribe ELIMINAR PEDIDOS FARMACIA para confirmar.",
        )

    order_ids = [row[0] for row in db.query(models.MarketplaceOrder.id).all()]
    deleted_tracking = 0
    deleted_items = 0
    deleted_orders = 0
    detached_points_transactions = 0
    if order_ids:
        detached_points_transactions = (
            db.query(models.PharmacyPointsTransaction)
            .filter(models.PharmacyPointsTransaction.marketplace_order_id.in_(order_ids))
            .update(
                {models.PharmacyPointsTransaction.marketplace_order_id: None},
                synchronize_session=False,
            )
        )
        deleted_tracking = (
            db.query(models.MarketplaceOrderTrackingHistory)
            .filter(models.MarketplaceOrderTrackingHistory.marketplace_order_id.in_(order_ids))
            .delete(synchronize_session=False)
        )
        deleted_items = (
            db.query(models.MarketplaceOrderItem)
            .filter(models.MarketplaceOrderItem.order_id.in_(order_ids))
            .delete(synchronize_session=False)
        )
        deleted_orders = (
            db.query(models.MarketplaceOrder)
            .filter(models.MarketplaceOrder.id.in_(order_ids))
            .delete(synchronize_session=False)
        )

    deleted_payphone_requests = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.provider == "payphone",
            models.MembershipPayment.payment_type == "marketplace_pharmacy",
            models.MembershipPayment.status.in_(["created", "pending"]),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return {
        "message": "Bandejas de Administrador y Logística Farmacia limpiadas.",
        "deleted_orders": deleted_orders,
        "deleted_items": deleted_items,
        "deleted_tracking": deleted_tracking,
        "deleted_payphone_requests": deleted_payphone_requests,
        "detached_points_transactions": detached_points_transactions,
        "financial_balances_preserved": True,
    }


@router.post("/marketplace/admin/payments/{payment_id}/confirm")
def admin_confirm_pharmacy_payphone_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_payphone_pharmacy_admin(current_user)
    payment = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.id == payment_id)
        .first()
    )
    if not payment or payment.payment_type != "marketplace_pharmacy" or payment.provider != "payphone":
        raise HTTPException(status_code=404, detail="Pago PayPhone de Farmacia no encontrado")
    if payment.status not in {"paid", "verified"}:
        payment.status = "paid"
        payment.paid_at = datetime.utcnow()
        payment.admin_verified = True
        payment.admin_verified_at = datetime.utcnow()
        payment.admin_verified_by = current_user.id
        previous_payload = payment.raw_payload
        payment.raw_payload = json.dumps({
            "previous_payload": previous_payload,
            "manual_pharmacy_confirmation": {
                "confirmed_by": current_user.id,
                "confirmed_at": datetime.utcnow().isoformat(),
                "note": "Pago comprobado manualmente en PayPhone Business",
            },
        })
        db.commit()
    return confirm_marketplace_payphone_payment(
        PayphoneConfirmRequest(
            clientTransactionId=payment.payment_reference or payment.paypal_order_id
        ),
        db,
        approved_by=current_user.id,
    )


@router.post("/confirm")
def confirm_payment(
    payload: PayphoneConfirmRequest,
    db: Session = Depends(get_db),
):
    return process_membership_initial_confirmation(payload, db)


@router.post("/membership/confirm-initial-payment")
def confirm_membership_initial_payment(
    payload: PayphoneConfirmRequest,
    db: Session = Depends(get_db),
):
    return process_membership_initial_confirmation(payload, db)


@router.get("/membership/confirm-initial-payment")
def confirm_membership_initial_payment_get(
    id: Optional[int] = None,
    clientTransactionId: Optional[str] = None,
    transactionId: Optional[str] = None,
    db: Session = Depends(get_db),
):
    payload = PayphoneConfirmRequest(
        id=id,
        clientTransactionId=clientTransactionId,
        transactionId=transactionId,
    )

    return process_membership_initial_confirmation(payload, db)


@router.post("/webhook")
async def payphone_webhook(
    payload: dict,
    db: Session = Depends(get_db),
):
    client_transaction_id = (
        payload.get("clientTransactionId")
        or payload.get("client_transaction_id")
        or payload.get("reference")
    )

    if not client_transaction_id:
        return {
            "success": True,
            "message": "Webhook recibido sin clientTransactionId",
            "payload": payload,
        }

    payment = find_local_payment(
        db=db,
        client_transaction_id=client_transaction_id,
        transaction_id=payload.get("transactionId"),
        payphone_id=payload.get("id"),
    )

    if not payment:
        return {
            "success": True,
            "message": "Webhook recibido, pero no se encontró pago local",
            "clientTransactionId": client_transaction_id,
        }

    if is_payphone_paid(payload):
        if payment.payment_type in {"marketplace_pharmacy", "marketplace_education"}:
            previous_payload = payment.raw_payload
            payment.status = "paid"
            payment.paid_at = datetime.utcnow()
            payment.admin_verified = True
            payment.admin_verified_at = datetime.utcnow()
            payment.raw_payload = json.dumps({
                "previous_payload": previous_payload,
                "payphone_webhook": payload,
            })
            if payload.get("transactionId"):
                payment.payment_reference = payload.get("transactionId")
            db.commit()
            confirm_payload = PayphoneConfirmRequest(
                id=payload.get("id"),
                clientTransactionId=client_transaction_id,
                transactionId=payload.get("transactionId"),
            )
            return confirm_marketplace_payphone_payment(confirm_payload, db)

        return activate_membership_from_payment(
            db=db,
            payment=payment,
            payphone_payload=payload,
        )

    previous_payload = payment.raw_payload
    payment.status = "pending"
    payment.raw_payload = json.dumps({
        "previous_payload": previous_payload,
        "payphone_pending": payload,
    })
    db.commit()

    return {
        "success": True,
        "message": "Webhook PayPhone procesado",
        "clientTransactionId": client_transaction_id,
        "paid": False,
    }


@router.get("/response")
def payphone_response(
    id: Optional[int] = None,
    clientTransactionId: Optional[str] = None,
    clientTransactionID: Optional[str] = None,
    transactionId: Optional[str] = None,
    db: Session = Depends(get_db),
):
    clientTransactionId = clientTransactionId or clientTransactionID
    if not id or not clientTransactionId:
        return RedirectResponse(f"{PAYPHONE_WEB_RETURN_URL}?payphone=invalid", status_code=302)

    payment = find_local_payment(
        db=db,
        client_transaction_id=clientTransactionId,
        transaction_id=transactionId,
        payphone_id=id,
    )
    if not payment:
        return RedirectResponse(f"{PAYPHONE_WEB_RETURN_URL}?payphone=not_found", status_code=302)

    confirmation = confirm_payphone_button_transaction(id, clientTransactionId)
    if not is_payphone_paid(confirmation):
        previous_payload = payment.raw_payload
        payment.status = "failed"
        payment.raw_payload = json.dumps({
            "previous_payload": previous_payload,
            "payphone_confirmation": confirmation,
        })
        db.commit()
        return RedirectResponse(f"{PAYPHONE_WEB_RETURN_URL}?payphone=failed", status_code=302)

    previous_payload = payment.raw_payload
    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    payment.admin_verified = True
    payment.admin_verified_at = datetime.utcnow()
    payment.raw_payload = json.dumps({
        "previous_payload": previous_payload,
        "payphone_confirmation": confirmation,
    })
    if confirmation.get("transactionId"):
        payment.payment_reference = str(confirmation.get("transactionId"))
    db.commit()

    if payment.payment_type in {"marketplace_pharmacy", "marketplace_education"}:
        confirm_marketplace_payphone_payment(
            PayphoneConfirmRequest(
                id=id,
                clientTransactionId=clientTransactionId,
                transactionId=str(confirmation.get("transactionId") or transactionId or ""),
            ),
            db,
        )
    else:
        activate_membership_from_payment(db=db, payment=payment, payphone_payload=confirmation)

    return RedirectResponse(f"{PAYPHONE_WEB_RETURN_URL}?payphone=success", status_code=302)
