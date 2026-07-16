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
    amount: Optional[float] = None
    currency: str = "USD"
    order_id: Optional[int] = None
    plan_level: Optional[int] = None


class PayPalCaptureOrderRequest(BaseModel):
    paypal_order_id: str


class AdminVerifyPaymentRequest(BaseModel):
    verification_notes: Optional[str] = None


def require_admin(user):
    if not user or user.role not in ["admin", "superadmin"]:
        raise HTTPException(status_code=403, detail="Acceso solo admin")


def generate_order_code(user_id: int, month: int, year: int):
    return f"MWC-{year}{month:02d}-U{user_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


def safe_set(obj, attr, value):
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def order_is_locked(order):
    if not order:
        return False

    return order.status in {
        "approved_for_logistics",
        "preparing",
        "shipped",
        "delivered",
        "cancelled",
    }


def add_tracking_history(
    db: Session,
    order: models.Order,
    current_user: models.User,
    status: str,
    note: Optional[str] = None,
):
    history = models.OrderTrackingHistory(
        order_id=order.id,
        status=status,
        note=note,
        carrier=getattr(order, "carrier", None),
        tracking_number=getattr(order, "tracking_number", None),
        tracking_url=getattr(order, "tracking_url", None),
        created_by=current_user.id if current_user else None,
    )
    db.add(history)


def get_monthly_amount_by_level(level: int) -> float:
    prices = {
        1: 40.00,
        2: 50.00,
        3: 60.00,
    }

    if level not in prices:
        raise HTTPException(status_code=400, detail="Nivel de plan inválido")

    return prices[level]


def get_iva_rate() -> float:
    return 0.12


def get_signup_fee() -> float:
    return 5.00


def with_iva(amount: float) -> float:
    return round(float(amount) * (1 + get_iva_rate()), 2)


def get_monthly_amount_with_iva_by_level(level: int) -> float:
    return with_iva(get_monthly_amount_by_level(level))


def get_signup_fee_with_iva() -> float:
    return with_iva(get_signup_fee())


def get_first_payment_amount_by_level(level: int) -> float:
    return round(
        get_monthly_amount_with_iva_by_level(level) + get_signup_fee_with_iva(),
        2,
    )


def infer_level_from_amount(amount: Optional[float]) -> Optional[int]:
    if amount is None:
        return None

    rounded = round(float(amount), 2)

    if rounded in [50.40, 45.00, 40.00]:
        return 1

    if rounded in [61.60, 55.00, 50.00]:
        return 2

    if rounded in [72.80, 65.00, 60.00]:
        return 3

    return None


def resolve_plan_level(payload: PayPalCreateOrderRequest, user: models.User) -> int:
    if payload.plan_level is not None:
        return int(payload.plan_level)

    if user.membership_level is not None:
        return int(user.membership_level)

    inferred = infer_level_from_amount(payload.amount)

    if inferred is not None:
        return inferred

    raise HTTPException(
        status_code=400,
        detail="Debe enviarse plan_level para calcular el primer pago",
    )


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


def get_monthly_selection(db: Session, user_id: int, month: int, year: int):
    return (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user_id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )


def get_best_selection_for_payment(db: Session, user: models.User):
    selections = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.status.in_(["confirmed", "draft"]),
        )
        .order_by(
            models.MonthlySelection.year.asc(),
            models.MonthlySelection.month.asc(),
            models.MonthlySelection.id.asc(),
        )
        .all()
    )

    for selection in selections:
        if not selection.items:
            continue

        existing_order = (
            db.query(models.Order)
            .filter(
                models.Order.user_id == user.id,
                models.Order.month == selection.month,
                models.Order.year == selection.year,
            )
            .first()
        )

        if not order_is_locked(existing_order):
            return selection

    now = datetime.utcnow()
    return get_monthly_selection(db, user.id, now.month, now.year)


def get_or_create_order_for_payment(
    db: Session,
    user: models.User,
    payment: models.MembershipPayment,
    current_user: models.User,
):
    if payment.order_id:
        order = db.query(models.Order).filter(models.Order.id == payment.order_id).first()
        if order:
            return order

    monthly = get_best_selection_for_payment(db, user)

    if not monthly:
        raise HTTPException(
            status_code=400,
            detail="No existe selección mensual para este socio. Primero debe guardar sus productos.",
        )

    if not monthly.items:
        raise HTTPException(
            status_code=400,
            detail="La selección mensual no tiene productos. No se puede crear orden para logística.",
        )

    existing_order = (
        db.query(models.Order)
        .filter(
            models.Order.user_id == user.id,
            models.Order.month == monthly.month,
            models.Order.year == monthly.year,
        )
        .first()
    )

    if existing_order:
        payment.order_id = existing_order.id
        monthly.editable = False
        monthly.status = "confirmed"
        return existing_order

    order = models.Order(
        order_code=generate_order_code(user.id, monthly.month, monthly.year),
        user_id=user.id,
        month=monthly.month,
        year=monthly.year,
        membership_level_snapshot=user.membership_level,
        user_status_snapshot="active",
        city_snapshot=user.city,
        address_snapshot=user.address,
        reference_snapshot=user.reference,
        delivery_notes_snapshot=user.delivery_notes,
        status="approved_for_logistics",
        logistics_notes="✔ Pago verificado - listo para despacho",
    )

    safe_set(order, "shipping_batch_date", None)

    db.add(order)
    db.flush()

    for item in monthly.items:
        product = db.query(models.Product).filter(
            models.Product.id == item.product_id
        ).first()

        if product:
            db.add(
                models.OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    product_name_snapshot=product.name,
                    quantity=item.quantity,
                )
            )

    monthly.editable = False
    monthly.status = "confirmed"
    payment.order_id = order.id

    add_tracking_history(
        db=db,
        order=order,
        current_user=current_user,
        status="approved_for_logistics",
        note="✔ Pago verificado - orden creada y lista para despacho",
    )

    return order


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


