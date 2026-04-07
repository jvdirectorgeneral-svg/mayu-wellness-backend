from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import SessionLocal
from models import Commission, Ambassador, AmbassadorReferral, User, Plan


router = APIRouter(prefix="/commissions", tags=["commissions"])


# =========================
# DB SESSION
# =========================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# REQUEST SCHEMAS
# =========================
class GenerateMonthlyCommissionsRequest(BaseModel):
    month: Optional[int] = None
    year: Optional[int] = None


# =========================
# HELPERS
# =========================
def calculate_commission(plan_price: float) -> float:
    return round(float(plan_price) * 0.145, 2)


def get_member_status(user: User) -> str:
    return "active" if user.membership_active else "inactive"


def get_payment_status(user: User) -> str:
    return "paid" if user.membership_active else "pending"


def get_eligibility_status(user: User, plan: Plan | None) -> str:
    if not user:
        return "cancelled"
    if not user.membership_active:
        return "ineligible"
    if user.membership_level is None:
        return "ineligible"
    if not plan:
        return "ineligible"
    if not plan.active:
        return "ineligible"
    return "eligible"


def is_user_eligible(user: User, plan: Plan | None) -> bool:
    return get_eligibility_status(user, plan) == "eligible"


def get_plan_by_user_level(db: Session, user: User) -> Plan | None:
    if user.membership_level is None:
        return None

    return db.query(Plan).filter(
        Plan.level == user.membership_level
    ).first()


# =========================
# TEST GENERAL
# =========================
@router.get("/test")
def commissions_test(db: Session = Depends(get_db)):
    total = db.query(Commission).count()
    pending = db.query(Commission).filter(Commission.status == "pending").count()
    paid = db.query(Commission).filter(Commission.status == "paid").count()
    cancelled = db.query(Commission).filter(Commission.status == "cancelled").count()

    return {
        "message": "commissions router ok",
        "total_commissions": total,
        "pending": pending,
        "paid": paid,
        "cancelled": cancelled
    }


# =========================
# GENERAR COMISIONES MENSUALES
# =========================
@router.post("/generate-monthly")
def generate_monthly_commissions(
    payload: GenerateMonthlyCommissionsRequest,
    db: Session = Depends(get_db)
):
    now = datetime.utcnow()
    month = payload.month or now.month
    year = payload.year or now.year

    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Mes inválido")

    if year < 2024 or year > 2100:
        raise HTTPException(status_code=400, detail="Año inválido")

    referrals = db.query(AmbassadorReferral).all()

    created_count = 0
    skipped_existing = 0
    skipped_not_eligible = 0
    skipped_missing_data = 0
    created_items = []
    skipped_items = []

    for referral in referrals:
        ambassador = db.query(Ambassador).filter(
            Ambassador.id == referral.ambassador_id
        ).first()

        if not ambassador:
            skipped_missing_data += 1
            skipped_items.append({
                "referral_id": referral.id,
                "reason": "Embajador no encontrado"
            })
            continue

        referred_user = db.query(User).filter(
            User.id == referral.user_id
        ).first()

        if not referred_user:
            skipped_missing_data += 1
            skipped_items.append({
                "referral_id": referral.id,
                "reason": "Usuario referido no encontrado"
            })
            continue

        plan = get_plan_by_user_level(db, referred_user)

        member_status = get_member_status(referred_user)
        payment_status = get_payment_status(referred_user)
        eligibility_status = get_eligibility_status(referred_user, plan)

        existing_commission = db.query(Commission).filter(
            Commission.ambassador_id == ambassador.id,
            Commission.referred_user_id == referred_user.id,
            Commission.month == month,
            Commission.year == year
        ).first()

        if existing_commission:
            skipped_existing += 1
            skipped_items.append({
                "ambassador_id": ambassador.id,
                "referred_user_id": referred_user.id,
                "reason": "Comisión ya existe para este mes"
            })
            continue

        if not is_user_eligible(referred_user, plan):
            skipped_not_eligible += 1
            skipped_items.append({
                "ambassador_id": ambassador.id,
                "referred_user_id": referred_user.id,
                "reason": "Usuario no elegible",
                "member_status": member_status,
                "payment_status": payment_status,
                "eligibility_status": eligibility_status
            })
            continue

        commission_amount = calculate_commission(plan.price)

        commission = Commission(
            ambassador_id=ambassador.id,
            referred_user_id=referred_user.id,
            plan_id=plan.id,
            month=month,
            year=year,
            base_amount=float(plan.price),
            commission_percent=14.5,
            commission_amount=commission_amount,
            member_status=member_status,
            payment_status=payment_status,
            eligibility_status=eligibility_status,
            status="pending",
            generated_at=datetime.utcnow(),
            notes=f"Comisión generada automáticamente para {month}/{year}"
        )

        db.add(commission)
        created_count += 1

        created_items.append({
            "ambassador_id": ambassador.id,
            "ambassador_code": ambassador.ambassador_code,
            "referred_user_id": referred_user.id,
            "referred_user_name": referred_user.name,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "base_amount": float(plan.price),
            "commission_amount": commission_amount,
            "month": month,
            "year": year
        })

    db.commit()

    return {
        "message": "Generación mensual completada",
        "month": month,
        "year": year,
        "created_count": created_count,
        "skipped_existing": skipped_existing,
        "skipped_not_eligible": skipped_not_eligible,
        "skipped_missing_data": skipped_missing_data,
        "created_items": created_items,
        "skipped_items": skipped_items
    }


