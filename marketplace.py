from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import SessionLocal
from dependencies import get_current_user
import models

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

    if current_user.role not in {
        "superadmin",
        "admin",
        "pharmacy_admin",
    }:
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para Farmacia Mayu",
        )


def category_to_dict(category: models.MarketplaceCategory):
    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "active": category.active,
        "created_at": category.created_at,
    }


def product_to_dict(product: models.MarketplaceProduct):
    category_name = None

    if product.category_rel:
        category_name = product.category_rel.name

    return {
        "id": product.id,
        "name": product.name,
        "category_id": product.category_id,
        "category_name": category_name,
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
        "created_by": product.created_by,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


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
            raise HTTPException(
                status_code=400,
                detail="El nombre es obligatorio",
            )

        duplicate = (
            db.query(models.MarketplaceCategory)
            .filter(
                models.MarketplaceCategory.name == name,
                models.MarketplaceCategory.id != category_id,
            )
            .first()
        )

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Ya existe otra categoría con ese nombre",
            )

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


@router.get("/products")
def get_public_products(
    category_id: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = (
        db.query(models.MarketplaceProduct)
        .filter(models.MarketplaceProduct.active == True)
    )

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
        raise HTTPException(
            status_code=400,
            detail="El nombre del producto es obligatorio",
        )

    if payload.category_id:
        category = (
            db.query(models.MarketplaceCategory)
            .filter(models.MarketplaceCategory.id == payload.category_id)
            .first()
        )

        if not category:
            raise HTTPException(
                status_code=404,
                detail="Categoría no encontrada",
            )

    product = models.MarketplaceProduct(
        name=name,
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
                raise HTTPException(
                    status_code=404,
                    detail="Categoría no encontrada",
                )

            product.category_id = payload.category_id
        else:
            product.category_id = None

    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "category_id":
            continue

        if field == "name" and value is not None:
            value = value.strip()

            if not value:
                raise HTTPException(
                    status_code=400,
                    detail="El nombre del producto es obligatorio",
                )

        setattr(product, field, value)

    db.commit()
    db.refresh(product)

    return {
        "message": "Producto actualizado correctamente",
        "product": product_to_dict(product),
    }
