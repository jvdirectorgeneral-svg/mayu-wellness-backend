from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from database import get_db
import models

router = APIRouter(prefix="/monthly-selection", tags=["Monthly Selection"])


class MonthlySelectionInitRequest(BaseModel):
    user_id: int


class MonthlySelectionItemInput(BaseModel):
    product_id: int | None = None
    product_name: str | None = None


class MonthlySelectionSaveRequest(BaseModel):
    items: list[MonthlySelectionItemInput]
    force_save: bool = False


def is_edit_window_open():
    return True


def current_cycle_status():
    today = datetime.now()
    weekday = today.weekday()

    if weekday in [0, 1, 2, 3]:
        return "admin_review"

    if weekday == 4:
        return "weekly_shipping"

    return "editable"


def get_cycle_status_label():
    status = current_cycle_status()

    if status == "editable":
        return "Productos editables disponibles"

    if status == "admin_review":
        return "Revisión administrativa de pagos y suscripciones"

    if status == "weekly_shipping":
        return "Despacho semanal de logística"

    return "Productos editables disponibles"


def get_plan_name_by_level(level: int):
    if level == 1:
        return "Nivel 1 - Cobre"

    if level == 2:
        return "Nivel 2 - Plata"

    if level == 3:
        return "Nivel 3 - Oro"

    return "Sin plan"


# =========================================================
# NUEVO SISTEMA DINÁMICO POR CATEGORÍAS
# =========================================================

def get_products_by_category(db: Session, category: str):
    products = (
        db.query(models.Product)
        .filter(
            models.Product.category == category,
            models.Product.active == True,
        )
        .order_by(models.Product.name.asc())
        .all()
    )

    return [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
        }
        for p in products
    ]


def get_available_editable_sections_by_level(
    db: Session,
    level: int,
):
    coloides = get_products_by_category(db, "coloide")
    cbd = get_products_by_category(db, "cbd")
    bienestar = get_products_by_category(db, "bienestar")
    hongos = get_products_by_category(db, "hongos")
    soporte = get_products_by_category(db, "soporte_funcional")

    if level == 1:
        return [
            {
                "section_key": "coloide",
                "section_name": "Coloide a libre elección",
                "max_items": 1,
                "products": coloides,
            },
            {
                "section_key": "cbd",
                "section_name": "Producto CBD a elección",
                "max_items": 1,
                "products": cbd,
            },
            {
                "section_key": "bienestar",
                "section_name": "Producto bienestar a elección",
                "max_items": 1,
                "products": bienestar,
            },
        ]

    if level == 2:
        return [
            {
                "section_key": "coloide",
                "section_name": "Coloide a libre elección",
                "max_items": 1,
                "products": coloides,
            },
            {
                "section_key": "hongos",
                "section_name": "Hongos medicinales",
                "max_items": 1,
                "products": hongos,
            },
            {
                "section_key": "cbd",
                "section_name": "Producto CBD a elección",
                "max_items": 1,
                "products": cbd,
            },
            {
                "section_key": "bienestar",
                "section_name": "Producto bienestar a elección",
                "max_items": 1,
                "products": bienestar,
            },
        ]

    if level == 3:
        return [
            {
                "section_key": "coloide",
                "section_name": "Coloide a libre elección",
                "max_items": 1,
                "products": coloides,
            },
            {
                "section_key": "cbd",
                "section_name": "Producto CBD a elección",
                "max_items": 1,
                "products": cbd,
            },
            {
                "section_key": "bienestar",
                "section_name": "Producto bienestar CBD",
                "max_items": 1,
                "products": bienestar,
            },
            {
                "section_key": "soporte_funcional",
                "section_name": "Soporte funcional",
                "max_items": 1,
                "products": soporte,
            },
            {
                "section_key": "extra_bienestar",
                "section_name": "Producto extra bienestar",
                "max_items": 1,
                "products": bienestar,
            },
        ]

    return []


def get_available_editable_products_by_level(
    db: Session,
    level: int,
):
    sections = get_available_editable_sections_by_level(db, level)

    products = []

    for section in sections:
        for product in section["products"]:
            if product["name"] not in products:
                products.append(product["name"])

    return products


# =========================================================
# UTILIDADES
# =========================================================

def format_month_label(month: int, year: int):
    month_names = {
        1: "Enero",
        2: "Febrero",
        3: "Marzo",
        4: "Abril",
        5: "Mayo",
        6: "Junio",
        7: "Julio",
        8: "Agosto",
        9: "Septiembre",
        10: "Octubre",
        11: "Noviembre",
        12: "Diciembre",
    }

    return f"{month_names.get(month, 'Mes')} {year}"


def get_product_by_input(
    db: Session,
    item: MonthlySelectionItemInput,
):
    if item.product_id is not None:
        return db.query(models.Product).filter(
            models.Product.id == item.product_id
        ).first()

    if (
        item.product_name is not None
        and item.product_name.strip() != ""
    ):
        return db.query(models.Product).filter(
            models.Product.name == item.product_name.strip()
        ).first()

    return None


# =========================================================
# INIT
# =========================================================

