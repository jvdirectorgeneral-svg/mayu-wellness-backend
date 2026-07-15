from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from pydantic import BaseModel, EmailStr
from typing import Optional

from database import SessionLocal
from dependencies import get_current_user
from auth import hash_password
from member_cards import ambassador_commission_summary
from commissions import sync_ambassador_wallets
from models import (
    User,
    Ambassador,
    Commission,
    MembershipPayment,
    Order,
    OrderTrackingHistory,
    MonthlySelection,
    MonthlySelectionItem,
    Product,
    OrderItem,
)

router = APIRouter(prefix="/admin-dashboard", tags=["admin-dashboard"])


class AdminResetPasswordRequest(BaseModel):
    new_password: str


class AdminUpdatePhoneRequest(BaseModel):
    phone: str


class AdminUpdateMemberRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    cedula: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    reference: Optional[str] = None
    delivery_notes: Optional[str] = None
    membership_level: Optional[int] = None
    membership_active: Optional[bool] = None
    is_active: Optional[bool] = None


class AdminUpdateAmbassadorRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    cedula: Optional[str] = None
    ambassador_code: Optional[str] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None
    bank_name: Optional[str] = None
    bank_account_type: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_holder: Optional[str] = None
    bank_identification: Optional[str] = None
    payment_notes: Optional[str] = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_admin_or_superadmin(current_user: User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")
    if current_user.role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="Acceso solo para admin o superadmin")


def set_if_exists(obj, field: str, value):
    if value is not None and hasattr(obj, field):
        setattr(obj, field, value)


def clean_optional(value):
    if value is None:
        return None
    return value.strip()


def format_month_label(month: int, year: int):
    months = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
    }
    return f"{months.get(month, 'Mes')} {year}"


def generate_order_code(user_id: int, month: int, year: int):
    return f"MWC-{year}{month:02d}-U{user_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


def order_is_locked(order: Order):
    if not order:
        return False
    return order.status in {
        "approved_for_logistics",
        "preparing",
        "shipped",
        "delivered",
        "cancelled",
    }


def add_order_tracking_history(db: Session, order: Order, current_user: User, status: str, note: str):
    db.add(OrderTrackingHistory(
        order_id=order.id,
        status=status,
        note=note,
        carrier=getattr(order, "carrier", None),
        tracking_number=getattr(order, "tracking_number", None),
        tracking_url=getattr(order, "tracking_url", None),
        created_by=current_user.id if current_user else None,
    ))


def user_to_dict(u: User):
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "phone": u.phone,
        "cedula": u.cedula,
        "city": getattr(u, "city", None),
        "address": getattr(u, "address", None),
        "reference": getattr(u, "reference", None),
        "delivery_notes": getattr(u, "delivery_notes", None),
        "membership_level": u.membership_level,
        "membership_active": u.membership_active,
        "is_active": u.is_active,
        "role": u.role,
        "created_at": u.created_at,
    }


def ambassador_to_dict(db: Session, a: Ambassador):
    user = a.user
    summary = ambassador_commission_summary(db, user) if user else None
    return {
        "id": a.id,
        "user_id": a.user_id,
        "name": user.name if user else None,
        "email": user.email if user else None,
        "phone": user.phone if user else None,
        "cedula": user.cedula if user else None,
        "code": a.ambassador_code,
        "ambassador_code": a.ambassador_code,
        "status": a.status,
        "is_active": a.is_active,
        "created_at": a.created_at,
        "bank_name": getattr(a, "bank_name", None),
        "bank_account_type": getattr(a, "bank_account_type", None),
        "bank_account_number": getattr(a, "bank_account_number", None),
        "bank_account_holder": getattr(a, "bank_account_holder", None),
        "bank_identification": getattr(a, "bank_identification", None),
        "payment_notes": getattr(a, "payment_notes", None),
        "commission_balance": summary["current_display_amount"] if summary else 0.0,
        "commission_balance_label": summary["current_display_label"] if summary else "Ganancia pendiente",
        "projected_monthly_commission": summary["secondary_projected_amount"] if summary else 0.0,
        "projected_monthly_commission_raw": summary["projected_monthly_commission"] if summary else 0.0,
        "total_paid_commissions": summary["total_paid"] if summary else 0.0,
        "total_generated_commissions": summary["total_generated"] if summary else 0.0,
    }


