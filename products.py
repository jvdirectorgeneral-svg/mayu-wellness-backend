from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("/")
def get_products(db: Session = Depends(get_db)):
    return db.query(models.Product).all()


@router.post("/seed")
def seed_products(db: Session = Depends(get_db)):
    products = [
        # COLOIDES
        {"name": "Plata Coloidal", "price": 2, "description": "Coloide a libre elección"},
        {"name": "Cobre Coloidal", "price": 2, "description": "Coloide a libre elección"},
        {"name": "Selenio Coloidal", "price": 2, "description": "Coloide a libre elección"},
        {"name": "Oro Coloidal", "price": 4, "description": "Coloide a libre elección"},
        {"name": "Zinc Coloidal", "price": 3, "description": "Coloide a libre elección"},
        {"name": "Shunguita", "price": 3, "description": "Coloide a libre elección"},
        {"name": "Silicio", "price": 3, "description": "Coloide a libre elección"},
        {"name": "Magnesio", "price": 3, "description": "Coloide a libre elección"},

        # BASES / FUNCIONALES
        {"name": "CBD 874 mg", "price": 6, "description": "Producto base del club"},
        {"name": "Chocomedical", "price": 2, "description": "Chocolate funcional"},
        {"name": "Melena de León", "price": 6, "description": "Neuroregenerador"},
        {"name": "Té CBD", "price": 3, "description": "Infusión funcional con CBD"},
        {"name": "Magnesio Bisglicinato", "price": 3, "description": "Soporte mineral funcional"},
    ]

    created = []

    for p in products:
        existing = db.query(models.Product).filter(models.Product.name == p["name"]).first()
        if not existing:
            product = models.Product(
                name=p["name"],
                price=p["price"],
                description=p["description"]
            )
            db.add(product)
            created.append(product.name)

    db.commit()

    return {
        "message": "Productos creados correctamente",
        "products": created
    }
