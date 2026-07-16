import os
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import SessionLocal
import models
from marketing import send_welcome_member_notifications

router = APIRouter(
    prefix="/payments/paypal/subscriptions",
    tags=["PayPal Subscriptions"],
)

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"


def get_mayu_app_public_url():
    return os.getenv("MAYU_APP_PUBLIC_URL", "http://127.0.0.1:5186").strip()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_paypal_mode():
    return os.getenv("PAYPAL_SUBSCRIPTIONS_MODE", "sandbox").lower().strip()


def get_paypal_client_id():
    value = os.getenv("PAYPAL_SUBSCRIPTIONS_CLIENT_ID")
    return value.strip() if value else None


def get_paypal_client_secret():
    value = os.getenv("PAYPAL_SUBSCRIPTIONS_CLIENT_SECRET")
    return value.strip() if value else None


def get_base_url():
    return (
        "https://api-m.sandbox.paypal.com"
        if get_paypal_mode() == "sandbox"
        else "https://api-m.paypal.com"
    )


def get_plan_id_by_level(level: int):
    sandbox_plan_ids = {
        1: "P-21C59754B6148072NNJMPWRQ",
        2: "P-1T277384JR2166440NJMPWRI",
        3: "P-6FL801269K374750WNJMPWRY",
    }
    if get_paypal_mode() == "sandbox":
        return sandbox_plan_ids.get(level)

    env_map = {
        1: "PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_1",
        2: "PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_2",
        3: "PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_3",
    }
    return os.getenv(env_map.get(level, "") or "")


def mask_paypal_id(value: Optional[str]):
    if not value:
        return None
    clean_value = value.strip()
    if len(clean_value) <= 6:
        return "••••"
    return f"{clean_value[:4]}••••{clean_value[-4:]}"


IVA_RATE = 0.12
SIGNUP_FEE_BASE = 5.00

BASE_MONTHLY_PRICES = {
    1: 40.00,
    2: 50.00,
    3: 60.00,
}

MONTHLY_PRICES = {
    level: round(price * (1 + IVA_RATE), 2)
    for level, price in BASE_MONTHLY_PRICES.items()
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


class ActivateLevel1Request(BaseModel):
    user_id: int
    subscription_id: str


class CreateProductRequest(BaseModel):
    name: str = "Mayu Wellness Club"
    description: str = "Membresías recurrentes Mayu Wellness Club"


class CreatePlanRequest(BaseModel):
    product_id: str
    plan_level: int
    currency: str = "USD"


def safe_set(obj, attr, value):
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def sync_member_cards_id_sequence(db: Session):
    db.execute(text("""
        SELECT setval(
            pg_get_serial_sequence('member_cards', 'id'),
            COALESCE((SELECT MAX(id) FROM member_cards), 0) + 1,
            false
        )
    """))
    db.flush()


def get_token():
    client_id = get_paypal_client_id()
    client_secret = get_paypal_client_secret()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Faltan PAYPAL_SUBSCRIPTIONS_CLIENT_ID o PAYPAL_SUBSCRIPTIONS_CLIENT_SECRET",
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


def get_current_cycle():
    now = datetime.now()
    return now.month, now.year


def get_plan_by_level(db: Session, level: int):
    return db.query(models.Plan).filter(models.Plan.level == level).first()


def get_signup_fee_with_iva() -> float:
    return round(SIGNUP_FEE_BASE * (1 + IVA_RATE), 2)


def get_first_payment_amount_by_level(level: int) -> float:
    if level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Nivel inválido")
    return round(MONTHLY_PRICES[level] + get_signup_fee_with_iva(), 2)


def get_member_card_number(db: Session, card: models.MemberCard):
    if not card or not card.id:
        return 0

    return (
        db.query(models.MemberCard)
        .join(models.User, models.User.id == models.MemberCard.user_id)
        .filter(
            models.User.role == "member",
            models.MemberCard.id <= card.id,
        )
        .count()
    )


def generate_member_code_for_card(db: Session, card: models.MemberCard, level: int):
    return f"MAYU-{level}-{get_member_card_number(db, card):06d}"


def get_or_create_member_card_core(db: Session, user: models.User):
    user_id = user.id
    membership_level = user.membership_level
    membership_active = user.membership_active

    card = (
        db.query(models.MemberCard)
        .filter(models.MemberCard.user_id == user_id)
        .first()
    )

    if card:
        member_code = generate_member_code_for_card(db, card, membership_level)
        card.member_code = member_code
        card.level_snapshot = membership_level
        card.status = "active" if membership_active else "inactive"
        card.expires_at = "Indefinido"
        db.commit()
        db.refresh(card)
        return card

    import uuid

    def create_member_card_once():
        card = models.MemberCard(
            user_id=user_id,
            member_code="",
            qr_token=str(uuid.uuid4()),
            level_snapshot=membership_level,
            status="active",
            expires_at="Indefinido",
        )

        db.add(card)
        db.flush()
        card.member_code = generate_member_code_for_card(db, card, membership_level)
        db.commit()
        db.refresh(card)
        return card

    sync_member_cards_id_sequence(db)
    try:
        return create_member_card_once()
    except IntegrityError:
        db.rollback()
        sync_member_cards_id_sequence(db)
        return create_member_card_once()


def get_latest_selection_with_items(db: Session, user_id: int):
    selections = (
        db.query(models.MonthlySelection)
        .filter(models.MonthlySelection.user_id == user_id)
        .order_by(
            models.MonthlySelection.year.desc(),
            models.MonthlySelection.month.desc(),
            models.MonthlySelection.id.desc(),
        )
        .all()
    )

    for selection in selections:
        count = (
            db.query(models.MonthlySelectionItem)
            .filter(models.MonthlySelectionItem.monthly_selection_id == selection.id)
            .count()
        )
        if count > 0:
            return selection

    return None


def get_pending_selection_for_payment(db: Session, user_id: int):
    return (
        db.query(models.MonthlySelection)
        .outerjoin(
            models.Order,
            models.Order.monthly_selection_id == models.MonthlySelection.id,
        )
        .filter(
            models.MonthlySelection.user_id == user_id,
            models.MonthlySelection.status.in_(["confirmed", "draft"]),
            models.Order.id == None,
        )
        .order_by(
            models.MonthlySelection.year.asc(),
            models.MonthlySelection.month.asc(),
            models.MonthlySelection.id.asc(),
        )
        .first()
    )


def get_or_create_initial_monthly_selection(db: Session, user: models.User):
    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene nivel de membresía asignado",
        )

    plan = get_plan_by_level(db, user.membership_level)

    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"No existe plan local para nivel {user.membership_level}",
        )

    month, year = get_current_cycle()

    selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )

    if selection:
        selection.plan_id = plan.id
        selection.editable = True
        db.commit()
        db.refresh(selection)
        return selection

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=month,
        year=year,
        status="draft",
        editable=True,
    )

    db.add(selection)
    db.commit()
    db.refresh(selection)
    return selection


def get_or_create_monthly_selection_for_payment(
    db: Session,
    user: models.User,
    month: int,
    year: int,
):
    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene nivel de membresía asignado",
        )

    plan = get_plan_by_level(db, user.membership_level)

    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"No existe plan local para nivel {user.membership_level}",
        )

    selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )

    if selection:
        selection.plan_id = plan.id
        db.flush()
        return selection

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=month,
        year=year,
        status="draft",
        editable=True,
    )

    db.add(selection)
    db.flush()

    previous = get_latest_selection_with_items(db, user.id)

    if previous:
        previous_items = (
            db.query(models.MonthlySelectionItem)
            .filter(models.MonthlySelectionItem.monthly_selection_id == previous.id)
            .all()
        )

        for item in previous_items:
            db.add(
                models.MonthlySelectionItem(
                    monthly_selection_id=selection.id,
                    product_id=item.product_id,
                    quantity=item.quantity or 1,
                )
            )

        selection.status = "confirmed"

    return selection


def get_order_for_selection(db: Session, user_id: int, month: int, year: int):
    return (
        db.query(models.Order)
        .filter(
            models.Order.user_id == user_id,
            models.Order.month == month,
            models.Order.year == year,
        )
        .order_by(models.Order.id.desc())
        .first()
    )


def extract_paypal_amount(resource: dict, default_amount: float):
    amount_data = resource.get("amount") or resource.get(
        "seller_receivable_breakdown", {}
    ).get("gross_amount")

    if isinstance(amount_data, dict):
        value = amount_data.get("total") or amount_data.get("value")
        try:
            return float(value)
        except Exception:
            return default_amount

    return default_amount


def create_monthly_membership_payment_from_webhook(
    db: Session,
    user: models.User,
    subscription_id: str,
    event: dict,
):
    resource = event.get("resource", {})

    paypal_payment_id = (
        resource.get("id")
        or resource.get("sale_id")
        or resource.get("capture_id")
        or f"{subscription_id}-{event.get('id', datetime.utcnow().timestamp())}"
    )

    existing = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.paypal_order_id == str(paypal_payment_id))
        .first()
    )

    if existing:
        return existing

    selection = get_pending_selection_for_payment(db, user.id)

    if selection:
        month = selection.month
        year = selection.year
    else:
        month, year = get_current_cycle()
        selection = get_or_create_monthly_selection_for_payment(
            db=db,
            user=user,
            month=month,
            year=year,
        )

    existing_order = get_order_for_selection(
        db=db,
        user_id=user.id,
        month=month,
        year=year,
    )

    level = user.membership_level or 1
    default_amount = MONTHLY_PRICES.get(level, 40.00)
    amount = extract_paypal_amount(resource, default_amount)

    payment = models.MembershipPayment(
        user_id=user.id,
        order_id=existing_order.id if existing_order else None,
        paypal_order_id=str(paypal_payment_id),
        amount=amount,
        currency="USD",
        status="subscription_paid",
        paid_at=datetime.utcnow(),
    )

    safe_set(payment, "provider", "paypal")
    safe_set(payment, "payment_type", "subscription_renewal")
    safe_set(payment, "payment_reference", subscription_id)
    safe_set(payment, "raw_payload", json.dumps(event))
    safe_set(payment, "admin_verified", False)
    safe_set(payment, "payer_email", resource.get("payer", {}).get("email_address"))
    safe_set(payment, "monthly_selection_id", selection.id if selection else None)

    db.add(payment)

    user.membership_active = True
    user.is_active = True
    user.status = "active"

    db.flush()
    return payment


