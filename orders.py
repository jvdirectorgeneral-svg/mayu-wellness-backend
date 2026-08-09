from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import html

from database import SessionLocal
from dependencies import get_current_user
import models
from commissions import safe_send_ambassador_push, sync_ambassador_wallets
from member_cards import get_or_create_card, safe_update_member_wallets
from notification_service import safe_send_email, safe_send_push_to_user

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


def is_future_cycle(order: models.Order) -> bool:
    now = datetime.utcnow()
    return (order.year, order.month) > (now.year, now.month)


def cycle_type_data(payment):
    payment_type = getattr(payment, "payment_type", None) if payment else None
    if payment_type == "subscription_renewal":
        return "renewal", "Renovación mensual"
    if payment_type in {"signup", "membership_initial", "subscription"}:
        return "initial", "Primera afiliación"
    return "unlinked", "Pago por vincular"


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
    cycle_type, cycle_label = cycle_type_data(payment)
    return {
        "payment_id": payment.id if payment else None,
        "payment_type": getattr(payment, "payment_type", None) if payment else None,
        "payment_status": payment.status if payment else None,
        "payment_amount": payment.amount if payment else None,
        "payer_email": payment.payer_email if payment else None,
        "admin_verified": payment.admin_verified if payment else False,
        "admin_verified_at": payment.admin_verified_at if payment else None,
        "membership_cycle_type": cycle_type,
        "membership_cycle_label": cycle_label,
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


def notify_membership_order_update(db: Session, order: models.Order, status: str):
    labels = {
        "preparing": "Tu pedido Mayu está en preparación",
        "shipped": "Tu pedido Mayu fue enviado",
        "delivered": "Tu pedido Mayu fue entregado",
    }
    subject = labels.get(status)
    if not subject or not order.user:
        return {"push": False, "email": False, "wallet": None, "ambassador": None}

    products = ", ".join(
        f"{item.product_name_snapshot} x{item.quantity or 1}" for item in order.items
    ) or "Productos de tu plan"
    tracking_text = (
        f" Guía {order.tracking_number} por {order.carrier}."
        if status == "shipped" else ""
    )
    push_message = (
        f"Orden {order.order_code} · {format_month_label(order.month, order.year)}. "
        f"{products}.{tracking_text}"
    )
    member_push = safe_send_push_to_user(db, order.user_id, subject, push_message)

    email_sent = False
    if status in {"shipped", "delivered"}:
        tracking_link = (
            f'<p><a href="{html.escape(order.tracking_url)}">Ver seguimiento</a></p>'
            if order.tracking_url else ""
        )
        email_sent = safe_send_email(
            order.user.email,
            subject,
            f"""
            <div style="font-family:Arial,sans-serif;line-height:1.6">
              <h2>{html.escape(subject)}</h2>
              <p>Hola {html.escape(order.user.name or 'socio Mayu')},</p>
              <p><strong>Orden:</strong> {html.escape(order.order_code)}</p>
              <p><strong>Período:</strong> {html.escape(format_month_label(order.month, order.year))}</p>
              <p><strong>Productos:</strong> {html.escape(products)}</p>
              <p><strong>Transportadora:</strong> {html.escape(order.carrier or '-')}</p>
              <p><strong>Guía:</strong> {html.escape(order.tracking_number or '-')}</p>
              {tracking_link}
              <p>Equipo Mayu Wellness Club</p>
            </div>
            """,
        )

    try:
        member_user, member_card = get_or_create_card(db, order.user_id)
        member_wallet = safe_update_member_wallets(db, member_user, member_card)
    except Exception as exc:
        member_wallet = {"updated": False, "detail": str(exc)}

    ambassador_result = None
    referral = (
        db.query(models.AmbassadorReferral)
        .filter(
            models.AmbassadorReferral.user_id == order.user_id,
            models.AmbassadorReferral.status == "active",
        )
        .first()
    )
    if referral:
        ambassador_result = {
            "push": safe_send_ambassador_push(
                db,
                referral.ambassador_id,
                f"Entrega de {order.user.name}",
                push_message,
            ),
            "wallet": sync_ambassador_wallets(db, referral.ambassador_id),
        }

    return {
        "push": member_push,
        "email": email_sent,
        "wallet": member_wallet,
        "ambassador": ambassador_result,
    }


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
    future_cycle = is_future_cycle(order)

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
        "is_future_cycle": future_cycle,
        "dispatch_blocked": future_cycle,
        "dispatch_block_reason": (
            f"Ciclo futuro: disponible desde el 01/{order.month:02d}/{order.year}"
            if future_cycle else None
        ),
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

    selection = get_monthly_selection(db, order.user_id, order.month, order.year)

    if payment:
        if payment.status not in valid_payment_statuses:
            raise HTTPException(
                status_code=400,
                detail="El pago vinculado no está en estado válido para liberar la orden",
            )

        if payment.status == "verified" and not payment.admin_verified:
            raise HTTPException(
                status_code=400,
                detail="Primero debes verificar el pago por administración",
            )

        payment.order_id = order.id

    else:
        if not selection:
            raise HTTPException(
                status_code=400,
                detail="La orden no tiene pago vinculado ni selección mensual relacionada",
            )

        if selection.status not in {"confirmed", "draft"}:
            raise HTTPException(
                status_code=400,
                detail=f"La selección mensual no está aprobable. Estado actual: {selection.status}",
            )

    order.status = "approved_for_logistics"
    order.user_status_snapshot = "active"
    order.logistics_notes = (
        payload.approval_notes.strip()
        if payload.approval_notes and payload.approval_notes.strip()
        else "Orden aprobada por administración - lista para logística"
    )

    if selection:
        selection.editable = False
        selection.status = "confirmed"
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
    if current_user.id != user_id:
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
    if current_user.id != user_id:
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
    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    if current_user.id != order.user_id:
        require_team_access(current_user)

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

    old_status = order.status
    if old_status == payload.status:
        return {
            "message": "La orden ya se encuentra en ese estado",
            "order": order_to_dict(db, order, include_history=True),
            "notifications": {"sent": False, "detail": "Sin cambio de estado"},
        }

    if payload.status in {"preparing", "shipped"} and is_future_cycle(order):
        raise HTTPException(
            status_code=400,
            detail=(
                f"No se puede despachar un ciclo futuro. "
                f"Disponible desde el 01/{order.month:02d}/{order.year}."
            ),
        )

    if payload.status == "shipped" and (
        not (order.carrier or "").strip() or not (order.tracking_number or "").strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="Registra transportadora y número de guía antes de marcar Enviado",
        )

    allowed_transitions = {
        "pending_payment_review": {"approved_for_logistics", "cancelled"},
        "approved_for_logistics": {"preparing", "cancelled"},
        "preparing": {"shipped", "cancelled"},
        "shipped": {"delivered", "cancelled"},
        "delivered": set(),
        "cancelled": set(),
    }
    if payload.status not in allowed_transitions.get(old_status, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Transición no permitida: {old_status} → {payload.status}",
        )

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
    notifications = notify_membership_order_update(db, order, payload.status)

    return {
        "message": "Estado de orden actualizado correctamente",
        "order": order_to_dict(db, order, include_history=True),
        "notifications": notifications,
    }