@router.post("/init")
def init_monthly_selection(
    payload: MonthlySelectionInitRequest,
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(
        models.User.id == payload.user_id
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene plan asignado",
        )

    plan = db.query(models.Plan).filter(
        models.Plan.level == user.membership_level
    ).first()

    if not plan:
        raise HTTPException(
            status_code=404,
            detail="Plan no encontrado",
        )

    now = datetime.now()

    month = now.month
    year = now.year

    editable_now = is_edit_window_open()

    existing = db.query(models.MonthlySelection).filter(
        models.MonthlySelection.user_id == user.id,
        models.MonthlySelection.month == month,
        models.MonthlySelection.year == year,
    ).first()

    if existing:
        existing.editable = editable_now

        db.commit()
        db.refresh(existing)

        return {
            "message": "Selección mensual existente",
            "selection_id": existing.id,
            "editable": editable_now,
            "status": existing.status,
            "cycle_status": current_cycle_status(),
            "cycle_status_label": get_cycle_status_label(),
            "plan_level": user.membership_level,
            "plan_name": get_plan_name_by_level(user.membership_level),
            "editable_sections": get_available_editable_sections_by_level(
                db,
                user.membership_level,
            ),
            "available_editable_products":
                get_available_editable_products_by_level(
                    db,
                    user.membership_level,
                ),
        }

    selection = models.MonthlySelection(
        user_id=user.id,
        plan_id=plan.id,
        month=month,
        year=year,
        status="draft",
        editable=editable_now,
    )

    db.add(selection)

    db.commit()
    db.refresh(selection)

    return {
        "message": "Selección mensual creada",
        "selection_id": selection.id,
        "editable": editable_now,
        "status": selection.status,
        "cycle_status": current_cycle_status(),
        "cycle_status_label": get_cycle_status_label(),
        "plan_level": user.membership_level,
        "plan_name": get_plan_name_by_level(user.membership_level),
        "editable_sections": get_available_editable_sections_by_level(
            db,
            user.membership_level,
        ),
        "available_editable_products":
            get_available_editable_products_by_level(
                db,
                user.membership_level,
            ),
    }


# =========================================================
# USER SELECTION
# =========================================================

@router.get("/user/{user_id}")
def get_user_monthly_selection(
    user_id: int,
    db: Session = Depends(get_db),
):
    now = datetime.now()

    month = now.month
    year = now.year

    editable_now = is_edit_window_open()

    user = db.query(models.User).filter(
        models.User.id == user_id
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    selection = db.query(models.MonthlySelection).filter(
        models.MonthlySelection.user_id == user_id,
        models.MonthlySelection.month == month,
        models.MonthlySelection.year == year,
    ).first()

    if not selection:
        raise HTTPException(
            status_code=404,
            detail="No existe selección mensual",
        )

    items = db.query(models.MonthlySelectionItem).filter(
        models.MonthlySelectionItem.monthly_selection_id == selection.id
    ).all()

    products = []

    for item in items:
        product = db.query(models.Product).filter(
            models.Product.id == item.product_id
        ).first()

        if product:
            products.append({
                "product_id": product.id,
                "name": product.name,
                "category": product.category,
                "quantity": item.quantity,
            })

    return {
        "selection_id": selection.id,
        "editable": editable_now,
        "status": selection.status,
        "plan_level": user.membership_level,
        "plan_name": get_plan_name_by_level(
            user.membership_level or 0
        ),
        "products": products,
        "editable_sections": get_available_editable_sections_by_level(
            db,
            user.membership_level or 0,
        ),
    }


# =========================================================
# SAVE ITEMS
# =========================================================

@router.post("/{selection_id}/save-items")
def save_monthly_selection_items(
    selection_id: int,
    payload: MonthlySelectionSaveRequest,
    db: Session = Depends(get_db),
):
    selection = db.query(models.MonthlySelection).filter(
        models.MonthlySelection.id == selection_id
    ).first()

    if not selection:
        raise HTTPException(
            status_code=404,
            detail="Selección no encontrada",
        )

    user = db.query(models.User).filter(
        models.User.id == selection.user_id
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    allowed_products = get_available_editable_products_by_level(
        db,
        user.membership_level or 0,
    )

    db.query(models.MonthlySelectionItem).filter(
        models.MonthlySelectionItem.monthly_selection_id == selection.id
    ).delete()

    saved_products = []

    for item_input in payload.items:
        product = get_product_by_input(db, item_input)

        if not product:
            continue

        if product.name not in allowed_products:
            continue

        if product.name in saved_products:
            continue

        item = models.MonthlySelectionItem(
            monthly_selection_id=selection.id,
            product_id=product.id,
            quantity=1,
        )

        db.add(item)

        saved_products.append(product.name)

    if not saved_products:
        raise HTTPException(
            status_code=400,
            detail="No se encontró ningún producto válido",
        )

    selection.status = "confirmed"

    db.commit()
    db.refresh(selection)

    return {
        "message": "Selección actualizada",
        "saved_products": saved_products,
    }


# =========================================================
# HISTORY
# =========================================================

@router.get("/user/{user_id}/history")
def get_user_monthly_selection_history(
    user_id: int,
    db: Session = Depends(get_db),
):
    selections = db.query(models.MonthlySelection).filter(
        models.MonthlySelection.user_id == user_id
    ).order_by(
        models.MonthlySelection.year.desc(),
        models.MonthlySelection.month.desc(),
    ).all()

    history = []

    for selection in selections:
        items = db.query(models.MonthlySelectionItem).filter(
            models.MonthlySelectionItem.monthly_selection_id == selection.id
        ).all()

        products = []

        for item in items:
            product = db.query(models.Product).filter(
                models.Product.id == item.product_id
            ).first()

            if product:
                products.append(product.name)

        history.append({
            "monthLabel": format_month_label(
                selection.month,
                selection.year,
            ),
            "editableProducts": products,
            "status": selection.status,
        })

    return {
        "history": history,
    }


# =========================================================
# CYCLE INFO
# =========================================================

@router.get("/cycle-info")
def get_cycle_info():
    return {
        "editable": is_edit_window_open(),
        "cycle_status": current_cycle_status(),
        "cycle_status_label": get_cycle_status_label(),
    }
