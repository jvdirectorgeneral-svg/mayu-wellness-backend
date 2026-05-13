from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from database import SessionLocal
from dependencies import get_current_user
import models

router = APIRouter(prefix="/orders", tags=["Orders"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class OrderCreateManual(BaseModel):
    user_id: int
    month: int
    year: int


class OrderStatusUpdate(BaseModel):
    status: str
    logistics_notes: Optional[str] = None


class OrderApprovalUpdate(BaseModel):
    approval_notes: Optional[str] = None


class OrderTrackingUpdate(BaseModel):
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[str] = None
    shipping_notes: Optional[str] = None
    note: Optional[str] = None


def generate_order_code(user_id: int, month: int, year: int) -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"MWC-{year}{month:02d}-U{user_id}-{timestamp}"


def require_team_access(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "supervisor", "logistics"}:
        raise HTTPException(status_code=403, detail="Acceso no autorizado")


def require_admin_or_superadmin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin"}:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para admin o superadmin",
        )


def require_logistics_or_admin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "logistics"}:
        raise HTTPException(status_code=403, detail="Acceso no autorizado")


def get_order_payment(db: Session, order_id: int):
    return (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.order_id == order_id)
        .order_by(models.MembershipPayment.created_at.desc())
        .first()
    )


def payment_data(payment):
    return {
        "payment_id": payment.id if payment else None,
        "payment_status": payment.status if payment else None,
        "payment_amount": payment.amount if payment else None,
        "payer_email": payment.payer_email if payment else None,
        "admin_verified": payment.admin_verified if payment else False,
        "admin_verified_at": payment.admin_verified_at if payment else None,
    }


def order_items_data(order):
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


def order_date_data(order):
    return {
        "created_at": order.created_at,
        "prepared_at": order.prepared_at,
        "shipped_at": order.shipped_at,
        "delivered_at": order.delivered_at,
        "shipping_batch_date": getattr(order, "shipping_batch_date", None),
    }


def tracking_data(order):
    return {
        "carrier": getattr(order, "carrier", None),
        "tracking_number": getattr(order, "tracking_number", None),
        "tracking_url": getattr(order, "tracking_url", None),
        "shipping_notes": getattr(order, "shipping_notes", None),
    }


def tracking_history_data(order):
    history = getattr(order, "tracking_history", []) or []

    return {
        "tracking_history": [
            {
                "id": h.id,
                "order_id": h.order_id,
                "status": h.status,
                "note": h.note,
                "carrier": h.carrier,
                "tracking_number": h.tracking_number,
                "tracking_url": h.tracking_url,
                "created_by": h.created_by,
                "created_at": h.created_at,
            }
            for h in sorted(
                history,
                key=lambda x: x.created_at or datetime.utcnow(),
                reverse=True,
            )
        ]
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


def get_monthly_selection_data(db: Session, user_id: int, month: int, year: int):
    selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user_id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
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

    for item in selection.items:
        if item.product:
            products.append(
                {
                    "product_id": item.product.id,
                    "name": item.product.name,
                    "quantity": item.quantity,
                }
            )

    return {
        "monthly_selection_id": selection.id,
        "editable_product": products[0]["name"] if products else None,
        "monthly_selection_products": products,
        "monthly_selection_status": selection.status,
    }


def order_to_dict(db: Session, order: models.Order, include_history: bool = False):
    payment = get_order_payment(db, order.id)

    data = {
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
        "items_count": len(order.items),
        **order_items_data(order),
        **order_date_data(order),
        **tracking_data(order),
        **payment_data(payment),
        **get_monthly_selection_data(db, order.user_id, order.month, order.year),
    }

    if include_history:
        data.update(tracking_history_data(order))

    return data


@router.post("/create-manual")
def create_order_manual(
    payload: OrderCreateManual,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    monthly_selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == payload.user_id,
            models.MonthlySelection.month == payload.month,
            models.MonthlySelection.year == payload.year,
        )
        .first()
    )

    if not monthly_selection:
        raise HTTPException(
            status_code=404,
            detail="No existe selección mensual para ese usuario en ese ciclo",
        )

    existing_order = (
        db.query(models.Order)
        .filter(
            models.Order.user_id == payload.user_id,
            models.Order.month == payload.month,
            models.Order.year == payload.year,
        )
        .first()
    )

    if existing_order:
        raise HTTPException(
            status_code=400,
            detail="Ya existe una orden para este usuario en ese ciclo",
        )

    if not monthly_selection.items:
        raise HTTPException(
            status_code=400,
            detail="La selección mensual no tiene productos",
        )

    new_order = models.Order(
        order_code=generate_order_code(user.id, payload.month, payload.year),
        user_id=user.id,
        month=payload.month,
        year=payload.year,
        membership_level_snapshot=user.membership_level,
        user_status_snapshot="active" if user.membership_active else "inactive",
        city_snapshot=user.city,
        address_snapshot=user.address,
        reference_snapshot=user.reference,
        delivery_notes_snapshot=user.delivery_notes,
        status="pending_payment_review",
        logistics_notes="Orden creada, pendiente de validación administrativa",
    )

    db.add(new_order)
    db.flush()

    for item in monthly_selection.items:
        product_name = item.product.name if item.product else "Producto no encontrado"

        db.add(
            models.OrderItem(
                order_id=new_order.id,
                product_id=item.product_id,
                product_name_snapshot=product_name,
                quantity=item.quantity,
            )
        )

    add_tracking_history(
        db=db,
        order=new_order,
        current_user=current_user,
        status="pending_payment_review",
        note="Orden creada manualmente por administración",
    )

    db.commit()
    db.refresh(new_order)

    return {
        "message": "Orden creada correctamente y pendiente de aprobación administrativa",
        "order": order_to_dict(db, new_order, include_history=True),
    }