def commission_to_admin_dict(c: Commission):
    ambassador = c.ambassador
    ambassador_user = ambassador.user if ambassador and ambassador.user else None
    referred_user = c.referred_user if hasattr(c, "referred_user") else None

    return {
        "id": c.id,
        "commission_id": c.id,
        "ambassador_id": c.ambassador_id,
        "ambassador_user_id": ambassador.user_id if ambassador else None,
        "ambassador_name": ambassador_user.name if ambassador_user else None,
        "ambassador_email": ambassador_user.email if ambassador_user else None,
        "ambassador_phone": ambassador_user.phone if ambassador_user else None,
        "ambassador_code": ambassador.ambassador_code if ambassador else None,
        "bank_name": getattr(ambassador, "bank_name", None) if ambassador else None,
        "bank_account_type": getattr(ambassador, "bank_account_type", None) if ambassador else None,
        "bank_account_number": getattr(ambassador, "bank_account_number", None) if ambassador else None,
        "bank_account_holder": getattr(ambassador, "bank_account_holder", None) if ambassador else None,
        "bank_identification": getattr(ambassador, "bank_identification", None) if ambassador else None,
        "payment_notes": getattr(ambassador, "payment_notes", None) if ambassador else None,
        "referred_user_id": c.referred_user_id,
        "referred_user_name": referred_user.name if referred_user else None,
        "referred_user_email": referred_user.email if referred_user else None,
        "plan_id": c.plan_id,
        "month": c.month,
        "year": c.year,
        "base_amount": c.base_amount,
        "commission_percent": c.commission_percent,
        "commission_amount": c.commission_amount,
        "amount": c.commission_amount,
        "member_status": c.member_status,
        "payment_status": c.payment_status,
        "eligibility_status": c.eligibility_status,
        "status": c.status,
        "generated_at": c.generated_at,
        "paid_at": c.paid_at,
        "notes": c.notes,
    }


def get_monthly_selection_data(db: Session, user_id: int, month: int, year: int):
    selection = (
        db.query(MonthlySelection)
        .filter(
            MonthlySelection.user_id == user_id,
            MonthlySelection.month == month,
            MonthlySelection.year == year,
        )
        .first()
    )

    if not selection:
        return {
            "monthly_selection_id": None,
            "editable_product": None,
            "monthly_selection_products": [],
            "monthly_selection_status": None,
        }

    products = []
    items = (
        db.query(MonthlySelectionItem)
        .filter(MonthlySelectionItem.monthly_selection_id == selection.id)
        .all()
    )

    for item in items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if product:
            products.append({
                "product_id": product.id,
                "name": product.name,
                "quantity": item.quantity,
            })

    return {
        "monthly_selection_id": selection.id,
        "editable_product": products[0]["name"] if products else None,
        "monthly_selection_products": products,
        "monthly_selection_status": selection.status,
    }


def order_items_data(order: Order):
    return {
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "name": item.product_name_snapshot,
                "product_name_snapshot": item.product_name_snapshot,
                "quantity": item.quantity,
            }
            for item in order.items
        ]
    }


def payment_to_dict(payment: MembershipPayment):
    return {
        "id": payment.id,
        "user_id": payment.user_id,
        "user_name": payment.user.name if payment.user else None,
        "order_id": payment.order_id,
        "payment_type": getattr(payment, "payment_type", None),
        "provider": getattr(payment, "provider", None),
        "paypal_order_id": payment.paypal_order_id,
        "paypal_capture_id": payment.paypal_capture_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "payer_email": payment.payer_email,
        "admin_verified": payment.admin_verified,
        "admin_verified_at": payment.admin_verified_at,
        "created_at": payment.created_at,
        "paid_at": payment.paid_at,
    }


