import os
import time
import json
from datetime import datetime
from typing import Optional, Literal, List

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
from dependencies import get_current_user
import models
from member_cards import get_or_create_card
from marketplace_paypal import (
    fulfill_education_payment_if_needed,
    fulfill_pharmacy_payment_if_needed,
)
from pharmacy_loyalty import sync_marketplace_loyalty_wallet_after_commit
from marketplace import (
    sync_marketplace_doctor_wallet_after_commit,
    validate_doctor_prescriber_identifier,
    validate_member_discount_code,
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


class PayphoneMarketplaceCartItem(BaseModel):
    product_id: int
    quantity: int = 1


class PayphoneMarketplaceCartRequest(BaseModel):
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
        return data

    if isinstance(data, dict):
        return (
            data.get("link")
            or data.get("url")
            or data.get("paymentUrl")
            or data.get("payment_url")
            or data.get("shortUrl")
            or data.get("short_url")
        )

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
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "message": "PayPhone rechazó la creación del link",
                "payphone_response": response.text,
                "sent_body": body,
            },
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

    if data.get("authorizationCode") or data.get("transactionId"):
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
        "payment_url": payphone_data.get("link"),
        "payphone": payphone_data,
    }


def confirm_marketplace_payphone_payment(
    payload: PayphoneConfirmRequest,
    db: Session,
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

    if payment.status not in ["paid", "verified"]:
        previous_payload = payment.raw_payload
        payment.status = "paid"
        payment.paid_at = datetime.utcnow()
        payment.admin_verified = True
        payment.admin_verified_at = datetime.utcnow()
        payment.raw_payload = json.dumps({
            "previous_payload": previous_payload,
            "payphone": payphone_payload,
        })

        if payload.transactionId:
            payment.payment_reference = payload.transactionId

    pharmacy_fulfillment = fulfill_pharmacy_payment_if_needed(payment, db)
    education_fulfillment = fulfill_education_payment_if_needed(payment, db)

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
    id: Optional[str] = None,
    clientTransactionId: Optional[str] = None,
    transactionId: Optional[str] = None,
):
    return {
        "success": True,
        "message": "Respuesta recibida desde PayPhone",
        "id": id,
        "clientTransactionId": clientTransactionId,
        "transactionId": transactionId,
    }
