from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
import models

router = APIRouter(prefix="/plan-products", tags=["Plan Products"])


class PlanProductCreate(BaseModel):
    plan_id: int
    product_id: int
    is_required: bool = False
    max_quantity: int = 1


class PlanProductUpdate(BaseModel):
    is_required: bool | None = None
    max_quantity: int | None = None


def plan_product_to_dict(relation: models.PlanProduct):
    return {
        "id": relation.id,
        "plan_id": relation.plan_id,
        "plan_name": relation.plan.name if relation.plan else None,
        "plan_level": relation.plan.level if relation.plan else None,
        "product_id": relation.product_id,
        "product_name": relation.product.name if relation.product else None,
        "product_category": relation.product.category if relation.product else None,
        "is_required": relation.is_required,
        "max_quantity": relation.max_quantity,
    }


@router.get("/")
def get_plan_products(db: Session = Depends(get_db)):
    relations = (
        db.query(models.PlanProduct)
        .join(models.Plan)
        .join(models.Product)
        .order_by(models.Plan.level.asc(), models.Product.category.asc(), models.Product.name.asc())
        .all()
    )

    return {
        "items": [plan_product_to_dict(r) for r in relations]
    }


@router.get("/plan/{plan_id}")
def get_products_by_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id).first()

    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    relations = (
        db.query(models.PlanProduct)
        .filter(models.PlanProduct.plan_id == plan_id)
        .join(models.Product)
        .order_by(models.Product.category.asc(), models.Product.name.asc())
        .all()
    )

    return {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "level": plan.level,
            "price": plan.price,
            "active": plan.active,
        },
        "items": [plan_product_to_dict(r) for r in relations],
    }


@router.post("/")
def create_plan_product(
    payload: PlanProductCreate,
    db: Session = Depends(get_db),
):
    plan = db.query(models.Plan).filter(models.Plan.id == payload.plan_id).first()
    product = db.query(models.Product).filter(models.Product.id == payload.product_id).first()

    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    existing = (
        db.query(models.PlanProduct)
        .filter(
            models.PlanProduct.plan_id == payload.plan_id,
            models.PlanProduct.product_id == payload.product_id,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Este producto ya está asignado a este plan",
        )

    relation = models.PlanProduct(
        plan_id=payload.plan_id,
        product_id=payload.product_id,
        is_required=payload.is_required,
        max_quantity=payload.max_quantity,
    )

    db.add(relation)
    db.commit()
    db.refresh(relation)

    return {
        "message": "Producto asignado al plan correctamente",
        "item": plan_product_to_dict(relation),
    }


@router.put("/{relation_id}")
def update_plan_product(
    relation_id: int,
    payload: PlanProductUpdate,
    db: Session = Depends(get_db),
):
    relation = (
        db.query(models.PlanProduct)
        .filter(models.PlanProduct.id == relation_id)
        .first()
    )

    if not relation:
        raise HTTPException(status_code=404, detail="Relación no encontrada")

    if payload.is_required is not None:
        relation.is_required = payload.is_required

    if payload.max_quantity is not None:
        relation.max_quantity = payload.max_quantity

    db.commit()
    db.refresh(relation)

    return {
        "message": "Relación actualizada correctamente",
        "item": plan_product_to_dict(relation),
    }


@router.delete("/{relation_id}")
def delete_plan_product(
    relation_id: int,
    db: Session = Depends(get_db),
):
    relation = (
        db.query(models.PlanProduct)
        .filter(models.PlanProduct.id == relation_id)
        .first()
    )

    if not relation:
        raise HTTPException(status_code=404, detail="Relación no encontrada")

    db.delete(relation)
    db.commit()

    return {
        "message": "Producto removido del plan correctamente",
        "relation_id": relation_id,
    }


@router.post("/seed")
def seed_plan_products(db: Session = Depends(get_db)):
    plans = db.query(models.Plan).filter(models.Plan.active == True).all()

    if not plans:
        raise HTTPException(
            status_code=400,
            detail="Primero ejecuta /plans/seed",
        )

    products = db.query(models.Product).filter(models.Product.active == True).all()

    if not products:
        raise HTTPException(
            status_code=400,
            detail="Primero ejecuta /products/seed-mayu-products",
        )

    db.query(models.PlanProduct).delete()
    db.commit()

    created = 0

    for plan in plans:
        for product in products:
            if not product.category:
                continue

            allowed = False

            if plan.level == 1:
                allowed = product.category in {
                    "coloides",
                    "cbd",
                    "bienestar",
                }

            elif plan.level == 2:
                allowed = product.category in {
                    "coloides",
                    "cbd",
                    "bienestar",
                    "hongos",
                }

            elif plan.level == 3:
                allowed = product.category in {
                    "coloides",
                    "cbd",
                    "bienestar",
                    "hongos",
                    "soporte_funcional",
                }

            if not allowed:
                continue

            relation = models.PlanProduct(
                plan_id=plan.id,
                product_id=product.id,
                is_required=False,
                max_quantity=1,
            )

            db.add(relation)
            created += 1

    db.commit()

    return {
        "message": "Productos asignados dinámicamente a los planes por categoría",
        "created": created,
        "rule": {
            "Nivel 1 - Cobre": ["coloides", "cbd", "bienestar"],
            "Nivel 2 - Plata": ["coloides", "cbd", "bienestar", "hongos"],
            "Nivel 3 - Oro": ["coloides", "cbd", "bienestar", "hongos", "soporte_funcional"],
        },
    }