def get_payment_for_order(db: Session, order: Order):
    payment = (
        db.query(MembershipPayment)
        .filter(MembershipPayment.order_id == order.id)
        .order_by(MembershipPayment.created_at.desc())
        .first()
    )

    if payment:
        return payment

    selection = (
        db.query(MonthlySelection)
        .filter(
            MonthlySelection.user_id == order.user_id,
            MonthlySelection.month == order.month,
            MonthlySelection.year == order.year,
        )
        .first()
    )

    if selection and hasattr(MembershipPayment, "monthly_selection_id"):
        payment = (
            db.query(MembershipPayment)
            .filter(MembershipPayment.monthly_selection_id == selection.id)
            .order_by(MembershipPayment.created_at.desc())
            .first()
        )

        if payment:
            payment.order_id = order.id
            db.flush()
            return payment

    return None


def order_to_dict(db: Session, order: Order):
    payment = get_payment_for_order(db, order)

    return {
        "id": order.id,
        "order_code": order.order_code,
        "user_id": order.user_id,
        "user_name": order.user.name if order.user else None,
        "user_phone": order.user.phone if order.user else None,
        "membership_level_snapshot": order.membership_level_snapshot,
        "user_status_snapshot": order.user_status_snapshot,
        "city_snapshot": order.city_snapshot,
        "address_snapshot": order.address_snapshot,
        "reference_snapshot": order.reference_snapshot,
        "delivery_notes_snapshot": order.delivery_notes_snapshot,
        "status": order.status,
        "order_status": order.status,
        "order_locked": order_is_locked(order),
        "logistics_notes": order.logistics_notes,
        "month": order.month,
        "year": order.year,
        "month_label": format_month_label(order.month, order.year),
        "monthLabel": format_month_label(order.month, order.year),
        "created_at": order.created_at,
        "prepared_at": order.prepared_at,
        "shipped_at": order.shipped_at,
        "delivered_at": order.delivered_at,
        "shipping_batch_date": getattr(order, "shipping_batch_date", None),
        "items_count": len(order.items),
        **order_items_data(order),
        "payment_id": payment.id if payment else None,
        "payment_type": getattr(payment, "payment_type", None) if payment else None,
        "provider": getattr(payment, "provider", None) if payment else None,
        "payment_status": payment.status if payment else None,
        "payment_amount": payment.amount if payment else None,
        "payer_email": payment.payer_email if payment else None,
        "admin_verified": payment.admin_verified if payment else False,
        "admin_verified_at": payment.admin_verified_at if payment else None,
        **get_monthly_selection_data(db, order.user_id, order.month, order.year),
    }


def get_best_selection_for_payment(db: Session, user: User):
    if not user:
        return None

    now = datetime.utcnow()

    current_selection = (
        db.query(MonthlySelection)
        .filter(
            MonthlySelection.user_id == user.id,
            MonthlySelection.month == now.month,
            MonthlySelection.year == now.year,
        )
        .first()
    )

    if current_selection:
        current_items = (
            db.query(MonthlySelectionItem)
            .filter(MonthlySelectionItem.monthly_selection_id == current_selection.id)
            .all()
        )

        current_order = (
            db.query(Order)
            .filter(
                Order.user_id == user.id,
                Order.month == now.month,
                Order.year == now.year,
            )
            .first()
        )

        if current_items and not order_is_locked(current_order):
            return current_selection

    selections = (
        db.query(MonthlySelection)
        .filter(
            MonthlySelection.user_id == user.id,
            MonthlySelection.status.in_(["draft", "confirmed"]),
        )
        .order_by(
            MonthlySelection.year.desc(),
            MonthlySelection.month.desc(),
        )
        .all()
    )

    for selection in selections:
        items = (
            db.query(MonthlySelectionItem)
            .filter(MonthlySelectionItem.monthly_selection_id == selection.id)
            .all()
        )
        if items:
            return selection

    return current_selection