def activate_user_subscription_core(
    db: Session,
    user: models.User,
    subscription_id: str,
    plan_level: int,
    paypal_payload=None,
):
    if not subscription_id or not subscription_id.strip():
        raise HTTPException(status_code=400, detail="subscription_id requerido")

    subscription_id = subscription_id.strip()

    existing_payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.paypal_order_id == subscription_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .first()
    )

    if existing_payment and existing_payment.user_id != user.id:
        raise HTTPException(
            status_code=409,
            detail="Esta suscripción ya está asociada a otro usuario",
        )

    user.membership_level = plan_level
    user.membership_active = True
    user.is_active = True
    user.status = "active"

    if existing_payment:
        existing_payment.status = "subscription_active"
        existing_payment.amount = get_first_payment_amount_by_level(plan_level)
        safe_set(existing_payment, "payment_reference", subscription_id)
        safe_set(existing_payment, "signup_amount", get_signup_fee_with_iva())
        safe_set(existing_payment, "monthly_amount", MONTHLY_PRICES[plan_level])
        safe_set(existing_payment, "plan_level", plan_level)
        if paypal_payload is not None:
            safe_set(existing_payment, "raw_payload", json.dumps(paypal_payload))
    else:
        payment = models.MembershipPayment(
            user_id=user.id,
            order_id=None,
            paypal_order_id=subscription_id,
            amount=get_first_payment_amount_by_level(plan_level),
            currency="USD",
            status="subscription_active",
        )

        safe_set(payment, "provider", "paypal")
        safe_set(payment, "payment_type", "subscription")
        safe_set(payment, "payment_reference", subscription_id)
        safe_set(payment, "admin_verified", False)
        safe_set(payment, "signup_amount", get_signup_fee_with_iva())
        safe_set(payment, "monthly_amount", MONTHLY_PRICES[plan_level])
        safe_set(payment, "plan_level", plan_level)

        if paypal_payload is not None:
            safe_set(payment, "raw_payload", json.dumps(paypal_payload))

        db.add(payment)

    db.commit()
    db.refresh(user)

    card = get_or_create_member_card_core(db, user)
    month, year = get_current_cycle()

    selection = get_or_create_monthly_selection_for_payment(
        db=db,
        user=user,
        month=month,
        year=year,
    )

    subscription_payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.paypal_order_id == subscription_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .first()
    )

    if subscription_payment:
        safe_set(subscription_payment, "monthly_selection_id", selection.id)
        db.commit()

    try:
        welcome_sync = send_welcome_member_notifications(
            db=db,
            user=user,
            trigger="paypal_subscription_activation",
        )
        db.commit()
    except Exception as exc:
        welcome_sync = {
            "sent": False,
            "error": str(exc),
        }

    return {
        "status": "activated",
        "user_id": user.id,
        "membership_active": user.membership_active,
        "membership_level": user.membership_level,
        "paypal_subscription_id": subscription_id,
        "card_id": card.id,
        "member_code": card.member_code,
        "selection_id": selection.id,
        "selection_month": selection.month,
        "selection_year": selection.year,
        "welcome_notifications": welcome_sync,
    }


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
        "base_monthly_prices": BASE_MONTHLY_PRICES,
        "monthly_prices": MONTHLY_PRICES,
        "iva_rate": IVA_RATE,
        "signup_fee": get_signup_fee_with_iva(),
        "first_payment_amounts": {
            level: get_first_payment_amount_by_level(level)
            for level in MONTHLY_PRICES
        },
    }


def extract_regular_monthly_amount(plan: dict):
    for cycle in plan.get("billing_cycles") or []:
        if cycle.get("tenure_type") != "REGULAR":
            continue
        fixed_price = (
            (cycle.get("pricing_scheme") or {})
            .get("fixed_price")
            or {}
        )
        value = fixed_price.get("value")
        if value is not None:
            return float(value)
    return None


def extract_setup_fee_amount(plan: dict):
    fixed_price = ((plan.get("payment_preferences") or {}).get("setup_fee") or {})
    value = fixed_price.get("value")
    return float(value) if value is not None else 0.0


@router.get("/debug-plans")
def debug_paypal_plans():
    token = get_token()
    plans = {}

    for level in sorted(MONTHLY_PRICES.keys()):
        plan_id = get_plan_id_by_level(level)
        expected_monthly = MONTHLY_PRICES[level]
        expected_first_payment = get_first_payment_amount_by_level(level)
        expected_setup_fee = expected_first_payment

        if not plan_id:
            plans[level] = {
                "configured": False,
                "status": "missing_plan_id",
                "expected_monthly": expected_monthly,
                "expected_setup_fee": expected_setup_fee,
                "expected_first_payment": expected_first_payment,
                "expected_signup_fee": get_signup_fee_with_iva(),
            }
            continue

        try:
            response = paypal_request("GET", f"/v1/billing/plans/{plan_id}", token)
            paypal_monthly = extract_regular_monthly_amount(response)
            paypal_setup_fee = extract_setup_fee_amount(response)

            plans[level] = {
                "configured": True,
                "plan_id": mask_paypal_id(plan_id),
                "paypal_status": response.get("status"),
                "paypal_monthly": paypal_monthly,
                "paypal_initial_charge": paypal_setup_fee,
                "expected_monthly": expected_monthly,
                "expected_first_payment": expected_first_payment,
                "expected_signup_fee": get_signup_fee_with_iva(),
                "matches_expected": (
                    paypal_monthly == expected_monthly
                    and paypal_setup_fee == expected_setup_fee
                ),
            }
        except HTTPException as exc:
            plans[level] = {
                "configured": True,
                "plan_id": mask_paypal_id(plan_id),
                "status": "paypal_check_failed",
                "detail": exc.detail,
                "expected_monthly": expected_monthly,
                "expected_setup_fee": expected_setup_fee,
                "expected_first_payment": expected_first_payment,
                "expected_signup_fee": get_signup_fee_with_iva(),
            }

    return {
        "paypal_mode": get_paypal_mode(),
        "iva_rate": IVA_RATE,
        "plans": plans,
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

    base_price = BASE_MONTHLY_PRICES[payload.plan_level]
    price = MONTHLY_PRICES[payload.plan_level]
    signup_fee = get_signup_fee_with_iva()
    first_payment_amount = get_first_payment_amount_by_level(payload.plan_level)
    plan_name = PLAN_NAMES[payload.plan_level]

    body = {
        "product_id": payload.product_id,
        "name": plan_name,
        "description": (
            f"Mensualidad recurrente {plan_name}. "
            "Valores incluyen IVA Ecuador 12%."
        ),
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
            "setup_fee": {
                "value": f"{first_payment_amount:.2f}",
                "currency_code": payload.currency,
            },
            "setup_fee_failure_action": "CONTINUE",
            "payment_failure_threshold": 1,
        },
    }

    response = paypal_request("POST", "/v1/billing/plans", token, body)

    return {
        "message": "Plan PayPal creado correctamente",
        "plan_level": payload.plan_level,
        "base_monthly_price": base_price,
        "monthly_price": price,
        "iva_rate": IVA_RATE,
        "signup_fee": signup_fee,
        "first_payment_amount": first_payment_amount,
        "setup_fee": first_payment_amount,
        "note": "PayPal cobra inscripción + primera mensualidad con IVA como setup_fee. La mensualidad recurrente empieza después y también incluye IVA.",
        "plan_id": response.get("id"),
        "response": response,
    }


@router.post("/create")
def create_subscription(payload: CreateSubscriptionRequest, db: Session = Depends(get_db)):
    if payload.plan_level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Nivel inválido")

    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    plan_id = get_plan_id_by_level(payload.plan_level)

    if not plan_id:
        raise HTTPException(
            status_code=500,
            detail=f"Falta configurar PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_{payload.plan_level} en Render.",
        )

    token = get_token()
    start_time = payload.start_time or first_day_next_month_utc()
    backend_url = BASE_PUBLIC_URL.rstrip("/")
    return_query = urllib.parse.urlencode(
        {
            "user_id": user.id,
            "plan_level": payload.plan_level,
        }
    )

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
            "return_url": (
                f"{backend_url}/payments/paypal/subscriptions/return"
                f"?{return_query}"
            ),
            "cancel_url": (
                f"{backend_url}/payments/paypal/subscriptions/cancel"
                f"?{return_query}"
            ),
        },
    }

    response = paypal_request("POST", "/v1/billing/subscriptions", token, body)

    subscription_id = response.get("id")
    approve_url = extract_approve_url(response.get("links", []))
    paypal_status = response.get("status", "APPROVAL_PENDING")
    monthly_amount = MONTHLY_PRICES[payload.plan_level]
    base_monthly_amount = BASE_MONTHLY_PRICES[payload.plan_level]
    signup_fee = get_signup_fee_with_iva()
    first_payment_amount = get_first_payment_amount_by_level(payload.plan_level)

    if not subscription_id:
        raise HTTPException(status_code=500, detail="PayPal no devolvió subscription_id")

    existing_payment = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.paypal_order_id == subscription_id)
        .first()
    )

    if not existing_payment:
        payment = models.MembershipPayment(
            user_id=user.id,
            order_id=None,
            paypal_order_id=subscription_id,
            amount=first_payment_amount,
            currency="USD",
            status="subscription_created",
        )

        safe_set(payment, "provider", "paypal")
        safe_set(payment, "payment_type", "subscription")
        safe_set(payment, "payment_reference", subscription_id)
        safe_set(payment, "raw_payload", json.dumps(response))
        safe_set(payment, "admin_verified", False)
        safe_set(payment, "signup_amount", signup_fee)
        safe_set(payment, "monthly_amount", monthly_amount)
        safe_set(payment, "plan_level", payload.plan_level)

        db.add(payment)

    user.membership_level = payload.plan_level
    db.commit()

    return {
        "message": "Suscripción PayPal creada",
        "user_id": user.id,
        "plan_level": payload.plan_level,
        "base_monthly_amount": base_monthly_amount,
        "monthly_amount": monthly_amount,
        "iva_rate": IVA_RATE,
        "signup_fee": signup_fee,
        "first_payment_amount": first_payment_amount,
        "setup_fee": first_payment_amount,
        "start_time": start_time,
        "paypal_subscription_id": subscription_id,
        "subscription_status": paypal_status,
        "approve_url": approve_url,
        "approval_url": approve_url,
        "links": response.get("links", []),
    }


