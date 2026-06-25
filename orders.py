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


def safe_set(obj, attr, value):
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def generate_order_code(user_id: int, month: int, year: int) -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"MWC-{year}{month:02d}-U{user_id}-{timestamp}"


def format_month_label(month: int, year: int) -> str:
    months = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
    }
    return f"{months.get(month, 'Mes')} {year}"


def get_next_month_year(month: int, year: int):
    if month == 12:
        return 1, year + 1
    return month + 1, year


def order_is_locked(order: models.Order) -> bool:
    if not order:
        return False

    return order.status in {
        "approved_for_logistics",
        "preparing",
        "shipped",
        "delivered",
        "cancelled",
    }


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
        raise HTTPException(status_code=403, detail="Acceso solo para admin o superadmin")


def require_logistics_or_admin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "logistics"}:
        raise HTTPException(status_code=403, detail="Acceso no autorizado")


def get_plan_by_level(db: Session, level: int):
    return db.query(models.Plan).filter(models.Plan.level == level).first()


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


def get_order_payment(db: Session, order: models.Order):
    payment = (
        db.query(models.MembershipPayment)
        .filter(models.MembershipPayment.order_id == order.id)
        .order_by(models.MembershipPayment.created_at.desc())
        .first()
    )

    if payment:
        return payment

    selection = get_monthly_selection(db, order.user_id, order.month, order.year)

    if selection and hasattr(models.MembershipPayment, "monthly_selection_id"):
        payment = (
            db.query(models.MembershipPayment)
            .filter(models.MembershipPayment.monthly_selection_id == selection.id)
            .order_by(models.MembershipPayment.created_at.desc())
            .first()
        )

        if payment:
            payment.order_id = order.id
            db.flush()
            return payment

    return None


def payment_data(payment):
    return {
        "payment_id": payment.id if payment else None,
        "payment_type": getattr(payment, "payment_type", None) if payment else None,
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
                "name": item.product_name_snapshot,
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
    db.add(
        models.OrderTrackingHistory(
            order_id=order.id,
            status=status,
            note=note,
            carrier=getattr(order, "carrier", None),
            tracking_number=getattr(order, "tracking_number", None),
            tracking_url=getattr(order, "tracking_url", None),
            created_by=current_user.id if current_user else None,
        )
    )


def get_monthly_selection_data(db: Session, user_id: int, month: int, year: int):
    selection = get_monthly_selection(db, user_id, month, year)

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


def open_next_selection_cycle(
    db: Session,
    user: models.User,
    current_selection: models.MonthlySelection,
):
    if not user or not current_selection:
        return None

    next_month, next_year = get_next_month_year(
        current_selection.month,
        current_selection.year,
    )

    existing_next = get_monthly_selection(
        db=db,
        user_id=user.id,
        month=next_month,
        year=next_year,
    )

    if existing_next:
        existing_next.editable = True
        db.flush()
        return existing_next

    plan = get_plan_by_level(db, user.membership_level)

    next_selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id if plan else current_selection.plan_id,
        month=next_month,
        year=next_year,
        status="confirmed",
        editable=True,
    )

    db.add(next_selection)
    db.flush()

    for item in current_selection.items:
        db.add(
            models.MonthlySelectionItem(
                monthly_selection_id=next_selection.id,
                product_id=item.product_id,
                quantity=item.quantity or 1,
            )
        )

    db.flush()
    return next_selection


def order_to_dict(db: Session, order: models.Order, include_history: bool = False):
    payment = get_order_payment(db, order)

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
        "order_status": order.status,
        "order_locked": order_is_locked(order),
        "logistics_notes": order.logistics_notes,
        "month": order.month,
        "year": order.year,
        "month_label": format_month_label(order.month, order.year),
        "monthLabel": format_month_label(order.month, order.year),
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