def get_or_create_order_for_payment(db: Session, user: User, payment: MembershipPayment, current_user: User):
    if payment.order_id:
        order = db.query(Order).filter(Order.id == payment.order_id).first()
        if order:
            return order

    monthly = get_best_selection_for_payment(db, user)

    if not monthly:
        raise HTTPException(
            status_code=400,
            detail="No existe selección mensual para este socio. Primero debe guardar sus productos.",
        )

    items = (
        db.query(MonthlySelectionItem)
        .filter(MonthlySelectionItem.monthly_selection_id == monthly.id)
        .all()
    )

    if not items:
        raise HTTPException(
            status_code=400,
            detail="La selección mensual no tiene productos. No se puede crear orden para logística.",
        )

    existing_order = (
        db.query(Order)
        .filter(
            Order.user_id == user.id,
            Order.month == monthly.month,
            Order.year == monthly.year,
        )
        .first()
    )

    if existing_order:
        payment.order_id = existing_order.id
        return existing_order

    order = Order(
        order_code=generate_order_code(user.id, monthly.month, monthly.year),
        user_id=user.id,
        month=monthly.month,
        year=monthly.year,
        membership_level_snapshot=user.membership_level,
        user_status_snapshot="active" if user.membership_active else "inactive",
        city_snapshot=getattr(user, "city", None),
        address_snapshot=getattr(user, "address", None),
        reference_snapshot=getattr(user, "reference", None),
        delivery_notes_snapshot=getattr(user, "delivery_notes", None),
        status="approved_for_logistics",
        logistics_notes="✔ Pago verificado - listo para despacho",
    )

    db.add(order)
    db.flush()

    for item in items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if product:
            db.add(OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name_snapshot=product.name,
                quantity=item.quantity,
            ))

    monthly.editable = False
    payment.order_id = order.id

    add_order_tracking_history(
        db=db,
        order=order,
        current_user=current_user,
        status="approved_for_logistics",
        note="✔ Pago verificado - orden creada y lista para despacho",
    )

    return order


@router.get("/summary")
def get_admin_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)

    total_socios = db.query(User).filter(User.role == "member").count()
    active_socios = db.query(User).filter(User.role == "member", User.membership_active == True).count()
    inactive_socios = db.query(User).filter(User.role == "member", User.membership_active == False).count()

    total_ambassadors = db.query(Ambassador).count()
    active_ambassadors = db.query(Ambassador).filter(Ambassador.is_active == True).count()

    pending_review_orders = db.query(Order).filter(Order.status == "pending_payment_review").count()
    ready_for_logistics = db.query(Order).filter(Order.status == "approved_for_logistics").count()
    shipped_orders = db.query(Order).filter(Order.status == "shipped").count()
    delivered_orders = db.query(Order).filter(Order.status == "delivered").count()

    total_generated = db.query(func.coalesce(func.sum(Commission.commission_amount), 0)).scalar()
    total_pending = db.query(func.coalesce(func.sum(Commission.commission_amount), 0)).filter(Commission.status == "pending").scalar()
    total_paid = db.query(func.coalesce(func.sum(Commission.commission_amount), 0)).filter(Commission.status == "paid").scalar()

    pending_commissions_count = db.query(Commission).filter(Commission.status == "pending").count()
    paid_commissions_count = db.query(Commission).filter(Commission.status == "paid").count()

    return {
        "total_socios": total_socios,
        "active_socios": active_socios,
        "inactive_socios": inactive_socios,
        "total_ambassadors": total_ambassadors,
        "active_ambassadors": active_ambassadors,
        "pending_review_orders": pending_review_orders,
        "ready_for_logistics": ready_for_logistics,
        "shipped_orders": shipped_orders,
        "delivered_orders": delivered_orders,
        "total_generated": float(total_generated or 0),
        "total_pending": float(total_pending or 0),
        "total_paid": float(total_paid or 0),
        "pending_commissions_count": pending_commissions_count,
        "paid_commissions_count": paid_commissions_count,
        "commission_rule": "Nivel 1: $5, Nivel 2: $6, Nivel 3: $7.",
        "admin_review_window": "lunes a jueves",
        "shipping_window": "viernes",
    }


