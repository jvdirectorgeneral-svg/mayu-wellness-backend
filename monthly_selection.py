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
    weekday = datetime.now().weekday()
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


def format_month_label(month: int, year: int):
    months = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
    }
    return f"{months.get(month, 'Mes')} {year}"


def product_to_option(p: models.Product):
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category,
        "price": p.price,
        "description": p.description,
        "image_url": p.image_url,
        "active": p.active,
    }


def get_products_by_category(db: Session, category: str):
    products = (
        db.query(models.Product)
        .filter(models.Product.category == category, models.Product.active == True)
        .order_by(models.Product.name.asc())
        .all()
    )
    return [product_to_option(p) for p in products]


def get_available_editable_sections_by_level(db: Session, level: int):
    coloides = get_products_by_category(db, "coloides")
    cbd = get_products_by_category(db, "cbd")
    bienestar = get_products_by_category(db, "bienestar")
    hongos = get_products_by_category(db, "hongos")
    soporte = get_products_by_category(db, "soporte_funcional")

    if level == 1:
        return [
            {"section_key": "coloides", "section_name": "Coloide a libre elección", "category": "coloides", "max_items": 1, "products": coloides},
            {"section_key": "cbd", "section_name": "Producto CBD a elección", "category": "cbd", "max_items": 1, "products": cbd},
            {"section_key": "bienestar", "section_name": "Producto bienestar a elección", "category": "bienestar", "max_items": 1, "products": bienestar},
        ]

    if level == 2:
        return [
            {"section_key": "coloides", "section_name": "Coloide a libre elección", "category": "coloides", "max_items": 1, "products": coloides},
            {"section_key": "hongos", "section_name": "Hongos medicinales", "category": "hongos", "max_items": 1, "products": hongos},
            {"section_key": "cbd", "section_name": "Producto CBD a elección", "category": "cbd", "max_items": 1, "products": cbd},
            {"section_key": "bienestar", "section_name": "Producto bienestar a elección", "category": "bienestar", "max_items": 1, "products": bienestar},
        ]

    if level == 3:
        return [
            {"section_key": "coloides", "section_name": "Coloide a libre elección", "category": "coloides", "max_items": 1, "products": coloides},
            {"section_key": "cbd", "section_name": "Producto CBD a elección", "category": "cbd", "max_items": 1, "products": cbd},
            {"section_key": "bienestar", "section_name": "Producto bienestar CBD", "category": "bienestar", "max_items": 1, "products": bienestar},
            {"section_key": "soporte_funcional", "section_name": "Soporte funcional", "category": "soporte_funcional", "max_items": 1, "products": soporte},
            {"section_key": "extra_bienestar", "section_name": "Producto extra bienestar", "category": "bienestar", "max_items": 1, "products": bienestar},
        ]

    return []


def get_available_editable_products_by_level(db: Session, level: int):
    sections = get_available_editable_sections_by_level(db, level)
    products = []

    for section in sections:
        for product in section["products"]:
            if product["name"] not in products:
                products.append(product["name"])

    return products


def get_product_by_input(db: Session, item: MonthlySelectionItemInput):
    if item.product_id is not None:
        return (
            db.query(models.Product)
            .filter(models.Product.id == item.product_id, models.Product.active == True)
            .first()
        )

    if item.product_name and item.product_name.strip():
        return (
            db.query(models.Product)
            .filter(models.Product.name == item.product_name.strip(), models.Product.active == True)
            .first()
        )

    return None


def get_selected_products(db: Session, selection_id: int):
    items = (
        db.query(models.MonthlySelectionItem)
        .filter(models.MonthlySelectionItem.monthly_selection_id == selection_id)
        .all()
    )

    products = []

    for item in items:
        product = db.query(models.Product).filter(models.Product.id == item.product_id).first()

        if product:
            products.append({
                "product_id": product.id,
                "name": product.name,
                "category": product.category,
                "quantity": item.quantity,
            })

    return products


def get_order_for_selection(db: Session, user_id: int, month: int, year: int):
    return (
        db.query(models.Order)
        .filter(
            models.Order.user_id == user_id,
            models.Order.month == month,
            models.Order.year == year,
        )
        .first()
    )


def get_tracking_history(order):
    if not order:
        return []

    history = getattr(order, "tracking_history", []) or []

    return [
        {
            "id": h.id,
            "order_id": h.order_id,
            "status": h.status,
            "note": h.note,
            "carrier": h.carrier,
            "tracking_number": h.tracking_number,
            "tracking_url": h.tracking_url,
            "created_by": h.created_by,
            "created_at": h.created_at,
        }
        for h in sorted(
            history,
            key=lambda x: x.created_at or datetime.utcnow(),
            reverse=True,
        )
    ]


def order_tracking_data(order):
    if not order:
        return {
            "order_id": None,
            "order_code": None,
            "order_status": None,
            "carrier": None,
            "tracking_number": None,
            "tracking_url": None,
            "shipping_notes": None,
            "shipping_batch_date": None,
            "prepared_at": None,
            "shipped_at": None,
            "delivered_at": None,
            "logistics_notes": None,
            "tracking_history": [],
        }

    return {
        "order_id": order.id,
        "order_code": order.order_code,
        "order_status": order.status,
        "carrier": getattr(order, "carrier", None),
        "tracking_number": getattr(order, "tracking_number", None),
        "tracking_url": getattr(order, "tracking_url", None),
        "shipping_notes": getattr(order, "shipping_notes", None),
        "shipping_batch_date": getattr(order, "shipping_batch_date", None),
        "prepared_at": getattr(order, "prepared_at", None),
        "shipped_at": getattr(order, "shipped_at", None),
        "delivered_at": getattr(order, "delivered_at", None),
        "logistics_notes": getattr(order, "logistics_notes", None),
        "tracking_history": get_tracking_history(order),
    }


