from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
import models

router = APIRouter(prefix="/products", tags=["Products"])


ALLOWED_CATEGORIES = {
    "coloides",
    "cbd",
    "bienestar",
    "hongos",
    "soporte_funcional",
}


class ProductCreate(BaseModel):
    name: str
    price: float = 0
    description: str = ""
    image_url: str | None = None
    category: str | None = None
    active: bool = True


class ProductUpdate(BaseModel):
    name: str | None = None
    price: float | None = None
    description: str | None = None
    image_url: str | None = None
    category: str | None = None
    active: bool | None = None


def clean_category(category: str | None):
    if category is None:
        return None

    clean = category.strip().lower()

    if not clean:
        return None

    if clean not in ALLOWED_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail="Categoría inválida. Usa: coloides, cbd, bienestar, hongos o soporte_funcional",
        )

    return clean


def product_to_dict(p: models.Product):
    return {
        "id": p.id,
        "name": p.name,
        "price": p.price,
        "description": p.description,
        "image_url": p.image_url,
        "category": getattr(p, "category", None),
        "active": p.active,
    }


@router.get("/")
def get_products(db: Session = Depends(get_db)):
    products = db.query(models.Product).order_by(models.Product.name.asc()).all()
    return [product_to_dict(p) for p in products]


@router.get("/active")
def get_active_products(db: Session = Depends(get_db)):
    products = (
        db.query(models.Product)
        .filter(models.Product.active == True)
        .order_by(models.Product.name.asc())
        .all()
    )

    return [product_to_dict(p) for p in products]


@router.get("/category/{category}")
def get_products_by_category(
    category: str,
    db: Session = Depends(get_db),
):
    clean = clean_category(category)

    products = (
        db.query(models.Product)
        .filter(
            models.Product.active == True,
            models.Product.category == clean,
        )
        .order_by(models.Product.name.asc())
        .all()
    )

    return [product_to_dict(p) for p in products]


@router.post("/")
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    name = payload.name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")

    existing = db.query(models.Product).filter(models.Product.name == name).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Ya existe un producto con este nombre",
        )

    product = models.Product(
        name=name,
        price=payload.price,
        description=payload.description,
        image_url=payload.image_url,
        category=clean_category(payload.category),
        active=payload.active,
    )

    db.add(product)
    db.commit()
    db.refresh(product)

    return {
        "message": "Producto creado correctamente",
        "product": product_to_dict(product),
    }


@router.put("/{product_id}")
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()

    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if payload.name is not None:
        name = payload.name.strip()

        if not name:
            raise HTTPException(status_code=400, detail="El nombre es obligatorio")

        existing = (
            db.query(models.Product)
            .filter(models.Product.name == name, models.Product.id != product_id)
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail="Ya existe otro producto con este nombre",
            )

        product.name = name

    if payload.price is not None:
        product.price = payload.price

    if payload.description is not None:
        product.description = payload.description

    if payload.image_url is not None:
        product.image_url = payload.image_url

    if payload.category is not None:
        product.category = clean_category(payload.category)

    if payload.active is not None:
        product.active = payload.active

    db.commit()
    db.refresh(product)

    return {
        "message": "Producto actualizado correctamente",
        "product": product_to_dict(product),
    }


@router.post("/seed-mayu-products")
def seed_mayu_products(db: Session = Depends(get_db)):
    products = [
        {"name": "Plata Coloidal", "price": 2, "description": "Coloide a libre elección", "category": "coloides"},
        {"name": "Cobre Coloidal", "price": 2, "description": "Coloide a libre elección", "category": "coloides"},
        {"name": "Selenio Coloidal", "price": 2, "description": "Coloide a libre elección", "category": "coloides"},
        {"name": "Oro Coloidal", "price": 4, "description": "Coloide a libre elección", "category": "coloides"},
        {"name": "Zinc Coloidal", "price": 3, "description": "Coloide a libre elección", "category": "coloides"},
        {"name": "Shunguita", "price": 3, "description": "Coloide a libre elección", "category": "coloides"},
        {"name": "Silicio", "price": 3, "description": "Coloide a libre elección", "category": "coloides"},
        {"name": "Magnesio", "price": 3, "description": "Coloide a libre elección", "category": "coloides"},

        {"name": "CBD 874 mg", "price": 6, "description": "Producto base CBD", "category": "cbd"},
        {"name": "CBD 4%", "price": 7, "description": "Extracto CBD funcional", "category": "cbd"},
        {"name": "Fórmula del Sueño", "price": 8, "description": "Soporte natural del sueño", "category": "cbd"},
        {"name": "Té de Cannabis", "price": 3, "description": "Infusión funcional", "category": "cbd"},

        {"name": "Chocomedical", "price": 2, "description": "Chocolate funcional", "category": "bienestar"},
        {"name": "Aceite terapéutico de Limón", "price": 4, "description": "Aceite esencial funcional", "category": "bienestar"},
        {"name": "Aceite terapéutico de Naranja", "price": 4, "description": "Aceite esencial funcional", "category": "bienestar"},
        {"name": "Ashwagandha", "price": 4, "description": "Adaptógeno funcional", "category": "bienestar"},

        {"name": "Melena de León", "price": 6, "description": "Neuroregenerador funcional", "category": "hongos"},
        {"name": "Choco + Lion’s Mane", "price": 4, "description": "Chocolate neurofuncional", "category": "hongos"},
        {"name": "Reishi", "price": 6, "description": "Hongo adaptógeno", "category": "hongos"},
        {"name": "Turkey Tail", "price": 6, "description": "Hongo funcional inmunológico", "category": "hongos"},
        {"name": "Chaga", "price": 6, "description": "Hongo antioxidante", "category": "hongos"},

        {"name": "Magnesio Bisglicinato", "price": 3, "description": "Soporte mineral funcional", "category": "soporte_funcional"},
        {"name": "MSM", "price": 3, "description": "Soporte articular", "category": "soporte_funcional"},
        {"name": "Koral Jade", "price": 4, "description": "Mineral funcional", "category": "soporte_funcional"},
    ]

    created = []
    updated = []

    for p in products:
        existing = db.query(models.Product).filter(
            models.Product.name == p["name"]
        ).first()

        if existing:
            existing.price = p["price"]
            existing.description = p["description"]
            existing.category = p["category"]
            existing.active = True
            updated.append(existing.name)
            continue

        product = models.Product(
            name=p["name"],
            price=p["price"],
            description=p["description"],
            category=p["category"],
            active=True,
        )

        db.add(product)
        created.append(product.name)

    db.commit()

    return {
        "message": "Seed Mayu ejecutado correctamente",
        "created_count": len(created),
        "updated_count": len(updated),
        "created_products": created,
        "updated_products": updated,
    }
