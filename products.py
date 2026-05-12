from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
import models

router = APIRouter(prefix="/products", tags=["Products"])


class ProductCreate(BaseModel):
    name: str
    price: float = 0
    description: str = ""
    is_active: bool = True


class ProductUpdate(BaseModel):
    name: str | None = None
    price: float | None = None
    description: str | None = None
    is_active: bool | None = None


@router.get("/")
def get_products(db: Session = Depends(get_db)):
    products = db.query(models.Product).order_by(models.Product.name.asc()).all()

    return [
        {
            "id": p.id,
            "name": p.name,
            "price": p.price,
            "description": p.description,
            "is_active": getattr(p, "is_active", True),
        }
        for p in products
    ]


@router.get("/active")
def get_active_products(db: Session = Depends(get_db)):
    products = db.query(models.Product).filter(
        models.Product.is_active == True
    ).order_by(models.Product.name.asc()).all()

    return [
        {
            "id": p.id,
            "name": p.name,
            "price": p.price,
            "description": p.description,
            "is_active": p.is_active,
        }
        for p in products
    ]


@router.post("/")
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
):
    existing = db.query(models.Product).filter(
        models.Product.name == payload.name.strip()
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Ya existe un producto con este nombre",
        )

    product = models.Product(
        name=payload.name.strip(),
        price=payload.price,
        description=payload.description,
        is_active=payload.is_active,
    )

    db.add(product)
    db.commit()
    db.refresh(product)

    return {
        "message": "Producto creado correctamente",
        "product": {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "description": product.description,
            "is_active": product.is_active,
        }
    }


@router.put("/{product_id}")
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
):
    product = db.query(models.Product).filter(
        models.Product.id == product_id
    ).first()

    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if payload.name is not None:
        product.name = payload.name.strip()

    if payload.price is not None:
        product.price = payload.price

    if payload.description is not None:
        product.description = payload.description

    if payload.is_active is not None:
        product.is_active = payload.is_active

    db.commit()
    db.refresh(product)

    return {
        "message": "Producto actualizado correctamente",
        "product": {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "description": product.description,
            "is_active": product.is_active,
        }
    }


@router.post("/seed-mayu-products")
def seed_mayu_products(db: Session = Depends(get_db)):
    products = [

        # =========================
        # COLOIDES
        # =========================
        {
            "name": "Plata Coloidal",
            "price": 2,
            "description": "Coloide a libre elección",
        },
        {
            "name": "Cobre Coloidal",
            "price": 2,
            "description": "Coloide a libre elección",
        },
        {
            "name": "Selenio Coloidal",
            "price": 2,
            "description": "Coloide a libre elección",
        },
        {
            "name": "Oro Coloidal",
            "price": 4,
            "description": "Coloide a libre elección",
        },
        {
            "name": "Zinc Coloidal",
            "price": 3,
            "description": "Coloide a libre elección",
        },
        {
            "name": "Shunguita",
            "price": 3,
            "description": "Coloide a libre elección",
        },
        {
            "name": "Silicio",
            "price": 3,
            "description": "Coloide a libre elección",
        },
        {
            "name": "Magnesio",
            "price": 3,
            "description": "Coloide a libre elección",
        },

        # =========================
        # CBD
        # =========================
        {
            "name": "CBD 874 mg",
            "price": 6,
            "description": "Producto base CBD",
        },
        {
            "name": "CBD 4%",
            "price": 7,
            "description": "Extracto CBD funcional",
        },
        {
            "name": "Fórmula del Sueño",
            "price": 8,
            "description": "Soporte natural del sueño",
        },

        # =========================
        # WELLNESS
        # =========================
        {
            "name": "Chocomedical",
            "price": 2,
            "description": "Chocolate funcional",
        },
        {
            "name": "Choco + Lion’s Mane",
            "price": 4,
            "description": "Chocolate neurofuncional",
        },
        {
            "name": "Té de Cannabis",
            "price": 3,
            "description": "Infusión funcional",
        },
        {
            "name": "Aceite terapéutico de Limón",
            "price": 4,
            "description": "Aceite esencial funcional",
        },
        {
            "name": "Aceite terapéutico de Naranja",
            "price": 4,
            "description": "Aceite esencial funcional",
        },

        # =========================
        # HONGOS
        # =========================
        {
            "name": "Melena de León",
            "price": 6,
            "description": "Neuroregenerador funcional",
        },
        {
            "name": "Reishi",
            "price": 6,
            "description": "Hongo adaptógeno",
        },
        {
            "name": "Turkey Tail",
            "price": 6,
            "description": "Hongo funcional inmunológico",
        },
        {
            "name": "Chaga",
            "price": 6,
            "description": "Hongo antioxidante",
        },

        # =========================
        # SOPORTE FUNCIONAL
        # =========================
        {
            "name": "Magnesio Bisglicinato",
            "price": 3,
            "description": "Soporte mineral funcional",
        },
        {
            "name": "MSM",
            "price": 3,
            "description": "Soporte articular",
        },
        {
            "name": "Koral Jade",
            "price": 4,
            "description": "Mineral funcional",
        },
        {
            "name": "Ashwagandha",
            "price": 4,
            "description": "Adaptógeno funcional",
        },
    ]

    created = []
    existing_products = []

    for p in products:
        existing = db.query(models.Product).filter(
            models.Product.name == p["name"]
        ).first()

        if existing:
            existing_products.append(existing.name)
            continue

        product = models.Product(
            name=p["name"],
            price=p["price"],
            description=p["description"],
            is_active=True,
        )

        db.add(product)
        created.append(product.name)

    db.commit()

    return {
        "message": "Seed Mayu ejecutado correctamente",
        "created_count": len(created),
        "existing_count": len(existing_products),
        "created_products": created,
        "existing_products": existing_products,
    }
