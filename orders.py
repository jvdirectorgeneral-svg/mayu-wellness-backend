from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from database import SessionLocal
from dependencies import get_current_user
import models

router = APIRouter(prefix="/orders", tags=["Orders"])


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
# SCHEMAS
# =========================
class OrderCreateManual(BaseModel):
    user_id: int
    month: int
    year: int


class OrderStatusUpdate(BaseModel):
    status: str
    logistics_notes: Optional[str] = None


class OrderApprovalUpdate(BaseModel):
    approval_notes: Optional[str] = None


# =========================
# HELPERS
# =========================
def generate_order_code(user_id: int, month: int, year: int) -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"MWC-{year}{month:02d}-U{user_id}-{timestamp}"


def require_team_access(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    allowed_roles = {"superadmin", "admin", "supervisor", "logistics"}
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Acceso no autorizado")


def require_admin_or_superadmin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    allowed_roles = {"superadmin", "admin"}
    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para admin o superadmin"
        )


def require_logistics_or_admin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    allowed_roles = {"superadmin", "admin", "logistics"}
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Acceso no autorizado")


# =========================
# CREAR ORDEN MANUAL DESDE SELECCIÓN MENSUAL
# =========================
@router.post("/create-manual")
def create_order_manual(
    payload: OrderCreateManual,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
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
            models.MonthlySelection.year == payload.year
        )
        .first()
    )

    if not monthly_selection:
        raise HTTPException(
            status_code=404,
            detail="No existe selección mensual para ese usuario en ese ciclo"
        )

    existing_order = (
        db.query(models.Order)
        .filter(
            models.Order.user_id == payload.user_id,
            models.Order.month == payload.month,
            models.Order.year == payload.year
        )
        .first()
    )

    if existing_order:
        raise HTTPException(
            status_code=400,
            detail="Ya existe una orden para este usuario en ese ciclo"
        )

    if not monthly_selection.items or len(monthly_selection.items) == 0:
        raise HTTPException(
            status_code=400,
            detail="La selección mensual no tiene productos"
        )

    user_status_snapshot = "active" if user.membership_active else "inactive"

    new_order = models.Order(
        order_code=generate_order_code(user.id, payload.month, payload.year),
        user_id=user.id,
        month=payload.month,
        year=payload.year,
        membership_level_snapshot=user.membership_level,
        user_status_snapshot=user_status_snapshot,
        city_snapshot=user.city,
        address_snapshot=user.address,
        reference_snapshot=user.reference,
        delivery_notes_snapshot=user.delivery_notes,
        status="pending_payment_review",
        logistics_notes="Orden creada, pendiente de validación administrativa"
    )

    db.add(new_order)
    db.flush()

    for item in monthly_selection.items:
        order_item = models.OrderItem(
            order_id=new_order.id,
            product_id=item.product_id,
            product_name_snapshot=item.product.name,
            quantity=item.quantity
        )
        db.add(order_item)

    db.commit()
    db.refresh(new_order)

    return {
        "message": "Orden creada correctamente y pendiente de aprobación administrativa",
        "order": {
            "id": new_order.id,
            "order_code": new_order.order_code,
            "user_id": new_order.user_id,
            "month": new_order.month,
            "year": new_order.year,
            "status": new_order.status,
            "user_status_snapshot": new_order.user_status_snapshot,
            "city_snapshot": new_order.city_snapshot,
            "address_snapshot": new_order.address_snapshot,
            "reference_snapshot": new_order.reference_snapshot,
            "delivery_notes_snapshot": new_order.delivery_notes_snapshot,
            "created_at": new_order.created_at
        }
    }


