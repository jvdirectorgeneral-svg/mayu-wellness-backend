from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db
import models

router = APIRouter(prefix="/plan-change", tags=["Plan Change"])


# =========================
# SOLICITAR CAMBIO DE PLAN
# =========================
@router.post("/request")
def request_plan_change(
    user_id: int,
    requested_plan_id: int,
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if not user.membership_level:
        raise HTTPException(status_code=400, detail="El usuario no tiene plan activo")

    current_plan = db.query(models.Plan).filter(
        models.Plan.level == user.membership_level
    ).first()

    if not current_plan:
        raise HTTPException(status_code=404, detail="Plan actual no encontrado")

    requested_plan = db.query(models.Plan).filter(
        models.Plan.id == requested_plan_id
    ).first()

    if not requested_plan:
        raise HTTPException(status_code=404, detail="Plan solicitado no encontrado")

    if current_plan.id == requested_plan.id:
        raise HTTPException(status_code=400, detail="Ya tienes este plan activo")

    now = datetime.now()
    effective_month = now.month + 1
    effective_year = now.year

    if effective_month == 13:
        effective_month = 1
        effective_year += 1

    existing = db.query(models.PlanChangeRequest).filter(
        models.PlanChangeRequest.user_id == user.id,
        models.PlanChangeRequest.status == "pending"
    ).first()

    if existing:
        return {
            "message": "Ya existe una solicitud pendiente",
            "request_id": existing.id,
            "effective_month": existing.effective_month,
            "effective_year": existing.effective_year
        }

    change_request = models.PlanChangeRequest(
        user_id=user.id,
        current_plan_id=current_plan.id,
        requested_plan_id=requested_plan.id,
        status="pending",
        effective_month=effective_month,
        effective_year=effective_year
    )

    db.add(change_request)
    db.commit()
    db.refresh(change_request)

    return {
        "message": "Solicitud de cambio creada",
        "request_id": change_request.id,
        "current_plan": current_plan.name,
        "requested_plan": requested_plan.name,
        "effective_month": effective_month,
        "effective_year": effective_year
    }


# =========================
# VER SOLICITUDES DE UN USUARIO
# =========================
@router.get("/user/{user_id}")
def get_user_plan_change_requests(user_id: int, db: Session = Depends(get_db)):
    requests = db.query(models.PlanChangeRequest).filter(
        models.PlanChangeRequest.user_id == user_id
    ).all()

    result = []
    for req in requests:
        current_plan = db.query(models.Plan).filter(models.Plan.id == req.current_plan_id).first()
        requested_plan = db.query(models.Plan).filter(models.Plan.id == req.requested_plan_id).first()

        result.append({
            "request_id": req.id,
            "status": req.status,
            "current_plan": current_plan.name if current_plan else None,
            "requested_plan": requested_plan.name if requested_plan else None,
            "effective_month": req.effective_month,
            "effective_year": req.effective_year,
        })

    return result


# =========================
# APLICAR CAMBIO DE PLAN
# (luego esto idealmente lo haría un proceso mensual)
# =========================
@router.post("/{request_id}/apply")
def apply_plan_change(request_id: int, db: Session = Depends(get_db)):
    req = db.query(models.PlanChangeRequest).filter(
        models.PlanChangeRequest.id == request_id
    ).first()

    if not req:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")

    if req.status != "pending":
        raise HTTPException(status_code=400, detail="La solicitud ya fue procesada")

    user = db.query(models.User).filter(models.User.id == req.user_id).first()
    requested_plan = db.query(models.Plan).filter(models.Plan.id == req.requested_plan_id).first()

    if not user or not requested_plan:
        raise HTTPException(status_code=404, detail="Usuario o plan no encontrado")

    user.membership_level = requested_plan.level
    req.status = "applied"

    db.commit()

    return {
        "message": "Cambio de plan aplicado correctamente",
        "user_id": user.id,
        "new_membership_level": user.membership_level
    }
