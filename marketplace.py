from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import os
import cloudinary
import cloudinary.uploader

from database import SessionLocal
from dependencies import get_current_user
import models
from marketing_contacts import upsert_marketing_contact
from notification_service import (
    add_tracking_history,
    notify_customer_order,
    safe_send_push_to_roles,
)
from pharmacy_loyalty import (
    credit_marketplace_order_if_paid,
    sync_marketplace_loyalty_wallet_after_commit,
)
from doctor_prescribers import (
    safe_send_doctor_apple_wallet_update_pushes,
    safe_update_doctor_google_wallet_object,
)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    active: bool = True


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


class ProductCreate(BaseModel):
    name: str
    category_id: Optional[int] = None
    price: float = 0
    stock: int = 0
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    short_description: Optional[str] = None
    description: Optional[str] = None
    benefits: Optional[str] = None
    ingredients: Optional[str] = None
    suggested_dose: Optional[str] = None
    usage_instructions: Optional[str] = None
    warnings: Optional[str] = None
    presentation: Optional[str] = None
    active: bool = True


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    category_id: Optional[int] = None
    price: Optional[float] = None
    stock: Optional[int] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    short_description: Optional[str] = None
    description: Optional[str] = None
    benefits: Optional[str] = None
    ingredients: Optional[str] = None
    suggested_dose: Optional[str] = None
    usage_instructions: Optional[str] = None
    warnings: Optional[str] = None
    presentation: Optional[str] = None
    active: Optional[bool] = None


class MarketplaceOrderItemCreate(BaseModel):
    product_id: int
    quantity: int = 1


class MarketplaceOrderCreate(BaseModel):
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    delivery_notes: Optional[str] = None

    billing_name: Optional[str] = None
    billing_identification: Optional[str] = None
    billing_email: Optional[str] = None
    billing_phone: Optional[str] = None
    billing_address: Optional[str] = None

    items: List[MarketplaceOrderItemCreate]
    discount_code: Optional[str] = None
    pharmacy_loyalty_identifier: Optional[str] = None
    doctor_prescriber_identifier: Optional[str] = None
    payment_method: str = "whatsapp"


class MarketplaceOrderAdminUpdate(BaseModel):
    payment_status: Optional[str] = None
    status: Optional[str] = None
    shipping_notes: Optional[str] = None


class MarketplaceOrderLogisticsUpdate(BaseModel):
    status: Optional[str] = None
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[str] = None
    shipping_notes: Optional[str] = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_pharmacy_admin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "pharmacy_admin"}:
        raise HTTPException(status_code=403, detail="Acceso solo para Farmacia Mayu")


def require_pharmacy_admin_or_logistics(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {
        "superadmin",
        "admin",
        "pharmacy_admin",
        "pharmacy_logistics",
        "logistics",
    }:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para Farmacia o Logística Mayu",
        )


def require_pharmacy_logistics(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {
        "superadmin",
        "admin",
        "pharmacy_admin",
        "pharmacy_logistics",
        "logistics",
    }:
        raise HTTPException(status_code=403, detail="Acceso solo para logística")


def category_to_dict(category: models.MarketplaceCategory):
    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "active": category.active,
        "created_at": category.created_at,
    }