# =========================
# APROBAR ORDEN PARA LOGÍSTICA
# =========================
@router.put("/{order_id}/approve")
def approve_order_for_logistics(
    order_id: int,
    payload: OrderApprovalUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_admin_or_superadmin(current_user)

    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    if order.status != "pending_payment_review":
        raise HTTPException(
            status_code=400,
            detail="Solo se pueden aprobar órdenes en revisión de pago"
        )

    order.status = "approved_for_logistics"

    if payload.approval_notes and payload.approval_notes.strip():
        order.logistics_notes = payload.approval_notes.strip()
    else:
        order.logistics_notes = "Pagos OK - orden liberada a logística"

    db.commit()
    db.refresh(order)

    return {
        "message": "Orden aprobada y liberada para logística",
        "order": {
            "id": order.id,
            "order_code": order.order_code,
            "status": order.status,
            "logistics_notes": order.logistics_notes
        }
    }


# =========================
# LISTAR ÓRDENES
# =========================
@router.get("")
def list_orders(
    status: Optional[str] = Query(default=None),
    city: Optional[str] = Query(default=None),
    month: Optional[int] = Query(default=None),
    year: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_team_access(current_user)

    query = db.query(models.Order)

    # Si es logística, solo ve órdenes ya aprobadas o en flujo logístico
    if current_user.role == "logistics":
        query = query.filter(
            models.Order.status.in_([
                "approved_for_logistics",
                "preparing",
                "shipped",
                "delivered"
            ])
        )

    if status:
        query = query.filter(models.Order.status == status)

    if city:
        query = query.filter(models.Order.city_snapshot.ilike(f"%{city}%"))

    if month is not None:
        query = query.filter(models.Order.month == month)

    if year is not None:
        query = query.filter(models.Order.year == year)

    orders = query.order_by(models.Order.created_at.desc()).all()

    return {
        "items": [
            {
                "id": order.id,
                "order_code": order.order_code,
                "user_id": order.user_id,
                "user_name": order.user.name,
                "user_phone": order.user.phone,
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
                "items_count": len(order.items)
            }
            for order in orders
        ]
    }


# =========================
# DETALLE DE ORDEN
# =========================
@router.get("/{order_id}")
def get_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_team_access(current_user)

    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    # Si es logística, no debería ver órdenes no aprobadas
    if current_user.role == "logistics" and order.status == "pending_payment_review":
        raise HTTPException(
            status_code=403,
            detail="La orden aún no ha sido liberada para logística"
        )

    return {
        "id": order.id,
        "order_code": order.order_code,
        "user": {
            "id": order.user.id,
            "name": order.user.name,
            "email": order.user.email,
            "phone": order.user.phone,
            "cedula": order.user.cedula,
            "membership_active": order.user.membership_active,
            "is_active": order.user.is_active,
            "role": order.user.role
        },
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
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name_snapshot": item.product_name_snapshot,
                "quantity": item.quantity
            }
            for item in order.items
        ]
    }


# =========================
# CAMBIAR ESTADO DE ORDEN
# =========================
@router.put("/{order_id}/status")
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    require_logistics_or_admin(current_user)

    allowed_statuses = {
        "pending_payment_review",
        "approved_for_logistics",
        "preparing",
        "shipped",
        "delivered",
        "cancelled"
    }

    if payload.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Estado inválido")

    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    # Logística NO puede tocar órdenes que no estén aprobadas
    if current_user.role == "logistics":
        if order.status not in {"approved_for_logistics", "preparing", "shipped"}:
            raise HTTPException(
                status_code=403,
                detail="La orden no está liberada para logística"
            )

        # Logística solo puede avanzar en flujo operativo
        allowed_logistics_transitions = {
            "approved_for_logistics": {"preparing"},
            "preparing": {"shipped"},
            "shipped": {"delivered"},
        }

        next_allowed = allowed_logistics_transitions.get(order.status, set())
        if payload.status not in next_allowed:
            raise HTTPException(
                status_code=400,
                detail="Transición de estado no permitida para logística"
            )

    order.status = payload.status

    if payload.logistics_notes is not None:
        order.logistics_notes = payload.logistics_notes

    now = datetime.utcnow()

    if payload.status == "preparing" and order.prepared_at is None:
        order.prepared_at = now

    if payload.status == "shipped" and order.shipped_at is None:
        order.shipped_at = now

    if payload.status == "delivered" and order.delivered_at is None:
        order.delivered_at = now

    db.commit()
    db.refresh(order)

    return {
        "message": "Estado de orden actualizado correctamente",
        "order": {
            "id": order.id,
            "order_code": order.order_code,
            "status": order.status,
            "logistics_notes": order.logistics_notes,
            "prepared_at": order.prepared_at,
            "shipped_at": order.shipped_at,
            "delivered_at": order.delivered_at
        }
    }