@router.get("")
def list_payments(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    require_admin(current_user)

    payments = (
        db.query(models.MembershipPayment)
        .order_by(models.MembershipPayment.created_at.desc())
        .all()
    )

    return {
        "items": [
            {
                "id": p.id,
                "user_id": p.user_id,
                "user_name": p.user.name if p.user else None,
                "order_id": p.order_id,
                "payment_type": p.payment_type,
                "provider": p.provider,
                "paypal_order_id": p.paypal_order_id,
                "paypal_capture_id": p.paypal_capture_id,
                "amount": p.amount,
                "currency": p.currency,
                "status": p.status,
                "payer_email": p.payer_email,
                "admin_verified": p.admin_verified,
                "admin_verified_at": p.admin_verified_at,
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
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    plan_level = resolve_plan_level(payload, user)

    first_payment_amount = get_first_payment_amount_by_level(plan_level)
    base_monthly_amount = get_monthly_amount_by_level(plan_level)
    monthly_amount = get_monthly_amount_with_iva_by_level(plan_level)
    signup_fee = get_signup_fee_with_iva()
    subtotal_without_iva = base_monthly_amount + get_signup_fee()

    user.membership_level = plan_level

    token = get_token()

    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": payload.currency,
                    "value": f"{first_payment_amount:.2f}",
                },
                "description": (
                    f"Mayu Wellness Club - Primer pago Nivel {plan_level} "
                    f"incluye mensualidad ${monthly_amount:.2f} con IVA "
                    f"+ inscripción inicial ${signup_fee:.2f} con IVA"
                ),
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
        user_id=user.id,
        order_id=payload.order_id,
        paypal_order_id=response["id"],
        amount=first_payment_amount,
        currency=payload.currency,
        status="created",
        provider="paypal",
        payment_type="membership_initial",
        payment_reference=response["id"],
        raw_payload=json.dumps(response),
    )

    safe_set(payment, "signup_amount", signup_fee)
    safe_set(payment, "monthly_amount", monthly_amount)
    safe_set(payment, "plan_level", plan_level)

    db.add(payment)
    db.commit()
    db.refresh(payment)

    return {
        "payment_id": payment.id,
        "paypal_order_id": response["id"],
        "amount": first_payment_amount,
        "plan_level": plan_level,
        "base_monthly_amount": base_monthly_amount,
        "monthly_amount": monthly_amount,
        "signup_fee": signup_fee,
        "iva_rate": get_iva_rate(),
        "subtotal_without_iva": subtotal_without_iva,
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

    if not payment.provider:
        payment.provider = "paypal"

    if not payment.payment_type:
        payment.payment_type = "membership_initial"

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
        "order_id": payment.order_id,
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

    payment = db.query(models.MembershipPayment).filter(
        models.MembershipPayment.id == payment_id
    ).first()

    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    if payment.status not in ["paid", "verified", "created", "pending"]:
        raise HTTPException(
            status_code=400,
            detail="Pago inválido. Solo se puede verificar un pago pagado, creado, pendiente o ya verificado.",
        )

    user = db.query(models.User).filter(models.User.id == payment.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    now = datetime.utcnow()

    user.membership_active = True
    user.is_active = True

    payment.status = "verified"
    payment.admin_verified = True
    payment.admin_verified_at = now
    payment.admin_verified_by = current_user.id

    if not payment.paid_at:
        payment.paid_at = now

    order = None
    visible_in_logistics = False
    logistics_message = "Pago verificado y socio activado. Pendiente selección mensual para crear orden logística."

    try:
        order = get_or_create_order_for_payment(
            db=db,
            user=user,
            payment=payment,
            current_user=current_user,
        )

        order.status = "approved_for_logistics"
        order.user_status_snapshot = "active"
        order.membership_level_snapshot = user.membership_level
        order.logistics_notes = (
            payload.verification_notes
            or "✔ Pago verificado - listo para despacho"
        )

        selection = get_monthly_selection(db, order.user_id, order.month, order.year)

        if selection:
            selection.editable = False
            selection.status = "confirmed"

        visible_in_logistics = True
        logistics_message = "Pago verificado y orden lista para logística"

    except HTTPException as e:
        if e.status_code != 400:
            raise e

    db.commit()
    db.refresh(payment)
    db.refresh(user)

    if order:
        db.refresh(order)

    return {
        "message": logistics_message,
        "payment_id": payment.id,
        "payment_status": payment.status,
        "payment_type": payment.payment_type,
        "user_id": user.id,
        "membership_active": user.membership_active,
        "membership_level": user.membership_level,
        "order_id": order.id if order else None,
        "order_status": order.status if order else None,
        "order_month": order.month if order else None,
        "order_year": order.year if order else None,
        "visible_in_logistics": visible_in_logistics,
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

            if payment and payment.status not in ["verified"]:
                payment.status = "paid"
                payment.paid_at = datetime.utcnow()
                payment.raw_payload = json.dumps(event)
                db.commit()

    return {"status": "ok"}