# =========================
# LISTAR COMISIONES DE UN EMBAJADOR
# =========================
@router.get("/ambassador/{ambassador_id}")
def get_commissions_by_ambassador(
    ambassador_id: int,
    db: Session = Depends(get_db)
):
    ambassador = db.query(Ambassador).filter(Ambassador.id == ambassador_id).first()
    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    commissions = (
        db.query(Commission)
        .filter(Commission.ambassador_id == ambassador_id)
        .order_by(Commission.year.desc(), Commission.month.desc(), Commission.id.desc())
        .all()
    )

    results = []
    for c in commissions:
        referred_user = db.query(User).filter(User.id == c.referred_user_id).first()
        plan = db.query(Plan).filter(Plan.id == c.plan_id).first()

        results.append({
            "commission_id": c.id,
            "referred_user_id": c.referred_user_id,
            "referred_user_name": referred_user.name if referred_user else None,
            "referred_user_email": referred_user.email if referred_user else None,
            "plan_id": c.plan_id,
            "plan_name": plan.name if plan else None,
            "month": c.month,
            "year": c.year,
            "base_amount": c.base_amount,
            "commission_percent": c.commission_percent,
            "commission_amount": c.commission_amount,
            "member_status": c.member_status,
            "payment_status": c.payment_status,
            "eligibility_status": c.eligibility_status,
            "status": c.status,
            "generated_at": c.generated_at,
            "paid_at": c.paid_at,
            "notes": c.notes
        })

    return {
        "ambassador_id": ambassador.id,
        "ambassador_code": ambassador.ambassador_code,
        "total_items": len(results),
        "items": results
    }


# =========================
# RESUMEN DE COMISIONES DE UN EMBAJADOR
# =========================
@router.get("/ambassador/{ambassador_id}/summary")
def get_commissions_summary_by_ambassador(
    ambassador_id: int,
    db: Session = Depends(get_db)
):
    ambassador = db.query(Ambassador).filter(Ambassador.id == ambassador_id).first()
    if not ambassador:
        raise HTTPException(status_code=404, detail="Embajador no encontrado")

    commissions = (
        db.query(Commission)
        .filter(Commission.ambassador_id == ambassador_id)
        .all()
    )

    total_generated = round(sum(c.commission_amount for c in commissions), 2)
    total_pending = round(sum(c.commission_amount for c in commissions if c.status == "pending"), 2)
    total_paid = round(sum(c.commission_amount for c in commissions if c.status == "paid"), 2)
    total_cancelled = round(sum(c.commission_amount for c in commissions if c.status == "cancelled"), 2)

    active_members = len([c for c in commissions if c.member_status == "active"])
    eligible_members = len([c for c in commissions if c.eligibility_status == "eligible"])

    return {
        "ambassador_id": ambassador.id,
        "ambassador_code": ambassador.ambassador_code,
        "total_generated": total_generated,
        "total_pending": total_pending,
        "total_paid": total_paid,
        "total_cancelled": total_cancelled,
        "active_members_count": active_members,
        "eligible_members_count": eligible_members,
        "total_commission_records": len(commissions)
    }


# =========================
# RESUMEN GENERAL PARA ADMIN / SUPERVISOR
# =========================
@router.get("/summary/general")
def get_general_commissions_summary(db: Session = Depends(get_db)):
    commissions = db.query(Commission).all()

    total_generated = round(sum(c.commission_amount for c in commissions), 2)
    total_pending = round(sum(c.commission_amount for c in commissions if c.status == "pending"), 2)
    total_paid = round(sum(c.commission_amount for c in commissions if c.status == "paid"), 2)
    total_cancelled = round(sum(c.commission_amount for c in commissions if c.status == "cancelled"), 2)

    total_eligible = len([c for c in commissions if c.eligibility_status == "eligible"])
    total_not_eligible = len([c for c in commissions if c.eligibility_status != "eligible"])

    return {
        "total_commissions": len(commissions),
        "total_generated": total_generated,
        "total_pending": total_pending,
        "total_paid": total_paid,
        "total_cancelled": total_cancelled,
        "total_eligible_records": total_eligible,
        "total_not_eligible_records": total_not_eligible
    }