@router.post("/activate-level-1")
def activate_level_1_subscription(payload: ActivateLevel1Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    token = get_token()

    response = paypal_request(
        "GET",
        f"/v1/billing/subscriptions/{payload.subscription_id}",
        token,
    )

    paypal_status = response.get("status")

    if paypal_status != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail=f"La suscripción PayPal todavía no está activa. Estado actual: {paypal_status}",
        )

    plan_id = response.get("plan_id")

    detected_level = None

    for level in [1, 2, 3]:
        expected_plan_id = get_plan_id_by_level(level)
        if expected_plan_id and plan_id == expected_plan_id:
            detected_level = level
            break

    if detected_level is None:
        detected_level = user.membership_level or 1

    result = activate_user_subscription_core(
        db=db,
        user=user,
        subscription_id=payload.subscription_id,
        plan_level=detected_level,
        paypal_payload=response,
    )

    return {
        "message": f"Membresía Nivel {detected_level} activada correctamente",
        **result,
    }

@router.get("/return", response_class=HTMLResponse)
def subscription_return(request: Request, db: Session = Depends(get_db)):
    user_id = request.query_params.get("user_id") or ""
    plan_level = request.query_params.get("plan_level") or ""
    subscription_id = request.query_params.get("subscription_id") or ""

    if user_id.isdigit() and not subscription_id:
        latest_payment = (
            db.query(models.MembershipPayment)
            .filter(
                models.MembershipPayment.user_id == int(user_id),
                models.MembershipPayment.payment_type == "subscription",
            )
            .order_by(models.MembershipPayment.id.desc())
            .first()
        )
        if latest_payment:
            subscription_id = latest_payment.paypal_order_id or ""

    if not subscription_id:
        subscription_id = (
            request.query_params.get("ba_token")
            or request.query_params.get("token")
            or ""
        )

    app_url = get_mayu_app_public_url().rstrip("/")
    query = {
        "payment": "approved",
    }
    if subscription_id:
        query["subscription_id"] = subscription_id
    if user_id:
        query["user_id"] = user_id
    if plan_level:
        query["plan_level"] = plan_level
    app_return_url = (
        f"{app_url}/membership/paypal-success?{urllib.parse.urlencode(query)}"
    )

    return HTMLResponse(
        content=f"""
        <html>
            <head>
                <title>Suscripción activada</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: Arial; background:#0f172a; color:white; text-align:center; padding:40px;">
                <h1>Pago aprobado en PayPal</h1>
                <p>Estamos regresando a Mayu Wellness Club para confirmar tu membresía.</p>
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


@router.get("/cancel", response_class=HTMLResponse)
def subscription_cancel(request: Request):
    user_id = request.query_params.get("user_id") or ""
    plan_level = request.query_params.get("plan_level") or ""
    app_url = get_mayu_app_public_url().rstrip("/")
    query = {"payment": "cancelled"}
    if user_id:
        query["user_id"] = user_id
    if plan_level:
        query["plan_level"] = plan_level
    app_return_url = (
        f"{app_url}/membership/paypal-success?{urllib.parse.urlencode(query)}"
    )

    return HTMLResponse(
        content=f"""
        <html>
            <head>
                <title>Suscripción cancelada</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: Arial; background:#111; color:white; text-align:center; padding:40px;">
                <h1>Proceso cancelado</h1>
                <p>No se activó la mensualidad automática.</p>
                <p style="margin-top:28px;">
                    <a href="{app_return_url}" style="background:#64748b;color:white;padding:14px 22px;border-radius:10px;text-decoration:none;font-weight:bold;">
                        Volver a Mayu
                    </a>
                </p>
            </body>
        </html>
        """
    )


@router.post("/webhook")
async def subscription_webhook(request: Request, db: Session = Depends(get_db)):
    event = await request.json()
    event_type = event.get("event_type")
    resource = event.get("resource", {})

    subscription_id = (
        resource.get("billing_agreement_id")
        or resource.get("subscription_id")
        or resource.get("id")
    )

    if not subscription_id:
        return {"status": "ignored", "reason": "No subscription id"}

    subscription_payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.paypal_order_id == subscription_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .first()
    )

    user = None

    if subscription_payment:
        user = (
            db.query(models.User)
            .filter(models.User.id == subscription_payment.user_id)
            .first()
        )

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        if subscription_payment:
            subscription_payment.status = "subscription_active"
            safe_set(subscription_payment, "raw_payload", json.dumps(event))

        if user:
            level = user.membership_level or 1
            activate_user_subscription_core(
                db=db,
                user=user,
                subscription_id=subscription_id,
                plan_level=level,
                paypal_payload=event,
            )
        else:
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
        if user:
            monthly_payment = create_monthly_membership_payment_from_webhook(
                db=db,
                user=user,
                subscription_id=subscription_id,
                event=event,
            )
            db.commit()

            return {
                "status": "ok",
                "event": event_type,
                "subscription_id": subscription_id,
                "payment_id": monthly_payment.id,
                "payment_status": monthly_payment.status,
                "admin_verified": monthly_payment.admin_verified,
            }

        if subscription_payment:
            subscription_payment.status = "subscription_paid"
            subscription_payment.paid_at = datetime.utcnow()
            safe_set(subscription_payment, "raw_payload", json.dumps(event))

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
        if subscription_payment:
            subscription_payment.status = "subscription_inactive"
            safe_set(subscription_payment, "raw_payload", json.dumps(event))

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
        next_billing_time = (
            billing_info.get("next_billing_time") if billing_info else None
        )

        if paypal_status == "ACTIVE":
            payment.status = "subscription_active"
            user.membership_active = True
            user.is_active = True
            user.status = "active"

            safe_set(user, "paypal_subscription_id", subscription_id)
            safe_set(user, "subscription_status", "ACTIVE")
            safe_set(user, "next_billing_date", next_billing_time)

            db.commit()

        elif paypal_status in ["APPROVAL_PENDING"]:
            payment.status = "subscription_created"
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
import os
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import SessionLocal
import models
from marketing import send_welcome_member_notifications

router = APIRouter(
    prefix="/payments/paypal/subscriptions",
    tags=["PayPal Subscriptions"],
)

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"


def get_mayu_app_public_url():
    return os.getenv("MAYU_APP_PUBLIC_URL", "http://127.0.0.1:5186").strip()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_paypal_mode():
    return os.getenv("PAYPAL_SUBSCRIPTIONS_MODE", "sandbox").lower().strip()


def get_paypal_client_id():
    value = os.getenv("PAYPAL_SUBSCRIPTIONS_CLIENT_ID")
    return value.strip() if value else None


def get_paypal_client_secret():
    value = os.getenv("PAYPAL_SUBSCRIPTIONS_CLIENT_SECRET")
    return value.strip() if value else None


def get_base_url():
    return (
        "https://api-m.sandbox.paypal.com"
        if get_paypal_mode() == "sandbox"
        else "https://api-m.paypal.com"
    )


def get_plan_id_by_level(level: int):
    sandbox_plan_ids = {
        1: "P-21C59754B6148072NNJMPWRQ",
        2: "P-1T277384JR2166440NJMPWRI",
        3: "P-6FL801269K374750WNJMPWRY",
    }
    if get_paypal_mode() == "sandbox":
        return sandbox_plan_ids.get(level)

    env_map = {
        1: "PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_1",
        2: "PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_2",
        3: "PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_3",
    }
    return os.getenv(env_map.get(level, "") or "")


def mask_paypal_id(value: Optional[str]):
    if not value:
        return None
    clean_value = value.strip()
    if len(clean_value) <= 6:
        return "••••"
    return f"{clean_value[:4]}••••{clean_value[-4:]}"


IVA_RATE = 0.12
SIGNUP_FEE_BASE = 5.00

BASE_MONTHLY_PRICES = {
    1: 40.00,
    2: 50.00,
    3: 60.00,
}

MONTHLY_PRICES = {
    level: round(price * (1 + IVA_RATE), 2)
    for level, price in BASE_MONTHLY_PRICES.items()
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


class ActivateLevel1Request(BaseModel):
    user_id: int
    subscription_id: str


class CreateProductRequest(BaseModel):
    name: str = "Mayu Wellness Club"
    description: str = "Membresías recurrentes Mayu Wellness Club"


class CreatePlanRequest(BaseModel):
    product_id: str
    plan_level: int
    currency: str = "USD"


def safe_set(obj, attr, value):
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def sync_member_cards_id_sequence(db: Session):
    db.execute(text("""
        SELECT setval(
            pg_get_serial_sequence('member_cards', 'id'),
            COALESCE((SELECT MAX(id) FROM member_cards), 0) + 1,
            false
        )
    """))
    db.flush()


def get_token():
    client_id = get_paypal_client_id()
    client_secret = get_paypal_client_secret()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Faltan PAYPAL_SUBSCRIPTIONS_CLIENT_ID o PAYPAL_SUBSCRIPTIONS_CLIENT_SECRET",
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


def get_current_cycle():
    now = datetime.now()
    return now.month, now.year


def get_plan_by_level(db: Session, level: int):
    return db.query(models.Plan).filter(models.Plan.level == level).first()


def get_signup_fee_with_iva() -> float:
    return round(SIGNUP_FEE_BASE * (1 + IVA_RATE), 2)


def get_first_payment_amount_by_level(level: int) -> float:
    if level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Nivel inválido")
    return round(MONTHLY_PRICES[level] + get_signup_fee_with_iva(), 2)


def get_member_card_number(db: Session, card: models.MemberCard):
    if not card or not card.id:
        return 0

    return (
        db.query(models.MemberCard)
        .join(models.User, models.User.id == models.MemberCard.user_id)
        .filter(
            models.User.role == "member",
            models.MemberCard.id <= card.id,
        )
        .count()
    )


def generate_member_code_for_card(db: Session, card: models.MemberCard, level: int):
    return f"MAYU-{level}-{get_member_card_number(db, card):06d}"


def get_or_create_member_card_core(db: Session, user: models.User):
    user_id = user.id
    membership_level = user.membership_level
    membership_active = user.membership_active

    card = (
        db.query(models.MemberCard)
        .filter(models.MemberCard.user_id == user_id)
        .first()
    )

    if card:
        member_code = generate_member_code_for_card(db, card, membership_level)
        card.member_code = member_code
        card.level_snapshot = membership_level
        card.status = "active" if membership_active else "inactive"
        card.expires_at = "Indefinido"
        db.commit()
        db.refresh(card)
        return card

    import uuid

    def create_member_card_once():
        card = models.MemberCard(
            user_id=user_id,
            member_code="",
            qr_token=str(uuid.uuid4()),
            level_snapshot=membership_level,
            status="active",
            expires_at="Indefinido",
        )

        db.add(card)
        db.flush()
        card.member_code = generate_member_code_for_card(db, card, membership_level)
        db.commit()
        db.refresh(card)
        return card

    sync_member_cards_id_sequence(db)
    try:
        return create_member_card_once()
    except IntegrityError:
        db.rollback()
        sync_member_cards_id_sequence(db)
        return create_member_card_once()


def get_latest_selection_with_items(db: Session, user_id: int):
    selections = (
        db.query(models.MonthlySelection)
        .filter(models.MonthlySelection.user_id == user_id)
        .order_by(
            models.MonthlySelection.year.desc(),
            models.MonthlySelection.month.desc(),
            models.MonthlySelection.id.desc(),
        )
        .all()
    )

    for selection in selections:
        count = (
            db.query(models.MonthlySelectionItem)
            .filter(models.MonthlySelectionItem.monthly_selection_id == selection.id)
            .count()
        )
        if count > 0:
            return selection

    return None


def get_pending_selection_for_payment(db: Session, user_id: int):
    return (
        db.query(models.MonthlySelection)
        .outerjoin(
            models.Order,
            models.Order.monthly_selection_id == models.MonthlySelection.id,
        )
        .filter(
            models.MonthlySelection.user_id == user_id,
            models.MonthlySelection.status.in_(["confirmed", "draft"]),
            models.Order.id == None,
        )
        .order_by(
            models.MonthlySelection.year.asc(),
            models.MonthlySelection.month.asc(),
            models.MonthlySelection.id.asc(),
        )
        .first()
    )


def get_or_create_initial_monthly_selection(db: Session, user: models.User):
    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene nivel de membresía asignado",
        )

    plan = get_plan_by_level(db, user.membership_level)

    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"No existe plan local para nivel {user.membership_level}",
        )

    month, year = get_current_cycle()

    selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )

    if selection:
        selection.plan_id = plan.id
        selection.editable = True
        db.commit()
        db.refresh(selection)
        return selection

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=month,
        year=year,
        status="draft",
        editable=True,
    )

    db.add(selection)
    db.commit()
    db.refresh(selection)
    return selection


def get_or_create_monthly_selection_for_payment(
    db: Session,
    user: models.User,
    month: int,
    year: int,
):
    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene nivel de membresía asignado",
        )

    plan = get_plan_by_level(db, user.membership_level)

    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"No existe plan local para nivel {user.membership_level}",
        )

    selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )

    if selection:
        selection.plan_id = plan.id
        db.flush()
        return selection

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=month,
        year=year,
        status="draft",
        editable=True,
    )

    db.add(selection)
    db.flush()

    previous = get_latest_selection_with_items(db, user.id)

    if previous:
        previous_items = (
            db.query(models.MonthlySelectionItem)
            .filter(models.MonthlySelectionItem.monthly_selection_id == previous.id)
            .all()
        )

        for item in previous_items:
            db.add(
                models.MonthlySelectionItem(
                    monthly_selection_id=selection.id,
                    product_id=item.product_id,
                    quantity=item.quantity or 1,
                )
            )

        selection.status = "confirmed"

    return selection


def get_order_for_selection(db: Session, user_id: int, month: int, year: int):
    return (
        db.query(models.Order)
        .filter(
            models.Order.user_id == user_id,
            models.Order.month == month,
            models.Order.year == year,
        )
        .order_by(models.Order.id.desc())
        .first()
    )


def extract_paypal_amount(resource: dict, default_amount: float):
    amount_data = resource.get("amount") or resource.get(
        "seller_receivable_breakdown", {}
    ).get("gross_amount")

    if isinstance(amount_data, dict):
        value = amount_data.get("total") or amount_data.get("value")
        try:
            return float(value)
        except Exception:
            return default_amount

    return default_amount


def create_monthly_membership_payment_from_webhook(
    db: Session,
    user: models.User,
    subscription_id: str,
    event: dict,
):
    resource = event.get("resource", {})

    paypal_payment_id = (
        resource.get("id")
        or resource.get("sale_id")
        or resource.get("capture_id")
        or f"{subscription_id}-{event.get('id', datetime.utcnow().timestamp())}"
    )

    existing = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.paypal_order_id == str(paypal_payment_id))
        .first()
    )

    if existing:
        return existing

    selection = get_pending_selection_for_payment(db, user.id)

    if selection:
        month = selection.month
        year = selection.year
    else:
        month, year = get_current_cycle()
        selection = get_or_create_monthly_selection_for_payment(
            db=db,
            user=user,
            month=month,
            year=year,
        )

    existing_order = get_order_for_selection(
        db=db,
        user_id=user.id,
        month=month,
        year=year,
    )

    level = user.membership_level or 1
    default_amount = MONTHLY_PRICES.get(level, 40.00)
    amount = extract_paypal_amount(resource, default_amount)

    payment = models.MembershipPayment(
        user_id=user.id,
        order_id=existing_order.id if existing_order else None,
        paypal_order_id=str(paypal_payment_id),
        amount=amount,
        currency="USD",
        status="subscription_paid",
        paid_at=datetime.utcnow(),
    )

    safe_set(payment, "provider", "paypal")
    safe_set(payment, "payment_type", "subscription_renewal")
    safe_set(payment, "payment_reference", subscription_id)
    safe_set(payment, "raw_payload", json.dumps(event))
    safe_set(payment, "admin_verified", False)
    safe_set(payment, "payer_email", resource.get("payer", {}).get("email_address"))
    safe_set(payment, "monthly_selection_id", selection.id if selection else None)

    db.add(payment)

    user.membership_active = True
    user.is_active = True
    user.status = "active"

    db.flush()
    return payment


def activate_user_subscription_core(
    db: Session,
    user: models.User,
    subscription_id: str,
    plan_level: int,
    paypal_payload=None,
):
    if not subscription_id or not subscription_id.strip():
        raise HTTPException(status_code=400, detail="subscription_id requerido")

    subscription_id = subscription_id.strip()

    existing_payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.paypal_order_id == subscription_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .first()
    )

    if existing_payment and existing_payment.user_id != user.id:
        raise HTTPException(
            status_code=409,
            detail="Esta suscripción ya está asociada a otro usuario",
        )

    user.membership_level = plan_level
    user.membership_active = True
    user.is_active = True
    user.status = "active"

    if existing_payment:
        existing_payment.status = "subscription_active"
        existing_payment.amount = get_first_payment_amount_by_level(plan_level)
        safe_set(existing_payment, "payment_reference", subscription_id)
        safe_set(existing_payment, "signup_amount", get_signup_fee_with_iva())
        safe_set(existing_payment, "monthly_amount", MONTHLY_PRICES[plan_level])
        safe_set(existing_payment, "plan_level", plan_level)
        if paypal_payload is not None:
            safe_set(existing_payment, "raw_payload", json.dumps(paypal_payload))
    else:
        payment = models.MembershipPayment(
            user_id=user.id,
            order_id=None,
            paypal_order_id=subscription_id,
            amount=get_first_payment_amount_by_level(plan_level),
            currency="USD",
            status="subscription_active",
        )

        safe_set(payment, "provider", "paypal")
        safe_set(payment, "payment_type", "subscription")
        safe_set(payment, "payment_reference", subscription_id)
        safe_set(payment, "admin_verified", False)
        safe_set(payment, "signup_amount", get_signup_fee_with_iva())
        safe_set(payment, "monthly_amount", MONTHLY_PRICES[plan_level])
        safe_set(payment, "plan_level", plan_level)

        if paypal_payload is not None:
            safe_set(payment, "raw_payload", json.dumps(paypal_payload))

        db.add(payment)

    db.commit()
    db.refresh(user)

    card = get_or_create_member_card_core(db, user)
    month, year = get_current_cycle()

    selection = get_or_create_monthly_selection_for_payment(
        db=db,
        user=user,
        month=month,
        year=year,
    )

    subscription_payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.paypal_order_id == subscription_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .first()
    )

    if subscription_payment:
        safe_set(subscription_payment, "monthly_selection_id", selection.id)
        db.commit()

    try:
        welcome_sync = send_welcome_member_notifications(
            db=db,
            user=user,
            trigger="paypal_subscription_activation",
        )
        db.commit()
    except Exception as exc:
        welcome_sync = {
            "sent": False,
            "error": str(exc),
        }

    return {
        "status": "activated",
        "user_id": user.id,
        "membership_active": user.membership_active,
        "membership_level": user.membership_level,
        "paypal_subscription_id": subscription_id,
        "card_id": card.id,
        "member_code": card.member_code,
        "selection_id": selection.id,
        "selection_month": selection.month,
        "selection_year": selection.year,
        "welcome_notifications": welcome_sync,
    }


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
        "base_monthly_prices": BASE_MONTHLY_PRICES,
        "monthly_prices": MONTHLY_PRICES,
        "iva_rate": IVA_RATE,
        "signup_fee": get_signup_fee_with_iva(),
        "first_payment_amounts": {
            level: get_first_payment_amount_by_level(level)
            for level in MONTHLY_PRICES
        },
    }


def extract_regular_monthly_amount(plan: dict):
    for cycle in plan.get("billing_cycles") or []:
        if cycle.get("tenure_type") != "REGULAR":
            continue
        fixed_price = (
            (cycle.get("pricing_scheme") or {})
            .get("fixed_price")
            or {}
        )
        value = fixed_price.get("value")
        if value is not None:
            return float(value)
    return None


def extract_setup_fee_amount(plan: dict):
    fixed_price = ((plan.get("payment_preferences") or {}).get("setup_fee") or {})
    value = fixed_price.get("value")
    return float(value) if value is not None else 0.0


@router.get("/debug-plans")
def debug_paypal_plans():
    token = get_token()
    plans = {}

    for level in sorted(MONTHLY_PRICES.keys()):
        plan_id = get_plan_id_by_level(level)
        expected_monthly = MONTHLY_PRICES[level]
        expected_first_payment = get_first_payment_amount_by_level(level)
        expected_setup_fee = expected_first_payment

        if not plan_id:
            plans[level] = {
                "configured": False,
                "status": "missing_plan_id",
                "expected_monthly": expected_monthly,
                "expected_setup_fee": expected_setup_fee,
                "expected_first_payment": expected_first_payment,
                "expected_signup_fee": get_signup_fee_with_iva(),
            }
            continue

        try:
            response = paypal_request("GET", f"/v1/billing/plans/{plan_id}", token)
            paypal_monthly = extract_regular_monthly_amount(response)
            paypal_setup_fee = extract_setup_fee_amount(response)

            plans[level] = {
                "configured": True,
                "plan_id": mask_paypal_id(plan_id),
                "paypal_status": response.get("status"),
                "paypal_monthly": paypal_monthly,
                "paypal_initial_charge": paypal_setup_fee,
                "expected_monthly": expected_monthly,
                "expected_first_payment": expected_first_payment,
                "expected_signup_fee": get_signup_fee_with_iva(),
                "matches_expected": (
                    paypal_monthly == expected_monthly
                    and paypal_setup_fee == expected_setup_fee
                ),
            }
        except HTTPException as exc:
            plans[level] = {
                "configured": True,
                "plan_id": mask_paypal_id(plan_id),
                "status": "paypal_check_failed",
                "detail": exc.detail,
                "expected_monthly": expected_monthly,
                "expected_setup_fee": expected_setup_fee,
                "expected_first_payment": expected_first_payment,
                "expected_signup_fee": get_signup_fee_with_iva(),
            }

    return {
        "paypal_mode": get_paypal_mode(),
        "iva_rate": IVA_RATE,
        "plans": plans,
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

    base_price = BASE_MONTHLY_PRICES[payload.plan_level]
    price = MONTHLY_PRICES[payload.plan_level]
    signup_fee = get_signup_fee_with_iva()
    first_payment_amount = get_first_payment_amount_by_level(payload.plan_level)
    plan_name = PLAN_NAMES[payload.plan_level]

    body = {
        "product_id": payload.product_id,
        "name": plan_name,
        "description": (
            f"Mensualidad recurrente {plan_name}. "
            "Valores incluyen IVA Ecuador 12%."
        ),
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
            "setup_fee": {
                "value": f"{first_payment_amount:.2f}",
                "currency_code": payload.currency,
            },
            "setup_fee_failure_action": "CONTINUE",
            "payment_failure_threshold": 1,
        },
    }

    response = paypal_request("POST", "/v1/billing/plans", token, body)

    return {
        "message": "Plan PayPal creado correctamente",
        "plan_level": payload.plan_level,
        "base_monthly_price": base_price,
        "monthly_price": price,
        "iva_rate": IVA_RATE,
        "signup_fee": signup_fee,
        "first_payment_amount": first_payment_amount,
        "setup_fee": first_payment_amount,
        "note": "PayPal cobra inscripción + primera mensualidad con IVA como setup_fee. La mensualidad recurrente empieza después y también incluye IVA.",
        "plan_id": response.get("id"),
        "response": response,
    }


@router.post("/create")
def create_subscription(payload: CreateSubscriptionRequest, db: Session = Depends(get_db)):
    if payload.plan_level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Nivel inválido")

    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    plan_id = get_plan_id_by_level(payload.plan_level)

    if not plan_id:
        raise HTTPException(
            status_code=500,
            detail=f"Falta configurar PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_{payload.plan_level} en Render.",
        )

    token = get_token()
    start_time = payload.start_time or first_day_next_month_utc()
    backend_url = BASE_PUBLIC_URL.rstrip("/")
    return_query = urllib.parse.urlencode(
        {
            "user_id": user.id,
            "plan_level": payload.plan_level,
        }
    )

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
            "return_url": (
                f"{backend_url}/payments/paypal/subscriptions/return"
                f"?{return_query}"
            ),
            "cancel_url": (
                f"{backend_url}/payments/paypal/subscriptions/cancel"
                f"?{return_query}"
            ),
        },
    }

    response = paypal_request("POST", "/v1/billing/subscriptions", token, body)

    subscription_id = response.get("id")
    approve_url = extract_approve_url(response.get("links", []))
    paypal_status = response.get("status", "APPROVAL_PENDING")
    monthly_amount = MONTHLY_PRICES[payload.plan_level]
    base_monthly_amount = BASE_MONTHLY_PRICES[payload.plan_level]
    signup_fee = get_signup_fee_with_iva()
    first_payment_amount = get_first_payment_amount_by_level(payload.plan_level)

    if not subscription_id:
        raise HTTPException(status_code=500, detail="PayPal no devolvió subscription_id")

    existing_payment = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.paypal_order_id == subscription_id)
        .first()
    )

    if not existing_payment:
        payment = models.MembershipPayment(
            user_id=user.id,
            order_id=None,
            paypal_order_id=subscription_id,
            amount=first_payment_amount,
            currency="USD",
            status="subscription_created",
        )

        safe_set(payment, "provider", "paypal")
        safe_set(payment, "payment_type", "subscription")
        safe_set(payment, "payment_reference", subscription_id)
        safe_set(payment, "raw_payload", json.dumps(response))
        safe_set(payment, "admin_verified", False)
        safe_set(payment, "signup_amount", signup_fee)
        safe_set(payment, "monthly_amount", monthly_amount)
        safe_set(payment, "plan_level", payload.plan_level)

        db.add(payment)

    user.membership_level = payload.plan_level
    db.commit()

    return {
        "message": "Suscripción PayPal creada",
        "user_id": user.id,
        "plan_level": payload.plan_level,
        "base_monthly_amount": base_monthly_amount,
        "monthly_amount": monthly_amount,
        "iva_rate": IVA_RATE,
        "signup_fee": signup_fee,
        "first_payment_amount": first_payment_amount,
        "setup_fee": first_payment_amount,
        "start_time": start_time,
        "paypal_subscription_id": subscription_id,
        "subscription_status": paypal_status,
        "approve_url": approve_url,
        "approval_url": approve_url,
        "links": response.get("links", []),
    }


@router.post("/activate-level-1")
def activate_level_1_subscription(payload: ActivateLevel1Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    token = get_token()

    response = paypal_request(
        "GET",
        f"/v1/billing/subscriptions/{payload.subscription_id}",
        token,
    )

    paypal_status = response.get("status")

    if paypal_status != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail=f"La suscripción PayPal todavía no está activa. Estado actual: {paypal_status}",
        )

    plan_id = response.get("plan_id")

    detected_level = None

    for level in [1, 2, 3]:
        expected_plan_id = get_plan_id_by_level(level)
        if expected_plan_id and plan_id == expected_plan_id:
            detected_level = level
            break

    if detected_level is None:
        detected_level = user.membership_level or 1

    result = activate_user_subscription_core(
        db=db,
        user=user,
        subscription_id=payload.subscription_id,
        plan_level=detected_level,
        paypal_payload=response,
    )

    return {
        "message": f"Membresía Nivel {detected_level} activada correctamente",
        **result,
    }

@router.get("/return", response_class=HTMLResponse)
def subscription_return(request: Request, db: Session = Depends(get_db)):
    user_id = request.query_params.get("user_id") or ""
    plan_level = request.query_params.get("plan_level") or ""
    subscription_id = request.query_params.get("subscription_id") or ""

    if user_id.isdigit() and not subscription_id:
        latest_payment = (
            db.query(models.MembershipPayment)
            .filter(
                models.MembershipPayment.user_id == int(user_id),
                models.MembershipPayment.payment_type == "subscription",
            )
            .order_by(models.MembershipPayment.id.desc())
            .first()
        )
        if latest_payment:
            subscription_id = latest_payment.paypal_order_id or ""

    if not subscription_id:
        subscription_id = (
            request.query_params.get("ba_token")
            or request.query_params.get("token")
            or ""
        )

    app_url = get_mayu_app_public_url().rstrip("/")
    query = {
        "payment": "approved",
    }
    if subscription_id:
        query["subscription_id"] = subscription_id
    if user_id:
        query["user_id"] = user_id
    if plan_level:
        query["plan_level"] = plan_level
    app_return_url = (
        f"{app_url}/membership/paypal-success?{urllib.parse.urlencode(query)}"
    )

    return HTMLResponse(
        content=f"""
        <html>
            <head>
                <title>Suscripción activada</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: Arial; background:#0f172a; color:white; text-align:center; padding:40px;">
                <h1>Pago aprobado en PayPal</h1>
                <p>Estamos regresando a Mayu Wellness Club para confirmar tu membresía.</p>
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


@router.get("/cancel", response_class=HTMLResponse)
def subscription_cancel(request: Request):
    user_id = request.query_params.get("user_id") or ""
    plan_level = request.query_params.get("plan_level") or ""
    app_url = get_mayu_app_public_url().rstrip("/")
    query = {"payment": "cancelled"}
    if user_id:
        query["user_id"] = user_id
    if plan_level:
        query["plan_level"] = plan_level
    app_return_url = (
        f"{app_url}/membership/paypal-success?{urllib.parse.urlencode(query)}"
    )

    return HTMLResponse(
        content=f"""
        <html>
            <head>
                <title>Suscripción cancelada</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: Arial; background:#111; color:white; text-align:center; padding:40px;">
                <h1>Proceso cancelado</h1>
                <p>No se activó la mensualidad automática.</p>
                <p style="margin-top:28px;">
                    <a href="{app_return_url}" style="background:#64748b;color:white;padding:14px 22px;border-radius:10px;text-decoration:none;font-weight:bold;">
                        Volver a Mayu
                    </a>
                </p>
            </body>
        </html>
        """
    )


@router.post("/webhook")
async def subscription_webhook(request: Request, db: Session = Depends(get_db)):
    event = await request.json()
    event_type = event.get("event_type")
    resource = event.get("resource", {})

    subscription_id = (
        resource.get("billing_agreement_id")
        or resource.get("subscription_id")
        or resource.get("id")
    )

    if not subscription_id:
        return {"status": "ignored", "reason": "No subscription id"}

    subscription_payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.paypal_order_id == subscription_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .first()
    )

    user = None

    if subscription_payment:
        user = (
            db.query(models.User)
            .filter(models.User.id == subscription_payment.user_id)
            .first()
        )

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        if subscription_payment:
            subscription_payment.status = "subscription_active"
            safe_set(subscription_payment, "raw_payload", json.dumps(event))

        if user:
            level = user.membership_level or 1
            activate_user_subscription_core(
                db=db,
                user=user,
                subscription_id=subscription_id,
                plan_level=level,
                paypal_payload=event,
            )
        else:
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
        if user:
            monthly_payment = create_monthly_membership_payment_from_webhook(
                db=db,
                user=user,
                subscription_id=subscription_id,
                event=event,
            )
            db.commit()

            return {
                "status": "ok",
                "event": event_type,
                "subscription_id": subscription_id,
                "payment_id": monthly_payment.id,
                "payment_status": monthly_payment.status,
                "admin_verified": monthly_payment.admin_verified,
            }

        if subscription_payment:
            subscription_payment.status = "subscription_paid"
            subscription_payment.paid_at = datetime.utcnow()
            safe_set(subscription_payment, "raw_payload", json.dumps(event))

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
        if subscription_payment:
            subscription_payment.status = "subscription_inactive"
            safe_set(subscription_payment, "raw_payload", json.dumps(event))

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
        next_billing_time = (
            billing_info.get("next_billing_time") if billing_info else None
        )

        if paypal_status == "ACTIVE":
            payment.status = "subscription_active"
            user.membership_active = True
            user.is_active = True
            user.status = "active"

            safe_set(user, "paypal_subscription_id", subscription_id)
            safe_set(user, "subscription_status", "ACTIVE")
            safe_set(user, "next_billing_date", next_billing_time)

            db.commit()

        elif paypal_status in ["APPROVAL_PENDING"]:
            payment.status = "subscription_created"
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
import os
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import SessionLocal
import models
from marketing import send_welcome_member_notifications

