from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional, List
from datetime import datetime
from urllib.parse import quote_plus
import os

from database import get_db
from dependencies import get_current_user
import models


router = APIRouter(prefix="/marketplace", tags=["Marketplace"])


def require_pharmacy_admin(current_user: models.User):
    if not current_user:
        raise HTTPException(status_code=401, detail="No autenticado")

    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    if current_user.role not in {"superadmin", "admin", "pharmacy_admin", "farmacia"}:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para Farmacia Mayu o administración",
        )

    return current_user


def marketplace_product_to_dict(product: models.MarketplaceProduct):
    return {
        "id": product.id,
        "name": product.name,
        "category_id": product.category_id,
        "category_name": product.category_rel.name if product.category_rel else None,
        "price": product.price,
        "stock": product.stock,
        "image_url": product.image_url,
        "short_description": product.short_description,
        "description": product.description,
        "benefits": product.benefits,
        "ingredients": product.ingredients,
        "suggested_dose": product.suggested_dose,
        "usage_instructions": product.usage_instructions,
        "warnings": product.warnings,
        "presentation": product.presentation,
        "active": product.active,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


class ProductCreate(BaseModel):
    name: str
    category_id: Optional[int] = None
    price: float
    stock: int = 0
    image_url: Optional[str] = None
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
    short_description: Optional[str] = None
    description: Optional[str] = None
    benefits: Optional[str] = None
    ingredients: Optional[str] = None
    suggested_dose: Optional[str] = None
    usage_instructions: Optional[str] = None
    warnings: Optional[str] = None
    presentation: Optional[str] = None
    active: Optional[bool] = None


class MarketplaceOrderItemIn(BaseModel):
    product_id: int
    quantity: int = 1


class MarketplaceOrderCreate(BaseModel):
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    delivery_notes: Optional[str] = None
    payment_method: str = "whatsapp"
    items: List[MarketplaceOrderItemIn]


@router.get("/categories/public")
def public_categories(db: Session = Depends(get_db)):
    categories = (
        db.query(models.MarketplaceCategory)
        .filter(models.MarketplaceCategory.active == True)
        .order_by(models.MarketplaceCategory.name.asc())
        .all()
    )

    return [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "active": c.active,
        }
        for c in categories
    ]


@router.get("/products/public")
def public_products(
    search: Optional[str] = None,
    category_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.MarketplaceProduct).filter(
        models.MarketplaceProduct.active == True
    )

    if category_id:
        query = query.filter(models.MarketplaceProduct.category_id == category_id)

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                models.MarketplaceProduct.name.ilike(pattern),
                models.MarketplaceProduct.description.ilike(pattern),
                models.MarketplaceProduct.benefits.ilike(pattern),
                models.MarketplaceProduct.ingredients.ilike(pattern),
            )
        )

    products = query.order_by(models.MarketplaceProduct.created_at.desc()).all()

    return [marketplace_product_to_dict(p) for p in products]


@router.get("/products/public/{product_id}")
def public_product_detail(product_id: int, db: Session = Depends(get_db)):
    product = (
        db.query(models.MarketplaceProduct)
        .filter(
            models.MarketplaceProduct.id == product_id,
            models.MarketplaceProduct.active == True,
        )
        .first()
    )

    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    return marketplace_product_to_dict(product)


@router.post("/categories")
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    existing = (
        db.query(models.MarketplaceCategory)
        .filter(models.MarketplaceCategory.name == payload.name)
        .first()
    )

    if existing:
        raise HTTPException(status_code=400, detail="La categoría ya existe")

    category = models.MarketplaceCategory(
        name=payload.name,
        description=payload.description,
        active=True,
    )

    db.add(category)
    db.commit()
    db.refresh(category)

    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "active": category.active,
    }


@router.get("/categories")
def admin_categories(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    categories = (
        db.query(models.MarketplaceCategory)
        .order_by(models.MarketplaceCategory.name.asc())
        .all()
    )

    return [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "active": c.active,
        }
        for c in categories
    ]


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

    data = payload.dict(exclude_unset=True)

    for key, value in data.items():
        setattr(category, key, value)

    db.commit()
    db.refresh(category)

    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "active": category.active,
    }


@router.post("/products")
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    if payload.category_id:
        category = (
            db.query(models.MarketplaceCategory)
            .filter(models.MarketplaceCategory.id == payload.category_id)
            .first()
        )
        if not category:
            raise HTTPException(status_code=404, detail="Categoría no encontrada")

    product = models.MarketplaceProduct(
        name=payload.name,
        category_id=payload.category_id,
        price=payload.price,
        stock=payload.stock,
        image_url=payload.image_url,
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

    return marketplace_product_to_dict(product)


@router.get("/products")
def admin_products(
    search: Optional[str] = None,
    category_id: Optional[int] = None,
    only_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    query = db.query(models.MarketplaceProduct)

    if only_active is not None:
        query = query.filter(models.MarketplaceProduct.active == only_active)

    if category_id:
        query = query.filter(models.MarketplaceProduct.category_id == category_id)

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                models.MarketplaceProduct.name.ilike(pattern),
                models.MarketplaceProduct.description.ilike(pattern),
                models.MarketplaceProduct.benefits.ilike(pattern),
                models.MarketplaceProduct.ingredients.ilike(pattern),
            )
        )

    products = query.order_by(models.MarketplaceProduct.created_at.desc()).all()

    return [marketplace_product_to_dict(p) for p in products]


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

    data = payload.dict(exclude_unset=True)

    if "category_id" in data and data["category_id"]:
        category = (
            db.query(models.MarketplaceCategory)
            .filter(models.MarketplaceCategory.id == data["category_id"])
            .first()
        )
        if not category:
            raise HTTPException(status_code=404, detail="Categoría no encontrada")

    for key, value in data.items():
        setattr(product, key, value)

    db.commit()
    db.refresh(product)

    return marketplace_product_to_dict(product)