@router.post("/init")
def init_monthly_selection(
    payload: MonthlySelectionInitRequest,
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()

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
    editable_now = is_edit_window_open()

    selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user.id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )

    if not selection:
        selection = models.MonthlySelection(
            user_id=user.id,
            plan_id=plan.id,
            month=month,
            year=year,
            status="draft",
            editable=editable_now,
        )
        db.add(selection)
    else:
        selection.editable = editable_now

    db.commit()
    db.refresh(selection)

    products = get_selected_products(db, selection.id)

    return {
        "message": "Selección mensual lista",
        "selection_id": selection.id,
        "month": selection.month,
        "year": selection.year,
        "editable": editable_now,
        "status": selection.status,
        "cycle_status": current_cycle_status(),
        "cycle_status_label": get_cycle_status_label(),
        "plan_level": user.membership_level,
        "plan_name": get_plan_name_by_level(user.membership_level),
        "products": products,
        "editable_product": products[0]["name"] if products else None,
        "editable_products": products,
        "editable_sections": get_available_editable_sections_by_level(db, user.membership_level),
        "available_editable_products": get_available_editable_products_by_level(db, user.membership_level),
        "fixed_products": [],
        "edit_window": "Disponible durante todo el mes",
        "admin_review_window": "lunes a jueves",
        "shipping_window": "viernes",
    }


@router.get("/user/{user_id}")
def get_user_monthly_selection(
    user_id: int,
    db: Session = Depends(get_db),
):
    now = datetime.now()
    month = now.month
    year = now.year
    editable_now = is_edit_window_open()

    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    selection = (
        db.query(models.MonthlySelection)
        .filter(
            models.MonthlySelection.user_id == user_id,
            models.MonthlySelection.month == month,
            models.MonthlySelection.year == year,
        )
        .first()
    )

    if not selection:
        raise HTTPException(
            status_code=404,
            detail="No existe selección mensual para este usuario",
        )

    selection.editable = editable_now
    db.commit()
    db.refresh(selection)

    products = get_selected_products(db, selection.id)
    order = get_order_for_selection(db, user_id, month, year)

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
        "editable_product": products[0]["name"] if products else None,
        "editable_products": products,
        "editable_sections": get_available_editable_sections_by_level(db, user.membership_level or 0),
        "available_editable_products": get_available_editable_products_by_level(db, user.membership_level or 0),
        "fixed_products": [],
        "edit_window": "Disponible durante todo el mes",
        "admin_review_window": "lunes a jueves",
        "shipping_window": "viernes",
        **order_tracking_data(order),
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

    user = db.query(models.User).filter(models.User.id == selection.user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    editable_now = is_edit_window_open()
    selection.editable = editable_now

    if not editable_now and not payload.force_save:
        raise HTTPException(
            status_code=400,
            detail="La ventana de edición no está disponible",
        )

    allowed_products = get_available_editable_products_by_level(db, user.membership_level or 0)

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

        db.add(
            models.MonthlySelectionItem(
                monthly_selection_id=selection.id,
                product_id=product.id,
                quantity=1,
            )
        )
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

    selections = (
        db.query(models.MonthlySelection)
        .filter(models.MonthlySelection.user_id == user_id)
        .order_by(
            models.MonthlySelection.year.desc(),
            models.MonthlySelection.month.desc(),
        )
        .all()
    )

    if not selections:
        return {"history": [], "upcoming": None}

    now = datetime.now()
    current_month = now.month
    current_year = now.year

    history = []
    upcoming = None

    for selection in selections:
        selected_products = get_selected_products(db, selection.id)
        selected_names = [p["name"] for p in selected_products]
        order = get_order_for_selection(db, user_id, selection.month, selection.year)

        is_current_or_future = (
            selection.year > current_year
            or (selection.year == current_year and selection.month >= current_month)
        )

        selection_data = {
            "monthLabel": format_month_label(selection.month, selection.year),
            "month": selection.month,
            "year": selection.year,
            "planName": get_plan_name_by_level(user.membership_level or 0),
            "fixedProducts": [],
            "editableProduct": selected_names[0] if selected_names else "No seleccionado",
            "editableProducts": selected_names,
            "products": selected_products,
            "status": "Pendiente para próximo despacho semanal" if is_current_or_future else "Procesado",
            "shippingWindow": "viernes",
            "editWindow": "Disponible durante todo el mes",
            **order_tracking_data(order),
        }

        if upcoming is None and is_current_or_future:
            upcoming = selection_data
        else:
            history.append(selection_data)

    return {"history": history, "upcoming": upcoming}


@router.get("/user/{user_id}/upcoming")
def get_user_upcoming_delivery(
    user_id: int,
    db: Session = Depends(get_db),
):
    data = get_user_monthly_selection_history(user_id=user_id, db=db)
    return {"upcoming": data.get("upcoming")}


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
