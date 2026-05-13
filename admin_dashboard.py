from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from pydantic import BaseModel, EmailStr
from typing import Optional

from database import SessionLocal
from dependencies import get_current_user
from auth import hash_password
from models import (
    User,
    Ambassador,
    Commission,
    MembershipPayment,
    Order,
    OrderItem,
    MonthlySelection,
    MonthlySelectionItem,
    Product,
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
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para admin o superadmin",
        )


def set_if_exists(obj, field: str, value):
    if value is not None and hasattr(obj, field):
        setattr(obj, field, value)


def clean_optional(value):
    if value is None:
        return None
    return value.strip()


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


def ambassador_to_dict(a: Ambassador):
    user = a.user

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

    items = (
        db.query(MonthlySelectionItem)
        .filter(MonthlySelectionItem.monthly_selection_id == selection.id)
        .all()
    )

    products = []

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
        "payment_type": payment.payment_type,
        "provider": payment.provider,
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


def order_to_dict(db: Session, order: Order):
    selection_info = get_monthly_selection_data(
        db,
        order.user_id,
        order.month,
        order.year,
    )

    payment = (
        db.query(MembershipPayment)
        .filter(MembershipPayment.order_id == order.id)
        .order_by(MembershipPayment.created_at.desc())
        .first()
    )

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
        "logistics_notes": order.logistics_notes,
        "month": order.month,
        "year": order.year,
        "created_at": order.created_at,
        "prepared_at": order.prepared_at,
        "shipped_at": order.shipped_at,
        "delivered_at": order.delivered_at,
        "shipping_batch_date": getattr(order, "shipping_batch_date", None),
        "items_count": len(order.items),
        **order_items_data(order),
        "payment_id": payment.id if payment else None,
        "payment_status": payment.status if payment else None,
        "payment_amount": payment.amount if payment else None,
        "payer_email": payment.payer_email if payment else None,
        "admin_verified": payment.admin_verified if payment else False,
        "admin_verified_at": payment.admin_verified_at if payment else None,
        **selection_info,
    }


@router.get("/summary")
def get_admin_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    total_socios = db.query(User).filter(User.role == "member").count()

    active_socios = db.query(User).filter(
        User.role == "member",
        User.membership_active == True,
    ).count()

    inactive_socios = db.query(User).filter(
        User.role == "member",
        User.membership_active == False,
    ).count()

    total_ambassadors = db.query(Ambassador).count()

    active_ambassadors = db.query(Ambassador).filter(
        Ambassador.is_active == True,
    ).count()

    pending_review_orders = db.query(Order).filter(
        Order.status == "pending_payment_review",
    ).count()

    ready_for_logistics = db.query(Order).filter(
        Order.status == "approved_for_logistics",
    ).count()

    shipped_orders = db.query(Order).filter(
        Order.status == "shipped",
    ).count()

    delivered_orders = db.query(Order).filter(
        Order.status == "delivered",
    ).count()

    total_generated = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).scalar()

    total_pending = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).filter(Commission.status == "pending").scalar()

    total_paid = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).filter(Commission.status == "paid").scalar()

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
        "admin_review_window": "lunes a jueves",
        "shipping_window": "viernes",
    }


@router.get("/users")
def get_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    users = (
        db.query(User)
        .filter(User.role == "member")
        .order_by(User.id.desc())
        .all()
    )

    return {"items": [user_to_dict(u) for u in users]}


@router.put("/users/{user_id}")
def admin_update_member(
    user_id: int,
    payload: AdminUpdateMemberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role != "member":
        raise HTTPException(
            status_code=403,
            detail="Este endpoint solo permite editar socios",
        )

    if payload.email is not None:
        clean_email = payload.email.strip().lower()

        existing_email = db.query(User).filter(
            User.email == clean_email,
            User.id != user.id,
        ).first()

        if existing_email:
            raise HTTPException(
                status_code=400,
                detail="El correo ya está registrado por otro usuario",
            )

        user.email = clean_email

    if payload.cedula is not None:
        clean_cedula = payload.cedula.strip()

        if clean_cedula:
            existing_cedula = db.query(User).filter(
                User.cedula == clean_cedula,
                User.id != user.id,
            ).first()

            if existing_cedula:
                raise HTTPException(
                    status_code=400,
                    detail="La cédula ya está registrada por otro usuario",
                )

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
            raise HTTPException(
                status_code=400,
                detail="El nivel debe ser 1, 2 o 3",
            )
        user.membership_level = payload.membership_level

    if payload.membership_active is not None:
        user.membership_active = payload.membership_active

    if payload.is_active is not None:
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)

    return {
        "message": "Socio actualizado correctamente",
        "user": user_to_dict(user),
    }


