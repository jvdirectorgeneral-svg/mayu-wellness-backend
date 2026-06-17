from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/plan-selection", tags=["Plan Selection"])


def get_plan_by_id(db: Session, plan_id: int):
    return db.query(models.Plan).filter(models.Plan.id == plan_id).first()


def get_plan_by_level(db: Session, level: int):
    return db.query(models.Plan).filter(models.Plan.level == level).first()


def product_to_dict(product: models.Product, relation: models.PlanProduct | None = None):
    return {
        "product_id": product.id,
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "image_url": product.image_url,
        "category": product.category,
        "product_category": product.category,
        "is_required": relation.is_required if relation else False,
        "max_quantity": relation.max_quantity if relation else 1,
    }


def section_name_by_category(category: str, level: int):
    if category == "coloides":
        return "Coloide a libre elección"
    if category == "cbd":
        return "Línea CBD"
    if category == "bienestar":
        return "Línea bienestar"
    if category == "hongos":
        return "Hongos medicinales"
    if category == "soporte_funcional":
        return "Soporte funcional"
    return "Otros productos"


def section_order_by_level(level: int):
    if level == 1:
        return ["coloides", "cbd", "bienestar"]

    if level == 2:
        return ["coloides", "hongos", "cbd", "bienestar"]

    if level == 3:
        return ["coloides", "cbd", "bienestar", "soporte_funcional"]

    return []


def build_sections_from_relations(plan: models.Plan, relations: list[models.PlanProduct]):
    grouped = {}

    for rel in relations:
        product = rel.product

        if not product:
            continue

        if not product.active:
            continue

        category = (product.category or "").strip().lower()

        if not category:
            continue

        if category not in section_order_by_level(plan.level):
            continue

        grouped.setdefault(category, [])
        grouped[category].append(product_to_dict(product, rel))

    sections = []

    for category in section_order_by_level(plan.level):
        products = grouped.get(category, [])

        products = sorted(
            products,
            key=lambda x: (x.get("name") or "").lower(),
        )

        if not products:
            continue

        sections.append(
            {
                "section_key": category,
                "key": category,
                "section_name": section_name_by_category(category, plan.level),
                "title": section_name_by_category(category, plan.level),
                "category": category,
                "max_items": 1,
                "products": products,
                "options": [p["name"] for p in products],
            }
        )

    return sections


@router.get("/plans/{plan_id}/options")
def get_plan_options(plan_id: int, db: Session = Depends(get_db)):
    plan = get_plan_by_id(db, plan_id)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    relations = (
        db.query(models.PlanProduct)
        .filter(models.PlanProduct.plan_id == plan.id)
        .join(models.Product)
        .order_by(models.Product.category.asc(), models.Product.name.asc())
        .all()
    )

    fixed_products = []
    editable_products = []

    for rel in relations:
        product = rel.product

        if not product:
            continue

        if not product.active:
            continue

        item = product_to_dict(product, rel)

        if rel.is_required:
            fixed_products.append(item)
        else:
            editable_products.append(item)

    sections = build_sections_from_relations(plan, relations)

    rules = [
        {
            "step": 1,
            "type": "fixed_products",
            "label": "Productos fijos del plan",
            "quantity": len(fixed_products),
        },
        {
            "step": 2,
            "type": "editable_sections",
            "label": "Elige tus productos iniciales por categoría",
            "quantity": len(sections),
        },
    ]

    return {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "level": plan.level,
            "price": plan.price,
            "description": plan.description,
            "active": plan.active,
        },
        "rules": rules,
        "fixed_products": fixed_products,
        "editable_products": editable_products,
        "sections": sections,
        "editable_sections": sections,
    }


@router.get("/plans/level/{level}/sections")
def get_plan_sections_by_level(level: int, db: Session = Depends(get_db)):
    plan = get_plan_by_level(db, level)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    data = get_plan_options(plan.id, db)

    return {
        "plan": data["plan"],
        "plan_level": level,
        "sections": data["sections"],
        "editable_sections": data["editable_sections"],
        "fixed_products": data["fixed_products"],
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
        "plan_id": plan.id,
    }


@router.get("/user/{user_id}/current-plan")
def get_user_current_plan(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene plan asignado",
        )

    plan = get_plan_by_level(db, user.membership_level)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    return get_plan_options(plan.id, db)
