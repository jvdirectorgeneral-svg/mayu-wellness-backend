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
# CONFIG DINÁMICA
# =========================
def get_paypal_mode() -> str:
    return os.getenv("PAYPAL_MODE", "sandbox").lower().strip()


def get_paypal_client_id() -> Optional[str]:
    value = os.getenv("PAYPAL_CLIENT_ID")
    return value.strip() if value else None


def get_paypal_client_secret() -> Optional[str]:
    value = os.getenv("PAYPAL_CLIENT_SECRET")
    return value.strip() if value else None


def get_paypal_webhook_id() -> Optional[str]:
    value = os.getenv("PAYPAL_WEBHOOK_ID")
    return value.strip() if value else None


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
    payment_type: str = "signup"   # signup / monthly
    order_id: Optional[int] = None
    month: Optional[int] = None
    year: Optional[int] = None


class PayPalCaptureOrderRequest(BaseModel):
    paypal_order_id: str


class AdminVerifyPaymentRequest(BaseModel):
    verification_notes: Optional[str] = None


# =========================
# HELPERS
# =========================
def require_admin_or_superadmin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"admin", "superadmin"}:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para admin o superadmin"
        )


def require_team_access(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"admin", "superadmin", "supervisor", "logistics"}:
        raise HTTPException(status_code=403, detail="Acceso no autorizado")


def require_paypal_config():
    client_id = get_paypal_client_id()
    client_secret = get_paypal_client_secret()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Faltan PAYPAL_CLIENT_ID o PAYPAL_CLIENT_SECRET en variables de entorno"
        )


def paypal_request(
    method: str,
    path: str,
    token: str,
    body: Optional[dict] = None,
    extra_headers: Optional[dict] = None,
):
    url = f"{get_paypal_base_url()}{path}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    if extra_headers:
        headers.update(extra_headers)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url=url,
        data=data,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urllib.request.urlopen(req) as response:
            response_body = response.read().decode("utf-8")
            return response.status, json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") if e.fp else ""
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"detail": raw or "Error HTTP PayPal"}
        return e.code, parsed
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al conectar con PayPal: {str(e)}"
        )


def get_paypal_access_token() -> str:
    require_paypal_config()

    client_id = get_paypal_client_id()
    client_secret = get_paypal_client_secret()

    token_url = f"{get_paypal_base_url()}/v1/oauth2/token"
    auth_bytes = f"{client_id}:{client_secret}".encode("utf-8")
    auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")

    data = "grant_type=client_credentials".encode("utf-8")
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    req = urllib.request.Request(
        url=token_url,
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as response:
            payload = json.loads(response.read().decode("utf-8"))
            access_token = payload.get("access_token")
            if not access_token:
                raise HTTPException(
                    status_code=500,
                    detail="No se pudo obtener access_token de PayPal"
                )
            return access_token
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") if e.fp else ""
        raise HTTPException(status_code=500, detail=f"Error OAuth PayPal: {raw}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error OAuth PayPal: {str(e)}")


def extract_capture_data(capture_response: dict):
    purchase_units = capture_response.get("purchase_units", [])
    if not purchase_units:
        return None, None, None

    payments = purchase_units[0].get("payments", {})
    captures = payments.get("captures", [])
    if not captures:
        return None, None, None

    capture = captures[0]
    capture_id = capture.get("id")
    payer_email = capture_response.get("payer", {}).get("email_address")
    amount = capture.get("amount", {}).get("value")

    return capture_id, payer_email, amount


# =========================
# DEBUG CONFIG
# =========================
@router.get("/debug-config")
def debug_paypal_config():
    client_id = get_paypal_client_id()
    client_secret = get_paypal_client_secret()

    return {
        "paypal_mode": get_paypal_mode(),
        "paypal_base_url": get_paypal_base_url(),
        "has_client_id": bool(client_id),
        "has_client_secret": bool(client_secret),
        "client_id_prefix": client_id[:12] if client_id else None,
        "client_secret_length": len(client_secret) if client_secret else 0,
    }


# =========================
# CREATE PAYPAL ORDER
# =========================
@router.post("/create-order")
def create_paypal_order(
    payload: PayPalCreateOrderRequest,
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if payload.order_id is not None:
        linked_order = db.query(models.Order).filter(models.Order.id == payload.order_id).first()
        if not linked_order:
            raise HTTPException(status_code=404, detail="Orden interna no encontrada")
    else:
        linked_order = None

    token = get_paypal_access_token()

    paypal_body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": f"user-{payload.user_id}",
                "amount": {
                    "currency_code": payload.currency,
                    "value": f"{payload.amount:.2f}",
                },
                "description": f"Mayu Wellness Club - {payload.payment_type}",
            }
        ],
        "application_context": {
            "brand_name": "Mayu Wellness Club",
            "user_action": "PAY_NOW",
        }
    }

    status_code, paypal_response = paypal_request(
        method="POST",
        path="/v2/checkout/orders",
        token=token,
        body=paypal_body,
    )

    if status_code not in {200, 201}:
        raise HTTPException(status_code=500, detail=paypal_response)

    paypal_order_id = paypal_response.get("id")
    if not paypal_order_id:
        raise HTTPException(status_code=500, detail="PayPal no devolvió paypal_order_id")

    new_payment = models.MembershipPayment(
        user_id=user.id,
        order_id=linked_order.id if linked_order else None,
        payment_type=payload.payment_type,
        provider="paypal",
        paypal_order_id=paypal_order_id,
        amount=payload.amount,
        currency=payload.currency,
        status="created",
        payment_reference=paypal_order_id,
        raw_payload=json.dumps(paypal_response),
    )

    db.add(new_payment)
    db.commit()
    db.refresh(new_payment)

    approval_url = None
    for link in paypal_response.get("links", []):
        if link.get("rel") == "approve":
            approval_url = link.get("href")
            break

    return {
        "message": "Orden PayPal creada correctamente",
        "payment": {
            "id": new_payment.id,
            "user_id": new_payment.user_id,
            "order_id": new_payment.order_id,
            "payment_type": new_payment.payment_type,
            "provider": new_payment.provider,
            "paypal_order_id": new_payment.paypal_order_id,
            "amount": new_payment.amount,
            "currency": new_payment.currency,
            "status": new_payment.status,
            "approval_url": approval_url,
        }
    }