router = APIRouter(
    prefix="/payments/paypal/subscriptions",
    tags=["PayPal Subscriptions"],
)

BASE_PUBLIC_URL = "https://mayu-wellness-backend-v1.onrender.com"


def get_mayu_app_public_url():
    return os.getenv("MAYU_APP_PUBLIC_URL", "http://127.0.0.1:5186").strip()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_paypal_mode():
    return os.getenv("PAYPAL_SUBSCRIPTIONS_MODE", "sandbox").lower().strip()


def get_paypal_client_id():
    value = os.getenv("PAYPAL_SUBSCRIPTIONS_CLIENT_ID")
    return value.strip() if value else None


def get_paypal_client_secret():
    value = os.getenv("PAYPAL_SUBSCRIPTIONS_CLIENT_SECRET")
    return value.strip() if value else None


def get_base_url():
    return (
        "https://api-m.sandbox.paypal.com"
        if get_paypal_mode() == "sandbox"
        else "https://api-m.paypal.com"
    )


def get_plan_id_by_level(level: int):
    sandbox_plan_ids = {
        1: "P-21C59754B6148072NNJMPWRQ",
        2: "P-1T277384JR2166440NJMPWRI",
        3: "P-6FL801269K374750WNJMPWRY",
    }
    if get_paypal_mode() == "sandbox":
        return sandbox_plan_ids.get(level)

    env_map = {
        1: "PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_1",
        2: "PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_2",
        3: "PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_3",
    }
    return os.getenv(env_map.get(level, "") or "")