def product_to_dict(product: models.MarketplaceProduct):
    return {
        "id": product.id,
        "name": product.name,
        "category_id": product.category_id,
        "category_name": product.category_rel.name if product.category_rel else None,
        "price": product.price,
        "stock": product.stock,
        "image_url": product.image_url,
        "video_url": getattr(product, "video_url", None),
        "short_description": product.short_description,
        "description": product.description,
        "benefits": product.benefits,
        "ingredients": product.ingredients,
        "suggested_dose": product.suggested_dose,
        "usage_instructions": product.usage_instructions,
        "warnings": product.warnings,
        "presentation": product.presentation,
        "active": product.active,
        "created_by": product.created_by,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


def order_to_dict(order: models.MarketplaceOrder):
    return {
        "id": order.id,
        "order_code": order.order_code,
        "user_id": order.user_id,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "customer_email": order.customer_email,
        "city": order.city,
        "address": order.address,
        "delivery_notes": order.delivery_notes,
        "billing_name": getattr(order, "billing_name", None),
        "billing_identification": getattr(order, "billing_identification", None),
        "billing_email": getattr(order, "billing_email", None),
        "billing_phone": getattr(order, "billing_phone", None),
        "billing_address": getattr(order, "billing_address", None),
        "subtotal": order.subtotal,
        "discount_code": getattr(order, "discount_code", None),
        "pharmacy_loyalty_identifier": getattr(order, "pharmacy_loyalty_identifier", None),
        "doctor_prescriber_identifier": getattr(order, "doctor_prescriber_identifier", None),
        "discount_percent": getattr(order, "discount_percent", 0),
        "discount_amount": getattr(order, "discount_amount", 0),
        "total": order.total,
        "currency": order.currency,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "status": order.status,
        "whatsapp_message": order.whatsapp_message,
        "payphone_transaction_id": order.payphone_transaction_id,
        "payphone_payment_url": order.payphone_payment_url,
        "raw_payment_payload": getattr(order, "raw_payment_payload", None),
        "admin_verified": getattr(order, "admin_verified", False),
        "admin_verified_at": getattr(order, "admin_verified_at", None),
        "admin_verified_by": getattr(order, "admin_verified_by", None),
        "carrier": getattr(order, "carrier", None),
        "tracking_number": getattr(order, "tracking_number", None),
        "tracking_url": getattr(order, "tracking_url", None),
        "shipping_notes": getattr(order, "shipping_notes", None),
        "approved_at": getattr(order, "approved_at", None),
        "prepared_at": getattr(order, "prepared_at", None),
        "shipped_at": getattr(order, "shipped_at", None),
        "delivered_at": getattr(order, "delivered_at", None),
        "created_at": order.created_at,
        "paid_at": order.paid_at,
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product_name_snapshot,
                "unit_price": item.unit_price_snapshot,
                "quantity": item.quantity,
                "total": item.total_snapshot,
            }
            for item in order.items
        ],
        "tracking_history": [
            {
                "id": event.id,
                "status": event.status,
                "note": event.note,
                "carrier": event.carrier,
                "tracking_number": event.tracking_number,
                "tracking_url": event.tracking_url,
                "created_by": event.created_by,
                "created_at": event.created_at,
            }
            for event in order.tracking_history
        ],
    }


