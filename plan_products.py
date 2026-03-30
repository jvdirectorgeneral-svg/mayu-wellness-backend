from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/plan-products", tags=["Plan Products"])


def get_product(db: Session, name: str):
    return db.query(models.Product).filter(models.Product.name == name).first()


def get_plan_by_level(db: Session, level: int):
    return db.query(models.Plan).filter(models.Plan.level == level).first()


@router.get("/")
def get_plan_products(db: Session = Depends(get_db)):
    relations = db.query(models.PlanProduct).all()
    return relations


@router.post("/seed")
def seed_plan_products(db: Session = Depends(get_db)):
    plan1 = get_plan_by_level(db, 1)
    plan2 = get_plan_by_level(db, 2)
    plan3 = get_plan_by_level(db, 3)

    if not plan1 or not plan2 or not plan3:
        return {"error": "Primero ejecuta /plans/seed"}

    # limpiar relaciones actuales
    db.query(models.PlanProduct).delete()
    db.commit()

    coloide_names = [
        "Plata Coloidal",
        "Cobre Coloidal",
        "Selenio Coloidal",
        "Oro Coloidal",
        "Zinc Coloidal",
        "Shunguita",
        "Silicio",
        "Magnesio",
    ]

    coloides = []
    for name in coloide_names:
        product = get_product(db, name)
        if product:
            coloides.append(product)

    cbd = get_product(db, "CBD 874 mg")
    chocomedical = get_product(db, "Chocomedical")
    melena = get_product(db, "Melena de León")
    te_cbd = get_product(db, "Té CBD")
    mag_bis = get_product(db, "Magnesio Bisglicinato")

    created = 0

    # =========================
    # NIVEL 1 - COBRE
    # Fijos: CBD + Chocomedical
    # Editable: 1 coloide
    # =========================
    fixed_level_1 = [cbd, chocomedical]
    for product in fixed_level_1:
        if product:
            db.add(models.PlanProduct(
                plan_id=plan1.id,
                product_id=product.id,
                is_required=True,
                max_quantity=1
            ))
            created += 1

    for product in coloides:
        db.add(models.PlanProduct(
            plan_id=plan1.id,
            product_id=product.id,
            is_required=False,
            max_quantity=1
        ))
        created += 1

    # =========================
    # NIVEL 2 - PLATA
    # Fijos: CBD + Chocomedical + Melena
    # Editable: 1 coloide
    # =========================
    fixed_level_2 = [cbd, chocomedical, melena]
    for product in fixed_level_2:
        if product:
            db.add(models.PlanProduct(
                plan_id=plan2.id,
                product_id=product.id,
                is_required=True,
                max_quantity=1
            ))
            created += 1

    for product in coloides:
        db.add(models.PlanProduct(
            plan_id=plan2.id,
            product_id=product.id,
            is_required=False,
            max_quantity=1
        ))
        created += 1

    # =========================
    # NIVEL 3 - ORO
    # Fijos: CBD + Té CBD + Melena + Magnesio Bisglicinato + Chocomedical
    # Editable: 1 coloide
    # =========================
    fixed_level_3 = [cbd, te_cbd, melena, mag_bis, chocomedical]
    for product in fixed_level_3:
        if product:
            db.add(models.PlanProduct(
                plan_id=plan3.id,
                product_id=product.id,
                is_required=True,
                max_quantity=1
            ))
            created += 1

    for product in coloides:
        db.add(models.PlanProduct(
            plan_id=plan3.id,
            product_id=product.id,
            is_required=False,
            max_quantity=1
        ))
        created += 1

    db.commit()

    return {
        "message": "Planes configurados correctamente",
        "created": created
    }