def mask_paypal_id(value: Optional[str]):
    if not value:
        return None
    clean_value = value.strip()
    if len(clean_value) <= 6:
        return "••••"
    return f"{clean_value[:4]}••••{clean_value[-4:]}"


IVA_RATE = 0.12
SIGNUP_FEE_BASE = 5.00

BASE_MONTHLY_PRICES = {
    1: 40.00,
    2: 50.00,
    3: 60.00,
}

MONTHLY_PRICES = {
    level: round(price * (1 + IVA_RATE), 2)
    for level, price in BASE_MONTHLY_PRICES.items()
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


class ActivateLevel1Request(BaseModel):
    user_id: int
    subscription_id: str


class CreateProductRequest(BaseModel):
    name: str = "Mayu Wellness Club"
    description: str = "Membresías recurrentes Mayu Wellness Club"


class CreatePlanRequest(BaseModel):
    product_id: str
    plan_level: int
    currency: str = "USD"


def safe_set(obj, attr, value):
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def sync_member_cards_id_sequence(db: Session):
    db.execute(text("""
        SELECT setval(
            pg_get_serial_sequence('member_cards', 'id'),
            COALESCE((SELECT MAX(id) FROM member_cards), 0) + 1,
            false
        )
    """))
    db.flush()


def get_token():
    client_id = get_paypal_client_id()
    client_secret = get_paypal_client_secret()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Faltan PAYPAL_SUBSCRIPTIONS_CLIENT_ID o PAYPAL_SUBSCRIPTIONS_CLIENT_SECRET",
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


def get_current_cycle():
    now = datetime.now()
    return now.month, now.year


def get_plan_by_level(db: Session, level: int):
    return db.query(models.Plan).filter(models.Plan.level == level).first()


def get_signup_fee_with_iva() -> float:
    return round(SIGNUP_FEE_BASE * (1 + IVA_RATE), 2)


def get_first_payment_amount_by_level(level: int) -> float:
    if level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Nivel inválido")
    return round(MONTHLY_PRICES[level] + get_signup_fee_with_iva(), 2)


def get_or_create_member_card_core(db: Session, user: models.User):
    user_id = user.id
    membership_level = user.membership_level
    membership_active = user.membership_active

    card = (
        db.query(models.MemberCard)
        .filter(models.MemberCard.user_id == user_id)
        .first()
    )

    if card:
        member_code = f"MAYU-{membership_level}-{card.id:06d}"
        card.member_code = member_code
        card.level_snapshot = membership_level
        card.status = "active" if membership_active else "inactive"
        card.expires_at = "Indefinido"
        db.commit()
        db.refresh(card)
        return card

    import uuid

    def create_member_card_once():
        card = models.MemberCard(
            user_id=user_id,
            member_code="",
            qr_token=str(uuid.uuid4()),
            level_snapshot=membership_level,
            status="active",
            expires_at="Indefinido",
        )

        db.add(card)
        db.flush()
        card.member_code = f"MAYU-{membership_level}-{card.id:06d}"
        db.commit()
        db.refresh(card)
        return card

    sync_member_cards_id_sequence(db)
    try:
        return create_member_card_once()
    except IntegrityError:
        db.rollback()
        sync_member_cards_id_sequence(db)
        return create_member_card_once()


def get_latest_selection_with_items(db: Session, user_id: int):
    selections = (
        db.query(models.MonthlySelection)
        .filter(models.MonthlySelection.user_id == user_id)
        .order_by(
            models.MonthlySelection.year.desc(),
            models.MonthlySelection.month.desc(),
            models.MonthlySelection.id.desc(),
        )
        .all()
    )

    for selection in selections:
        count = (
            db.query(models.MonthlySelectionItem)
            .filter(models.MonthlySelectionItem.monthly_selection_id == selection.id)
            .count()
        )
        if count > 0:
            return selection

    return None


def get_pending_selection_for_payment(db: Session, user_id: int):
    return (
        db.query(models.MonthlySelection)
        .outerjoin(
            models.Order,
            models.Order.monthly_selection_id == models.MonthlySelection.id,
        )
        .filter(
            models.MonthlySelection.user_id == user_id,
            models.MonthlySelection.status.in_(["confirmed", "draft"]),
            models.Order.id == None,
        )
        .order_by(
            models.MonthlySelection.year.asc(),
            models.MonthlySelection.month.asc(),
            models.MonthlySelection.id.asc(),
        )
        .first()
    )


def get_or_create_initial_monthly_selection(db: Session, user: models.User):
    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene nivel de membresía asignado",
        )

    plan = get_plan_by_level(db, user.membership_level)

    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"No existe plan local para nivel {user.membership_level}",
        )

    month, year = get_current_cycle()

    selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )

    if selection:
        selection.plan_id = plan.id
        selection.editable = True
        db.commit()
        db.refresh(selection)
        return selection

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=month,
        year=year,
        status="draft",
        editable=True,
    )

    db.add(selection)
    db.commit()
    db.refresh(selection)
    return selection


