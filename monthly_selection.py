from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db
import models

router = APIRouter(prefix="/monthly-selection", tags=["Monthly Selection"])


# =========================
# LÓGICA DE VENTANA 21 al 5
# =========================
def is_edit_window_open():
    now = datetime.now()
    day = now.day
    return day >= 21 or day <= 5


def current_cycle_status():
    now = datetime.now()
    day = now.day

    if day >= 21 or day <= 5:
        return "editable"
    elif 6 <= day <= 8:
        return "preparing"
    elif day in [9, 10]:
        return "shipping"
    return "closed"


# =========================
# CREAR OBTENER SELECCIÓN DEL MES
# =========================
@router.post("/init")
def init_monthly_selection(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not user.membership_level:
        raise HTTPException(status_code=400, detail="El usuario no tiene plan asignado")

    plan = db.query(models.Plan).filter(models.Plan.level == user.membership_level).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

    now = datetime.now()
    month = now.month
    year = now.year

    existing = db.query(models.MonthlySelection).filter(
        models.MonthlySelection.user_id == user.id,
        models.MonthlySelection.month == month,
        models.MonthlySelection.year == year
    ).first()

    if existing:
        return {
            "message": "Selección mensual existente",
            "selection_id": existing.id,
            "editable": existing.editable,
            "status": existing.status
        }

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=month,
        year=year,
        status="draft",
        editable=is_edit_window_open()
    )

    db.add(selection)
    db.commit()
    db.refresh(selection)

    return {
        "message": "Selección mensual creada",
        "selection_id": selection.id,
        "editable": selection.editable,
        "status": selection.status
    }


# =========================
# VER SELECCIÓN MENSUAL DE USUARIO
# =========================
@router.get("/user/{user_id}")
def get_user_monthly_selection(user_id: int, db: Session = Depends(get_db)):
    now = datetime.now()
    month = now.month
    year = now.year

    selection = db.query(models.MonthlySelection).filter(
        models.MonthlySelection.user_id == user_id,
        models.MonthlySelection.month == month,
        models.MonthlySelection.year == year
    ).first()

    if not selection:
        raise HTTPException(status_code=404, detail="No existe selección mensual para este usuario")

    items = db.query(models.MonthlySelectionItem).filter(
        models.MonthlySelectionItem.monthly_selection_id == selection.id
    ).all()

    products = []
    for item in items:
        product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
        if product:
            products.append({
                "product_id": product.id,
                "name": product.name,
                "quantity": item.quantity
            })

    return {
        "selection_id": selection.id,
        "month": selection.month,
        "year": selection.year,
        "editable": selection.editable,
        "status": selection.status,
        "cycle_status": current_cycle_status(),
        "products": products
    }


# =========================
# GUARDAR ITEMS DE LA SELECCIÓN
# =========================
@router.post("/{selection_id}/save-items")
def save_monthly_selection_items(
    selection_id: int,
    product_ids: list[int],
    db: Session = Depends(get_db)
):
    selection = db.query(models.MonthlySelection).filter(
        models.MonthlySelection.id == selection_id
    ).first()

    if not selection:
        raise HTTPException(status_code=404, detail="Selección no encontrada")

    if not selection.editable or not is_edit_window_open():
        raise HTTPException(
            status_code=400,
            detail="La ventana de edición está cerrada. Solo se permite del 21 al 5."
        )

    # borrar items anteriores
    db.query(models.MonthlySelectionItem).filter(
        models.MonthlySelectionItem.monthly_selection_id == selection.id
    ).delete()

    # crear nuevos items
    for product_id in product_ids:
        product = db.query(models.Product).filter(models.Product.id == product_id).first()
        if not product:
            continue

        item = models.MonthlySelectionItem(
            monthly_selection_id=selection.id,
            product_id=product.id,
            quantity=1
        )
        db.add(item)

    db.commit()

    return {
        "message": "Selección mensual actualizada correctamente",
        "selection_id": selection.id
    }


# =========================
# INFO DE CICLO
# =========================
@router.get("/cycle-info")
def get_cycle_info():
    return {
        "editable": is_edit_window_open(),
        "cycle_status": current_cycle_status(),
        "edit_window": "21 al 5",
        "shipping_window": "9 o 10 de cada mes"
    }
