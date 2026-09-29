import json
import time
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from marketplace import (
    sync_marketplace_doctor_wallet_after_commit,
    validate_doctor_prescriber_identifier,
    validate_member_discount_code,
)
from marketplace_paypal import (
    fulfill_education_payment_if_needed,
    fulfill_pharmacy_payment_if_needed,
    resolve_marketplace_buyer_user,
)
from nuvei_membership import (
    get_callback_url,
    get_client_app_code,
    get_client_app_key,
    get_nuvei_mode,
    is_nuvei_success,
    nuvei_order,
    nuvei_request,
    signup_nuvei_user_id,
)
from pharmacy_loyalty import sync_marketplace_loyalty_wallet_after_commit


router = APIRouter(
    prefix="/payments/nuvei/marketplace",
    tags=["Nuvei Marketplace"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class MarketplaceItem(BaseModel):
    product_id: int
    quantity: int = 1


class NuveiMarketplaceCheckout(BaseModel):
    item_type: str
    buyer_name: str
    buyer_phone: str
    buyer_email: EmailStr
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
    items: List[MarketplaceItem]
    token: str
    status: str = "valid"
    holder_name: Optional[str] = None
    bin: Optional[str] = None
    last4: Optional[str] = None
    card_type: Optional[str] = None
    expiry_month: Optional[str] = None
    expiry_year: Optional[str] = None
    origin: Optional[str] = None
    transaction_reference: Optional[str] = None


@router.get("/client-config")
def client_config(email: EmailStr, phone: str):
    normalized_email = email.strip().lower()
    clean_phone = phone.strip()
    if not clean_phone:
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")
    return {
        "mode": get_nuvei_mode(),
        "client_app_code": get_client_app_code(),
        "client_app_key": get_client_app_key(),
        "user": {
            "id": signup_nuvei_user_id(normalized_email, clean_phone),
            "email": normalized_email,
            "phone": clean_phone,
        },
        "callback_url": get_callback_url(),
    }


def _prepare_cart(payload: NuveiMarketplaceCheckout, db: Session):
    item_type = payload.item_type.strip().lower()
    if item_type not in {"pharmacy", "education"}:
        raise HTTPException(status_code=400, detail="item_type debe ser pharmacy o education")
    if not payload.items:
        raise HTTPException(status_code=400, detail="El carrito está vacío")

    subtotal = 0.0
    items = []
    for requested in payload.items:
        if requested.quantity <= 0:
            raise HTTPException(status_code=400, detail="La cantidad debe ser mayor a cero")
        if item_type == "education":
            resource = (
                db.query(models.EducationResource)
                .filter(models.EducationResource.id == requested.product_id)
                .filter(models.EducationResource.active == True)
                .first()
            )
            if not resource:
                raise HTTPException(status_code=404, detail=f"Contenido educativo {requested.product_id} no encontrado")
            unit_price = float(resource.price or 0)
            line_total = round(unit_price * requested.quantity, 2)
            items.append({
                "product_id": resource.id,
                "resource_id": resource.id,
                "title": resource.title,
                "quantity": requested.quantity,
                "unit_price": unit_price,
                "total": line_total,
            })
        else:
            product = (
                db.query(models.MarketplaceProduct)
                .filter(models.MarketplaceProduct.id == requested.product_id)
                .filter(models.MarketplaceProduct.active == True)
                .first()
            )
            if not product:
                raise HTTPException(status_code=404, detail=f"Producto {requested.product_id} no encontrado")
            if product.stock < requested.quantity:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para {product.name}")
            unit_price = float(product.price or 0)
            line_total = round(unit_price * requested.quantity, 2)
            items.append({
                "product_id": product.id,
                "title": product.name,
                "quantity": requested.quantity,
                "unit_price": unit_price,
                "total": line_total,
            })
        subtotal += line_total

    subtotal = round(subtotal, 2)
    discount_code = None
    discount_percent = 0.0
    discount_amount = 0.0
    doctor_identifier = None
    if item_type == "pharmacy":
        discount = validate_member_discount_code(db, payload.discount_code)
        if discount:
            discount_code = discount["discount_code"]
            discount_percent = float(discount["discount_percent"])
            discount_amount = round(subtotal * discount_percent / 100, 2)
        doctor = validate_doctor_prescriber_identifier(
            db, payload.doctor_prescriber_identifier
        )
        if doctor:
            doctor_identifier = doctor["doctor_prescriber_identifier"]

    total = round(subtotal - discount_amount, 2)
    if total <= 0:
        raise HTTPException(status_code=400, detail="El total debe ser mayor a cero")
    return {
        "item_type": item_type,
        "items": items,
        "subtotal": subtotal,
        "discount_code": discount_code,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "doctor_prescriber_identifier": doctor_identifier,
        "total": total,
    }


@router.post("/checkout")
def checkout(payload: NuveiMarketplaceCheckout, db: Session = Depends(get_db)):
    if not payload.token.strip():
        raise HTTPException(status_code=400, detail="Nuvei no devolvió token de tarjeta")

    cart = _prepare_cart(payload, db)
    user = resolve_marketplace_buyer_user(
        db,
        None,
        payload.buyer_name,
        payload.buyer_phone,
        payload.buyer_email,
    )
    now = datetime.utcnow()
    dev_reference = f"MKT-NUVEI-{cart['item_type'][:3].upper()}-{user.id}-{int(time.time())}"
    description = (
        f"Mayu Educación - {len(cart['items'])} contenidos"
        if cart["item_type"] == "education"
        else f"Marketplace Farmacia Mayu - {len(cart['items'])} productos"
    )
    original_payload = {
        "marketplace": {
            **cart,
            "pharmacy_loyalty_identifier": (
                payload.pharmacy_loyalty_identifier.strip()
                if payload.pharmacy_loyalty_identifier
                else None
            ),
        },
        "buyer": {
            "user_id": user.id,
            "name": payload.buyer_name.strip(),
            "email": payload.buyer_email.strip().lower(),
            "phone": payload.buyer_phone.strip(),
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
        "nuvei_card": {
            "holder_name": payload.holder_name,
            "bin": payload.bin,
            "last4": payload.last4,
            "type": payload.card_type,
            "expiry_month": payload.expiry_month,
            "expiry_year": payload.expiry_year,
            "origin": payload.origin,
            "transaction_reference": payload.transaction_reference,
        },
    }
    payment = models.MembershipPayment(
        user_id=user.id,
        order_id=None,
        amount=cart["total"],
        currency=payload.currency,
        status="created",
        provider="nuvei",
        payment_type=f"marketplace_{cart['item_type']}",
        payment_reference=dev_reference,
        payer_email=payload.buyer_email.strip().lower(),
        raw_payload=json.dumps(original_payload),
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    request_body = {
        "user": {
            "id": signup_nuvei_user_id(payload.buyer_email, payload.buyer_phone),
            "email": payload.buyer_email.strip().lower(),
            "phone": payload.buyer_phone.strip(),
        },
        "order": nuvei_order(cart["total"], description, dev_reference),
        "card": {"token": payload.token.strip()},
    }
    try:
        response = nuvei_request("POST", "/v2/transaction/debit/", request_body)
        transaction = response.get("transaction") or response
        payment.paypal_order_id = str(transaction.get("id") or dev_reference)
        payment.payment_reference = str(transaction.get("id") or dev_reference)
        payment.raw_payload = json.dumps({**original_payload, "nuvei": response})
        if not is_nuvei_success(response):
            payment.status = "failed"
            db.commit()
            raise HTTPException(
                status_code=402,
                detail={
                    "message": "Nuvei no aprobó el pago.",
                    "status": transaction.get("status"),
                    "status_detail": transaction.get("status_detail"),
                },
            )

        payment.status = "verified"
        payment.paid_at = now
        payment.admin_verified = True
        payment.admin_verified_at = now
        db.flush()

        pharmacy = fulfill_pharmacy_payment_if_needed(payment, db)
        education = fulfill_education_payment_if_needed(payment, db)
        db.commit()

        if pharmacy and pharmacy.get("loyalty"):
            pharmacy["loyalty"] = sync_marketplace_loyalty_wallet_after_commit(
                db, pharmacy["loyalty"], pharmacy.get("marketplace_order_code")
            )
        if pharmacy and pharmacy.get("doctor_commission"):
            pharmacy["doctor_commission"] = sync_marketplace_doctor_wallet_after_commit(
                db,
                pharmacy["doctor_commission"],
                pharmacy.get("marketplace_order_code"),
            )
        return {
            "success": True,
            "message": "Pago Nuvei aprobado y compra procesada.",
            "payment_id": payment.id,
            "transaction_id": transaction.get("id"),
            "amount": cart["total"],
            "item_type": cart["item_type"],
            "pharmacy_fulfillment": pharmacy,
            "education_fulfillment": education,
        }
    except HTTPException:
        raise
    except Exception:
        payment.status = "failed"
        db.commit()
        raise