def get_or_create_monthly_selection_for_payment(
    db: Session,
    user: models.User,
    month: int,
    year: int,
):
    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene nivel de membresía asignado",
        )

    plan = get_plan_by_level(db, user.membership_level)

    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"No existe plan local para nivel {user.membership_level}",
        )

    selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )

    if selection:
        selection.plan_id = plan.id
        db.flush()
        return selection

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=month,
        year=year,
        status="draft",
        editable=True,
    )

    db.add(selection)
    db.flush()

    previous = get_latest_selection_with_items(db, user.id)

    if previous:
        previous_items = (
            db.query(models.MonthlySelectionItem)
            .filter(models.MonthlySelectionItem.monthly_selection_id == previous.id)
            .all()
        )

        for item in previous_items:
            db.add(
                models.MonthlySelectionItem(
                    monthly_selection_id=selection.id,
                    product_id=item.product_id,
                    quantity=item.quantity or 1,
                )
            )

        selection.status = "confirmed"

    return selection


def get_order_for_selection(db: Session, user_id: int, month: int, year: int):
    return (
        db.query(models.Order)
        .filter(
            models.Order.user_id == user_id,
            models.Order.month == month,
            models.Order.year == year,
        )
        .order_by(models.Order.id.desc())
        .first()
    )


