from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/plan-selection", tags=["Plan Selection"])


def get_plan_by_id(db: Session, plan_id: int):
    return db.query(models.Plan).filter(models.Plan.id == plan_id).first()


def get_plan_by_level(db: Session, level: int):
    return db.query(models.Plan).filter(models.Plan.level == level).first()


@router.get("/plans/{plan_id}/options")
def get_plan_options(plan_id: int, db: Session = Depends(get_db)):
    plan = get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    relations = db.query(models.PlanProduct).filter(
        models.PlanProduct.plan_id == plan.id
    ).all()

    fixed_products = []
    editable_products = []

    for rel in relations:
        product = db.query(models.Product).filter(
            models.Product.id == rel.product_id
        ).first()

        if not product:
            continue

        item = {
            "product_id": product.id,
            "name": product.name,
            "description": product.description,
            "image_url": product.image_url,
            "is_required": rel.is_required,
            "max_quantity": rel.max_quantity,
        }

        if rel.is_required:
            fixed_products.append(item)
        else:
            editable_products.append(item)

    rules = [
        {
            "step": 1,
            "type": "fixed_products",
            "label": "Productos fijos del plan",
            "quantity": len(fixed_products),
        },
        {
            "step": 2,
            "type": "choose_one_coloid",
            "label": "Elige 1 coloide para tu próxima entrega",
            "quantity": 1,
        },
    ]

    return {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "level": plan.level,
            "price": plan.price,
            "description": plan.description,
        },
        "rules": rules,
        "fixed_products": fixed_products,
        "editable_products": editable_products,
    }


@router.post("/start")
def start_plan_selection(user_id: int, plan_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    return {
        "message": "Selección iniciada",
        "user_id": user.id,
        "plan_id": plan.id
    }


@router.get("/user/{user_id}/current-plan")
def get_user_current_plan(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not user.membership_level:
        raise HTTPException(status_code=400, detail="El usuario no tiene plan asignado")

    plan = get_plan_by_level(db, user.membership_level)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    return get_plan_options(plan.id, db)