def build_marketplace_whatsapp_message(order: models.MarketplaceOrder) -> str:
    lines = [
        f"Hola Mayu, deseo confirmar mi pedido {order.order_code}.",
        "",
        "🛒 Productos:",
    ]

    for item in order.items:
        product_name = item.product_name_snapshot or "Producto Mayu"
        quantity = item.quantity or 1
        line_total = float(item.total_snapshot or 0)
        lines.append(f"- {product_name} x{quantity}: ${line_total:.2f} USD")

    lines.extend(
        [
            "",
            "🚚 Datos de entrega:",
            f"Cliente: {order.customer_name}",
            f"Teléfono: {order.customer_phone}",
        ]
    )

    if order.customer_email:
        lines.append(f"Email: {order.customer_email}")
    if order.city:
        lines.append(f"Ciudad: {order.city}")
    if order.address:
        lines.append(f"Dirección: {order.address}")
    if order.delivery_notes:
        lines.append(f"Notas de entrega: {order.delivery_notes}")

    if getattr(order, "billing_name", None) or getattr(order, "billing_identification", None):
        lines.extend(["", "🧾 Datos de facturación:"])
        if order.billing_name:
            lines.append(f"Nombre/Razón social: {order.billing_name}")
        if order.billing_identification:
            lines.append(f"Cédula/RUC: {order.billing_identification}")
        if order.billing_email:
            lines.append(f"Email factura: {order.billing_email}")
        if order.billing_phone:
            lines.append(f"Teléfono factura: {order.billing_phone}")
        if order.billing_address:
            lines.append(f"Dirección factura: {order.billing_address}")

    lines.extend(["", f"Subtotal: ${float(order.subtotal or 0):.2f} USD"])

    discount_amount = float(getattr(order, "discount_amount", 0) or 0)
    if discount_amount > 0:
        if getattr(order, "discount_code", None):
            lines.append(f"Código Mayu Wellness Club: {order.discount_code}")
        lines.append(f"Descuento Mayu Wellness Club 10%: -${discount_amount:.2f} USD")

    if getattr(order, "pharmacy_loyalty_identifier", None):
        lines.append(f"Tarjeta Mayu Magistral: {order.pharmacy_loyalty_identifier}")
    if getattr(order, "doctor_prescriber_identifier", None):
        lines.append(f"Doctor Prescriptor Mayu: {order.doctor_prescriber_identifier}")

    lines.append(f"Total: ${float(order.total or 0):.2f} USD")
    lines.append("")
    lines.append("Gracias, quedo atento/a a la confirmación.")

    return "\n".join(lines)


def generate_order_code():
    now = datetime.utcnow()
    return f"MP-MAYU-{now.strftime('%Y%m%d%H%M%S')}"


def _normalize_marketplace_identifier(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    cleaned = str(value).strip()
    if not cleaned:
        return None

    if "://" in cleaned:
        for marker in (
            "/doctor-prescribers/qr/",
            "/member-cards/validate/",
            "/pharmacy-loyalty/qr/",
        ):
            if marker in cleaned:
                tail = cleaned.split(marker, 1)[1]
                return tail.split("?", 1)[0].split("/", 1)[0].strip() or None
        cleaned = cleaned.rstrip("/").rsplit("/", 1)[-1]
        cleaned = cleaned.split("?", 1)[0].strip()

    return cleaned or None


def validate_member_discount_code(db: Session, discount_code: Optional[str]):
    code = _normalize_marketplace_identifier(discount_code)
    if not code:
        return None

    member_card = (
        db.query(models.MemberCard)
        .filter(
            (models.MemberCard.member_code == code)
            | (models.MemberCard.qr_token == code)
        )
        .first()
    )

    if not member_card:
        raise HTTPException(status_code=400, detail="Código de socio no válido")

    user = db.query(models.User).filter(models.User.id == member_card.user_id).first()

    if not user:
        raise HTTPException(status_code=400, detail="Socio no encontrado")

    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo. No aplica descuento.")

    if not getattr(user, "membership_active", False):
        raise HTTPException(status_code=403, detail="La membresía no está activa. No aplica descuento.")

    if member_card.status != "active":
        raise HTTPException(status_code=403, detail="Tarjeta de socio inactiva. No aplica descuento.")

    return {
        "user": user,
        "member_card": member_card,
        "discount_code": code,
        "discount_percent": 10.0,
    }


def validate_doctor_prescriber_identifier(db: Session, identifier: Optional[str]):
    cleaned = _normalize_marketplace_identifier(identifier)
    if not cleaned:
        return None

    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(
            (models.DoctorPrescriber.doctor_code == cleaned)
            | (models.DoctorPrescriber.qr_token == cleaned)
        )
        .first()
    )

    if not doctor:
        raise HTTPException(status_code=400, detail="Código de doctor no válido")

    if not getattr(doctor, "is_active", True):
        raise HTTPException(status_code=403, detail="Doctor inactivo. No aplica registro.")

    return {
        "doctor": doctor,
        "doctor_prescriber_identifier": doctor.doctor_code,
        "commission_rate_percent": doctor.commission_rate_bps / 100,
    }