@router.put("/{order_id}/approve")
def approve_order_for_logistics(
    order_id: int,
    payload: OrderApprovalUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_admin_or_superadmin(current_user)

    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    if order.status != "pending_payment_review":
        raise HTTPException(
            status_code=400,
            detail="Solo se pueden aprobar órdenes en revisión de pago",
        )

    payment = get_order_payment(db, order.id)

    if not payment or payment.status != "verified" or not payment.admin_verified:
        raise HTTPException(
            status_code=400,
            detail="La orden necesita un pago verificado por administración",
        )

    order.status = "approved_for_logistics"
    order.logistics_notes = (
        payload.approval_notes.strip()
        if payload.approval_notes and payload.approval_notes.strip()
        else "Pagos OK - orden liberada a logística"
    )

    add_tracking_history(
        db=db,
        order=order,
        current_user=current_user,
        status="approved_for_logistics",
        note=order.logistics_notes,
    )

    db.commit()
    db.refresh(order)

    return {
        "message": "Orden aprobada y liberada para logística",
        "order": order_to_dict(db, order, include_history=True),
    }


@router.get("")
def list_orders(
    status: Optional[str] = Query(default=None),
    city: Optional[str] = Query(default=None),
    month: Optional[int] = Query(default=None),
    year: Optional[int] = Query(default=None),
    search: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_team_access(current_user)

    query = db.query(models.Order).join(models.User)

    if current_user.role == "logistics":
        query = query.filter(
            models.Order.status.in_(
                [
                    "approved_for_logistics",
                    "preparing",
                    "shipped",
                    "delivered",
                ]
            )
        )

    if status:
        query = query.filter(models.Order.status == status)

    if city:
        query = query.filter(models.Order.city_snapshot.ilike(f"%{city}%"))

    if month is not None:
        query = query.filter(models.Order.month == month)

    if year is not None:
        query = query.filter(models.Order.year == year)

    if search and search.strip():
        clean = f"%{search.strip()}%"

        query = query.filter(
            or_(
                models.Order.order_code.ilike(clean),
                models.Order.tracking_number.ilike(clean),
                models.Order.carrier.ilike(clean),
                models.Order.city_snapshot.ilike(clean),
                models.Order.address_snapshot.ilike(clean),
                models.User.name.ilike(clean),
                models.User.phone.ilike(clean),
                models.User.email.ilike(clean),
                models.User.cedula.ilike(clean),
            )
        )

    orders = query.order_by(models.Order.created_at.desc()).all()

    return {
        "items": [
            order_to_dict(db, order, include_history=False)
            for order in orders
        ]
    }


@router.get("/user/{user_id}")
def list_user_orders(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_team_access(current_user)

    orders = (
        db.query(models.Order)
        .filter(models.Order.user_id == user_id)
        .order_by(models.Order.created_at.desc())
        .all()
    )

    return {
        "items": [
            order_to_dict(db, order, include_history=True)
            for order in orders
        ]
    }


@router.get("/user/{user_id}/delivered")
def list_user_delivered_orders(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_team_access(current_user)

    orders = (
        db.query(models.Order)
        .filter(
            models.Order.user_id == user_id,
            models.Order.status == "delivered",
        )
        .order_by(models.Order.delivered_at.desc())
        .all()
    )

    return {
        "items": [
            order_to_dict(db, order, include_history=True)
            for order in orders
        ]
    }


@router.get("/{order_id}")
def get_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_team_access(current_user)

    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    if current_user.role == "logistics" and order.status == "pending_payment_review":
        raise HTTPException(
            status_code=403,
            detail="La orden aún no ha sido liberada para logística",
        )

    data = order_to_dict(db, order, include_history=True)

    data["user"] = {
        "id": order.user.id,
        "name": order.user.name,
        "email": order.user.email,
        "phone": order.user.phone,
        "cedula": order.user.cedula,
        "membership_active": order.user.membership_active,
        "is_active": order.user.is_active,
        "role": order.user.role,
    }

    return data


@router.put("/{order_id}/tracking")
def update_order_tracking(
    order_id: int,
    payload: OrderTrackingUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_logistics_or_admin(current_user)

    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    if payload.carrier is not None:
        order.carrier = payload.carrier.strip() if payload.carrier else None

    if payload.tracking_number is not None:
        order.tracking_number = (
            payload.tracking_number.strip() if payload.tracking_number else None
        )

    if payload.tracking_url is not None:
        order.tracking_url = (
            payload.tracking_url.strip() if payload.tracking_url else None
        )

    if payload.shipping_notes is not None:
        order.shipping_notes = (
            payload.shipping_notes.strip() if payload.shipping_notes else None
        )

    note = payload.note or "Datos de guía / tracking actualizados"

    add_tracking_history(
        db=db,
        order=order,
        current_user=current_user,
        status=order.status,
        note=note,
    )

    db.commit()
    db.refresh(order)

    return {
        "message": "Tracking actualizado correctamente",
        "order": order_to_dict(db, order, include_history=True),
    }


@router.put("/{order_id}/status")
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_logistics_or_admin(current_user)

    allowed_statuses = {
        "pending_payment_review",
        "approved_for_logistics",
        "preparing",
        "shipped",
        "delivered",
        "cancelled",
    }

    if payload.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Estado inválido")

    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    if current_user.role == "logistics":
        if order.status not in {
            "approved_for_logistics",
            "preparing",
            "shipped",
        }:
            raise HTTPException(
                status_code=403,
                detail="La orden no está liberada para logística",
            )

        allowed_logistics_transitions = {
            "approved_for_logistics": {"preparing"},
            "preparing": {"shipped"},
            "shipped": {"delivered"},
        }

        next_allowed = allowed_logistics_transitions.get(order.status, set())

        if payload.status not in next_allowed:
            raise HTTPException(
                status_code=400,
                detail="Transición de estado no permitida para logística",
            )

    order.status = payload.status

    if payload.logistics_notes is not None:
        order.logistics_notes = payload.logistics_notes

    now = datetime.utcnow()

    if payload.status == "preparing" and order.prepared_at is None:
        order.prepared_at = now

    if payload.status == "shipped":
        if order.shipped_at is None:
            order.shipped_at = now

        if getattr(order, "shipping_batch_date", None) is None:
            order.shipping_batch_date = now

    if payload.status == "delivered" and order.delivered_at is None:
        order.delivered_at = now

    add_tracking_history(
        db=db,
        order=order,
        current_user=current_user,
        status=payload.status,
        note=payload.logistics_notes or f"Estado actualizado a {payload.status}",
    )

    db.commit()
    db.refresh(order)

    return {
        "message": "Estado de orden actualizado correctamente",
        "order": order_to_dict(db, order, include_history=True),
    }