def extract_paypal_amount(resource: dict, default_amount: float):
    amount_data = resource.get("amount") or resource.get(
        "seller_receivable_breakdown", {}
    ).get("gross_amount")

    if isinstance(amount_data, dict):
        value = amount_data.get("total") or amount_data.get("value")
        try:
            return float(value)
        except Exception:
            return default_amount

    return default_amount


def create_monthly_membership_payment_from_webhook(
    db: Session,
    user: models.User,
    subscription_id: str,
    event: dict,
):
    resource = event.get("resource", {})

    paypal_payment_id = (
        resource.get("id")
        or resource.get("sale_id")
        or resource.get("capture_id")
        or f"{subscription_id}-{event.get('id', datetime.utcnow().timestamp())}"
    )

    existing = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.paypal_order_id == str(paypal_payment_id))
        .first()
    )

    if existing:
        return existing

    selection = get_pending_selection_for_payment(db, user.id)

    if selection:
        month = selection.month
        year = selection.year
    else:
        month, year = get_current_cycle()
        selection = get_or_create_monthly_selection_for_payment(
            db=db,
            user=user,
            month=month,
            year=year,
        )

    existing_order = get_order_for_selection(
        db=db,
        user_id=user.id,
        month=month,
        year=year,
    )

    level = user.membership_level or 1
    default_amount = MONTHLY_PRICES.get(level, 40.00)
    amount = extract_paypal_amount(resource, default_amount)

    payment = models.MembershipPayment(
        user_id=user.id,
        order_id=existing_order.id if existing_order else None,
        paypal_order_id=str(paypal_payment_id),
        amount=amount,
        currency="USD",
        status="subscription_paid",
        paid_at=datetime.utcnow(),
    )

    safe_set(payment, "provider", "paypal")
    safe_set(payment, "payment_type", "subscription_renewal")
    safe_set(payment, "payment_reference", subscription_id)
    safe_set(payment, "raw_payload", json.dumps(event))
    safe_set(payment, "admin_verified", False)
    safe_set(payment, "payer_email", resource.get("payer", {}).get("email_address"))
    safe_set(payment, "monthly_selection_id", selection.id if selection else None)

    db.add(payment)

    user.membership_active = True
    user.is_active = True
    user.status = "active"

    db.flush()
    return payment


def activate_user_subscription_core(
    db: Session,
    user: models.User,
    subscription_id: str,
    plan_level: int,
    paypal_payload=None,
):
    if not subscription_id or not subscription_id.strip():
        raise HTTPException(status_code=400, detail="subscription_id requerido")

    subscription_id = subscription_id.strip()

    existing_payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.paypal_order_id == subscription_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .first()
    )

    if existing_payment and existing_payment.user_id != user.id:
        raise HTTPException(
            status_code=409,
            detail="Esta suscripción ya está asociada a otro usuario",
        )

    user.membership_level = plan_level
    user.membership_active = True
    user.is_active = True
    user.status = "active"

    if existing_payment:
        existing_payment.status = "subscription_active"
        existing_payment.amount = get_first_payment_amount_by_level(plan_level)
        safe_set(existing_payment, "payment_reference", subscription_id)
        safe_set(existing_payment, "signup_amount", get_signup_fee_with_iva())
        safe_set(existing_payment, "monthly_amount", MONTHLY_PRICES[plan_level])
        safe_set(existing_payment, "plan_level", plan_level)
        if paypal_payload is not None:
            safe_set(existing_payment, "raw_payload", json.dumps(paypal_payload))
    else:
        payment = models.MembershipPayment(
            user_id=user.id,
            order_id=None,
            paypal_order_id=subscription_id,
            amount=get_first_payment_amount_by_level(plan_level),
            currency="USD",
            status="subscription_active",
        )

        safe_set(payment, "provider", "paypal")
        safe_set(payment, "payment_type", "subscription")
        safe_set(payment, "payment_reference", subscription_id)
        safe_set(payment, "admin_verified", False)
        safe_set(payment, "signup_amount", get_signup_fee_with_iva())
        safe_set(payment, "monthly_amount", MONTHLY_PRICES[plan_level])
        safe_set(payment, "plan_level", plan_level)

        if paypal_payload is not None:
            safe_set(payment, "raw_payload", json.dumps(paypal_payload))

        db.add(payment)

    db.commit()
    db.refresh(user)

    card = get_or_create_member_card_core(db, user)
    month, year = get_current_cycle()

    selection = get_or_create_monthly_selection_for_payment(
        db=db,
        user=user,
        month=month,
        year=year,
    )

    subscription_payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.paypal_order_id == subscription_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .first()
    )

    if subscription_payment:
        safe_set(subscription_payment, "monthly_selection_id", selection.id)
        db.commit()

    try:
        welcome_sync = send_welcome_member_notifications(
            db=db,
            user=user,
            trigger="paypal_subscription_activation",
        )
        db.commit()
    except Exception as exc:
        welcome_sync = {
            "sent": False,
            "error": str(exc),
        }

    return {
        "status": "activated",
        "user_id": user.id,
        "membership_active": user.membership_active,
        "membership_level": user.membership_level,
        "paypal_subscription_id": subscription_id,
        "card_id": card.id,
        "member_code": card.member_code,
        "selection_id": selection.id,
        "selection_month": selection.month,
        "selection_year": selection.year,
        "welcome_notifications": welcome_sync,
    }


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
        "base_monthly_prices": BASE_MONTHLY_PRICES,
        "monthly_prices": MONTHLY_PRICES,
        "iva_rate": IVA_RATE,
        "signup_fee": get_signup_fee_with_iva(),
        "first_payment_amounts": {
            level: get_first_payment_amount_by_level(level)
            for level in MONTHLY_PRICES
        },
    }


def extract_regular_monthly_amount(plan: dict):
    for cycle in plan.get("billing_cycles") or []:
        if cycle.get("tenure_type") != "REGULAR":
            continue
        fixed_price = (
            (cycle.get("pricing_scheme") or {})
            .get("fixed_price")
            or {}
        )
        value = fixed_price.get("value")
        if value is not None:
            return float(value)
    return None


def extract_setup_fee_amount(plan: dict):
    fixed_price = ((plan.get("payment_preferences") or {}).get("setup_fee") or {})
    value = fixed_price.get("value")
    return float(value) if value is not None else 0.0


@router.get("/debug-plans")
def debug_paypal_plans():
    token = get_token()
    plans = {}

    for level in sorted(MONTHLY_PRICES.keys()):
        plan_id = get_plan_id_by_level(level)
        expected_monthly = MONTHLY_PRICES[level]
        expected_first_payment = get_first_payment_amount_by_level(level)
        expected_setup_fee = expected_first_payment

        if not plan_id:
            plans[level] = {
                "configured": False,
                "status": "missing_plan_id",
                "expected_monthly": expected_monthly,
                "expected_setup_fee": expected_setup_fee,
                "expected_first_payment": expected_first_payment,
                "expected_signup_fee": get_signup_fee_with_iva(),
            }
            continue

        try:
            response = paypal_request("GET", f"/v1/billing/plans/{plan_id}", token)
            paypal_monthly = extract_regular_monthly_amount(response)
            paypal_setup_fee = extract_setup_fee_amount(response)

            plans[level] = {
                "configured": True,
                "plan_id": mask_paypal_id(plan_id),
                "paypal_status": response.get("status"),
                "paypal_monthly": paypal_monthly,
                "paypal_initial_charge": paypal_setup_fee,
                "expected_monthly": expected_monthly,
                "expected_first_payment": expected_first_payment,
                "expected_signup_fee": get_signup_fee_with_iva(),
                "matches_expected": (
                    paypal_monthly == expected_monthly
                    and paypal_setup_fee == expected_setup_fee
                ),
            }
        except HTTPException as exc:
            plans[level] = {
                "configured": True,
                "plan_id": mask_paypal_id(plan_id),
                "status": "paypal_check_failed",
                "detail": exc.detail,
                "expected_monthly": expected_monthly,
                "expected_setup_fee": expected_setup_fee,
                "expected_first_payment": expected_first_payment,
                "expected_signup_fee": get_signup_fee_with_iva(),
            }

    return {
        "paypal_mode": get_paypal_mode(),
        "iva_rate": IVA_RATE,
        "plans": plans,
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

    base_price = BASE_MONTHLY_PRICES[payload.plan_level]
    price = MONTHLY_PRICES[payload.plan_level]
    signup_fee = get_signup_fee_with_iva()
    first_payment_amount = get_first_payment_amount_by_level(payload.plan_level)
    plan_name = PLAN_NAMES[payload.plan_level]

    body = {
        "product_id": payload.product_id,
        "name": plan_name,
        "description": (
            f"Mensualidad recurrente {plan_name}. "
            "Valores incluyen IVA Ecuador 12%."
        ),
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
            "setup_fee": {
                "value": f"{first_payment_amount:.2f}",
                "currency_code": payload.currency,
            },
            "setup_fee_failure_action": "CONTINUE",
            "payment_failure_threshold": 1,
        },
    }

    response = paypal_request("POST", "/v1/billing/plans", token, body)

    return {
        "message": "Plan PayPal creado correctamente",
        "plan_level": payload.plan_level,
        "base_monthly_price": base_price,
        "monthly_price": price,
        "iva_rate": IVA_RATE,
        "signup_fee": signup_fee,
        "first_payment_amount": first_payment_amount,
        "setup_fee": first_payment_amount,
        "note": "PayPal cobra inscripción + primera mensualidad con IVA como setup_fee. La mensualidad recurrente empieza después y también incluye IVA.",
        "plan_id": response.get("id"),
        "response": response,
    }


