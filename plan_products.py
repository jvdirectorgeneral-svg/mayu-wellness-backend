from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/plan-products", tags=["Plan Products"])


def get_product(db, name):
    return db.query(models.Product).filter(models.Product.name == name).first()


@router.post("/seed")
def seed_plan_products(db: Session = Depends(get_db)):
    db.query(models.PlanProduct).delete()
    db.commit()

    # COLOIDES (TODOS)
    coloides = db.query(models.Product).filter(
        models.Product.name.in_([
            "Plata Coloidal",
            "Cobre Coloidal",
            "Selenio Coloidal",
            "Oro Coloidal",
            "Zinc Coloidal",
            "Shunguita",
            "Silicio",
            "Magnesio"
        ])
    ).all()

    # -------- NIVEL 1 – COBRE --------
    nivel1 = [
        get_product(db, "CBD 874 mg"),
        get_product(db, "Chocomedical"),
    ]

    for p in nivel1:
        db.add(models.PlanProduct(
            plan_level=1,
            product_id=p.id,
            is_editable=False
        ))

    for c in coloides:
        db.add(models.PlanProduct(
            plan_level=1,
            product_id=c.id,
            is_editable=True
        ))

    # -------- NIVEL 2 – PLATA --------
    nivel2 = [
        get_product(db, "CBD 874 mg"),
        get_product(db, "Chocomedical"),
        get_product(db, "Melena de León"),
    ]

    for p in nivel2:
        db.add(models.PlanProduct(
            plan_level=2,
            product_id=p.id,
            is_editable=False
        ))

    for c in coloides:
        db.add(models.PlanProduct(
            plan_level=2,
            product_id=c.id,
            is_editable=True
        ))

    # -------- NIVEL 3 – ORO --------
    nivel3 = [
        get_product(db, "CBD 874 mg"),
        get_product(db, "Chocomedical"),
        get_product(db, "Melena de León"),
        get_product(db, "Té CBD"),
        get_product(db, "Magnesio Bisglicinato"),
    ]

    for p in nivel3:
        db.add(models.PlanProduct(
            plan_level=3,
            product_id=p.id,
            is_editable=False
        ))

    for c in coloides:
        db.add(models.PlanProduct(
            plan_level=3,
            product_id=c.id,
            is_editable=True
        ))

    db.commit()

    return {"message": "Planes configurados correctamente"}
