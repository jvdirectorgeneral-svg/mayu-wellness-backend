from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from database import SessionLocal
from dependencies import get_current_user
import models
from marketing_contacts import upsert_marketing_contact

router = APIRouter(prefix="/education-orders", tags=["education_orders"])


class EducationOrderItemCreate(BaseModel):
    resource_id: int
    quantity: int = 1


class EducationOrderCreate(BaseModel):
    buyer_name: str
    buyer_phone: str
    buyer_email: Optional[str] = None
    items: List[EducationOrderItemCreate]
    payment_method: str = "whatsapp"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_education_admin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "education_admin"}:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para Mayu Educación",
        )


def generate_education_order_code():
    now = datetime.utcnow()
    return f"EDU-MAYU-{now.strftime('%Y%m%d%H%M%S')}"


def order_to_dict(order: models.EducationOrder):
    return {
        "id": order.id,
        "order_code": order.order_code,
        "user_id": order.user_id,
        "buyer_name": order.buyer_name,
        "buyer_phone": order.buyer_phone,
        "buyer_email": order.buyer_email,
        "subtotal": order.subtotal,
        "total": order.total,
        "currency": order.currency,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "status": order.status,
        "whatsapp_message": order.whatsapp_message,
        "payphone_transaction_id": order.payphone_transaction_id,
        "payphone_payment_url": order.payphone_payment_url,
        "created_at": order.created_at,
        "paid_at": order.paid_at,
        "items": [
            {
                "id": item.id,
                "resource_id": item.resource_id,
                "resource_title": item.resource_title_snapshot,
                "unit_price": item.unit_price_snapshot,
                "quantity": item.quantity,
                "total": item.total_snapshot,
                "access_code": item.access_code,
            }
            for item in order.items
        ],
    }


@router.post("/orders")
def create_education_order(
    payload: EducationOrderCreate,
    db: Session = Depends(get_db),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="El carrito está vacío")

    buyer_name = payload.buyer_name.strip()
    buyer_phone = payload.buyer_phone.strip()

    if not buyer_name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")

    if not buyer_phone:
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")

    subtotal = 0.0
    order_items_data = []

    for item in payload.items:
        if item.quantity <= 0:
            raise HTTPException(
                status_code=400,
                detail="La cantidad debe ser mayor a cero",
            )

        resource = (
            db.query(models.EducationResource)
            .filter(models.EducationResource.id == item.resource_id)
            .filter(models.EducationResource.active == True)
            .first()
        )

        if not resource:
            raise HTTPException(
                status_code=404,
                detail=f"Contenido educativo {item.resource_id} no encontrado",
            )

        unit_price = float(getattr(resource, "price", 0) or 0)
        line_total = unit_price * int(item.quantity)
        subtotal += line_total

        order_items_data.append(
            {
                "resource": resource,
                "quantity": item.quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )

    total = round(subtotal, 2)

    order = models.EducationOrder(
        order_code=generate_education_order_code(),
        user_id=None,
        buyer_name=buyer_name,
        buyer_phone=buyer_phone,
        buyer_email=payload.buyer_email,
        subtotal=round(subtotal, 2),
        total=total,
        currency="USD",
        payment_method=payload.payment_method,
        payment_status="pending",
        status="created",
    )

    db.add(order)
    db.commit()
    db.refresh(order)
    upsert_marketing_contact(
        db, name=order.buyer_name, email=order.buyer_email, phone=order.buyer_phone,
        source="education_marketplace", user_id=order.user_id,
        purchase_kind="education", purchase_at=order.created_at, increment_purchase=True,
    )
    db.commit()

    for item_data in order_items_data:
        resource = item_data["resource"]
        quantity = item_data["quantity"]
        unit_price = item_data["unit_price"]
        line_total = item_data["line_total"]

        order_item = models.EducationOrderItem(
            order_id=order.id,
            resource_id=resource.id,
            resource_title_snapshot=resource.title,
            unit_price_snapshot=unit_price,
            quantity=quantity,
            total_snapshot=round(line_total, 2),
            access_code=None,
        )

        db.add(order_item)

    whatsapp_lines = [
        f"Hola Mayu Educación, deseo confirmar mi compra {order.order_code}.",
        f"Cliente: {order.buyer_name}",
        f"Teléfono: {order.buyer_phone}",
    ]

    if order.buyer_email:
        whatsapp_lines.append(f"Email: {order.buyer_email}")

    whatsapp_lines.append("")
    whatsapp_lines.append("Contenidos:")

    for item_data in order_items_data:
        resource = item_data["resource"]
        quantity = item_data["quantity"]
        unit_price = item_data["unit_price"]
        line_total = item_data["line_total"]

        whatsapp_lines.append(
            f"- {resource.title} x{quantity} | ${unit_price:.2f} = ${line_total:.2f} USD"
        )

    whatsapp_lines.append("")
    whatsapp_lines.append(f"Total: ${order.total:.2f} USD")
    whatsapp_lines.append("")
    whatsapp_lines.append("Vengo desde Mayu Educación.")

    order.whatsapp_message = "\n".join(whatsapp_lines)

    db.commit()
    db.refresh(order)

    return {
        "message": "Orden educativa creada correctamente",
        "order": order_to_dict(order),
    }


@router.get("/orders/{order_id}")
def get_education_order(
    order_id: int,
    db: Session = Depends(get_db),
):
    order = (
        db.query(models.EducationOrder)
        .filter(models.EducationOrder.id == order_id)
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Orden educativa no encontrada")

    return order_to_dict(order)


@router.get("/admin/orders")
def get_admin_education_orders(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_education_admin(current_user)

    orders = (
        db.query(models.EducationOrder)
        .order_by(models.EducationOrder.id.desc())
        .all()
    )

    return {"items": [order_to_dict(o) for o in orders]}
