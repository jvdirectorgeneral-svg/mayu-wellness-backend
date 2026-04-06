from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import SessionLocal
from models import Commission, Ambassador, User


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

        results.append({
            "commission_id": c.id,
            "referred_user_id": c.referred_user_id,
            "referred_user_name": referred_user.name if referred_user else None,
            "plan_id": c.plan_id,
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
            func.coalesce(func.sum(Commission.commission_amount), 0).label("total_generated")
        )
        .group_by(Commission.ambassador_id)
        .order_by(func.sum(Commission.commission_amount).desc())
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
            "total_generated": round(float(row.total_generated or 0), 2)
        })

    return {
        "total_ambassadors_in_ranking": len(ranking),
        "items": ranking
    }
