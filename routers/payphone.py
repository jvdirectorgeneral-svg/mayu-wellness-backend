import os
import time
import uuid
from typing import Optional, Literal

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import SessionLocal
from dependencies import get_current_user
from models import User

router = APIRouter(prefix="/payphone", tags=["payphone"])


PAYPHONE_TOKEN = os.getenv("PAYPHONE_TOKEN")
PAYPHONE_STORE_ID = os.getenv("PAYPHONE_STORE_ID")
PAYPHONE_BASE_URL = os.getenv(
    "PAYPHONE_BASE_URL",
    "https://pay.payphonetodoesposible.com/api",
)
PAYPHONE_RESPONSE_URL = os.getenv(
    "PAYPHONE_RESPONSE_URL",
    "https://mayuwellnesclub.com/payphone/response",
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


class PayphoneConfirmRequest(BaseModel):
    id: Optional[int] = None
    clientTransactionId: Optional[str] = None
    transactionId: Optional[str] = None


def cents(amount: float) -> int:
    return int(round(amount * 100))


def generate_client_transaction_id(prefix: str = "MWC") -> str:
    """
    PayPhone suele pedir clientTransactionId corto y único.
    Máximo recomendado: 15 caracteres.
    """
    raw = f"{prefix}{int(time.time())}"
    return raw[-15:]


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


@router.get("/health")
def payphone_health():
    return {
        "status": "ok",
        "provider": "PayPhone",
        "store_id_configured": bool(PAYPHONE_STORE_ID),
        "token_configured": bool(PAYPHONE_TOKEN),
        "base_url": PAYPHONE_BASE_URL,
    }


@router.post("/create-link")
def create_payment_link(
    payload: PayphoneCreateLinkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Crea un link de pago PayPhone para:
    - Primer pago socio
    - Pago mensual socio
    - Marketplace Farmacia
    - Marketplace Educación
    """

    if not PAYPHONE_STORE_ID:
        raise HTTPException(
            status_code=500,
            detail="PAYPHONE_STORE_ID no configurado en Render",
        )

    client_transaction_id = generate_client_transaction_id("MWC")

    subtotal = cents(payload.amount)

    body = {
        "amount": subtotal,
        "amountWithoutTax": subtotal,
        "tax": 0,
        "clientTransactionId": client_transaction_id,
        "storeId": PAYPHONE_STORE_ID,
        "currency": "USD",
        "reference": payload.description,
        "responseUrl": PAYPHONE_RESPONSE_URL,
    }

    if payload.buyer_email:
        body["email"] = payload.buyer_email

    if payload.buyer_name:
        body["clientName"] = payload.buyer_name

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
            },
        )

    data = response.json()

    return {
        "success": True,
        "payment_type": payload.payment_type,
        "amount": payload.amount,
        "clientTransactionId": client_transaction_id,
        "reference_id": payload.reference_id,
        "payphone": data,
    }


@router.post("/confirm")
def confirm_payment(
    payload: PayphoneConfirmRequest,
    db: Session = Depends(get_db),
):
    """
    Confirma una transacción PayPhone.
    Este endpoint se usará después del retorno de PayPhone o webhook.
    """

    if not payload.id and not payload.clientTransactionId and not payload.transactionId:
        raise HTTPException(
            status_code=400,
            detail="Debes enviar id, transactionId o clientTransactionId",
        )

    body = {}

    if payload.id:
        body["id"] = payload.id

    if payload.clientTransactionId:
        body["clientTransactionId"] = payload.clientTransactionId

    if payload.transactionId:
        body["transactionId"] = payload.transactionId

    url = f"{PAYPHONE_BASE_URL}/Sale"

    try:
        response = requests.get(
            url,
            params=body,
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

    data = response.json()

    return {
        "success": True,
        "confirmed": True,
        "payphone": data,
    }


@router.post("/webhook")
async def payphone_webhook(
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    Notificación externa de PayPhone.

    Aquí luego conectamos:
    - Activar membresía
    - Crear orden farmacia
    - Generar código educativo
    - Enviar email
    """

    return {
        "success": True,
        "message": "Webhook PayPhone recibido",
        "payload": payload,
    }


@router.get("/response")
def payphone_response(
    id: Optional[str] = None,
    clientTransactionId: Optional[str] = None,
    transactionId: Optional[str] = None,
):
    """
    URL de retorno después del pago.
    Esta ruta permite recibir al usuario luego de pagar.
    """

    return {
        "success": True,
        "message": "Respuesta recibida desde PayPhone",
        "id": id,
        "clientTransactionId": clientTransactionId,
        "transactionId": transactionId,
    }
