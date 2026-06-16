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


def allowed_categories_by_level(level: int):
    if level == 1:
        return {"coloides", "cbd", "bienestar"}

    if level == 2:
        return {"coloides", "cbd", "bienestar", "hongos"}

    if level == 3:
        return {"coloides", "cbd", "bienestar", "hongos", "soporte_funcional"}

    return set()


def sync_active_products_to_plans(db: Session):
    plans = (
        db.query(models.Plan)
        .filter(models.Plan.active == True)
        .order_by(models.Plan.level.asc())
        .all()
    )

    if not plans:
        raise HTTPException(
            status_code=400,
            detail="No existen planes activos. Primero ejecuta /plans/seed",
        )

    products = (
        db.query(models.Product)
        .filter(models.Product.active == True)
        .order_by(models.Product.category.asc(), models.Product.name.asc())
        .all()
    )

    if not products:
        raise HTTPException(
            status_code=400,
            detail="No existen productos activos",
        )

    created = 0
    skipped = 0
    ignored = 0
    created_items = []

    for plan in plans:
        allowed_categories = allowed_categories_by_level(plan.level)

        for product in products:
            category = (product.category or "").strip().lower()

            if not category or category not in allowed_categories:
                ignored += 1
                continue

            existing = (
                db.query(models.PlanProduct)
                .filter(
                    models.PlanProduct.plan_id == plan.id,
                    models.PlanProduct.product_id == product.id,
                )
                .first()
            )

            if existing:
                skipped += 1
                continue

            relation = models.PlanProduct(
                plan_id=plan.id,
                product_id=product.id,
                is_required=False,
                max_quantity=1,
            )

            db.add(relation)
            db.flush()

            created += 1
            created_items.append(
                {
                    "plan_id": plan.id,
                    "plan_level": plan.level,
                    "plan_name": plan.name,
                    "product_id": product.id,
                    "product_name": product.name,
                    "product_category": product.category,
                }
            )

    db.commit()

    return {
        "message": "Sincronización completada sin borrar relaciones existentes",
        "created": created,
        "skipped_existing": skipped,
        "ignored_not_allowed": ignored,
        "created_items": created_items,
        "rule": {
            "Nivel 1 - Cobre": ["coloides", "cbd", "bienestar"],
            "Nivel 2 - Plata": ["coloides", "cbd", "bienestar", "hongos"],
            "Nivel 3 - Oro": [
                "coloides",
                "cbd",
                "bienestar",
                "hongos",
                "soporte_funcional",
            ],
        },
    }


@router.get("/")
def get_plan_products(db: Session = Depends(get_db)):
    relations = (
        db.query(models.PlanProduct)
        .join(models.Plan)
        .join(models.Product)
        .order_by(
            models.Plan.level.asc(),
            models.Product.category.asc(),
            models.Product.name.asc(),
        )
        .all()
    )

    return {
        "items": [plan_product_to_dict(r) for r in relations],
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


@router.post("/sync-active-products")
def sync_active_products(db: Session = Depends(get_db)):
    return sync_active_products_to_plans(db)


@router.post("/seed")
def seed_plan_products(db: Session = Depends(get_db)):
    db.query(models.PlanProduct).delete()
    db.commit()

    result = sync_active_products_to_plans(db)

    result["message"] = "Seed ejecutado: se borraron relaciones anteriores y se reasignaron productos activos por categoría"
    return result