# =========================
# RANKING SIMPLE DE EMBAJADORES
# =========================
@router.get("/ranking/ambassadors")
def get_ambassadors_ranking(db: Session = Depends(get_db)):
    rows = (
        db.query(
            Commission.ambassador_id,
            func.count(Commission.id).label("total_records"),
            func.coalesce(func.sum(Commission.commission_amount), 0).label("total_generated"),
            func.coalesce(
                func.sum(
                    func.case(
                        (Commission.status == "pending", Commission.commission_amount),
                        else_=0
                    )
                ),
                0
            ).label("total_pending"),
            func.coalesce(
                func.sum(
                    func.case(
                        (Commission.status == "paid", Commission.commission_amount),
                        else_=0
                    )
                ),
                0
            ).label("total_paid")
        )
        .group_by(Commission.ambassador_id)
        .order_by(func.coalesce(func.sum(Commission.commission_amount), 0).desc())
        .all()
    )

    ranking = []
    for row in rows:
        ambassador = db.query(Ambassador).filter(Ambassador.id == row.ambassador_id).first()
        ambassador_user = db.query(User).filter(User.id == ambassador.user_id).first() if ambassador else None

        ranking.append({
            "ambassador_id": row.ambassador_id,
            "ambassador_code": ambassador.ambassador_code if ambassador else None,
            "ambassador_name": ambassador_user.name if ambassador_user else None,
            "total_records": row.total_records,
            "total_generated": round(float(row.total_generated or 0), 2),
            "total_pending": round(float(row.total_pending or 0), 2),
            "total_paid": round(float(row.total_paid or 0), 2)
        })

    return {
        "total_ambassadors_in_ranking": len(ranking),
        "items": ranking
    }


# =========================
# LISTAR COMISIONES PENDIENTES
# =========================
@router.get("/pending")
def get_pending_commissions(db: Session = Depends(get_db)):
    commissions = (
        db.query(Commission)
        .filter(Commission.status == "pending")
        .order_by(Commission.year.desc(), Commission.month.desc(), Commission.id.desc())
        .all()
    )

    items = []
    for c in commissions:
        ambassador = db.query(Ambassador).filter(Ambassador.id == c.ambassador_id).first()
        ambassador_user = db.query(User).filter(User.id == ambassador.user_id).first() if ambassador else None
        referred_user = db.query(User).filter(User.id == c.referred_user_id).first()
        plan = db.query(Plan).filter(Plan.id == c.plan_id).first()

        items.append({
            "commission_id": c.id,
            "ambassador_id": c.ambassador_id,
            "ambassador_name": ambassador_user.name if ambassador_user else None,
            "ambassador_code": ambassador.ambassador_code if ambassador else None,
            "referred_user_id": c.referred_user_id,
            "referred_user_name": referred_user.name if referred_user else None,
            "plan_name": plan.name if plan else None,
            "commission_amount": c.commission_amount,
            "month": c.month,
            "year": c.year,
            "status": c.status
        })

    return {
        "total_items": len(items),
        "items": items
    }


# =========================
# LISTAR COMISIONES PAGADAS
# =========================
@router.get("/paid")
def get_paid_commissions(db: Session = Depends(get_db)):
    commissions = (
        db.query(Commission)
        .filter(Commission.status == "paid")
        .order_by(Commission.year.desc(), Commission.month.desc(), Commission.id.desc())
        .all()
    )

    items = []
    for c in commissions:
        ambassador = db.query(Ambassador).filter(Ambassador.id == c.ambassador_id).first()
        ambassador_user = db.query(User).filter(User.id == ambassador.user_id).first() if ambassador else None
        referred_user = db.query(User).filter(User.id == c.referred_user_id).first()
        plan = db.query(Plan).filter(Plan.id == c.plan_id).first()

        items.append({
            "commission_id": c.id,
            "ambassador_id": c.ambassador_id,
            "ambassador_name": ambassador_user.name if ambassador_user else None,
            "ambassador_code": ambassador.ambassador_code if ambassador else None,
            "referred_user_id": c.referred_user_id,
            "referred_user_name": referred_user.name if referred_user else None,
            "plan_name": plan.name if plan else None,
            "commission_amount": c.commission_amount,
            "month": c.month,
            "year": c.year,
            "status": c.status,
            "paid_at": c.paid_at
        })

    return {
        "total_items": len(items),
        "items": items
    }


# =========================
# MARCAR COMISIÓN COMO PAGADA
# =========================
@router.put("/{commission_id}/mark-paid")
def mark_commission_as_paid(
    commission_id: int,
    db: Session = Depends(get_db)
):
    commission = db.query(Commission).filter(Commission.id == commission_id).first()

    if not commission:
        raise HTTPException(status_code=404, detail="Comisión no encontrada")

    if commission.status == "paid":
        return {
            "message": "La comisión ya estaba pagada",
            "commission_id": commission.id,
            "status": commission.status,
            "paid_at": commission.paid_at
        }

    commission.status = "paid"
    commission.paid_at = datetime.utcnow()
    commission.notes = (
        f"{commission.notes or ''} | Marcada como pagada manualmente el {datetime.utcnow().isoformat()}"
    ).strip(" |")

    db.commit()
    db.refresh(commission)

    return {
        "message": "Comisión marcada como pagada correctamente",
        "commission_id": commission.id,
        "status": commission.status,
        "paid_at": commission.paid_at
    }
