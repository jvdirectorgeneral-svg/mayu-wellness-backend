from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
import models

router = APIRouter(prefix="/plans", tags=["Plans"])


# =========================
# 📦 SCHEMAS
# =========================
class PlanCreate(BaseModel):
    name: str
    level: int
    price: float
    description: str = ""
    active: bool = True


class PlanUpdate(BaseModel):
    name: str | None = None
    level: int | None = None
    price: float | None = None
    description: str | None = None
    active: bool | None = None


# =========================
# 📋 LISTAR PLANES
# =========================
@router.get("/")
def get_plans(db: Session = Depends(get_db)):
    plans = (
        db.query(models.Plan)
        .order_by(models.Plan.level.asc())
        .all()
    )

    return [
        {
            "id": p.id,
            "name": p.name,
            "level": p.level,
            "price": p.price,
            "description": p.description,
            "active": p.active,
        }
        for p in plans
    ]


# =========================
# 📋 PLAN POR ID
# =========================
@router.get("/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = (
        db.query(models.Plan)
        .filter(models.Plan.id == plan_id)
        .first()
    )

    if not plan:
        raise HTTPException(
            status_code=404,
            detail="Plan no encontrado",
        )

    return {
        "id": plan.id,
        "name": plan.name,
        "level": plan.level,
        "price": plan.price,
        "description": plan.description,
        "active": plan.active,
    }


# =========================
# ➕ CREAR PLAN
# =========================
@router.post("/")
def create_plan(
    payload: PlanCreate,
    db: Session = Depends(get_db),
):
    existing_level = (
        db.query(models.Plan)
        .filter(models.Plan.level == payload.level)
        .first()
    )

    if existing_level:
        raise HTTPException(
            status_code=400,
            detail="Ya existe un plan con este nivel",
        )

    existing_name = (
        db.query(models.Plan)
        .filter(models.Plan.name == payload.name.strip())
        .first()
    )

    if existing_name:
        raise HTTPException(
            status_code=400,
            detail="Ya existe un plan con este nombre",
        )

    plan = models.Plan(
        name=payload.name.strip(),
        level=payload.level,
        price=payload.price,
        description=payload.description,
        active=payload.active,
    )

    db.add(plan)
    db.commit()
    db.refresh(plan)

    return {
        "message": "Plan creado correctamente",
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "level": plan.level,
            "price": plan.price,
            "description": plan.description,
            "active": plan.active,
        },
    }


# =========================
# ✏️ ACTUALIZAR PLAN
# =========================
@router.put("/{plan_id}")
def update_plan(
    plan_id: int,
    payload: PlanUpdate,
    db: Session = Depends(get_db),
):
    plan = (
        db.query(models.Plan)
        .filter(models.Plan.id == plan_id)
        .first()
    )

    if not plan:
        raise HTTPException(
            status_code=404,
            detail="Plan no encontrado",
        )

    if payload.name is not None:
        name = payload.name.strip()

        existing_name = (
            db.query(models.Plan)
            .filter(
                models.Plan.name == name,
                models.Plan.id != plan_id,
            )
            .first()
        )

        if existing_name:
            raise HTTPException(
                status_code=400,
                detail="Ya existe otro plan con este nombre",
            )

        plan.name = name

    if payload.level is not None:
        existing_level = (
            db.query(models.Plan)
            .filter(
                models.Plan.level == payload.level,
                models.Plan.id != plan_id,
            )
            .first()
        )

        if existing_level:
            raise HTTPException(
                status_code=400,
                detail="Ya existe otro plan con este nivel",
            )

        plan.level = payload.level

    if payload.price is not None:
        plan.price = payload.price

    if payload.description is not None:
        plan.description = payload.description

    if payload.active is not None:
        plan.active = payload.active

    db.commit()
    db.refresh(plan)

    return {
        "message": "Plan actualizado correctamente",
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "level": plan.level,
            "price": plan.price,
            "description": plan.description,
            "active": plan.active,
        },
    }


# =========================
# 🌱 SEED PLANES
# =========================
@router.post("/seed")
def seed_plans(db: Session = Depends(get_db)):
    plans = [
        {
            "name": "Nivel 1 - Cobre",
            "level": 1,
            "price": 40,
            "description": "Plan base",
        },
        {
            "name": "Nivel 2 - Plata",
            "level": 2,
            "price": 50,
            "description": "Plan intermedio",
        },
        {
            "name": "Nivel 3 - Oro",
            "level": 3,
            "price": 60,
            "description": "Plan premium",
        },
    ]

    created = []
    updated = []

    for p in plans:
        existing = (
            db.query(models.Plan)
            .filter(models.Plan.level == p["level"])
            .first()
        )

        if existing:
            existing.name = p["name"]
            existing.price = p["price"]
            existing.description = p["description"]
            existing.active = True

            updated.append(existing.name)

        else:
            plan = models.Plan(
                name=p["name"],
                level=p["level"],
                price=p["price"],
                description=p["description"],
                active=True,
            )

            db.add(plan)
            created.append(plan.name)

    db.commit()

    return {
        "message": "Planes creados o actualizados correctamente",
        "created": created,
        "updated": updated,
    }
