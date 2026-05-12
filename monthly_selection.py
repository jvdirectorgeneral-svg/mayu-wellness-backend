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


def get_available_editable_sections_by_level(level: int):
    coloides = [
        "Plata Coloidal",
        "Cobre Coloidal",
        "Selenio Coloidal",
        "Oro Coloidal",
        "Zinc Coloidal",
        "Shunguita",
        "Silicio",
        "Magnesio",
    ]

    cbd_line = [
        "CBD 874 mg",
        "Fórmula del Sueño",
        "CBD 4%",
    ]

    wellness_line = [
        "Chocomedical",
        "Choco + Lion’s Mane",
        "Té de Cannabis",
        "Aceite terapéutico de Limón",
        "Aceite terapéutico de Naranja",
    ]

    mushrooms = [
        "Melena de León",
        "Reishi",
        "Turkey Tail",
        "Chaga",
    ]

    functional_support = [
        "Magnesio Bisglicinato",
        "MSM",
        "Koral Jade",
        "Ashwagandha",
    ]

    if level == 1:
        return [
            {
                "section_key": "coloide",
                "section_name": "Coloide a libre elección",
                "max_items": 1,
                "products": coloides,
            },
            {
                "section_key": "cbd_line",
                "section_name": "Línea CBD",
                "max_items": 1,
                "products": cbd_line,
            },
            {
                "section_key": "wellness_line",
                "section_name": "Línea bienestar",
                "max_items": 1,
                "products": wellness_line,
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
                "section_key": "mushrooms",
                "section_name": "Hongos medicinales",
                "max_items": 1,
                "products": mushrooms,
            },
            {
                "section_key": "cbd_line",
                "section_name": "Línea CBD",
                "max_items": 1,
                "products": cbd_line,
            },
            {
                "section_key": "wellness_line",
                "section_name": "Línea bienestar",
                "max_items": 1,
                "products": wellness_line,
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
                "section_key": "cbd_line",
                "section_name": "Línea CBD",
                "max_items": 1,
                "products": cbd_line,
            },
            {
                "section_key": "wellness_cbd_line",
                "section_name": "Línea bienestar CBD",
                "max_items": 1,
                "products": wellness_line,
            },
            {
                "section_key": "functional_support",
                "section_name": "Soporte funcional",
                "max_items": 1,
                "products": functional_support,
            },
            {
                "section_key": "extra_wellness",
                "section_name": "Producto extra de bienestar",
                "max_items": 1,
                "products": wellness_line,
            },
        ]

    return []


def get_available_editable_products_by_level(level: int):
    sections = get_available_editable_sections_by_level(level)
    products = []

    for section in sections:
        for product in section["products"]:
            if product not in products:
                products.append(product)

    return products


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


def get_product_by_input(db: Session, item: MonthlySelectionItemInput):
    if item.product_id is not None:
        return db.query(models.Product).filter(
            models.Product.id == item.product_id
        ).first()

    if item.product_name is not None and item.product_name.strip() != "":
        return db.query(models.Product).filter(
            models.Product.name == item.product_name.strip()
        ).first()

    return None


@router.post("/init")
def init_monthly_selection(
    payload: MonthlySelectionInitRequest,
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not user.membership_level:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene plan asignado",
        )

    plan = db.query(models.Plan).filter(
        models.Plan.level == user.membership_level
    ).first()

    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")

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
                user.membership_level
            ),
            "available_editable_products": get_available_editable_products_by_level(
                user.membership_level
            ),
            "edit_window": "Disponible durante todo el mes",
            "admin_review_window": "lunes a jueves",
            "shipping_window": "viernes",
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
            user.membership_level
        ),
        "available_editable_products": get_available_editable_products_by_level(
            user.membership_level
        ),
        "edit_window": "Disponible durante todo el mes",
        "admin_review_window": "lunes a jueves",
        "shipping_window": "viernes",
    }