@router.patch("/products/{product_id}/toggle")
def toggle_product_status(
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

    product.active = not product.active

    db.commit()
    db.refresh(product)

    return marketplace_product_to_dict(product)


def generate_marketplace_order_code():
    now = datetime.utcnow()
    return f"MKP-{now.strftime('%Y%m%d%H%M%S')}"


def build_whatsapp_message(order: models.MarketplaceOrder):
    lines = []
    lines.append("Hola Mayu, quiero realizar este pedido desde el Marketplace:")
    lines.append("")
    lines.append(f"Código de pedido: {order.order_code}")
    lines.append("")
    lines.append("Productos:")

    for item in order.items:
        lines.append(
            f"- {item.product_name_snapshot} x{item.quantity} | ${item.total_snapshot:.2f}"
        )

    lines.append("")
    lines.append(f"Total: ${order.total:.2f} {order.currency}")
    lines.append("")
    lines.append(f"Nombre: {order.customer_name}")
    lines.append(f"Teléfono: {order.customer_phone}")

    if order.customer_email:
        lines.append(f"Email: {order.customer_email}")

    if order.city:
        lines.append(f"Ciudad: {order.city}")

    if order.address:
        lines.append(f"Dirección: {order.address}")

    if order.delivery_notes:
        lines.append(f"Notas de entrega: {order.delivery_notes}")

    return "\n".join(lines)


@router.post("/orders")
def create_marketplace_order(
    payload: MarketplaceOrderCreate,
    db: Session = Depends(get_db),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="El pedido no tiene productos")

    order_code = generate_marketplace_order_code()

    order = models.MarketplaceOrder(
        order_code=order_code,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        customer_email=payload.customer_email,
        city=payload.city,
        address=payload.address,
        delivery_notes=payload.delivery_notes,
        payment_method=payload.payment_method,
        payment_status="pending",
        status="created",
        subtotal=0,
        total=0,
        currency="USD",
    )

    db.add(order)
    db.flush()

    subtotal = 0

    for item_in in payload.items:
        if item_in.quantity <= 0:
            raise HTTPException(status_code=400, detail="Cantidad inválida")

        product = (
            db.query(models.MarketplaceProduct)
            .filter(
                models.MarketplaceProduct.id == item_in.product_id,
                models.MarketplaceProduct.active == True,
            )
            .first()
        )

        if not product:
            raise HTTPException(
                status_code=404,
                detail=f"Producto {item_in.product_id} no encontrado",
            )

        if product.stock < item_in.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Stock insuficiente para {product.name}",
            )

        line_total = product.price * item_in.quantity
        subtotal += line_total

        order_item = models.MarketplaceOrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name_snapshot=product.name,
            unit_price_snapshot=product.price,
            quantity=item_in.quantity,
            total_snapshot=line_total,
        )

        db.add(order_item)

    order.subtotal = subtotal
    order.total = subtotal

    db.flush()
    db.refresh(order)

    whatsapp_message = build_whatsapp_message(order)
    order.whatsapp_message = whatsapp_message

    db.commit()
    db.refresh(order)

    whatsapp_phone = os.getenv("MARKETPLACE_WHATSAPP_PHONE", "593999999999")
    whatsapp_url = f"https://wa.me/{whatsapp_phone}?text={quote_plus(whatsapp_message)}"

    return {
        "id": order.id,
        "order_code": order.order_code,
        "subtotal": order.subtotal,
        "total": order.total,
        "currency": order.currency,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "status": order.status,
        "whatsapp_message": order.whatsapp_message,
        "whatsapp_url": whatsapp_url,
        "items": [
            {
                "product_id": item.product_id,
                "product_name": item.product_name_snapshot,
                "unit_price": item.unit_price_snapshot,
                "quantity": item.quantity,
                "total": item.total_snapshot,
            }
            for item in order.items
        ],
    }


@router.get("/orders")
def admin_marketplace_orders(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    require_pharmacy_admin(current_user)

    orders = (
        db.query(models.MarketplaceOrder)
        .order_by(models.MarketplaceOrder.created_at.desc())
        .all()
    )

    return [
        {
            "id": order.id,
            "order_code": order.order_code,
            "customer_name": order.customer_name,
            "customer_phone": order.customer_phone,
            "total": order.total,
            "currency": order.currency,
            "payment_method": order.payment_method,
            "payment_status": order.payment_status,
            "status": order.status,
            "created_at": order.created_at,
        }
        for order in orders
    ]