def credit_marketplace_doctor_if_paid(db: Session, order, sync_wallet: bool = True):
    payment_status = (getattr(order, "payment_status", "") or "").strip().lower()
    if payment_status != "paid":
        return {"credited": False, "detail": "El pedido aún no está pagado"}

    doctor_identifier = _normalize_marketplace_identifier(
        getattr(order, "doctor_prescriber_identifier", None)
    )
    if not doctor_identifier:
        return {"credited": False, "detail": "El pedido no tiene doctor prescriptor"}

    reference = f"marketplace:{getattr(order, 'order_code', '')}"
    existing = (
        db.query(models.DoctorCommissionTransaction)
        .filter(models.DoctorCommissionTransaction.reference == reference)
        .first()
    )
    if existing:
        return {
            "credited": False,
            "already_credited": True,
            "doctor_id": existing.doctor_prescriber_id,
            "transaction_id": existing.id,
            "commission_earned": round((existing.commission_cents or 0) / 100, 2),
        }

    doctor_info = validate_doctor_prescriber_identifier(db, doctor_identifier)
    doctor = doctor_info["doctor"]
    sale_amount = round(float(getattr(order, "total", 0) or 0), 2)
    sale_cents = int(round(sale_amount * 100))
    if sale_cents <= 0:
        return {"credited": False, "detail": "El total del pedido no permite acreditar comisión"}

    gross_commission_cents = int(round(sale_cents * doctor.commission_rate_bps / 10000))
    transaction = models.DoctorCommissionTransaction(
        doctor_prescriber_id=doctor.id,
        sale_amount_cents=sale_cents,
        gross_commission_cents=gross_commission_cents,
        deduction_bps=0,
        deduction_cents=0,
        commission_cents=gross_commission_cents,
        commission_rate_bps=doctor.commission_rate_bps,
        source="marketplace_online",
        reference=reference,
        note="Compra online Marketplace Farmacia con código/QR Doctor Prescriptor",
    )
    doctor.total_sales_cents += sale_cents
    doctor.commission_balance_cents += gross_commission_cents
    doctor.lifetime_commission_cents += gross_commission_cents
    doctor.updated_at = datetime.utcnow()
    db.add(transaction)
    db.flush()

    result = {
        "credited": True,
        "doctor_id": doctor.id,
        "doctor_code": doctor.doctor_code,
        "commission_rate_percent": doctor.commission_rate_bps / 100,
        "sale_amount": round(sale_cents / 100, 2),
        "commission_earned": round(gross_commission_cents / 100, 2),
        "transaction_id": transaction.id,
        "wallet_sync": {"google": None},
    }
    if sync_wallet:
        result = sync_marketplace_doctor_wallet_after_commit(
            db,
            result,
            getattr(order, "order_code", None),
        )
    return result


def sync_marketplace_doctor_wallet_after_commit(
    db: Session,
    doctor_result: Optional[dict],
    order_code: Optional[str] = None,
):
    if not doctor_result or not doctor_result.get("credited"):
        return doctor_result

    doctor_id = doctor_result.get("doctor_id")
    if not doctor_id:
        return doctor_result

    doctor = (
        db.query(models.DoctorPrescriber)
        .filter(models.DoctorPrescriber.id == doctor_id)
        .first()
    )
    if not doctor:
        return doctor_result

    google_sync = safe_update_doctor_google_wallet_object(doctor)
    apple_sync = safe_send_doctor_apple_wallet_update_pushes(db, doctor)
    doctor_result["wallet_sync"] = {
        "google": google_sync,
        "apple": apple_sync,
    }
    doctor_result["doctor_code"] = doctor.doctor_code
    doctor_result["order_code"] = order_code
    return doctor_result


