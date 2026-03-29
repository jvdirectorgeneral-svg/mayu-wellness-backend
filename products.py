from fastapi import APIRouter
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Product

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("/")
def get_products():
    db: Session = SessionLocal()
    products = db.query(Product).filter(Product.active == True).all()

    return [
        {
            "id": p.id,
            "name": p.name,
            "price": p.price,
            "description": p.description,
        }
        for p in products
    ]


@router.post("/seed")
def seed_products():
    db: Session = SessionLocal()

    if db.query(Product).count() > 0:
        return {"message": "Los productos ya existen"}

    products = [
        Product(name="CBD 874 mg", price=6, description="CBD base del club"),
        Product(name="Coloide Plata", price=2, description="Plata coloidal"),
        Product(name="Coloide Cobre", price=2, description="Cobre coloidal"),
        Product(name="Coloide Selenio", price=2, description="Selenio coloidal"),
        Product(name="Chocomedical", price=2, description="Chocolate funcional"),
        Product(name="Melena de León", price=6, description="Apoyo cognitivo"),
    ]

    db.add_all(products)
    db.commit()

    return {"message": "Productos creados correctamente"}