# =========================
# CAPTURE PAYPAL ORDER
# =========================
@router.post("/capture-order")
def capture_paypal_order(
    payload: PayPalCaptureOrderRequest,
    db: Session = Depends(get_db)
):
    payment = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.paypal_order_id == payload.paypal_order_id)
        .first()
    )

    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado para ese paypal_order_id")

    token = get_paypal_access_token()

    status_code, capture_response = paypal_request(
        method="POST",
        path=f"/v2/checkout/orders/{payload.paypal_order_id}/capture",
        token=token,
        body={}
    )

    if status_code not in {200, 201}:
        payment.status = "failed"
        payment.raw_payload = json.dumps(capture_response)
        db.commit()
        raise HTTPException(status_code=500, detail=capture_response)

    capture_id, payer_email, captured_amount = extract_capture_data(capture_response)

    payment.paypal_capture_id = capture_id
    payment.payer_email = payer_email
    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    payment.raw_payload = json.dumps(capture_response)
    payment.receipt_url = payload.paypal_order_id

    if captured_amount is not None:
        try:
            payment.amount = float(captured_amount)
        except Exception:
            pass

    db.commit()
    db.refresh(payment)

    return {
        "message": "Pago PayPal capturado correctamente",
        "payment": {
            "id": payment.id,
            "user_id": payment.user_id,
            "order_id": payment.order_id,
            "paypal_order_id": payment.paypal_order_id,
            "paypal_capture_id": payment.paypal_capture_id,
            "payer_email": payment.payer_email,
            "amount": payment.amount,
            "currency": payment.currency,
            "status": payment.status,
            "paid_at": payment.paid_at,
        }
    }


