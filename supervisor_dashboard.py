from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from database import SessionLocal
from models import User, Ambassador, Commission


router = APIRouter(prefix="/supervisor-dashboard", tags=["supervisor-dashboard"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# KPI GENERALES
# =========================
@router.get("/kpis")
def get_supervisor_kpis(db: Session = Depends(get_db)):
    total_users = db.query(User).count()

    active_users = db.query(User).filter(
        User.membership_active == True
    ).count()

    inactive_users = db.query(User).filter(
        User.membership_active == False
    ).count()

    total_ambassadors = db.query(Ambassador).count()

    total_generated = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).scalar()

    total_pending = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).filter(
        Commission.status == "pending"
    ).scalar()

    total_paid = db.query(
        func.coalesce(func.sum(Commission.commission_amount), 0)
    ).filter(
        Commission.status == "paid"
    ).scalar()

    total_commission_records = db.query(Commission).count()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "inactive_users": inactive_users,
        "total_ambassadors": total_ambassadors,
        "total_commission_records": total_commission_records,
        "total_generated": float(total_generated or 0),
        "total_pending": float(total_pending or 0),
        "total_paid": float(total_paid or 0)
    }


# =========================
# DISTRIBUCIÓN DE PLANES
# =========================
@router.get("/plan-distribution")
def get_plan_distribution(db: Session = Depends(get_db)):
    cobre = db.query(User).filter(
        User.membership_level == 1,
        User.membership_active == True
    ).count()

    plata = db.query(User).filter(
        User.membership_level == 2,
        User.membership_active == True
    ).count()

    oro = db.query(User).filter(
        User.membership_level == 3,
        User.membership_active == True
    ).count()

    total = cobre + plata + oro

    return {
        "total_active_members": total,
        "cobre": cobre,
        "plata": plata,
        "oro": oro
    }


# =========================
# RANKING DE EMBAJADORES
# =========================
@router.get("/ambassador-ranking")
def get_supervisor_ambassador_ranking(db: Session = Depends(get_db)):
    rows = (
        db.query(
            Commission.ambassador_id,
            func.count(Commission.id).label("total_records"),
            func.coalesce(func.sum(Commission.commission_amount), 0).label("total_generated")
        )
        .group_by(Commission.ambassador_id)
        .order_by(func.coalesce(func.sum(Commission.commission_amount), 0).desc())
        .all()
    )

    items = []
    for row in rows:
        ambassador = db.query(Ambassador).filter(
            Ambassador.id == row.ambassador_id
        ).first()

        ambassador_name = None
        ambassador_code = None

        if ambassador:
            ambassador_code = ambassador.ambassador_code
            ambassador_user = db.query(User).filter(
                User.id == ambassador.user_id
            ).first()
            if ambassador_user:
                ambassador_name = ambassador_user.name

        items.append({
            "ambassador_id": row.ambassador_id,
            "ambassador_name": ambassador_name,
            "ambassador_code": ambassador_code,
            "total_records": row.total_records,
            "total_generated": float(row.total_generated or 0)
        })

    return {
        "total_items": len(items),
        "items": items
    }


# =========================
# CRECIMIENTO MENSUAL DE USUARIOS
# =========================
@router.get("/monthly-users-growth")
def get_monthly_users_growth(db: Session = Depends(get_db)):
    rows = (
        db.query(
            extract("year", User.created_at).label("year"),
            extract("month", User.created_at).label("month"),
            func.count(User.id).label("total_users")
        )
        .group_by(
            extract("year", User.created_at),
            extract("month", User.created_at)
        )
        .order_by(
            extract("year", User.created_at),
            extract("month", User.created_at)
        )
        .all()
    )

    return [
        {
            "year": int(row.year),
            "month": int(row.month),
            "total_users": row.total_users
        }
        for row in rows
    ]


# =========================
# CRECIMIENTO MENSUAL DE COMISIONES
# =========================
@router.get("/monthly-commissions-growth")
def get_monthly_commissions_growth(db: Session = Depends(get_db)):
    rows = (
        db.query(
            Commission.year,
            Commission.month,
            func.count(Commission.id).label("total_records"),
            func.coalesce(func.sum(Commission.commission_amount), 0).label("total_generated")
        )
        .group_by(Commission.year, Commission.month)
        .order_by(Commission.year, Commission.month)
        .all()
    )

    return [
        {
            "year": row.year,
            "month": row.month,
            "total_records": row.total_records,
            "total_generated": float(row.total_generated or 0)
        }
        for row in rows
    ]