@router.post("/create")
def create_subscription(payload: CreateSubscriptionRequest, db: Session = Depends(get_db)):
    if payload.plan_level not in MONTHLY_PRICES:
        raise HTTPException(status_code=400, detail="Nivel inválido")

    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    plan_id = get_plan_id_by_level(payload.plan_level)

    if not plan_id:
        raise HTTPException(
            status_code=500,
            detail=f"Falta configurar PAYPAL_SUBSCRIPTIONS_PLAN_ID_LEVEL_{payload.plan_level} en Render.",
        )

    token = get_token()
    start_time = payload.start_time or first_day_next_month_utc()
    backend_url = BASE_PUBLIC_URL.rstrip("/")
    return_query = urllib.parse.urlencode(
        {
            "user_id": user.id,
            "plan_level": payload.plan_level,
        }
    )

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
            "return_url": (
                f"{backend_url}/payments/paypal/subscriptions/return"
                f"?{return_query}"
            ),
            "cancel_url": (
                f"{backend_url}/payments/paypal/subscriptions/cancel"
                f"?{return_query}"
            ),
        },
    }

    response = paypal_request("POST", "/v1/billing/subscriptions", token, body)

    subscription_id = response.get("id")
    approve_url = extract_approve_url(response.get("links", []))
    paypal_status = response.get("status", "APPROVAL_PENDING")
    monthly_amount = MONTHLY_PRICES[payload.plan_level]
    base_monthly_amount = BASE_MONTHLY_PRICES[payload.plan_level]
    signup_fee = get_signup_fee_with_iva()
    first_payment_amount = get_first_payment_amount_by_level(payload.plan_level)

    if not subscription_id:
        raise HTTPException(status_code=500, detail="PayPal no devolvió subscription_id")

    existing_payment = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.paypal_order_id == subscription_id)
        .first()
    )

    if not existing_payment:
        payment = models.MembershipPayment(
            user_id=user.id,
            order_id=None,
            paypal_order_id=subscription_id,
            amount=first_payment_amount,
            currency="USD",
            status="subscription_created",
        )

        safe_set(payment, "provider", "paypal")
        safe_set(payment, "payment_type", "subscription")
        safe_set(payment, "payment_reference", subscription_id)
        safe_set(payment, "raw_payload", json.dumps(response))
        safe_set(payment, "admin_verified", False)
        safe_set(payment, "signup_amount", signup_fee)
        safe_set(payment, "monthly_amount", monthly_amount)
        safe_set(payment, "plan_level", payload.plan_level)

        db.add(payment)

    user.membership_level = payload.plan_level
    db.commit()

    return {
        "message": "Suscripción PayPal creada",
        "user_id": user.id,
        "plan_level": payload.plan_level,
        "base_monthly_amount": base_monthly_amount,
        "monthly_amount": monthly_amount,
        "iva_rate": IVA_RATE,
        "signup_fee": signup_fee,
        "first_payment_amount": first_payment_amount,
        "setup_fee": first_payment_amount,
        "start_time": start_time,
        "paypal_subscription_id": subscription_id,
        "subscription_status": paypal_status,
        "approve_url": approve_url,
        "approval_url": approve_url,
        "links": response.get("links", []),
    }


@router.post("/activate-level-1")
def activate_level_1_subscription(payload: ActivateLevel1Request, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    token = get_token()

    response = paypal_request(
        "GET",
        f"/v1/billing/subscriptions/{payload.subscription_id}",
        token,
    )

    paypal_status = response.get("status")

    if paypal_status != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail=f"La suscripción PayPal todavía no está activa. Estado actual: {paypal_status}",
        )

    plan_id = response.get("plan_id")

    detected_level = None

    for level in [1, 2, 3]:
        expected_plan_id = get_plan_id_by_level(level)
        if expected_plan_id and plan_id == expected_plan_id:
            detected_level = level
            break

    if detected_level is None:
        detected_level = user.membership_level or 1

    result = activate_user_subscription_core(
        db=db,
        user=user,
        subscription_id=payload.subscription_id,
        plan_level=detected_level,
        paypal_payload=response,
    )

    return {
        "message": f"Membresía Nivel {detected_level} activada correctamente",
        **result,
    }

@router.get("/return", response_class=HTMLResponse)
def subscription_return(request: Request, db: Session = Depends(get_db)):
    user_id = request.query_params.get("user_id") or ""
    plan_level = request.query_params.get("plan_level") or ""
    subscription_id = request.query_params.get("subscription_id") or ""

    if user_id.isdigit() and not subscription_id:
        latest_payment = (
            db.query(models.MembershipPayment)
            .filter(
                models.MembershipPayment.user_id == int(user_id),
                models.MembershipPayment.payment_type == "subscription",
            )
            .order_by(models.MembershipPayment.id.desc())
            .first()
        )
        if latest_payment:
            subscription_id = latest_payment.paypal_order_id or ""

    if not subscription_id:
        subscription_id = (
            request.query_params.get("ba_token")
            or request.query_params.get("token")
            or ""
        )

    app_url = get_mayu_app_public_url().rstrip("/")
    query = {
        "payment": "approved",
    }
    if subscription_id:
        query["subscription_id"] = subscription_id
    if user_id:
        query["user_id"] = user_id
    if plan_level:
        query["plan_level"] = plan_level
    app_return_url = (
        f"{app_url}/membership/paypal-success?{urllib.parse.urlencode(query)}"
    )

    return HTMLResponse(
        content=f"""
        <html>
            <head>
                <title>Suscripción activada</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: Arial; background:#0f172a; color:white; text-align:center; padding:40px;">
                <h1>Pago aprobado en PayPal</h1>
                <p>Estamos regresando a Mayu Wellness Club para confirmar tu membresía.</p>
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


@router.get("/cancel", response_class=HTMLResponse)
def subscription_cancel(request: Request):
    user_id = request.query_params.get("user_id") or ""
    plan_level = request.query_params.get("plan_level") or ""
    app_url = get_mayu_app_public_url().rstrip("/")
    query = {"payment": "cancelled"}
    if user_id:
        query["user_id"] = user_id
    if plan_level:
        query["plan_level"] = plan_level
    app_return_url = (
        f"{app_url}/membership/paypal-success?{urllib.parse.urlencode(query)}"
    )

    return HTMLResponse(
        content=f"""
        <html>
            <head>
                <title>Suscripción cancelada</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: Arial; background:#111; color:white; text-align:center; padding:40px;">
                <h1>Proceso cancelado</h1>
                <p>No se activó la mensualidad automática.</p>
                <p style="margin-top:28px;">
                    <a href="{app_return_url}" style="background:#64748b;color:white;padding:14px 22px;border-radius:10px;text-decoration:none;font-weight:bold;">
                        Volver a Mayu
                    </a>
                </p>
            </body>
        </html>
        """
    )


@router.post("/webhook")
async def subscription_webhook(request: Request, db: Session = Depends(get_db)):
    event = await request.json()
    event_type = event.get("event_type")
    resource = event.get("resource", {})

    subscription_id = (
        resource.get("billing_agreement_id")
        or resource.get("subscription_id")
        or resource.get("id")
    )

    if not subscription_id:
        return {"status": "ignored", "reason": "No subscription id"}

    subscription_payment = (
        db.query(models.MembershipPayment)
        .filter(
            models.MembershipPayment.paypal_order_id == subscription_id,
            models.MembershipPayment.payment_type == "subscription",
        )
        .first()
    )

    user = None

    if subscription_payment:
        user = (
            db.query(models.User)
            .filter(models.User.id == subscription_payment.user_id)
            .first()
        )

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        if subscription_payment:
            subscription_payment.status = "subscription_active"
            safe_set(subscription_payment, "raw_payload", json.dumps(event))

        if user:
            level = user.membership_level or 1
            activate_user_subscription_core(
                db=db,
                user=user,
                subscription_id=subscription_id,
                plan_level=level,
                paypal_payload=event,
            )
        else:
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
        if user:
            monthly_payment = create_monthly_membership_payment_from_webhook(
                db=db,
                user=user,
                subscription_id=subscription_id,
                event=event,
            )
            db.commit()

            return {
                "status": "ok",
                "event": event_type,
                "subscription_id": subscription_id,
                "payment_id": monthly_payment.id,
                "payment_status": monthly_payment.status,
                "admin_verified": monthly_payment.admin_verified,
            }

        if subscription_payment:
            subscription_payment.status = "subscription_paid"
            subscription_payment.paid_at = datetime.utcnow()
            safe_set(subscription_payment, "raw_payload", json.dumps(event))

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
        if subscription_payment:
            subscription_payment.status = "subscription_inactive"
            safe_set(subscription_payment, "raw_payload", json.dumps(event))

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
        next_billing_time = (
            billing_info.get("next_billing_time") if billing_info else None
        )

        if paypal_status == "ACTIVE":
            payment.status = "subscription_active"
            user.membership_active = True
            user.is_active = True
            user.status = "active"

            safe_set(user, "paypal_subscription_id", subscription_id)
            safe_set(user, "subscription_status", "ACTIVE")
            safe_set(user, "next_billing_date", next_billing_time)

            db.commit()

        elif paypal_status in ["APPROVAL_PENDING"]:
            payment.status = "subscription_created"
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