@router.get("/user/{user_id}")
def get_user_monthly_selection(user_id: int, db: Session = Depends(get_db)):
    now = datetime.now()
    month = now.month
    year = now.year
    editable_now = is_edit_window_open()

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    selection = db.query(models.MonthlySelection).filter(
        models.MonthlySelection.user_id == user_id,
        models.MonthlySelection.month == month,
        models.MonthlySelection.year == year,
    ).first()

    if not selection:
        raise HTTPException(
            status_code=404,
            detail="No existe selección mensual para este usuario",
        )

    selection.editable = editable_now
    db.commit()
    db.refresh(selection)

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
                "quantity": item.quantity,
            })

    editable_product = products[0]["name"] if products else None

    return {
        "selection_id": selection.id,
        "month": selection.month,
        "year": selection.year,
        "editable": editable_now,
        "status": selection.status,
        "cycle_status": current_cycle_status(),
        "cycle_status_label": get_cycle_status_label(),
        "plan_level": user.membership_level,
        "plan_name": get_plan_name_by_level(user.membership_level or 0),
        "products": products,
        "editable_product": editable_product,
        "editable_products": products,
        "editable_sections": get_available_editable_sections_by_level(
            user.membership_level or 0
        ),
        "available_editable_products": get_available_editable_products_by_level(
            user.membership_level or 0
        ),
        "fixed_products": [],
        "edit_window": "Disponible durante todo el mes",
        "admin_review_window": "lunes a jueves",
        "shipping_window": "viernes",
    }


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
        raise HTTPException(status_code=404, detail="Selección no encontrada")

    user = db.query(models.User).filter(
        models.User.id == selection.user_id
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    editable_now = is_edit_window_open()
    selection.editable = editable_now

    if not editable_now and not payload.force_save:
        raise HTTPException(
            status_code=400,
            detail="La ventana de edición no está disponible",
        )

    allowed_products = get_available_editable_products_by_level(
        user.membership_level or 0
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
            detail="No se encontró ningún producto válido para guardar",
        )

    selection.status = "confirmed"
    db.commit()
    db.refresh(selection)

    return {
        "message": "Selección mensual actualizada correctamente",
        "selection_id": selection.id,
        "saved_products": saved_products,
        "editable": editable_now,
        "cycle_status": current_cycle_status(),
        "cycle_status_label": get_cycle_status_label(),
        "edit_window": "Disponible durante todo el mes",
        "shipping_window": "viernes",
    }


@router.get("/user/{user_id}/history")
def get_user_monthly_selection_history(
    user_id: int,
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    selections = db.query(models.MonthlySelection).filter(
        models.MonthlySelection.user_id == user_id
    ).order_by(
        models.MonthlySelection.year.desc(),
        models.MonthlySelection.month.desc(),
    ).all()

    if not selections:
        return {
            "history": [],
            "upcoming": None,
        }

    now = datetime.now()
    current_month = now.month
    current_year = now.year

    history = []
    upcoming = None

    for selection in selections:
        items = db.query(models.MonthlySelectionItem).filter(
            models.MonthlySelectionItem.monthly_selection_id == selection.id
        ).all()

        selected_products = []

        for item in items:
            product = db.query(models.Product).filter(
                models.Product.id == item.product_id
            ).first()

            if product:
                selected_products.append(product.name)

        selection_data = {
            "monthLabel": format_month_label(selection.month, selection.year),
            "planName": get_plan_name_by_level(user.membership_level or 0),
            "fixedProducts": [],
            "editableProduct": selected_products[0] if selected_products else "No seleccionado",
            "editableProducts": selected_products,
            "status": "Pendiente para próximo despacho semanal" if (
                selection.year > current_year
                or (
                    selection.year == current_year
                    and selection.month >= current_month
                )
            ) else "Procesado",
            "shippingWindow": "viernes",
            "editWindow": "Disponible durante todo el mes",
        }

        is_upcoming_candidate = (
            selection.year > current_year
            or (
                selection.year == current_year
                and selection.month >= current_month
            )
        )

        if upcoming is None and is_upcoming_candidate:
            upcoming = selection_data
        else:
            history.append(selection_data)

    return {
        "history": history,
        "upcoming": upcoming,
    }


@router.get("/cycle-info")
def get_cycle_info():
    return {
        "editable": is_edit_window_open(),
        "cycle_status": current_cycle_status(),
        "cycle_status_label": get_cycle_status_label(),
        "edit_window": "Disponible durante todo el mes",
        "admin_review_window": "lunes a jueves",
        "shipping_window": "viernes",
        "business_rule": "El socio puede cambiar sus productos editables durante todo el mes. Administración revisa pagos y suscripciones de lunes a jueves. Logística despacha los viernes las órdenes aprobadas.",
    }
