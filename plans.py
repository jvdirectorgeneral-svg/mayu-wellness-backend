from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/plans", tags=["Plans"])


@router.get("/")
def get_plans(db: Session = Depends(get_db)):
    return db.query(models.Plan).filter(models.Plan.active == True).all()


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
        existing = db.query(models.Plan).filter(models.Plan.level == p["level"]).first()

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