@router.get("/users")
def get_users(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)
    users = db.query(User).filter(User.role == "member").order_by(User.id.desc()).all()
    return {"items": [user_to_dict(u) for u in users]}


@router.put("/users/{user_id}")
def admin_update_member(user_id: int, payload: AdminUpdateMemberRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if user.role != "member":
        raise HTTPException(status_code=403, detail="Este endpoint solo permite editar socios")

    if payload.email is not None:
        clean_email = payload.email.strip().lower()
        existing_email = db.query(User).filter(User.email == clean_email, User.id != user.id).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="El correo ya está registrado por otro usuario")
        user.email = clean_email

    if payload.cedula is not None:
        clean_cedula = payload.cedula.strip()
        if clean_cedula:
            existing_cedula = db.query(User).filter(User.cedula == clean_cedula, User.id != user.id).first()
            if existing_cedula:
                raise HTTPException(status_code=400, detail="La cédula ya está registrada por otro usuario")
        user.cedula = clean_cedula

    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.phone is not None:
        user.phone = payload.phone.strip()

    set_if_exists(user, "city", clean_optional(payload.city))
    set_if_exists(user, "address", clean_optional(payload.address))
    set_if_exists(user, "reference", clean_optional(payload.reference))
    set_if_exists(user, "delivery_notes", clean_optional(payload.delivery_notes))

    if payload.membership_level is not None:
        if payload.membership_level not in {1, 2, 3}:
            raise HTTPException(status_code=400, detail="El nivel debe ser 1, 2 o 3")
        user.membership_level = payload.membership_level

    if payload.membership_active is not None:
        user.membership_active = payload.membership_active
    if payload.is_active is not None:
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)

    return {"message": "Socio actualizado correctamente", "user": user_to_dict(user)}


@router.get("/ambassadors")
def get_ambassadors(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)
    ambassadors = db.query(Ambassador).order_by(Ambassador.id.desc()).all()

    items = []
    for ambassador in ambassadors:
        ambassador_id = ambassador.id
        try:
            sync_ambassador_wallets(db, ambassador_id)
            db.expire_all()
            ambassador = db.query(Ambassador).filter(Ambassador.id == ambassador_id).first() or ambassador
        except Exception:
            pass
        items.append(ambassador_to_dict(db, ambassador))

    return {"items": items}