def create_order_from_selection(
    db: Session,
    user: models.User,
    monthly_selection: models.MonthlySelection,
    current_user: models.User,
    status: str = "pending_payment_review",
    logistics_notes: str = "Orden creada, pendiente de validación administrativa",
):
    if not monthly_selection.items:
        raise HTTPException(
            status_code=400,
            detail="La selección mensual no tiene productos",
        )

    existing_order = (
        db.query(models.Order)
        .filter(
            models.Order.user_id == user.id,
            models.Order.month == monthly_selection.month,
            models.Order.year == monthly_selection.year,
        )
        .first()
    )

    if existing_order:
        raise HTTPException(
            status_code=400,
            detail="Ya existe una orden para este usuario en ese ciclo",
        )

    new_order = models.Order(
        order_code=generate_order_code(
            user.id,
            monthly_selection.month,
            monthly_selection.year,
        ),
        user_id=user.id,
        month=monthly_selection.month,
        year=monthly_selection.year,
        membership_level_snapshot=user.membership_level,
        user_status_snapshot="active" if user.membership_active else "inactive",
        city_snapshot=user.city,
        address_snapshot=user.address,
        reference_snapshot=user.reference,
        delivery_notes_snapshot=user.delivery_notes,
        status=status,
        logistics_notes=logistics_notes,
    )

    safe_set(new_order, "monthly_selection_id", monthly_selection.id)

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

    payment = get_order_payment(db, new_order)

    if payment:
        payment.order_id = new_order.id
        db.flush()

    add_tracking_history(db, new_order, current_user, status, logistics_notes)

    return new_order


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

    monthly_selection = get_monthly_selection(
        db,
        payload.user_id,
        payload.month,
        payload.year,
    )

    if not monthly_selection:
        raise HTTPException(
            status_code=404,
            detail="No existe selección mensual para ese usuario en ese ciclo",
        )

    new_order = create_order_from_selection(
        db=db,
        user=user,
        monthly_selection=monthly_selection,
        current_user=current_user,
        status="pending_payment_review",
        logistics_notes="Orden creada manualmente, pendiente de validación administrativa",
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

    payment = get_order_payment(db, order)

    valid_payment_statuses = {
        "verified",
        "subscription_active",
        "subscription_paid",
    }

    if (
        not payment
        or payment.status not in valid_payment_statuses
        or not payment.admin_verified
    ):
        raise HTTPException(
            status_code=400,
            detail="La orden necesita un pago de membresía verificado por administración",
        )

    payment.order_id = order.id

    order.status = "approved_for_logistics"
    order.user_status_snapshot = "active"
    order.logistics_notes = (
        payload.approval_notes.strip()
        if payload.approval_notes and payload.approval_notes.strip()
        else "Pagos OK - orden liberada a logística"
    )

    selection = get_monthly_selection(db, order.user_id, order.month, order.year)

    if selection:
        selection.editable = False
        open_next_selection_cycle(db, order.user, selection)

    add_tracking_history(
        db,
        order,
        current_user,
        "approved_for_logistics",
        order.logistics_notes,
    )

    db.commit()
    db.refresh(order)

    return {
        "message": "Orden aprobada, selección cerrada y siguiente ciclo abierto",
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
                ["approved_for_logistics", "preparing", "shipped", "delivered"]
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

    return {"items": [order_to_dict(db, order, include_history=False) for order in orders]}


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
        .order_by(
            models.Order.year.desc(),
            models.Order.month.desc(),
            models.Order.created_at.desc(),
        )
        .all()
    )

    return {"items": [order_to_dict(db, order, include_history=True) for order in orders]}


@router.get("/user/{user_id}/delivered")
def list_user_delivered_orders(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_team_access(current_user)

    orders = (
        db.query(models.Order)
        .filter(models.Order.user_id == user_id, models.Order.status == "delivered")
        .order_by(models.Order.delivered_at.desc())
        .all()
    )

    return {"items": [order_to_dict(db, order, include_history=True) for order in orders]}


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

    if current_user.role == "logistics" and order.status not in {
        "approved_for_logistics",
        "preparing",
        "shipped",
    }:
        raise HTTPException(
            status_code=403,
            detail="La orden no está disponible para actualizar tracking",
        )

    if payload.carrier is not None:
        order.carrier = payload.carrier.strip() if payload.carrier else None

    if payload.tracking_number is not None:
        order.tracking_number = (
            payload.tracking_number.strip() if payload.tracking_number else None
        )

    if payload.tracking_url is not None:
        order.tracking_url = payload.tracking_url.strip() if payload.tracking_url else None

    if payload.shipping_notes is not None:
        order.shipping_notes = (
            payload.shipping_notes.strip() if payload.shipping_notes else None
        )

    note = payload.note or "Datos de guía / tracking actualizados"

    add_tracking_history(db, order, current_user, order.status, note)

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
        if order.status not in {"approved_for_logistics", "preparing", "shipped"}:
            raise HTTPException(
                status_code=403,
                detail="La orden no está liberada para logística",
            )

        allowed_logistics_transitions = {
            "approved_for_logistics": {"preparing"},
            "preparing": {"shipped"},
            "shipped": {"delivered"},
        }

        if payload.status not in allowed_logistics_transitions.get(order.status, set()):
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

    selection = get_monthly_selection(db, order.user_id, order.month, order.year)

    if selection and payload.status in {
        "approved_for_logistics",
        "preparing",
        "shipped",
        "delivered",
        "cancelled",
    }:
        selection.editable = False

        if payload.status in {"approved_for_logistics", "preparing"}:
            open_next_selection_cycle(db, order.user, selection)

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