# =========================
# LIST PAYMENTS
# =========================
@router.get("")
def list_membership_payments(
    status: Optional[str] = Query(default=None),
    payment_type: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_team_access(current_user)

    query = db.query(models.MembershipPayment)

    if status:
        query = query.filter(models.MembershipPayment.status == status)

    if payment_type:
        query = query.filter(models.MembershipPayment.payment_type == payment_type)

    payments = query.order_by(models.MembershipPayment.created_at.desc()).all()

    return {
        "items": [
            {
                "id": payment.id,
                "user_id": payment.user_id,
                "user_name": payment.user.name if payment.user else None,
                "order_id": payment.order_id,
                "payment_type": payment.payment_type,
                "provider": payment.provider,
                "paypal_order_id": payment.paypal_order_id,
                "paypal_capture_id": payment.paypal_capture_id,
                "amount": payment.amount,
                "currency": payment.currency,
                "status": payment.status,
                "payer_email": payment.payer_email,
                "payment_reference": payment.payment_reference,
                "receipt_url": payment.receipt_url,
                "admin_verified": payment.admin_verified,
                "admin_verified_at": payment.admin_verified_at,
                "admin_verified_by": payment.admin_verified_by,
                "created_at": payment.created_at,
                "paid_at": payment.paid_at,
            }
            for payment in payments
        ]
    }


# =========================
# VERIFY PAYMENT BY ADMIN
# =========================
@router.put("/{payment_id}/verify")
def verify_payment_by_admin(
    payment_id: int,
    payload: AdminVerifyPaymentRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_admin_or_superadmin(current_user)

    payment = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.id == payment_id)
        .first()
    )

    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    if payment.status != "paid":
        raise HTTPException(
            status_code=400,
            detail="Solo se pueden verificar pagos que ya estén capturados como paid"
        )

    payment.admin_verified = True
    payment.admin_verified_at = datetime.utcnow()
    payment.admin_verified_by = current_user.id
    payment.status = "verified"

    if payment.order_id:
        order = db.query(models.Order).filter(models.Order.id == payment.order_id).first()
        if order and order.status == "pending_payment_review":
            order.logistics_notes = (
                payload.verification_notes.strip()
                if payload.verification_notes and payload.verification_notes.strip()
                else "Pago OK - pendiente de liberación a despacho"
            )

    db.commit()
    db.refresh(payment)

    return {
        "message": "Pago verificado correctamente por admin",
        "payment": {
            "id": payment.id,
            "status": payment.status,
            "admin_verified": payment.admin_verified,
            "admin_verified_at": payment.admin_verified_at,
            "admin_verified_by": payment.admin_verified_by,
        }
    }


# =========================
# PAYPAL WEBHOOK
# =========================
@router.post("/webhook")
async def paypal_webhook_listener(
    request: Request,
    db: Session = Depends(get_db)
):
    raw_body = await request.body()

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Payload inválido")

    webhook_id = get_paypal_webhook_id()

    if webhook_id:
        token = get_paypal_access_token()

        verification_body = {
            "auth_algo": request.headers.get("PAYPAL-AUTH-ALGO"),
            "cert_url": request.headers.get("PAYPAL-CERT-URL"),
            "transmission_id": request.headers.get("PAYPAL-TRANSMISSION-ID"),
            "transmission_sig": request.headers.get("PAYPAL-TRANSMISSION-SIG"),
            "transmission_time": request.headers.get("PAYPAL-TRANSMISSION-TIME"),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }

        status_code, verify_response = paypal_request(
            method="POST",
            path="/v1/notifications/verify-webhook-signature",
            token=token,
            body=verification_body,
        )

        if status_code not in {200, 201}:
            raise HTTPException(status_code=400, detail="No se pudo verificar webhook PayPal")

        verification_status = verify_response.get("verification_status")
        if verification_status != "SUCCESS":
            raise HTTPException(status_code=400, detail="Firma de webhook inválida")

    event_type = event.get("event_type")
    resource = event.get("resource", {})

    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        capture_id = resource.get("id")
        supplementary = resource.get("supplementary_data", {})
        related_ids = supplementary.get("related_ids", {})
        paypal_order_id = related_ids.get("order_id")

        if paypal_order_id:
            payment = (
                db.query(models.MembershipPayment)
                .filter(models.MembershipPayment.paypal_order_id == paypal_order_id)
                .first()
            )

            if payment:
                payment.paypal_capture_id = capture_id
                payment.status = "paid"
                payment.paid_at = datetime.utcnow()
                payment.payer_email = (
                    resource.get("payer", {}).get("email_address")
                    if isinstance(resource.get("payer"), dict)
                    else payment.payer_email
                )
                payment.raw_payload = json.dumps(event)

                amount_info = resource.get("amount", {})
                if amount_info.get("value"):
                    try:
                        payment.amount = float(amount_info["value"])
                    except Exception:
                        pass

                db.commit()

    return {
        "message": "Webhook recibido correctamente",
        "event_type": event_type
    }