@router.put("/ambassadors/{ambassador_id}")
def update_ambassador(ambassador_id: int, payload: AdminUpdateAmbassadorRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)

    ambassador = db.query(Ambassador).filter(Ambassador.id == ambassador_id).first()
    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    user = db.query(User).filter(User.id == ambassador.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario del embajador no encontrado")

    if payload.email is not None:
        clean_email = payload.email.strip().lower()
        existing_email = db.query(User).filter(User.email == clean_email, User.id != user.id).first()
        if existing_email:
            raise HTTPException(status_code=400, detail="El correo ya está registrado por otro usuario")
        user.email = clean_email

    if payload.cedula is not None:
        clean_cedula = payload.cedula.strip()
        if clean_cedula:
            existing_cedula = db.query(User).filter(User.cedula == clean_cedula, User.id != user.id).first()
            if existing_cedula:
                raise HTTPException(status_code=400, detail="La cédula ya está registrada por otro usuario")
        user.cedula = clean_cedula

    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.phone is not None:
        user.phone = payload.phone.strip()

    if payload.ambassador_code is not None:
        clean_code = payload.ambassador_code.strip()
        existing_code = db.query(Ambassador).filter(
            Ambassador.ambassador_code == clean_code,
            Ambassador.id != ambassador.id,
        ).first()
        if existing_code:
            raise HTTPException(status_code=400, detail="El código de embajador ya existe")
        ambassador.ambassador_code = clean_code

    if payload.status is not None:
        ambassador.status = payload.status.strip()
    if payload.is_active is not None:
        ambassador.is_active = payload.is_active
        user.is_active = payload.is_active

    set_if_exists(ambassador, "bank_name", clean_optional(payload.bank_name))
    set_if_exists(ambassador, "bank_account_type", clean_optional(payload.bank_account_type))
    set_if_exists(ambassador, "bank_account_number", clean_optional(payload.bank_account_number))
    set_if_exists(ambassador, "bank_account_holder", clean_optional(payload.bank_account_holder))
    set_if_exists(ambassador, "bank_identification", clean_optional(payload.bank_identification))
    set_if_exists(ambassador, "payment_notes", clean_optional(payload.payment_notes))

    db.commit()
    db.refresh(ambassador)
    db.refresh(user)

    return {"message": "Embajador actualizado correctamente", "ambassador": ambassador_to_dict(db, ambassador)}


@router.put("/users/{user_id}/phone")
def admin_update_user_phone(user_id: int, payload: AdminUpdatePhoneRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role in ["admin", "superadmin", "supervisor", "logistics"]:
        raise HTTPException(status_code=403, detail="Admin solo puede modificar teléfono de socios o embajadores")

    clean_phone = payload.phone.strip()
    if not clean_phone:
        raise HTTPException(status_code=400, detail="El número de celular es obligatorio")

    user.phone = clean_phone
    db.commit()
    db.refresh(user)

    return {"message": "Teléfono actualizado correctamente", "user": user_to_dict(user)}


@router.get("/payments")
def get_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    payments = (
        db.query(MembershipPayment)
        .filter(
            MembershipPayment.payment_type.in_([
                "signup",
                "subscription",
                "subscription_renewal",
            ])
        )
        .order_by(MembershipPayment.created_at.desc())
        .all()
    )

    items = []

    for payment in payments:
        data = payment_to_dict(payment)
        user = db.query(User).filter(User.id == payment.user_id).first()
        selected_products = []

        if user:
            selection = get_best_selection_for_payment(db, user)

            if selection:
                selection_items = (
                    db.query(MonthlySelectionItem)
                    .filter(MonthlySelectionItem.monthly_selection_id == selection.id)
                    .all()
                )

                for item in selection_items:
                    product = db.query(Product).filter(Product.id == item.product_id).first()
                    if product:
                        selected_products.append(product.name)

        data["selected_products"] = selected_products
        items.append(data)

    return {"items": items}


@router.put("/payments/{payment_id}/verify")
def verify_payment(payment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)

    payment = db.query(MembershipPayment).filter(MembershipPayment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    user = db.query(User).filter(User.id == payment.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    allowed_status = [
        "completed",
        "paid",
        "verified",
        "subscription_paid",
        "subscription_active",
    ]

    if payment.status not in allowed_status:
        raise HTTPException(
            status_code=400,
            detail=f"Estado no verificable: {payment.status}",
        )

    now = datetime.utcnow()

    payment.status = "verified"
    payment.admin_verified = True
    payment.admin_verified_at = now
    payment.admin_verified_by = current_user.id

    if not payment.paid_at:
        payment.paid_at = now

    user.membership_active = True
    user.is_active = True

    order = get_or_create_order_for_payment(
        db=db,
        user=user,
        payment=payment,
        current_user=current_user,
    )

    order.status = "approved_for_logistics"
    order.user_status_snapshot = "active"
    order.membership_level_snapshot = user.membership_level
    order.logistics_notes = "✔ Pago verificado - listo para despacho"

    selection = (
        db.query(MonthlySelection)
        .filter(
            MonthlySelection.user_id == order.user_id,
            MonthlySelection.month == order.month,
            MonthlySelection.year == order.year,
        )
        .first()
    )

    if selection:
        selection.editable = False

    db.commit()
    db.refresh(payment)
    db.refresh(order)

    return {
        "message": "Pago verificado, suscripción activa y orden liberada a logística",
        "payment": payment_to_dict(payment),
        "order": order_to_dict(db, order),
    }


@router.get("/orders")
def get_orders(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)
    orders = db.query(Order).order_by(Order.created_at.desc()).all()
    return {"items": [order_to_dict(db, order) for order in orders]}


@router.put("/orders/{order_id}/approve")
def approve_order(order_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    payment = get_payment_for_order(db, order)

    if payment:
        if payment.status != "verified" or not payment.admin_verified:
            raise HTTPException(status_code=400, detail="Primero debes verificar el pago")
        payment.order_id = order.id
    else:
        selection = (
            db.query(MonthlySelection)
            .filter(
                MonthlySelection.user_id == order.user_id,
                MonthlySelection.month == order.month,
                MonthlySelection.year == order.year,
            )
            .first()
        )

        if not selection:
            raise HTTPException(
                status_code=400,
                detail="La orden no tiene pago vinculado ni selección mensual relacionada",
            )

        if selection.status not in ["confirmed", "draft"]:
            raise HTTPException(
                status_code=400,
                detail=f"La selección mensual no está aprobable. Estado actual: {selection.status}",
            )

    user = db.query(User).filter(User.id == order.user_id).first()
    if user:
        user.membership_active = True
        user.is_active = True

    old_status = order.status

    order.status = "approved_for_logistics"
    order.user_status_snapshot = "active"
    order.logistics_notes = "✔ Orden aprobada por administración - lista para despacho"

    selection = (
        db.query(MonthlySelection)
        .filter(
            MonthlySelection.user_id == order.user_id,
            MonthlySelection.month == order.month,
            MonthlySelection.year == order.year,
        )
        .first()
    )

    if selection:
        selection.editable = False
        selection.status = "confirmed"

    if old_status != "approved_for_logistics":
        add_order_tracking_history(
            db=db,
            order=order,
            current_user=current_user,
            status="approved_for_logistics",
            note="Orden aprobada manualmente por administración y liberada a logística",
        )

    db.commit()
    db.refresh(order)

    return {"message": "Orden liberada a logística", "order": order_to_dict(db, order)}


@router.get("/commissions/pending")
def get_pending_commissions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)

    commissions = (
        db.query(Commission)
        .filter(Commission.status == "pending")
        .order_by(Commission.generated_at.desc())
        .all()
    )

    return {
        "total_items": len(commissions),
        "total_pending": round(sum(float(c.commission_amount or 0) for c in commissions), 2),
        "items": [commission_to_admin_dict(c) for c in commissions],
    }


@router.get("/commissions/ranking")
def get_admin_commissions_ranking(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)

    ambassadors = db.query(Ambassador).order_by(Ambassador.id.asc()).all()
    items = []

    for ambassador in ambassadors:
        user = ambassador.user

        commissions = db.query(Commission).filter(
            Commission.ambassador_id == ambassador.id
        ).all()

        total_generated = round(sum(float(c.commission_amount or 0) for c in commissions), 2)
        total_pending = round(sum(float(c.commission_amount or 0) for c in commissions if c.status == "pending"), 2)
        total_paid = round(sum(float(c.commission_amount or 0) for c in commissions if c.status == "paid"), 2)

        items.append({
            "ambassador_id": ambassador.id,
            "ambassador_user_id": ambassador.user_id,
            "ambassador_code": ambassador.ambassador_code,
            "ambassador_name": user.name if user else None,
            "ambassador_email": user.email if user else None,
            "ambassador_phone": user.phone if user else None,
            "bank_name": getattr(ambassador, "bank_name", None),
            "bank_account_type": getattr(ambassador, "bank_account_type", None),
            "bank_account_number": getattr(ambassador, "bank_account_number", None),
            "bank_account_holder": getattr(ambassador, "bank_account_holder", None),
            "bank_identification": getattr(ambassador, "bank_identification", None),
            "payment_notes": getattr(ambassador, "payment_notes", None),
            "total_records": len(commissions),
            "total_generated": total_generated,
            "total_pending": total_pending,
            "total_paid": total_paid,
        })

    items.sort(key=lambda x: x["total_pending"], reverse=True)

    return {
        "total_ambassadors_in_ranking": len(items),
        "business_rule": "Nivel 1: $5, Nivel 2: $6, Nivel 3: $7.",
        "items": items,
    }


@router.put("/users/{user_id}/reset-password")
def admin_reset_user_password(user_id: int, payload: AdminResetPasswordRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_admin_or_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role in ["admin", "superadmin", "supervisor", "logistics"]:
        raise HTTPException(status_code=403, detail="Admin solo puede resetear socios o embajadores")

    if not payload.new_password or payload.new_password.strip() == "":
        raise HTTPException(status_code=400, detail="La nueva contraseña es obligatoria")

    user.password = hash_password(payload.new_password.strip())

    db.commit()
    db.refresh(user)

    return {
        "message": "Contraseña reseteada correctamente",
        "user_id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
    }


@router.get("/membership-cycles")
def get_membership_cycles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    payments = (
        db.query(MembershipPayment)
        .filter(
            MembershipPayment.payment_type.in_([
                "signup",
                "subscription",
                "subscription_renewal",
            ])
        )
        .order_by(MembershipPayment.created_at.desc())
        .all()
    )

    items = []

    for payment in payments:
        user = db.query(User).filter(User.id == payment.user_id).first()

        selection = None
        order = None

        if payment.order_id:
            order = db.query(Order).filter(Order.id == payment.order_id).first()

            if order and user:
                selection = (
                    db.query(MonthlySelection)
                    .filter(
                        MonthlySelection.user_id == user.id,
                        MonthlySelection.month == order.month,
                        MonthlySelection.year == order.year,
                    )
                    .first()
                )
        else:
            if user:
                selection = get_best_selection_for_payment(db, user)

        products = []

        if selection:
            selection_items = (
                db.query(MonthlySelectionItem)
                .filter(MonthlySelectionItem.monthly_selection_id == selection.id)
                .all()
            )

            for item in selection_items:
                product = (
                    db.query(Product)
                    .filter(Product.id == item.product_id)
                    .first()
                )

                if product:
                    products.append(product.name)

        items.append({
            "payment_id": payment.id,
            "user_id": user.id if user else None,
            "user_name": user.name if user else None,
            "membership_level": user.membership_level if user else None,
            "payment_status": payment.status,
            "payment_type": getattr(payment, "payment_type", None),
            "admin_verified": payment.admin_verified,
            "amount": payment.amount,
            "selection_id": selection.id if selection else None,
            "selected_products": products,
            "selection_status": selection.status if selection else None,
            "order_id": order.id if order else None,
            "order_status": order.status if order else None,
        })

    return {"items": items}


@router.get("/membership-payments")
def get_membership_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    payments = (
        db.query(MembershipPayment)
        .filter(
            MembershipPayment.payment_type.in_([
                "signup",
                "subscription",
                "subscription_renewal",
            ])
        )
        .order_by(MembershipPayment.created_at.desc())
        .all()
    )

    return {"items": [payment_to_dict(payment) for payment in payments]}


@router.get("/marketplace-payments")
def get_marketplace_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    payments = (
        db.query(MembershipPayment)
        .filter(
            MembershipPayment.payment_type.in_([
                "marketplace_pharmacy",
                "marketplace_education",
            ])
        )
        .order_by(MembershipPayment.created_at.desc())
        .all()
    )

    return {"items": [payment_to_dict(payment) for payment in payments]}
