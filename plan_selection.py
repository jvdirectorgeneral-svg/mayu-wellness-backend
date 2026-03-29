from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/plan-selection", tags=["Plan Selection"])


# =========================================
# VER OPCIONES Y REGLAS DE UN PLAN
# =========================================
@router.get("/plans/{plan_id}/options")
def get_plan_options(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    relations = db.query(models.PlanProduct).filter(models.PlanProduct.plan_id == plan_id).all()

    products = []
    for rel in relations:
        product = db.query(models.Product).filter(models.Product.id == rel.product_id).first()
        if product:
            products.append({
                "product_id": product.id,
                "name": product.name,
                "description": product.description,
                "image_url": product.image_url,
                "is_required": rel.is_required,
                "max_quantity": rel.max_quantity
            })

    # 🔥 reglas guiadas por nivel
    rules = []
    if plan.level == 1:
        rules = [
            {"step": 1, "type": "required_fixed", "label": "CBD 874 mg", "quantity": 1},
            {"step": 2, "type": "choose_one", "label": "Elige 1 coloide", "options": ["Plata Coloidal", "Cobre Coloidal", "Selenio Coloidal"]},
            {"step": 3, "type": "required_fixed", "label": "Chocomedical", "quantity": 1},
        ]
    elif plan.level == 2:
        rules = [
            {"step": 1, "type": "required_fixed", "label": "CBD 874 mg", "quantity": 1},
            {"step": 2, "type": "required_fixed", "label": "Melena de León", "quantity": 1},
            {"step": 3, "type": "choose_multiple", "label": "Elige 2 coloides", "quantity": 2, "options": ["Plata Coloidal", "Cobre Coloidal", "Selenio Coloidal", "Zinc Coloidal"]},
            {"step": 4, "type": "required_fixed", "label": "Chocomedical", "quantity": 1},
        ]
    elif plan.level == 3:
        rules = [
            {"step": 1, "type": "required_fixed", "label": "CBD 874 mg", "quantity": 1},
            {"step": 2, "type": "required_fixed", "label": "Melena de León", "quantity": 1},
            {"step": 3, "type": "choose_multiple", "label": "Elige 3 coloides", "quantity": 3, "options": ["Plata Coloidal", "Cobre Coloidal", "Selenio Coloidal", "Oro Coloidal", "Zinc Coloidal", "Magnesio Coloidal", "Silicio Coloidal"]},
            {"step": 4, "type": "required_fixed", "label": "Chocomedical", "quantity": 1},
            {"step": 5, "type": "choose_one", "label": "Elige 1 adaptógeno", "options": ["Ashwagandha", "Reishi", "Chaga"]},
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
        "products": products
    }


# =========================================
# INICIAR SELECCIÓN DE PLAN
# =========================================
@router.post("/start")
def start_plan_selection(user_id: int, plan_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    selection = models.UserPlanSelection(
        user_id=user.id,
        plan_id=plan.id,
        status="draft"
    )
    db.add(selection)
    db.commit()
    db.refresh(selection)

    return {
        "message": "Selección iniciada",
        "selection_id": selection.id,
        "user_id": user.id,
        "plan_id": plan.id
    }


# =========================================
# AGREGAR PRODUCTO A SELECCIÓN
# =========================================
@router.post("/{selection_id}/add-item")
def add_item_to_selection(
    selection_id: int,
    product_id: int,
    quantity: int = 1,
    db: Session = Depends(get_db)
):
    selection = db.query(models.UserPlanSelection).filter(
        models.UserPlanSelection.id == selection_id
    ).first()

    if not selection:
        raise HTTPException(status_code=404, detail="Selección no encontrada")

    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    item = models.UserPlanSelectionItem(
        selection_id=selection.id,
        product_id=product.id,
        quantity=quantity
    )

    db.add(item)
    db.commit()
    db.refresh(item)

    return {
        "message": "Producto agregado a la selección",
        "item_id": item.id
    }
