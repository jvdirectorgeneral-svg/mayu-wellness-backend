import os
import time
import json
from datetime import datetime
from typing import Optional, Literal

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
from dependencies import get_current_user
import models
from member_cards import get_or_create_card

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
    accepted_recurring_debit: bool = False


class PayphoneConfirmRequest(BaseModel):
    id: Optional[int] = None
    clientTransactionId: Optional[str] = None
    transactionId: Optional[str] = None


def cents(amount: float) -> int:
    return int(round(float(amount) * 100))


def generate_client_transaction_id(prefix: str = "MW") -> str:
    raw = f"{prefix}{int(time.time())}"
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
    safe_reference = (description or "Mayu Wellness Club")[:50]

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
        return response.json()
    except Exception:
        return response.text


def confirm_payphone_transaction(
    id: Optional[int] = None,
    clientTransactionId: Optional[str] = None,
    transactionId: Optional[str] = None,
):
    if not id and not clientTransactionId and not transactionId:
        raise HTTPException(
            status_code=400,
            detail="Debes enviar id, transactionId o clientTransactionId",
        )

    params = {}

    if id:
        params["id"] = id

    if clientTransactionId:
        params["clientTransactionId"] = clientTransactionId

    if transactionId:
        params["transactionId"] = transactionId

    url = f"{PAYPHONE_BASE_URL}/Sale"

    try:
        response = requests.get(
            url,
            params=params,
            headers=payphone_headers(),
            timeout=30,
        )
    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Error confirmando con PayPhone: {str(e)}",
        )

    if response.status_code not in [200, 201]:
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "message": "No se pudo confirmar el pago en PayPhone",
                "payphone_response": response.text,
            },
        )

    return response.json()


def is_payphone_paid(data: dict) -> bool:
    status = str(
        data.get("status")
        or data.get("transactionStatus")
        or data.get("paymentStatus")
        or ""
    ).lower()

    if status in ["approved", "success", "paid", "completed", "aprobado"]:
        return True

    if data.get("authorizationCode") or data.get("transactionId"):
        return True

    return False


def create_initial_monthly_selection_if_possible(db: Session, user: models.User):
    now = datetime.utcnow()

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
        return existing

    selection = models.MonthlySelection(
        user_id=user.id,
        month=now.month,
        year=now.year,
        status="draft",
        editable=True,
    )

    db.add(selection)
    db.flush()

    return selection


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


@router.post("/confirm")
def confirm_payment(
    payload: PayphoneConfirmRequest,
    db: Session = Depends(get_db),
):
    data = confirm_payphone_transaction(
        id=payload.id,
        clientTransactionId=payload.clientTransactionId,
        transactionId=payload.transactionId,
    )

    return {
        "success": True,
        "confirmed": True,
        "paid": is_payphone_paid(data),
        "payphone": data,
    }


@router.post("/membership/confirm-initial-payment")
def confirm_membership_initial_payment(
    payload: PayphoneConfirmRequest,
    db: Session = Depends(get_db),
):
    data = confirm_payphone_transaction(
        id=payload.id,
        clientTransactionId=payload.clientTransactionId,
        transactionId=payload.transactionId,
    )

    client_transaction_id = (
        payload.clientTransactionId
        or str(data.get("clientTransactionId") or "")
        or str(data.get("client_transaction_id") or "")
    )

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

    if not payment:
        raise HTTPException(
            status_code=404,
            detail="Pago local no encontrado para este clientTransactionId",
        )

    user = db.query(models.User).filter(models.User.id == payment.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    paid = is_payphone_paid(data)

    if not paid:
        payment.status = "pending"
        payment.raw_payload = json.dumps(data)
        db.commit()

        return {
            "success": False,
            "message": "PayPhone todavía no reporta pago aprobado",
            "payment_id": payment.id,
            "payment_status": payment.status,
            "payphone": data,
        }

    now = datetime.utcnow()

    payment.status = "verified"
    payment.paid_at = now
    payment.admin_verified = True
    payment.admin_verified_at = now
    payment.raw_payload = json.dumps(data)

    user.membership_active = True
    user.is_active = True

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
        "payphone": data,
    }


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

    payment = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.payment_reference == client_transaction_id)
        .first()
    )

    if not payment:
        return {
            "success": True,
            "message": "Webhook recibido, pero no se encontró pago local",
            "clientTransactionId": client_transaction_id,
        }

    user = db.query(models.User).filter(models.User.id == payment.user_id).first()
    paid = is_payphone_paid(payload)

    if paid:
        now = datetime.utcnow()

        payment.status = "verified"
        payment.paid_at = now
        payment.admin_verified = True
        payment.admin_verified_at = now
        payment.raw_payload = json.dumps(payload)

        card_id = None

        if user:
            user.membership_active = True
            user.is_active = True
            create_initial_monthly_selection_if_possible(db, user)
            user, card = get_or_create_card(db, user.id)
            card_id = card.id

        db.commit()

        return {
            "success": True,
            "message": "Webhook PayPhone procesado. Socio activado.",
            "clientTransactionId": client_transaction_id,
            "paid": True,
            "card_id": card_id,
        }

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