@router.get("/ambassadors")
def get_ambassadors(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    ambassadors = db.query(Ambassador).order_by(Ambassador.id.desc()).all()

    return {"items": [ambassador_to_dict(a) for a in ambassadors]}


@router.put("/ambassadors/{ambassador_id}")
def update_ambassador(
    ambassador_id: int,
    payload: AdminUpdateAmbassadorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    ambassador = db.query(Ambassador).filter(
        Ambassador.id == ambassador_id
    ).first()

    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    user = db.query(User).filter(User.id == ambassador.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario del embajador no encontrado")

    if payload.email is not None:
        clean_email = payload.email.strip().lower()

        existing_email = db.query(User).filter(
            User.email == clean_email,
            User.id != user.id,
        ).first()

        if existing_email:
            raise HTTPException(
                status_code=400,
                detail="El correo ya está registrado por otro usuario",
            )

        user.email = clean_email

    if payload.cedula is not None:
        clean_cedula = payload.cedula.strip()

        if clean_cedula:
            existing_cedula = db.query(User).filter(
                User.cedula == clean_cedula,
                User.id != user.id,
            ).first()

            if existing_cedula:
                raise HTTPException(
                    status_code=400,
                    detail="La cédula ya está registrada por otro usuario",
                )

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
            raise HTTPException(
                status_code=400,
                detail="El código de embajador ya existe",
            )

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

    return {
        "message": "Embajador actualizado correctamente",
        "ambassador": ambassador_to_dict(ambassador),
    }


@router.put("/users/{user_id}/phone")
def admin_update_user_phone(
    user_id: int,
    payload: AdminUpdatePhoneRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role in ["admin", "superadmin", "supervisor", "logistics"]:
        raise HTTPException(
            status_code=403,
            detail="Admin solo puede modificar teléfono de socios o embajadores",
        )

    clean_phone = payload.phone.strip()

    if not clean_phone:
        raise HTTPException(
            status_code=400,
            detail="El número de celular es obligatorio",
        )

    user.phone = clean_phone

    db.commit()
    db.refresh(user)

    return {
        "message": "Teléfono actualizado correctamente",
        "user": user_to_dict(user),
    }


@router.get("/payments")
def get_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    payments = (
        db.query(MembershipPayment)
        .order_by(MembershipPayment.created_at.desc())
        .all()
    )

    return {"items": [payment_to_dict(payment) for payment in payments]}


@router.put("/payments/{payment_id}/verify")
def verify_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    payment = db.query(MembershipPayment).filter(
        MembershipPayment.id == payment_id
    ).first()

    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    user = db.query(User).filter(User.id == payment.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    now = datetime.utcnow()
    month = now.month
    year = now.year

    payment.status = "verified"
    payment.admin_verified = True
    payment.admin_verified_at = now
    payment.admin_verified_by = current_user.id

    if not payment.paid_at:
        payment.paid_at = now

    user.membership_active = True
    user.is_active = True

    order = None

    if payment.order_id:
        order = db.query(Order).filter(Order.id == payment.order_id).first()

        if order:
            order.user_status_snapshot = "active"

            if order.status == "pending_payment_review":
                order.status = "approved_for_logistics"
                order.logistics_notes = "✔ Pago verificado - listo para despacho"

    if not order:
        order = (
            db.query(Order)
            .filter(
                Order.user_id == user.id,
                Order.month == month,
                Order.year == year,
            )
            .first()
        )

        if order:
            payment.order_id = order.id

    if not order:
        selection = (
            db.query(MonthlySelection)
            .filter(
                MonthlySelection.user_id == user.id,
                MonthlySelection.month == month,
                MonthlySelection.year == year,
            )
            .first()
        )

        if selection:
            items = (
                db.query(MonthlySelectionItem)
                .filter(MonthlySelectionItem.monthly_selection_id == selection.id)
                .all()
            )

            if items:
                order = Order(
                    order_code=f"MWC-{year}{month:02d}-U{user.id}-{now.strftime('%Y%m%d%H%M%S')}",
                    user_id=user.id,
                    month=month,
                    year=year,
                    membership_level_snapshot=user.membership_level,
                    user_status_snapshot="active",
                    city_snapshot=user.city,
                    address_snapshot=user.address,
                    reference_snapshot=user.reference,
                    delivery_notes_snapshot=user.delivery_notes,
                    status="approved_for_logistics",
                    logistics_notes="✔ Pago verificado - listo para despacho",
                )

                db.add(order)
                db.flush()

                for item in items:
                    product = db.query(Product).filter(Product.id == item.product_id).first()

                    if product:
                        order_item = OrderItem(
                            order_id=order.id,
                            product_id=product.id,
                            product_name_snapshot=product.name,
                            quantity=item.quantity,
                        )
                        db.add(order_item)

                payment.order_id = order.id

    if order:
        order.user_status_snapshot = "active"
        order.status = "approved_for_logistics"
        order.logistics_notes = "✔ Pago verificado - listo para despacho"

    db.commit()
    db.refresh(payment)

    if order:
        db.refresh(order)

    return {
        "message": "Pago verificado, suscripción activa y orden liberada a logística",
        "payment": payment_to_dict(payment),
        "order": order_to_dict(db, order) if order else None,
    }


@router.get("/orders")
def get_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    orders = db.query(Order).order_by(Order.created_at.desc()).all()

    return {"items": [order_to_dict(db, order) for order in orders]}


@router.put("/orders/{order_id}/approve")
def approve_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    order = db.query(Order).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    payment = (
        db.query(MembershipPayment)
        .filter(MembershipPayment.order_id == order.id)
        .order_by(MembershipPayment.created_at.desc())
        .first()
    )

    if not payment:
        raise HTTPException(
            status_code=400,
            detail="La orden no tiene pago vinculado",
        )

    if payment.status != "verified" or not payment.admin_verified:
        raise HTTPException(
            status_code=400,
            detail="Primero debes verificar el pago",
        )

    user = db.query(User).filter(User.id == order.user_id).first()

    if user:
        user.membership_active = True
        user.is_active = True

    order.status = "approved_for_logistics"
    order.user_status_snapshot = "active"
    order.logistics_notes = "✔ Pago verificado - listo para despacho"

    db.commit()
    db.refresh(order)

    return {
        "message": "Orden liberada a logística",
        "order": order_to_dict(db, order),
    }


@router.get("/commissions/pending")
def get_pending_commissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    commissions = db.query(Commission).filter(
        Commission.status == "pending"
    ).order_by(Commission.generated_at.desc()).all()

    return {
        "items": [
            {
                "id": c.id,
                "ambassador_id": c.ambassador_id,
                "ambassador_name": c.ambassador.user.name if c.ambassador and c.ambassador.user else None,
                "amount": c.commission_amount,
                "commission_amount": c.commission_amount,
                "month": c.month,
                "year": c.year,
                "status": c.status,
                "generated_at": c.generated_at,
            }
            for c in commissions
        ]
    }


@router.put("/users/{user_id}/reset-password")
def admin_reset_user_password(
    user_id: int,
    payload: AdminResetPasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user.role in ["admin", "superadmin", "supervisor", "logistics"]:
        raise HTTPException(
            status_code=403,
            detail="Admin solo puede resetear socios o embajadores",
        )

    if not payload.new_password or payload.new_password.strip() == "":
        raise HTTPException(
            status_code=400,
            detail="La nueva contraseña es obligatoria",
        )

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