@router.post("/upload-image")
def upload_marketplace_image(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=400, detail="Solo se permiten imágenes JPG, PNG o WEBP")

    try:
        cloudinary.config(
            cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
            api_key=os.getenv("CLOUDINARY_API_KEY"),
            api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        )

        result = cloudinary.uploader.upload(
            file.file,
            folder="mayu_marketplace",
            resource_type="image",
        )

        return {
            "message": "Imagen subida correctamente",
            "image_url": result.get("secure_url"),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error subiendo imagen: {str(e)}")


@router.get("/categories")
def get_public_categories(db: Session = Depends(get_db)):
    categories = (
        db.query(models.MarketplaceCategory)
        .filter(models.MarketplaceCategory.active == True)
        .order_by(models.MarketplaceCategory.name.asc())
        .all()
    )

    return {"items": [category_to_dict(c) for c in categories]}


@router.get("/admin/categories")
def get_admin_categories(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    categories = (
        db.query(models.MarketplaceCategory)
        .order_by(models.MarketplaceCategory.id.desc())
        .all()
    )

    return {"items": [category_to_dict(c) for c in categories]}


@router.post("/categories")
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    name = payload.name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")

    existing = (
        db.query(models.MarketplaceCategory)
        .filter(models.MarketplaceCategory.name == name)
        .first()
    )

    if existing:
        raise HTTPException(status_code=400, detail="La categoría ya existe")

    category = models.MarketplaceCategory(
        name=name,
        description=payload.description,
        active=payload.active,
    )

    db.add(category)
    db.commit()
    db.refresh(category)

    return {
        "message": "Categoría creada correctamente",
        "category": category_to_dict(category),
    }


@router.put("/categories/{category_id}")
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    category = (
        db.query(models.MarketplaceCategory)
        .filter(models.MarketplaceCategory.id == category_id)
        .first()
    )

    if not category:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")

    if payload.name is not None:
        name = payload.name.strip()

        if not name:
            raise HTTPException(status_code=400, detail="El nombre es obligatorio")

        duplicate = (
            db.query(models.MarketplaceCategory)
            .filter(
                models.MarketplaceCategory.name == name,
                models.MarketplaceCategory.id != category_id,
            )
            .first()
        )

        if duplicate:
            raise HTTPException(status_code=400, detail="Ya existe otra categoría con ese nombre")

        category.name = name

    if payload.description is not None:
        category.description = payload.description

    if payload.active is not None:
        category.active = payload.active

    db.commit()
    db.refresh(category)

    return {
        "message": "Categoría actualizada correctamente",
        "category": category_to_dict(category),
    }


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    category = (
        db.query(models.MarketplaceCategory)
        .filter(models.MarketplaceCategory.id == category_id)
        .first()
    )

    if not category:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")

    has_products = (
        db.query(models.MarketplaceProduct)
        .filter(models.MarketplaceProduct.category_id == category_id)
        .first()
    )

    if has_products:
        raise HTTPException(
            status_code=400,
            detail="No puedes borrar una categoría que tiene productos. Primero mueve o elimina esos productos.",
        )

    db.delete(category)
    db.commit()

    return {
        "message": "Categoría eliminada correctamente",
        "category_id": category_id,
    }


@router.get("/products")
def get_public_products(
    category_id: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.MarketplaceProduct).filter(models.MarketplaceProduct.active == True)

    if category_id:
        query = query.filter(models.MarketplaceProduct.category_id == category_id)

    if search and search.strip():
        clean_search = f"%{search.strip()}%"
        query = query.filter(models.MarketplaceProduct.name.ilike(clean_search))

    products = query.order_by(models.MarketplaceProduct.id.desc()).all()

    return {"items": [product_to_dict(p) for p in products]}


@router.get("/admin/products")
def get_admin_products(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    products = (
        db.query(models.MarketplaceProduct)
        .order_by(models.MarketplaceProduct.id.desc())
        .all()
    )

    return {"items": [product_to_dict(p) for p in products]}


@router.post("/products")
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    name = payload.name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="El nombre del producto es obligatorio")

    if payload.category_id:
        category = (
            db.query(models.MarketplaceCategory)
            .filter(models.MarketplaceCategory.id == payload.category_id)
            .first()
        )

        if not category:
            raise HTTPException(status_code=404, detail="Categoría no encontrada")

    product = models.MarketplaceProduct(
        name=name,
        category_id=payload.category_id,
        price=payload.price,
        stock=payload.stock,
        image_url=payload.image_url,
        video_url=payload.video_url,
        short_description=payload.short_description,
        description=payload.description,
        benefits=payload.benefits,
        ingredients=payload.ingredients,
        suggested_dose=payload.suggested_dose,
        usage_instructions=payload.usage_instructions,
        warnings=payload.warnings,
        presentation=payload.presentation,
        active=payload.active,
        created_by=current_user.id,
    )

    db.add(product)
    db.commit()
    db.refresh(product)

    return {
        "message": "Producto creado correctamente",
        "product": product_to_dict(product),
    }


@router.put("/products/{product_id}")
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    product = (
        db.query(models.MarketplaceProduct)
        .filter(models.MarketplaceProduct.id == product_id)
        .first()
    )

    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if payload.category_id is not None:
        if payload.category_id != 0:
            category = (
                db.query(models.MarketplaceCategory)
                .filter(models.MarketplaceCategory.id == payload.category_id)
                .first()
            )

            if not category:
                raise HTTPException(status_code=404, detail="Categoría no encontrada")

            product.category_id = payload.category_id
        else:
            product.category_id = None

    for field, value in payload.dict(exclude_unset=True).items():
        if field == "category_id":
            continue

        if field == "name" and value is not None:
            value = value.strip()

            if not value:
                raise HTTPException(status_code=400, detail="El nombre del producto es obligatorio")

        setattr(product, field, value)

    db.commit()
    db.refresh(product)

    return {
        "message": "Producto actualizado correctamente",
        "product": product_to_dict(product),
    }


@router.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    product = (
        db.query(models.MarketplaceProduct)
        .filter(models.MarketplaceProduct.id == product_id)
        .first()
    )

    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    db.delete(product)
    db.commit()

    return {
        "message": "Producto eliminado correctamente",
        "product_id": product_id,
    }


@router.post("/orders")
def create_marketplace_order(
    payload: MarketplaceOrderCreate,
    db: Session = Depends(get_db),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="El carrito está vacío")

    customer_name = payload.customer_name.strip()
    customer_phone = payload.customer_phone.strip()

    if not customer_name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")

    if not customer_phone:
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")

    subtotal = 0.0
    order_items_data = []

    for item in payload.items:
        if item.quantity <= 0:
            raise HTTPException(status_code=400, detail="La cantidad debe ser mayor a cero")

        product = (
            db.query(models.MarketplaceProduct)
            .filter(models.MarketplaceProduct.id == item.product_id)
            .filter(models.MarketplaceProduct.active == True)
            .first()
        )

        if not product:
            raise HTTPException(status_code=404, detail=f"Producto {item.product_id} no encontrado")

        if product.stock < item.quantity:
            raise HTTPException(status_code=400, detail=f"Stock insuficiente para {product.name}")

        line_total = float(product.price) * int(item.quantity)
        subtotal += line_total

        order_items_data.append(
            {
                "product": product,
                "quantity": item.quantity,
                "line_total": line_total,
            }
        )

    discount_info = validate_member_discount_code(db, payload.discount_code)
    doctor_info = validate_doctor_prescriber_identifier(
        db,
        payload.doctor_prescriber_identifier,
    )

    discount_code = None
    discount_percent = 0.0
    discount_amount = 0.0
    user_id = None

    if discount_info:
        discount_code = discount_info["discount_code"]
        discount_percent = discount_info["discount_percent"]
        discount_amount = round(subtotal * (discount_percent / 100), 2)
        user_id = discount_info["user"].id

    total = round(subtotal - discount_amount, 2)

    order = models.MarketplaceOrder(
        order_code=generate_order_code(),
        user_id=user_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_email=payload.customer_email,
        city=payload.city,
        address=payload.address,
        delivery_notes=payload.delivery_notes,
        billing_name=payload.billing_name,
        billing_identification=payload.billing_identification,
        billing_email=payload.billing_email,
        billing_phone=payload.billing_phone,
        billing_address=payload.billing_address,
        subtotal=round(subtotal, 2),
        discount_code=discount_code,
        pharmacy_loyalty_identifier=(
            payload.pharmacy_loyalty_identifier.strip()
            if payload.pharmacy_loyalty_identifier and payload.pharmacy_loyalty_identifier.strip()
            else None
        ),
        doctor_prescriber_identifier=(
            doctor_info["doctor_prescriber_identifier"] if doctor_info else None
        ),
        discount_percent=discount_percent,
        discount_amount=discount_amount,
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
        db, name=order.customer_name, email=order.customer_email or order.billing_email,
        phone=order.customer_phone or order.billing_phone, source="marketplace",
        user_id=order.user_id, city=order.city, purchase_kind="marketplace",
        purchase_at=order.created_at, increment_purchase=True,
    )
    db.commit()

    for item_data in order_items_data:
        product = item_data["product"]
        quantity = item_data["quantity"]
        line_total = item_data["line_total"]

        order_item = models.MarketplaceOrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name_snapshot=product.name,
            unit_price_snapshot=product.price,
            quantity=quantity,
            total_snapshot=round(line_total, 2),
        )

        product.stock = product.stock - quantity
        db.add(order_item)

    db.flush()
    order.whatsapp_message = build_marketplace_whatsapp_message(order)

    db.commit()
    db.refresh(order)

    return {
        "message": "Orden creada correctamente",
        "order": order_to_dict(order),
    }


@router.get("/orders/{order_id}")
def get_marketplace_order(
    order_id: int,
    db: Session = Depends(get_db),
):
    order = (
        db.query(models.MarketplaceOrder)
        .filter(models.MarketplaceOrder.id == order_id)
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    return order_to_dict(order)


@router.get("/my-orders")
def get_my_marketplace_orders(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    orders = (
        db.query(models.MarketplaceOrder)
        .filter(models.MarketplaceOrder.user_id == current_user.id)
        .order_by(models.MarketplaceOrder.id.desc())
        .all()
    )

    return {"items": [order_to_dict(order) for order in orders]}


@router.get("/my-orders/{order_id}")
def get_my_marketplace_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    order = (
        db.query(models.MarketplaceOrder)
        .filter(
            models.MarketplaceOrder.id == order_id,
            models.MarketplaceOrder.user_id == current_user.id,
        )
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    return order_to_dict(order)


@router.get("/admin/orders")
def get_admin_orders(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin_or_logistics(current_user)

    orders = (
        db.query(models.MarketplaceOrder)
        .order_by(models.MarketplaceOrder.id.desc())
        .all()
    )

    return {"items": [order_to_dict(o) for o in orders]}


@router.put("/admin/orders/{order_id}/admin")
def update_order_by_pharmacy_admin(
    order_id: int,
    payload: MarketplaceOrderAdminUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    order = (
        db.query(models.MarketplaceOrder)
        .filter(models.MarketplaceOrder.id == order_id)
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    if payload.payment_status is not None:
        order.payment_status = payload.payment_status

    status_changed = False
    if payload.status is not None:
        previous_status = order.status
        status_changed = payload.status != previous_status
        order.status = payload.status

        if payload.status in {"approved", "admin_approved"}:
            order.admin_verified = True
            order.admin_verified_at = datetime.utcnow()
            order.admin_verified_by = current_user.id
            order.approved_at = datetime.utcnow()

        if payload.status == "cancelled":
            order.admin_verified = False

        if status_changed:
            add_tracking_history(
                db,
                order,
                payload.status,
                payload.shipping_notes,
                current_user.id,
            )

    if payload.shipping_notes is not None:
        order.shipping_notes = payload.shipping_notes

    loyalty_result = None
    doctor_result = None
    if (
        payload.payment_status is not None
        and payload.payment_status.strip().lower() == "paid"
    ):
        loyalty_result = credit_marketplace_order_if_paid(
            db,
            order,
            sync_wallet=False,
        )
        doctor_result = credit_marketplace_doctor_if_paid(
            db,
            order,
            sync_wallet=False,
        )

    db.commit()
    db.refresh(order)
    if loyalty_result:
        loyalty_result = sync_marketplace_loyalty_wallet_after_commit(
            db,
            loyalty_result,
            order.order_code,
        )
    if doctor_result:
        doctor_result = sync_marketplace_doctor_wallet_after_commit(
            db,
            doctor_result,
            order.order_code,
        )

    if status_changed and payload.status in {"approved", "admin_approved"}:
        notify_customer_order(
            db,
            order,
            "Pedido aprobado",
            f"Tu pedido {order.order_code} fue aprobado por Farmacia Mayu.",
        )
        safe_send_push_to_roles(
            db,
            {"pharmacy_logistics", "logistics"},
            "Pedido listo para preparar",
            f"El pedido {order.order_code} fue aprobado por Farmacia.",
        )
    elif status_changed and payload.status == "cancelled":
        notify_customer_order(
            db,
            order,
            "Pedido cancelado",
            f"Tu pedido {order.order_code} fue cancelado. Contáctanos si necesitas ayuda.",
        )

    return {
        "message": "Orden actualizada por farmacia",
        "order": order_to_dict(order),
        "loyalty": loyalty_result,
        "doctor_commission": doctor_result,
    }


@router.put("/admin/orders/{order_id}/logistics")
def update_order_by_pharmacy_logistics(
    order_id: int,
    payload: MarketplaceOrderLogisticsUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_logistics(current_user)

    order = (
        db.query(models.MarketplaceOrder)
        .filter(models.MarketplaceOrder.id == order_id)
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    if not getattr(order, "admin_verified", False):
        raise HTTPException(
            status_code=403,
            detail="La orden todavía no ha sido aprobada por Farmacia",
        )

    status_changed = False
    if payload.status is not None:
        previous_status = order.status
        status_changed = payload.status != previous_status
        order.status = payload.status

        if payload.status == "prepared":
            order.prepared_at = datetime.utcnow()

        if payload.status == "shipped":
            order.shipped_at = datetime.utcnow()

        if payload.status == "delivered":
            order.delivered_at = datetime.utcnow()

    if payload.carrier is not None:
        order.carrier = payload.carrier

    if payload.tracking_number is not None:
        order.tracking_number = payload.tracking_number

    if payload.tracking_url is not None:
        order.tracking_url = payload.tracking_url

    if payload.shipping_notes is not None:
        order.shipping_notes = payload.shipping_notes

    if status_changed:
        add_tracking_history(
            db,
            order,
            payload.status,
            payload.shipping_notes,
            current_user.id,
        )

    db.commit()
    db.refresh(order)

    status_notifications = {
        "prepared": (
            "Pedido preparado",
            f"Tu pedido {order.order_code} está preparado.",
        ),
        "shipped": (
            "Pedido enviado",
            f"Tu pedido {order.order_code} fue enviado. Revisa la información de seguimiento.",
        ),
        "delivered": (
            "Pedido entregado",
            f"Tu pedido {order.order_code} fue entregado.",
        ),
    }
    if status_changed and payload.status in status_notifications:
        subject, message = status_notifications[payload.status]
        notify_customer_order(db, order, subject, message)

    return {
        "message": "Orden actualizada por logística",
        "order": order_to_dict(order),
    }
