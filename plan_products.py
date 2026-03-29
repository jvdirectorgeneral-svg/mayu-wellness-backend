from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/plan-products", tags=["Plan Products"])


@router.get("/")
def get_plan_products(db: Session = Depends(get_db)):
    relations = db.query(models.PlanProduct).all()
    return relations


@router.post("/seed")
def seed_plan_products(db: Session = Depends(get_db)):
    # =========================
    # BUSCAR PLANES
    # =========================
    plan1 = db.query(models.Plan).filter(models.Plan.level == 1).first()
    plan2 = db.query(models.Plan).filter(models.Plan.level == 2).first()
    plan3 = db.query(models.Plan).filter(models.Plan.level == 3).first()

    if not plan1 or not plan2 or not plan3:
        return {"error": "Primero debes crear los planes con /plans/seed"}

    # =========================
    # BUSCAR PRODUCTOS
    # =========================
    products = {p.name: p for p in db.query(models.Product).all()}

    required_products = [
        "CBD 874 mg",
        "Melena de León",
        "Chocomedical",
        "Plata Coloidal",
        "Cobre Coloidal",
        "Selenio Coloidal",
        "Oro Coloidal",
        "Zinc Coloidal",
        "Magnesio Coloidal",
        "Silicio Coloidal",
        "Ashwagandha",
        "Reishi",
        "Chaga",
    ]

    for name in required_products:
        if name not in products:
            return {"error": f"Falta el producto: {name}. Ejecuta /products/seed primero"}

    relations_to_create = [
        # =========================
        # NIVEL 1 - COBRE
        # =========================
        {"plan_id": plan1.id, "product_id": products["CBD 874 mg"].id, "is_required": True, "max_quantity": 1},
        {"plan_id": plan1.id, "product_id": products["Plata Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan1.id, "product_id": products["Cobre Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan1.id, "product_id": products["Selenio Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan1.id, "product_id": products["Chocomedical"].id, "is_required": True, "max_quantity": 1},

        # =========================
        # NIVEL 2 - PLATA
        # =========================
        {"plan_id": plan2.id, "product_id": products["CBD 874 mg"].id, "is_required": True, "max_quantity": 1},
        {"plan_id": plan2.id, "product_id": products["Melena de León"].id, "is_required": True, "max_quantity": 1},
        {"plan_id": plan2.id, "product_id": products["Plata Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan2.id, "product_id": products["Cobre Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan2.id, "product_id": products["Selenio Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan2.id, "product_id": products["Zinc Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan2.id, "product_id": products["Chocomedical"].id, "is_required": True, "max_quantity": 1},
        {"plan_id": plan2.id, "product_id": products["Ashwagandha"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan2.id, "product_id": products["Reishi"].id, "is_required": False, "max_quantity": 1},

        # =========================
        # NIVEL 3 - ORO
        # =========================
        {"plan_id": plan3.id, "product_id": products["CBD 874 mg"].id, "is_required": True, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Melena de León"].id, "is_required": True, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Plata Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Cobre Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Selenio Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Oro Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Zinc Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Magnesio Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Silicio Coloidal"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Chocomedical"].id, "is_required": True, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Ashwagandha"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Reishi"].id, "is_required": False, "max_quantity": 1},
        {"plan_id": plan3.id, "product_id": products["Chaga"].id, "is_required": False, "max_quantity": 1},
    ]

    created = 0

    for rel in relations_to_create:
        existing = db.query(models.PlanProduct).filter(
            models.PlanProduct.plan_id == rel["plan_id"],
            models.PlanProduct.product_id == rel["product_id"]
        ).first()

        if not existing:
            new_rel = models.PlanProduct(
                plan_id=rel["plan_id"],
                product_id=rel["product_id"],
                is_required=rel["is_required"],
                max_quantity=rel["max_quantity"]
            )
            db.add(new_rel)
            created += 1

    db.commit()

    return {"message": "Relaciones plan-producto creadas", "created": created}
