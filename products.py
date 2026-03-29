from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("/")
def get_products(db: Session = Depends(get_db)):
    return db.query(models.Product).filter(models.Product.active == True).all()


@router.post("/seed")
def seed_products(db: Session = Depends(get_db)):

    products = [
        {"name": "Plata Coloidal", "price": 2, "description": "Coloide base"},
        {"name": "Cobre Coloidal", "price": 2, "description": "Coloide base"},
        {"name": "Selenio Coloidal", "price": 2, "description": "Coloide base"},
        {"name": "Oro Coloidal", "price": 4, "description": "Coloide premium"},
        {"name": "Zinc Coloidal", "price": 3, "description": "Inmunidad y regeneración"},
        {"name": "Magnesio Coloidal", "price": 3, "description": "Relajación muscular"},
        {"name": "Silicio Coloidal", "price": 3, "description": "Soporte estructural"},
        {"name": "CBD 874 mg", "price": 6, "description": "CBD base del club"},
        {"name": "Melena de León", "price": 6, "description": "Neuroregenerador"},
        {"name": "Chocomedical", "price": 2, "description": "Chocolate funcional"},
        {"name": "Ashwagandha", "price": 5, "description": "Adaptógeno"},
        {"name": "Reishi", "price": 5, "description": "Inmunidad"},
        {"name": "Chaga", "price": 6, "description": "Antioxidante premium"},
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
